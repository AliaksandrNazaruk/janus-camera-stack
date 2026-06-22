# CAMERA_SESSION_STATE_MODEL — Cycle 12 recon (desired-vs-actual, GATED, no code yet)

The last big "implicit state" question: where do DESIRED and ACTUAL camera/stream/session state live, who
owns each, and where do they diverge. The recon's honest finding: a desired-vs-actual reconcile
architecture **already exists and is mature** for the gateway/remote domain + runtime-config + NAT +
firewall + fleet (the G5.3 + Cycle 3/7/9 work). The genuine gaps are narrow and mostly about making the
IMPLICIT contract EXPLICIT — not building a new state machine (per the steer). No code until GO.

## State-ownership map (verified 2026-06-21)
| domain | DESIRED owner | ACTUAL probe | reconciler / bridge | modeled? |
|---|---|---|---|---|
| remote stream bindings | `stream_binding_store.StreamBinding` + allocator `desired_active` | `janus.janus_summary` (RTP age), node `reachability` | `binding_provision.reconcile_janus` (boot + run-once), `remote_stream_monitor` (steady-state), `reconcile_drift` (report) | ✅ strong |
| mountpoint allocations | `mountpoint_allocator` `desired_active` (`list_desired_active`) | live Janus mountpoints | `reconcile_janus` | ✅ |
| firewall (per-node RTP) | `firewall_sync.desired_rules` (derived from bindings) | nftables | `firewall_sync.reconcile` (dry-run diff = drift) | ✅ |
| fleet / nodes | declarative manifest + store nodes | node reachability probe | `fleet.plan` / `fleet.reconcile` | ✅ |
| runtime config | `runtime_revision_store` (validated revisions) | live settings / `rs-runtime.env` | `runtime_config_apply` (Cycle 3) | ✅ |
| NAT / TURN | `janus-nat.json` + apply-status sidecar | live `janus.jcfg` | the apply operation (Cycle 7; status pending→applied) | ✅ |
| thermal / fps mode | `mode_enforcer` (`SystemMode`) | thermal temp reads | thermal loop → `fps_profile` file (Cycle 9, one owner) | ✅ |
| **local camera (cam10)** | allocator `desired_active` + `activate_local` | encoder status, mux, snapshot freshness | watchdogs/FDIR (liveness recovery) | ⚠️ **partial** |
| **encoder / systemd** | implicit (desired_active → which units should run) | `encoder_admin` status (systemd) | sensor_lifecycle / FDIR | ⚠️ **implicit** |
| **Janus protocol sessions** | active viewers / streams | Janus session list | opportunistic orphan-destroy + `orphaned_janus_sessions_total` count | ⚠️ **gap (counted, not reconciled)** |
| admin auth sessions | `core/session_store` (sid→expiry) | in-memory | TTL prune on every check | ✅ self-contained |

The aggregated read-model (`services/ui_viewmodel`) already fuses DESIRED (binding store) + ACTUAL
(janus_summary, reachability, firewall dry-run diff) into the operator dashboard — a real desired/actual
view already ships.

## The genuine gaps (the recon owes you the precise list)
- **G-A — `binding.status` conflates intent / last-known / actual.** `StreamStatus`
  (`configured_offline` / `online` / …) is a STORED field. For LOCAL projections it's DERIVED from
  `desired_active` (`bindings.py:97` → online iff desired_active) = **desired intent**. For REMOTE it's
  written via `set_status` = **last-known operational**. The LIVE actual is a SEPARATE probe
  (`janus_summary` RTP). So one field means three things by context, and nothing documents "status is
  intent/last-known, NOT a live probe — read the drift report for actual." This is the sharpest model
  ambiguity.
- **G-B — local camera (cam10) has no drift PARITY with remote.** `reconcile_drift` is explicitly
  "stored REMOTE bindings vs live Janus" — the local stream's `desired_active` vs actual encoder/mux/RTP
  is reconciled only implicitly by FDIR liveness, with no read-only drift view like remote has.
- **G-C — Janus session orphans are counted, not reconciled.** Orphaned Janus sessions (actual sessions
  with no live owner) are best-effort destroyed on the proxy path + counted
  (`orphaned_janus_sessions_total`), but there's no systematic "active viewers (desired) vs Janus sessions
  (actual)" reaper. Minor (a metric + opportunistic cleanup, not a leak of correctness).

## What's NOT a gap (don't churn)
The remote/gateway reconcile model, `desired_active` as the desired source-of-truth, `reconcile_drift`,
the firewall/fleet/runtime/NAT reconcilers, `ui_viewmodel`, and `session_store` (cohesive auth sessions)
are all sound. There is NO call to build a unified state machine — the steer says so, and the existing
per-domain reconcilers are the right shape for a heterogeneous system.

## The minimal contract (D1 — gate)
- **(A) Document the desired/actual/last-known CONTRACT explicitly [LEAN].** A short
  `docs/CONTRACT.md` section (or a `StreamStatus` docstring contract) stating: `desired_active` = desired
  intent (operator's choice, the boot reconciler's source of truth); `binding.status` = last-known /
  intent projection, NOT a live probe; ACTUAL liveness = `janus_summary` / `reconcile_drift` /
  `remote_stream_monitor`; reconcilers own desired→actual convergence. Make the IMPLICIT model EXPLICIT so
  it's not re-derived or misused. Recon/docs only — zero code risk. Closes G-A's ambiguity.
- **(B) Close G-B — local drift parity:** extend `reconcile_drift` (or add `reconcile_drift_local`) to
  report cam10 `desired_active` vs actual encoder/mux/RTP, so local has the same read-only drift view as
  remote. A real but bounded code cut; behavior-additive (a new read-only report).
- **(C) Close G-C — a Janus-session orphan reaper:** a periodic "Janus sessions with no active owner →
  destroy" reconcile. Bounded, but lower value (orphans are already counted + opportunistically cleaned).
- **(D) Declare the model mature; document only the map (this note).** Cheapest; the gaps are minor.

## Open decisions to gate (GO before any code)
- **D1 — (A) contract doc / (B) local drift parity / (C) session reaper / (D) map-only.** Lean **(A)** —
  the highest value is making the existing strong model EXPLICIT (closes the one real ambiguity, G-A),
  with (B) as a worthwhile follow-up if you want local/remote drift symmetry.
- **D2 — guard?** A guard could assert "`binding.status` is never set from a live probe" (locks
  status = intent/last-known), but it's hard to express statically and risks being decorative. Lean: no
  guard, or a tiny doc-presence guard only if (A) lands.

## Status — DONE (2026-06-21), scope (A)
Decision **D1=(A)**: make the implicit desired/actual/last-known model EXPLICIT; no code-behavior change,
no guard (a "status never set from a probe" guard is hard to express statically → would be decorative).
- `app/services/stream_binding_store/models.py` — `StreamStatus` gained a contract docstring: it is the
  STORED last-known / intent projection, NOT a live probe; `desired_active` is the desired source of
  truth; ACTUAL is `janus_summary` / `reconcile_drift`. Closes the G-A ambiguity at the type.
- `docs/CONTRACT.md` — new "State model: desired vs actual" section: the DESIRED / STORED / ACTUAL /
  RECONCILER roles, the per-domain owners, and the rule (act on `desired_active` + the probes, not on
  `status`). Names the known asymmetries (G-B local drift parity, G-C session orphans) with a pointer to
  this design note.
- This recon (the state-ownership map) is the lasting artifact.

**Deferred (only if a concrete need appears):** G-B local cam10 drift parity (extend `reconcile_drift`);
G-C a systematic Janus-session orphan reaper. Both are bounded, additive, lower-value than the (now
explicit) contract.

## Red lines (incl. the steer)
Do NOT write a new unified state machine now. Don't touch the working per-domain reconcilers
(reconcile_janus / remote_stream_monitor / FDIR / firewall_sync / runtime_config_apply). No FastAPI in
application. No API response-shape change without a characterization test. Keep `desired_active` as the
desired source of truth. The first cut is making the model EXPLICIT, not rebuilding it. Tests-first for
any code; full non-e2e suite green per sub-commit.

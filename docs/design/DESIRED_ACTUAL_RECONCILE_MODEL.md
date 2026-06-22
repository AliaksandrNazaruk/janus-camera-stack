# Desired / Actual Reconcile Model (gateway control plane)

**Status:** Proposed · 2026-06-20
**Scope:** `janus_camera_page` (.10 gateway control plane). Generalizes
`UNIFIED_FDIR_OVER_STREAM_BINDINGS.md` §4.6–4.7 into one named model.

## Context

The 2026-06-20 incident and the fixes that followed showed the gateway *already*
reconciles desired→actual in several places — but with **no single, named model**, so
the invariants lived only in scattered code and could be silently lost:

- `binding_provision.reconcile_janus` — stored remote bindings → live Janus mountpoints (startup sweep).
- `remote_stream_monitor` — per-binding RTP liveness → status / producer-domain recovery.
- `firewall_sync` — per-binding desired RTP allow → actual nft/ipt rules.
- local sensor reconcile — static cameras 1305–1308 on startup.
- `_load_state` — the desired-state file itself vs. a readable, well-formed shape.

Because the model was implicit, three failure modes were possible and two occurred:
1. A working-tree regression **deleted `reconcile_janus`** → `.55` never recovered after a Janus restart.
2. `reconcile_janus` **re-created an operator-stopped stream** (ir1 mp 2002) — the "don't resurrect a stopped stream" rule wasn't written down (fixed in `e24759d`).
3. A corrupt desired-state store **silently became "empty desired"** → convergence would tear down the live fleet (H-02, fixed in `63487bd`).

**Decision:** name the model explicitly. Every present and future reconciler obeys the
same invariants (R1–R9 below) and is testable against them. This ADR does **not** add a
generic reconcile engine — it documents the contract the code already (mostly) follows
and that the recent fixes restored.

## The model

**DESIRED state** — what the operator / system wants. Sources:
- `stream_bindings.json` — bindings `(node, sensor) → mountpoint/port`, the node table.
- Per-binding FDIR policy + `fdir.enabled` (operator **Stop** marker) + node `maintenance`.
- Operator intent: Stop / maintenance / `configured_offline` (created-but-not-activated).
- Desired firewall rules (per-binding RTP allow from the producer host).

**ACTUAL state** — what is live. Sources:
- Janus live mountpoints (`janus_admin.list_mountpoints`).
- systemd unit states (`janus`, `rs-stream@*`, node-agent).
- RTP activity (`janus.janus_summary(...).video_age_ms`).
- node-agent health (`/healthz` on the remote node).
- Actual firewall rules.

**RECONCILER INVARIANTS** — every reconciler MUST obey:

| # | Invariant |
|---|---|
| **R1** | Converge ACTIVE desired → actual: create what the operator wants live and that is missing. |
| **R2** | **Respect operator Stop**: a binding with `fdir.enabled=false` (or node `maintenance`) is left alone — never auto-create its listener, never "recover" it. *(the ir1 invariant; `e24759d`)* |
| **R3** | Never mutate secrets as part of a reconcile pass. |
| **R4** | **Never restart the media plane** (Janus, `rs-stream@*`, producers) unless the specific use case explicitly authorizes it. Control-plane (L4) reconcile must be media-safe. |
| **R5** | **Fail closed on unreadable/corrupt DESIRED state** — never read corruption as "empty desired" (which would converge by deleting everything). *(H-02; `63487bd`)* |
| **R6** | Ownership guard: a remote binding may never touch a cam10-reserved (local-range) id. *(UNIFIED_FDIR §4.6)* |
| **R7** | **Report drift clearly** — emit `created/existing/failed/skipped` counts; surface degraded via `readyz` / diagnostics. Converge silently only for the genuine no-op. |
| **R8** | Per-item isolation: one bad binding never aborts the sweep. |
| **R9** | Idempotent: a second pass with no drift is a no-op. |

## How existing code maps to the model

| Reconciler | Desired | Actual | Invariants it carries |
|---|---|---|---|
| `reconcile_janus` | stored remote bindings | Janus mountpoints | R1, **R2** (`e24759d`), R6, R7, R8, R9 |
| `remote_stream_monitor` | per-binding intent | RTP `video_age_ms` | R2, R4, R7 |
| `firewall_sync` | per-binding allow | nft/ipt rules | R1, R7 |
| `_load_state` (store) | the desired file itself | — | **R5** (fail-closed + quarantine) |
| `tests/test_regression_guards` | "model is wired" | code + routes | guards R1/R2/R5 wiring against regression |

## Non-goals

- No new generic reconcile framework/engine **now** — avoid premature abstraction.
- No runtime behavior change — this ADR *names* the contract; the few spots that
  violated it were already fixed (`e24759d`, `63487bd`). It is documentation that
  makes the invariants explicit and testable.
- Local sensor pipeline is covered only by the mountpoint-existence invariant.

## Consequences

- New reconcilers — and a future desired/actual **unification** (one pass over all
  sources) — are written against R1–R9; each invariant becomes a test (the regression
  guards are the seed).
- **Implemented (read):** `GET /api/v1/admin/reconcile/drift` (read-only) computes
  `desired − actual` per the named sources and reports per R7 — it must never
  auto-converge destructively, and by construction cannot (`services/reconcile_drift.py`
  is a pure classifier; the route does no `ensure_janus`/firewall/`systemctl`).
- **Implemented (write):** `POST /api/v1/admin/reconcile/janus/run-once` — the explicit,
  idempotent write-counterpart (`binding_provision.run_janus_reconcile_once`). Creates the
  MISSING mountpoint for ACTIVE remote bindings only; the skip predicate is SHARED with the
  drift report (ensures are driven off its `missing_janus_mountpoint` items), so R2 (skip
  stopped/maintenance) and R6 (ownership guard) hold uniformly. Never restarts media,
  applies firewall, destroys an orphan, or provisions (R3/R4). Returns before/after drift.
- The deferred backlog (`admin_dashboard.py` split, operation journal, single console)
  is sequenced **after** this ADR and each item must respect R3/R4/R5.

## References

- Commits: `e24759d` (R2 — ir1 skip), `63487bd` (R5 — store fail-closed/quarantine),
  `747deca` (release secret gate), `a17d26f` (regression guards).
- `docs/design/UNIFIED_FDIR_OVER_STREAM_BINDINGS.md` (§4.6 ownership guard, §4.7 `reconcile_janus`).
- `docs/STATE_BASELINE_2026-06-20_AFTER_RECONCILE.md`.

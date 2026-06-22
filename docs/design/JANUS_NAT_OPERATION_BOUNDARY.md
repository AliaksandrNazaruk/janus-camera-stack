# JANUS_NAT_OPERATION_BOUNDARY — Cycle 7 recon + plan (GATED)

The external audit re-scored to 7.5/10 and named the residual risk: the runtime-orchestration knot around
Janus / NAT / TURN. `POST /janus/nat` is the densest mixed-runtime operation — it persists, renders,
applies, and restarts (local + a remote depth node) with NO transactional boundary and NO failure-stage
reporting. Cycle 7 makes this a proper, observable operation. This is a **runtime-consistency** fix, not a
cosmetic split. Tests-first: characterization (7A) is DONE and FROZEN before any change.

## The operation today — `app/routes/janus.py::update_janus_nat_config` (color_camera only)
```
POST /janus/nat  (admin-gated, rate-limited)
  1. keep-password: turn_pwd in ("","***") → reload stored secret (mask never clobbers the secret)
  2. save_nat_config(new_cfg)               # PERSIST desired → /etc/robot/janus-nat.json (atomic_write_text)
  3. patch_janus_cfg_with_nat(new_cfg)      # APPLY: ship JSON to `sudo janus-admin nat-config` (L3 writes jcfg)
       except RuntimeError → 500
  4. restart_janus(); restart_depth_camera_janus()   # RESTART local + remote depth-node Janus
       except RuntimeError → 500
  5. return masked cfg (turn_pwd → "***")
```

### The boundary, answered (the recon questions)
| stage | what | where | failure handling |
|---|---|---|---|
| desired | the submitted `JanusNatConfig` | request body | pydantic validation |
| **persisted** | `/etc/robot/janus-nat.json` | `save_nat_config` → `atomic_write_text` (Cycle-1 safe) | **no try/except** — OSError escapes UNMAPPED (bare 500) |
| applied (rendered) | jcfg NAT block | `patch_janus_cfg_with_nat` → `janus-admin nat-config` (L3 owns the write+flock) | maps `FileNotFoundError`/`TimeoutExpired`→`RuntimeError`; rc!=0→`RuntimeError` → 500 |
| restarted (local) | local janus | `restart_janus` → `janus-admin restart` | rc!=0→`RuntimeError`→500; **but Timeout/FileNotFound ESCAPE unmapped** |
| restarted (remote) | depth-node janus | `restart_depth_camera_janus` → HTTP POST to the depth peer | `RuntimeError`→500 (shared with local) |
| operator sees | success | masked 200 | — |
| operator sees | any failure | `500 {"detail": "<str>"}` | **no `failure_stage`, no applied-flags** |

## The gaps (what Cycle 7B closes)
- **G1 — persist-before-apply.** Desired is fully persisted (atomically) BEFORE apply is confirmed. If
  patch/restart fails, the store says the NEW config while live Janus runs the OLD one → silent
  desired/actual drift behind a 500. (The persist is atomic, so the file isn't torn — the drift is clean
  but real.)
- **G2 — conflated restart stages.** `restart_janus()` + `restart_depth_camera_janus()` share ONE try.
  Local-success + depth-fail = split fleet state (local applied & restarted, depth not), surfaced as one
  generic 500 with NO rollback and NO indication of which side is live.
- **G3 — restart error-mapping asymmetry.** `patch_*` defends against a hung/absent CLI (wraps
  `TimeoutExpired`/`FileNotFoundError`→`RuntimeError`); `restart_janus` does NOT → those escape the
  route's `except RuntimeError` as an unhandled bare 500. The sharpest current gap.
- **G4 — no operation result shape.** The error body is `{"detail": str}` only — no `failure_stage`
  (persist/apply/restart_local/restart_depth), no `applied_local`/`applied_depth`/`desired_persisted`
  flags. The operator cannot tell what state the system is in after a partial failure.
- **G5 (read path) — silent depth fallback.** On a depth node, `load_nat_config` whose color peer is
  unreachable silently returns baked-in defaults (no error) → masks node divergence. (Lower priority.)
- **Observations (not gaps, for 7B):** `render_nat_block` looks **vestigial** — `patch_*` ships
  `json.dumps(cfg.model_dump())` to the CLI, NOT the rendered block (L3 renders now). And `nat-config`'s
  docstring claims the CLI "owns write + restart" while the route ALSO calls `restart_janus` → a possible
  double-restart / unclear apply contract to pin down with the host `janus-admin` contract.

## Host contract VERIFIED — `host_infra/roles/janus/files/janus-admin.py` (Cycle 7A.1, 2026-06-21)
Read the deployed L3 CLI (the two copies — repo-root + scoped tree — are byte-identical). Answers the
open ambiguity definitively:

1. **`janus-admin nat-config`** = read JSON (stdin/`--file`) → acquire `/var/lock/janus-jcfg.lock`
   (flock, 60s) → **render NAT block in L3** → patch jcfg between BEGIN/END markers (missing markers →
   exit 3) → `atomic_write` jcfg → **`systemctl restart janus.service`** (UNLESS `--no-restart`, which
   stops after the patch, exit 0). So it is **patch + restart**, atomic under the lock.
2. **`janus-admin restart`** = acquire the same lock → `systemctl restart janus.service`. No reload, **no
   health verification**, no probe.
3. **Shared lock:** YES — both subcommands take `/var/lock/janus-jcfg.lock` (shared with the cron NAT
   updater + TURN rotator). nat-config then restart = TWO serialized lock acquisitions (released between).
4. **Exit-code contract:** `0` ok / `1` invalid input / `2` lock timeout / `3` jcfg mutation failed /
   `4` service restart failed / `5` unknown. L4's `patch_janus_cfg_with_nat` only checks `rc != 0` and
   raises ONE generic `RuntimeError(stderr)` — it **collapses** the rich exit codes (operator can't tell
   lock-timeout from restart-failed from jcfg-missing). **New fidelity gap (G6).**
5. **Partial apply within one call:** YES — `nat-config` can `atomic_write` the jcfg and THEN fail the
   restart → exit 4 with the **new jcfg already on disk** but janus not cleanly restarted. The sharpest
   partial-state case, and it happens INSIDE the single L3 call (invisible to L4's rc!=0 collapse).
6. **Is the route's separate `restart_janus()` needed after `nat-config`? NO.** L4 calls `nat-config`
   WITHOUT `--no-restart` (verified: argv is `["sudo",".../janus-admin","nat-config"]`, stdin payload),
   so L3 ALREADY restarts. The route then calls `restart_janus()` → **a redundant SECOND
   `systemctl restart janus.service`** (extra downtime blip + extra lock take). **Confirmed double
   restart (G7).** The docstring "owns write + restart" was CORRECT; the route didn't account for it.

**So the REAL operation today is:**
```
save (L4 atomic persist)
→ janus-admin nat-config   [lock → render(L3) → patch jcfg → restart janus]   (exit 1/2/3/4 → 1 RuntimeError)
→ janus-admin restart      [lock → restart janus AGAIN]                       (REDUNDANT, G7)
→ HTTP depth restart
```
Confirmed: L4 `render_nat_block` is **vestigial** (L3 renders); the double restart is real; the
exit-code richness is lost at L4. These reshape the 7B stage model below.

## Plan — sub-cycles (tests-first, suite green between)
- **7A — characterization (DONE, this commit).** `tests/test_janus_nat_operation_boundary.py` (14 tests)
  FREEZES current behavior incl. every gap above: stage order, keep-password, persist-OSError-escapes,
  patch maps timeout/missing, restart asymmetry (local RuntimeError→500; local timeout/missing escapes;
  depth-fail-after-local-success = split state), error shape lacks failure_stage, registration mode,
  silent depth read-fallback. **No code change.** A future diff to these tests is the audit trail for any
  error-model change.
- **7B — use-case extraction + close G1–G4.** Extract `app/application/janus_nat/update_nat_config.py`
  (FastAPI-free; the route becomes a thin adapter). Model the operation as explicit STAGES returning a
  result with `failure_stage` + applied-flags. Decisions to gate: persist-before-apply ordering
  (apply-then-persist? two-phase?), restart-stage separation (distinguish local vs depth), uniform error
  mapping (wrap restart's timeout/missing like patch). Re-point the 7A tests DELIBERATELY to the new
  result model (the diff = the audit trail). Likely a fitness guard for the operation result shape.
- **Cycle 8 — `AdminOperationRunner`.** ONLY after 7B gives the first concrete operation (NAT). Generalize
  the stage/result/audit pattern (operation_id, pending/running/succeeded/failed, failure_stage,
  duration_ms, audit; idempotency-key later). Do NOT introduce it abstractly before NAT motivates it.

## 7B design — decisions GO'd (2026-06-21)
- **D1 = patch-only + explicit restart.** L4 calls `janus-admin nat-config --no-restart` (patch jcfg
  ONLY), then ONE explicit `restart_janus()`. Kills the G7 double restart; gives distinct
  `patch_local_jcfg` + `restart_local_janus` stages (precise `failure_stage`).
- **D2 = staged desired/applied status.** Persist desired as `pending`, apply, then mark `applied` (or
  `failed`). The operator can see desired≠applied → closes G1 (no silent drift). Store-schema work →
  its own sub-commit (7B.2).
- **D3 = depth restart best-effort.** local-success + depth-fail ⇒ operation SUCCEEDS with a warning
  (`depth_restarted=false`) + audit, NOT a 500. Reflects reality (local already applied, no rollback) →
  closes G2.
- **D4 = structured `NatUpdateResult`, keep 200/500.** Result carries
  `failure_stage / desired_persisted / local_applied / local_restarted / depth_restarted / detail`
  (+ L3 `exit_code`/`reason` where surfaced → closes G6). HTTP status codes unchanged (200 success / 500
  hard failure) for backward compat. Also fix G3: make `restart_janus` map `TimeoutExpired`/
  `FileNotFoundError`→`RuntimeError` (symmetric with patch) so the use-case's error handling is uniform.

### 7B sub-commits
- **7B.1** — extract `app/application/janus_nat/update_nat_config.py` (FastAPI-free) with `NatUpdateResult`
  + the stage model (D1 `--no-restart`+explicit restart, D3 depth best-effort, D4 structured result,
  G3 uniform error mapping, G7 double-restart removed). Route → thin adapter (keep-password resolves in
  the use-case; route masks the response + maps result→HTTP). Re-point the 7A char tests DELIBERATELY
  (the diff = the audit trail). Suite green.
- **7B.2** — D2 staged desired/applied status (store schema + GET exposes it) closing G1; fitness guard
  for the operation-result shape; close the design note.

## Red lines (incl. the user's explicit steer)
7A is behavior-FREEZE — no production change. Don't touch the FDIR recovery ladder. Don't do a mass
`app/services` regroup. Don't pull everything out of routes at once. No generic Manager/Provider/Facade.
**Do NOT introduce `AdminOperationRunner` abstractly before the NAT use-case exists.** 7B changes the
error MODEL deliberately — never weaken a 7A assertion silently; update it as the documented audit trail.
Full non-e2e suite green per sub-commit.

Expected: a Janus NAT/TURN update that is transactional + observable (failure_stage, applied-flags), the
restart asymmetry closed, the operator no longer blind to partial state. ~7.5 → ~8.0 once 7B + Cycle 8
land. This is the audit's top residual-risk node.

## Status — 7B DONE (2026-06-21)
- **7A** `bad24db` — characterization (14 tests, behavior-freeze) + this design note.
- **7A.1** `f108772` — host janus-admin contract verified (see "Host contract VERIFIED" above): G7
  double-restart, vestigial L4 render, exit-code collapse, partial-apply-in-one-call all confirmed.
- **7B.1** `f30e57d` — extracted `app/application/janus_nat/update_nat_config.py` (FastAPI-free) +
  `NatUpdateResult`; route = thin adapter. Closed **G7** (no double restart — `nat-config --no-restart`
  + one explicit `restart_janus`), **G3** (`restart_janus` maps timeout/missing → `JanusAdminError`),
  **G4/G6** (structured 500 body + L3 `exit_code` via `JanusAdminError.exit_code`), **G2** (depth restart
  best-effort → 200 + `warnings`). 7A tests re-pointed deliberately (the diff is the audit trail).
- **7B.2** (this) — closed **G1**: the apply-status SIDECAR (`janus-nat.status.json`, sibling of the
  config; `pending` after persist → `applied`/`failed` after apply, with `diff_hash` + `failure_stage`).
  Best-effort write (never breaks the operation); fail-safe read (`unknown` on missing/corrupt). New
  read-only `GET /janus/nat/status`. Desired≠applied (partial-apply / crash-mid-apply) is now VISIBLE,
  not silently masked by the persisted config. Fitness guard **#23** locks the `NatUpdateResult`
  observability fields against a silent regression of G4. **23 fitness guards.**

**G5 (silent depth read-fallback) is NOT addressed** — it is a read-path concern, characterized + left
intentionally (an operator-divergence masking issue, lower priority than the write-path operation).

**Cycle 8 (AdminOperationRunner) is now unblocked** — the NAT operation is the concrete use-case that
motivates generalizing the stage/result/status pattern (operation_id, pending/running/succeeded/failed,
failure_stage, duration_ms, audit). Do it as its own cycle, recon-first.

# NAT_CONFIG_SPLIT — Cycle 10 recon (nat_config.py, GATED, no code yet)

Cycle 7 straightened the `POST /janus/nat` operation boundary (`NatUpdateResult`, status sidecar, no
double restart). With the operation now extracted into `app/application/janus_nat`, the underlying
`nat_config.py` (327 lines) can be taken apart safely. The recon's headline: the biggest win is NOT a
5-way split — it's **~70 lines (21%) of production-DEAD code** that Cycle 7 left vestigial, plus ONE
clean extraction. A full split would churn ~10 Cycle-7 patch anchors for limited cohesion gain. No code
until GO.

## Inventory — `nat_config.py` responsibilities (verified 2026-06-21)
| lines | symbol(s) | responsibility | live? |
|---|---|---|---|
| 32-33 | `NAT_BEGIN_MARKER` / `NAT_END_MARKER` | jcfg markers | **DEAD** (0 refs) |
| 39-40 | `JCFG_LOCK_PATH` / `JCFG_LOCK_TIMEOUT` | lock config | **DEAD** (only `_jcfg_lock`) |
| 44-74 | `_jcfg_lock` | flock CM on `/var/lock/janus-jcfg.lock` | **DEAD** — never called in nat_config; only `test_nat_config_lock` |
| 75-86 | `_env` | env helper for the model defaults | live (model) |
| 87-114 | `JanusNatConfig` | the config model | live |
| 115-138 | `generate_turn_credentials` | coturn HMAC-SHA1 ephemeral creds | live — **pure, zero coupling** |
| 139-184 | `_janus_nat_json` / `load_nat_config` / `save_nat_config` | config STORE (+ depth HTTP fallback) | live |
| 185-220 | `_UNKNOWN_STATUS` / `_janus_nat_status_json` / `config_diff_hash` / `write_apply_status` / `read_apply_status` | apply-status SIDECAR (Cycle 7B.2) | live |
| 222-259 | `render_nat_block` | render the jcfg NAT block | **DEAD** — never called in nat_config; only `test_janus_routes`; **L3 renders** (Cycle 7A.1) |
| 260-271 | `JanusAdminError` | typed CLI error (+ `exit_code`) | live |
| 272-319 | `patch_janus_cfg_with_nat` / `restart_janus` | janus-admin CLI wrapper | live |
| 320-327 | `restart_depth_camera_janus` | depth-node restart (HTTP) | live |

## Classification (the steer's A–G)
- **F — DEAD, remove** (~70 lines, the clean win): `render_nat_block` + `NAT_BEGIN/END_MARKER` (L3 owns
  rendering — proven in Cycle 7A.1: `patch_janus_cfg_with_nat` ships JSON to `janus-admin nat-config`,
  never the rendered block); `_jcfg_lock` + `JCFG_LOCK_PATH/TIMEOUT` (L3 owns the flock — `_jcfg_lock`
  has NO caller in nat_config). Both are only exercised by tests (`test_janus_routes` render tests;
  `test_nat_config_lock`); the REAL render + lock are tested in L3's `test_janus_admin_cli`. Removing
  them also drops the now-unused `import fcntl` / `import contextlib`.
- **D — extract (one clean cut):** `generate_turn_credentials` → `app/services/turn_credentials.py`. It's
  a PURE function (params + `hmac/hashlib/base64/time`), ZERO coupling to the rest of nat_config, with 2
  route importers (`routes/janus.py`, `routes/system.py`). The textbook low-churn extraction.
- **A — keep together (cohesive "NAT config persistence + apply primitives"):** the config model+store
  (`JanusNatConfig` / `_env` / `_janus_nat_json` / load/save), the status sidecar (Cycle 7B.2), the
  janus-admin CLI wrapper (`JanusAdminError` / `patch_janus_cfg_with_nat` / `restart_janus`), and the
  depth restart. These are the primitives the Cycle-7 use-case orchestrates; they share the config-path
  logic and are **heavily patch-anchored** (see below). Splitting them further churns Cycle 7 for little
  cohesion gain — the depth restart is 8 lines, the status sidecar derives its path from the config path.

## Importers + patch anchors (the split-churn surface)
- **Route imports:** `routes/janus.py` (`JanusNatConfig`, `generate_turn_credentials`, `load_nat_config`,
  `read_apply_status`, `restart_janus`), `routes/system.py` (`generate_turn_credentials`, `load_nat_config`).
- **Use-case:** `app/application/janus_nat/update_nat_config.py` calls `nat_config.{load,save}_nat_config`,
  `nat_config.patch_janus_cfg_with_nat`, `nat_config.restart_janus`, `nat_config.restart_depth_camera_janus`,
  `nat_config.config_diff_hash`, `nat_config.write_apply_status`, `nat_config.JanusAdminError`.
- **Cycle-7 test patch anchors (patch-at-source):** `app.services.nat_config.{_janus_nat_json, httpx,
  get_settings, subprocess, load_nat_config, save_nat_config, patch_janus_cfg_with_nat, restart_janus,
  restart_depth_camera_janus, JanusAdminError, read/write_apply_status, config_diff_hash}` across
  `test_janus_nat_operation_boundary`, `test_janus_routes`, `test_janus_service`, `test_nat_config_lock`.
A full 5-way split moves ALL of these → re-point ~10 anchors + the use-case + 2 routes. High churn on the
just-stabilized Cycle-7 operation, for splitting a (post-dead-removal) ~255-line cohesive module.

## Proposal — phased, minimal (D1 — gate)
- **Phase 1 — DELETE the dead code [unambiguous win].** Remove `render_nat_block` + markers + `_jcfg_lock`
  + `JCFG_LOCK_*` + the now-unused `fcntl`/`contextlib` imports; remove the dead tests they pin
  (`test_janus_routes` render-block tests; `test_nat_config_lock`). ~70 lines + 2 test groups gone, ZERO
  production behavior change (the code has no production caller). Low risk, high value.
- **Phase 2 — extract `turn_credentials.py` [one clean cut].** Move `generate_turn_credentials` (pure) to
  `app/services/turn_credentials.py`; re-point the 2 route imports + any test. nat_config re-exports it
  for one release if needed (avoids churn), or the 2 importers move directly. Clean, low churn.
- **NOT recommended now — the full 5-way split** (status / janus-admin-cli / depth into separate modules).
  The remaining module is cohesive ("NAT/TURN config persistence + apply primitives"), and the split
  churns ~10 Cycle-7 patch anchors + the use-case for little gain. (If wanted, a facade-package like
  Cycle 5 keeps `nat_config.X` resolving zero-churn — but that's machinery for a 255-line module.)

## Explicitly DEAD (removable) — the list the recon owes you
`render_nat_block`, `NAT_BEGIN_MARKER`, `NAT_END_MARKER`, `_jcfg_lock`, `JCFG_LOCK_PATH`,
`JCFG_LOCK_TIMEOUT`, `import fcntl`, `import contextlib`; dead tests: `test_janus_routes` render-block
tests (the `render_nat_block` import + the 3 calls at ~247/260/267) and `test_nat_config_lock.py`
(exercises only `_jcfg_lock`; the real lock is L3's, tested in `host_infra/.../test_janus_admin_cli.py`).

## Open decisions to gate (GO before any code)
- **D1 — scope.** (A1) Phase 1 only (dead-code delete). (A2) Phase 1 + 2 (dead delete + extract
  turn_credentials) **[LEAN]**. (A3) full 5-way split (facade to avoid Cycle-7 churn).
- **D2 — turn_credentials re-export shim** in nat_config for one release, or move the 2 importers
  directly. Lean: move directly (only 2 importers — clean, no shim).
- **D3 — confirm L3 lock/render coverage** before deleting L4's copies (the lock + render ARE tested in
  `host_infra/.../test_janus_admin_cli.py`). Lean: verify, then delete.

## Status — DONE (2026-06-21), scope (A2)
Decisions: **D1=(A2)** dead-delete + extract turn_credentials (full 5-way split rejected as Cycle-7
churn); **D2** moved the 2 importers directly (no shim); **D3** verified L3 covers render+lock first;
**no guard** (dead-delete + a clean extraction has no meaningful new invariant — Cycle-6 honesty).
- **Phase 1 — dead-code delete:** removed `render_nat_block` + `NAT_BEGIN/END_MARKER` (L3 renders),
  `_jcfg_lock` + `JCFG_LOCK_PATH/TIMEOUT` (L3 owns the flock), and the now-unused `fcntl`/`contextlib`
  imports — all confirmed to have NO production caller (verified L3 covers render+lock in
  `host_infra/roles/janus/tests/test_janus_admin_cli.py`). Deleted the dead tests: `test_nat_config_lock.py`
  (9 funcs, exercised only `_jcfg_lock`) and `test_janus_routes.TestRenderNatBlock` (+ the import).
- **Phase 2 — extract `app/services/turn_credentials.py`:** moved the pure `generate_turn_credentials`
  (zero coupling); re-pointed the 2 importers (`routes/janus.py`, `routes/system.py`) directly; added
  `tests/test_turn_credentials.py` (the helper had no unit test before). Removed the now-unused
  `hmac`/`base64`/`Tuple` from nat_config.
- **Result:** `nat_config.py` 327 → **234 lines (−93, −28%)**, now a cohesive "NAT/TURN config
  persistence + status sidecar + janus-admin apply primitives" module + a separate `turn_credentials`.
  Zero `POST /janus/nat` behavior change, `NatUpdateResult` + status-sidecar schema untouched, Cycle-7
  patch anchors all intact (nothing in the cohesive core moved). Full non-e2e suite green; zero new lint
  debt. The further 5-way split stays deferred (cohesive + Cycle-7-anchor churn not worth it).

## Red lines (incl. the steer)
No change to `POST /janus/nat` behavior, `NatUpdateResult`, or the status-sidecar schema. Don't touch
`host_infra` janus-admin (it owns the real render+lock). No generic Manager/Provider. Don't move code for
symmetry — the dead-code removal + the one pure extraction are the value; the rest stays cohesive.
Tests-first; full non-e2e suite green per sub-commit. Confirm L3 covers the lock/render before deleting
L4's dead copies.

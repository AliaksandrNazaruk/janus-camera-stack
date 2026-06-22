# SERVICE_CONTROL_CONSISTENCY — Cycle 2 recon + plan (GATED, no code yet)

Finishes what P1 started: P1 routed the broad `sudo /bin/systemctl` paths (services_admin restart,
recovery reboot) through the scoped `service-admin` CLI. Cycle 2 closes the LAST destructive mutation
path that still bypasses it, and separates the read-only systemd surface from the destructive one.
Behavior-preserving. Not host-coupled (service-admin is already deployed). No code until GO.

## Recon — the full mutation inventory (verified 2026-06-21)
Destructive service mutations in app/**:
| path | mechanism | scoped? |
|---|---|---|
| `services_admin.restart_service` (systemctl branch) | `service_control.restart_unit` → service-admin | ✅ P1 |
| `services_admin` (encoder branch) | `encoder_admin.invoke` → encoder-admin CLI | ✅ |
| `recovery_executor` | `service-admin` reboot / janus-admin / encoder-admin / camera-admin | ✅ P1 |
| `nat_config` | `system.run([sudo, /usr/local/bin/janus-admin, restart/nat-config])` | ✅ (raw) |
| `mode_enforcer` | `system.run([sudo, /usr/local/bin/encoder-admin, stop])` | ✅ (raw) |
| `application/camera/color_config`, `services/sensor_tuning_env` | `system.run([sudo, /usr/local/bin/encoder-admin, restart])` | ✅ (raw) |
| **`application/config_apply`** (restart janus / relay / hook) | **bare `systemd.systemctl_action("restart", ...)`** | ❌ **BYPASS** |

`systemctl_action` literal-action usage across app/**: **`is-active` ×1** (read, `systemd.is_active`) +
**`restart` ×4 (ALL in config_apply)**. So once config_apply is routed through the port, `systemctl_action`
is read-only by use.

### The bypass (the one thing Cycle 2 fixes)
`config_apply.apply` restarts the SAME units the port already owns — janus, janus-textroom-relay,
janus_camera_page_hook — but via the **bare** `systemctl_action` (no sudo; relies on the unit's
override.conf systemctl rights), not the scoped `service-admin`. So janus-restart has TWO code paths
(services_admin → service-admin; config_apply → bare systemctl). `service-admin`'s internal allowlist
ALREADY includes janus / coturn / janus-textroom-relay / janus_camera_page_hook and normalises a trailing
`.service`, so routing config_apply through `service_control.restart_unit` needs no host change.

### Read-only surface (must stay separate, untouched)
`systemd.show`, `systemd.is_active` (→ `systemctl_action("is-active")`) — status reads used by
services_admin / encoder_admin / config_view. These stay as the read path.

### Test oracles (re-point with identical outcomes)
- `test_config_admin.test_apply_restart_order_and_fallback` / `test_apply_partial_failure_strings` patch
  `config_apply.systemd.systemctl_action` and assert the call order + the `janus_restarted`/`relay_restarted`
  bools + error strings. → re-point to patch `config_apply.service_control.restart_unit`; the OUTCOME
  assertions stay identical; the call sequence simplifies (janus once — service-admin normalises `.service`;
  relay keeps its janus-textroom-relay → janus_camera_page_hook fallback).
- `test_config_admin` lines 48-65 characterize the bare `systemctl_action("restart", ...)` PRIMITIVE — these
  stay valid (the primitive still exists); production just stops calling it destructively (see D2).

## Plan — sub-commits (tests-first, suite green between)
1. **char/re-point** — update the config_apply apply-restart tests to drive `service_control.restart_unit`
   (same bools / errors / fallback), RED-until step 2.
2. **route config_apply → service_control** — `restart janus/relay/hook` via `service_control.restart_unit`
   (wrapped to the existing bool + the relay OR-fallback). Response shape + audit unchanged.
3. **guard** — fitness guard banning a DESTRUCTIVE bare `systemctl_action("restart"|"start"|"stop"|"reload")`
   in app/** (reads `is-active`/`show`/`status` allowed). Locks the separation; passes once step 2 lands.

## Open decisions to gate (GO before any code)
- **D1 — route config_apply through `service_control`** (the bypass fix). **Lean: yes** — it's the core.
- **D2 — `systemctl_action` read/write split:** (A) keep it a generic primitive + the guard enforces "no
  destructive use in production" (minimal; the restart-primitive tests stay). (B) restrict the function to
  read-only actions (raise on restart/start/stop) → stronger split, but rewrites the primitive tests.
  **Lean: (A)** — minimal; the guard IS the separation.
- **D3 — raw-CLI call-style (optional Tier 2):** consolidate `system.run([sudo, .../encoder-admin, ...])`
  in color_config / sensor_tuning_env / mode_enforcer / nat_config onto the `encoder_admin.invoke` /
  janus-admin adapters. **Lean: DEFER** — they ARE scoped (just a call-style nit); color_config needs a
  specific `--instance color` + timeout the invoke adapter doesn't model yet. Separate cycle.
- **D4 — guard scope:** ban `systemctl_action("restart"|"start"|"stop"|"reload")` in app/**, no allowlist.
  **Lean: yes.**

## Red lines
Behavior-preserving: same units restarted, same `ApplyResponse` shape + audit strings, same
`janus_restarted`/`relay_restarted` bool semantics (failures still swallowed to a bool — service_control's
RuntimeError is caught → False). Do NOT touch the read-only `is_active`/`show` path. Don't fold in the
raw-CLI call-style cleanup (D3). No host change (service-admin already deployed + allowlists these units).
Tests-first; never weaken an assertion. Full non-e2e suite green per sub-commit.

Expected: closes the last service-mutation bypass (one consistent destructive path: service_control /
scoped admin CLI) + a guard. ~7.1–7.5 → ~7.4–7.7 on the consistency axis.

## Status — DONE (2026-06-21)
Decisions: **D1** route config_apply; **D2** keep `systemctl_action` a generic primitive (read-only BY
USE now) + the guard enforces it; **D3** DEFER the raw-CLI call-style consolidation; **D4** no-allowlist guard.
- **2.1** `17420d8` — `config_apply.apply` restart janus/relay/hook → `service_control.restart_unit`
  (the scoped service-admin port, shared with services_admin + recovery). The bare
  `systemctl_action("restart")` bypass is gone; the janus `.service`/bare double-try collapsed to one call
  (service-admin normalises the suffix); relay keeps its fallback; `ApplyResponse` + audit unchanged; exec
  failure / non-zero rc still swallowed to the bool. Not host-coupled. 3 apply tests re-pointed + 1 added.
- **2.2** (this) — fitness guard **#19** `test_no_destructive_systemctl_action_in_app`: bans
  `systemctl_action("restart"|"start"|"stop"|"reload")` in app/** (reads `is-active`/`show` allowed),
  unconditional. **19 fitness guards.**

**Result:** every destructive service mutation in app/** now goes through ONE boundary — `service_control`
(scoped `service-admin` CLI) or a scoped admin CLI (encoder-admin / janus-admin / camera-admin). `systemctl_action`
is read-only by use (only `is-active`); the read path (`show`/`is_active`) is untouched. **Deferred (D3, separate
cycle):** the raw `system.run([sudo, .../encoder-admin, ...])` call-style in `color_config` / `sensor_tuning_env`
/ `mode_enforcer` / `nat_config` (they ARE scoped CLIs — just not via the `encoder_admin.invoke` / janus-admin
adapters; `color_config` needs `--instance color` + a custom timeout the adapter doesn't model yet).

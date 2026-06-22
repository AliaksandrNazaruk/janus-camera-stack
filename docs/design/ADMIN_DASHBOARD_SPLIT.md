# admin_dashboard.py split — design note

**Status:** Proposed · 2026-06-20 · audit finding **C-04**
**Goal:** reduce route/orchestration surface and pay down architectural debt **without
adding capabilities**. Behavior-preserving, test-guarded, one commit per phase.

## Why

`app/routes/admin_dashboard.py` is **1213 lines / ~20 routes** and mixes all three layers
in one file: HTTP routing, orchestration, and five inlined infra concerns —

| Infra concern inlined today | Evidence |
|---|---|
| systemd | `subprocess.run(["systemctl"…])`, `sudo -n /bin/systemctl restart` (17× systemctl) |
| encoder-admin CLI | `sudo -n /usr/local/bin/encoder-admin <action>` |
| v4l2 / realsense probing | `_parse_v4l2_list_devices`, `_probe_v4l2_device_formats` |
| env-file IO | `_read_env_file`, `_write_env_files` |
| Janus admin HTTP | `_janus_admin_url`, `_streaming_attach/_destroy`, `_list_mountpoints_via_janus` (duplicates `services/janus_admin.py`) |

The reconcile endpoints (`drift`, `run-once`) already follow the layered model
(route → `binding_provision`/`reconcile_drift`). This note brings `admin_dashboard` to the
same shape; it is the inverse of adding features — it only **moves** code.

## Target architecture (the rule)

```
route (app/routes/admin_dashboard.py)   auth + request parse + call use-case + map to HTTP/response model
  → application use-case (app/application/…)   orchestration: validate, sequence, audit, shape result
      → infra adapter (app/services/…)         the side effect: subprocess / CLI / file IO / Janus HTTP
```

Routes keep their **paths, methods, response models, auth, and rate-limits unchanged**
(`test_url_contract_fitness` + `test_url_audit` stay green). Adapters keep the
`sudo -n /usr/local/bin/*-admin` and `sudo -n /bin/systemctl` contract verbatim
(`test_architecture_fitness` already guards that — must stay green).

## The full map (function → target home)

| Current (admin_dashboard.py) | Target layer | Target module |
|---|---|---|
| `_systemctl_show`, raw `systemctl restart` exec | infra | **`services/systemd.py`** (new): `show(unit)`, `restart_unit(unit)` |
| `_service_state`, `list_services`, `restart_service` orchestration | use-case | **`application/services_admin.py`** (new): `service_state`, `service_states`, `restart_service` |
| `_encoder_admin`, `_discover_encoder_units` | infra | **`services/encoder_admin.py`** (new): `invoke(action,family,instance)`, `discover_units()` |
| `_read_env_file`, `_write_env_files` | infra | **`services/encoder_env.py`** (new): `read_env_file`, `write_env_files` |
| `_validate_encoder_target`, `_encoder_instance_status`, start/stop routes orchestration | use-case | **`application/encoder_admin.py`** (new) |
| `provision_stream` body (mountpoint+env+encoder) | use-case | **`application/provision_stream.py`** (new) — fixes the route→route call into the create-mountpoint use-case |
| `_parse_v4l2_list_devices`, `_probe_v4l2_device_formats`, realsense/v4l2 list | infra | **`services/v4l2.py`** (EXISTS — consolidate into it) |
| `_janus_admin_url`, `_streaming_admin_key`, `_streaming_attach`, `_streaming_destroy_session`, `_list_mountpoints_via_janus`, create/destroy/info mountpoint exec | infra | **`services/janus_admin.py`** (EXISTS — de-duplicate into it) |
| `_read_audit_tail`, `get_audit_log` | use-case | **`application/audit_view.py`** (new) — `read_audit_tail` + `AuditEntry`; raw file path/writer stays in `services/audit_log.py` |
| `_primary_ip` | infra | **`services/netinfo.py`** (new, tiny) |
| `list_soak_files`, `get_soak_file` body | infra | **`services/soak_files.py`** (new): list + safe read (keep the no-traversal guard) |
| `dashboard_snapshot` aggregation | use-case | **`application/dashboard.py`** (new) |
| all `@router.*` handlers | route | stay in `admin_dashboard.py`, now THIN (call use-case, map result) |

Pydantic response models (`ServiceState`, `EncoderActionResponse`, …) stay where the route
imports them (or move to `app/routes/_models` / the use-case module) — unchanged shape.
`app/application/` is a **new package** (lightweight; no framework, just functions).

## Phases (ordered by risk/reward; one commit each; suite green after each)

1. ✅ **DONE** (`d6e9f3d`) — `services/systemd.py` + `services/encoder_admin.py` +
   `application/services_admin.py`; thinned `list_services` / `restart_service` /
   `dashboard_snapshot`. admin_dashboard 1213→1070 lines. (`encoder_admin.invoke` lives
   here already; phase 2 grows it with discovery + instance status.)
2. ✅ **DONE** (`a70d331`) — `services/encoder_env.py`, expanded `services/encoder_admin.py`,
   `application/encoder_admin.py` + `application/provision_stream.py`; thinned encoder/provision
   routes; fixed the route→route smell (create_mountpoint INJECTED, never imports routes).
   admin_dashboard 1070→871 (1213→871, −28% overall).
3A. ✅ **DONE** (`d55739c`) — v4l2/realsense probing consolidated into `services/v4l2.py` +
    `application/device_inventory.py`; device routes thinned. admin_dashboard 871→698 (−42% overall).
3B. ✅ **DONE** (`85c3dbe`) — dashboard Janus client → NEW `services/janus_dashboard_admin.py`
    (kept SEPARATE from the reconcile-path `janus_admin.py`, asserted by a test) +
    `application/mountpoint_admin.py`; mountpoint create/destroy/info routes thinned.
    Characterized first, de-dup only. admin_dashboard 698→476 (1213→476, −61% overall).
4. ✅ **DONE** (`66c5fa7`) — capstone. `application/audit_view.py` (`read_audit_tail` +
   `AuditEntry`; landed as a read **use-case**, not `services/audit_log.py`, since it filters
   + builds models — the raw log-file path/writer stays in `services/audit_log.py`),
   `services/netinfo.py` (`primary_ip` — the last route subprocess), `services/soak_files.py`
   (`list_files` + `read_file_bytes`, keeping the basename whitelist + 1MB cap),
   `application/dashboard.py` (`snapshot` + `DashboardSnapshot`). Routes thinned to delegates;
   admin_dashboard.py imports no subprocess/json/os/re/pathlib. **476→336 (1213→336, −72% overall).**
   Added fitness guard `test_routes_have_no_subprocess_systemctl_httpx`: `app/routes/**` must
   not import `subprocess`/`httpx` or carry a `"systemctl"` command literal. Pre-existing
   offenders (`depth.py` httpx mux client, `admin_config.py` janus/relay restart) are recorded
   in an explicit `_ROUTE_INFRA_BASELINE` (debt named, not hidden, can't grow); admin_dashboard.py
   is deliberately NOT allowlisted, locking the cleanup. Characterized first, then re-pointed.

## Non-goals (explicit)

- No new endpoints, no path/method/response-shape/auth/rate-limit changes.
- No behavior change — pure relocation; existing route tests are the behavior oracle.
- No Clean-Architecture framework; `app/application/` is just plain functions.
- The sudo'd `*-admin` / `systemctl` command contract is preserved byte-for-byte.

## Verification per phase

- `pytest tests/test_system_routes.py tests/test_architecture_fitness.py
  tests/test_url_contract_fitness.py tests/test_url_audit.py tests/test_boundary_fitness.py`
  (+ any `test_admin_dashboard*`) green.
- New adapter/use-case unit tests (subprocess mocked) for moved logic.
- `git diff` shows **moves**, not rewrites; route handler bodies shrink to call-and-map.

## Outcome

`admin_dashboard.py` dropped from **1213 → 336 lines (−72%)**, all handlers now thin
delegates; the infra concerns live in named adapters (`systemd`, `encoder_admin`,
`encoder_env`, `v4l2`, `janus_dashboard_admin`, `netinfo`, `soak_files`); orchestration
lives in `app/application/` (`services_admin`, `encoder_admin`, `provision_stream`,
`device_inventory`, `mountpoint_admin`, `audit_view`, `dashboard`); the
`test_routes_have_no_subprocess_systemctl_httpx` fitness guard prevents the fat-route
pattern from returning. No capability added or removed across any phase.

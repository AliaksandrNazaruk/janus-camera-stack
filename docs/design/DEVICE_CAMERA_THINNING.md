# DEVICE_CAMERA_THINNING — Phase 2 recon + plan (GATED, no code yet)

Part of [STRICT_ARCHITECTURE_HARDENING.md](STRICT_ARCHITECTURE_HARDENING.md). Thins the **A-01**
fat route `routes/device_camera.py` (413L, 9 endpoints) and **empties the G1 route→route allowlist**.
Strictly behavior-preserving. No code until GO.

## Recon — what device_camera.py does (verified 2026-06-21)

9 endpoints under `/cameras/{serial}/{sensor}/`:

| Endpoint | Orchestration in the route |
|---|---|
| GET `camera_config.html` (99) | `_require_running` + Jinja render + api_prefix logic |
| GET `viewer.html` (128) | `_require_running` + mountpoint check + template select + cold-start timings + rotation inject + api_prefix |
| POST `initialize` (203) | `lifecycle_initialize` + audit; map UnsupportedSensor→501 / LifecycleError→500 |
| POST `stop` (250) | `lifecycle_stop` + audit; map errors |
| GET `modes` (285) | `_require_running` → **camera.get_camera_modes()** |
| GET `sensors` (291) | `_require_running` → **camera.get_realsense_sensors()** |
| GET `rotation` (297) | `_read_ffmpeg_rotation_deg` (no auth) |
| GET `config` (311) | color → **camera.get_camera_stream_config()**; else `_read_rs_sensor_config` (env read) |
| POST `config` (324) | color → **camera.update_camera_stream_config()**; else `_write_rs_sensor_config` (env write + encoder-admin restart) |

### Debt found
- **G1 route→route (A-01):** imports five `@router`-decorated ROUTE HANDLERS + the model from
  `routes.camera` (`CameraStreamConfig`, `get_camera_modes`, `get_camera_stream_config`,
  `get_realsense_sensors`, `update_camera_stream_config`) and `_api_prefix_from_request` from
  `routes.templates`. device_camera calls camera.py's *handlers* as if they were use-cases.
- **Infra in the route module:** `_read_ffmpeg_rotation_deg` / `_read_rs_sensor_config` /
  `_write_rs_sensor_config` (lines 350-413) read+write `/etc/robot/rs-{sensor}.tuning.env` (via
  `env_store`) and run `encoder-admin restart` (via `system.run`). `_write_rs_sensor_config` raises
  `HTTPException` directly — a D3 leak inside a route helper.
- **Import-time config read:** `_jinja_env = Environment(FileSystemLoader(get_settings().templates_dir))`
  at module import (line 54). Tracked under **G5** (settings centralization), not fixed here.

### Characterization gap (A-05)
There is **no dedicated `test_device_camera.py`**. Coverage is scattered (test_streams_dashboard,
test_templates, url-audit / url-contract fitness, sensor_lock, e2e depth_click). So Phase 2 must
**add characterization tests for the 9 endpoints first** (lock current status codes + payloads),
then refactor against them.

## Plan — split 2A (self-contained) then 2B (decouple → clears G1)

### Phase 2A — extract device_camera's own logic (does NOT touch camera.py)
1. **Characterization tests** for the 9 endpoints (status + body shape + audit events).
2. NEW `services/sensor_tuning_env.py` adapter — move `_rs_tuning_path`, `_read_ffmpeg_rotation_deg`,
   `_read_rs_sensor_config`, `_write_rs_sensor_config` (the rs-{sensor}.tuning.env read/write +
   encoder-admin restart). **De-leak:** it raises a DOMAIN error (e.g. `TuningWriteFailed`); the route
   maps it to 500 (D3 parity).
3. NEW `application/device_camera/*` use-cases:
   - `resolve_running_sensor` — the `_resolve_or_404` / `_require_running` guards → domain results
     (SensorNotFound / NotProvisionable / Stopped); the route maps 404 / 501 / 409.
   - `initialize_sensor` / `stop_sensor` — wrap lifecycle + audit; route maps errors.
   - `read_sensor_config` / `write_sensor_config` — the color→delegate seam, else the tuning adapter.
4. **HTML endpoints** (camera_config / viewer): rendering stays in the route (it IS the boundary);
   extract the *data-gathering* into a view-model builder (entry / mountpoint / timings / rotation /
   template name / api_prefix) so the handler is parse → build-view-model → render.

### Phase 2B — break the sibling-route couplings (EMPTIES the G1 allowlist)
1. Move `CameraStreamConfig` to a shared module both routes import (not from each other).
2. Extract the four camera config functions into a use-case/service that BOTH `camera.py` and
   `device_camera` call; re-point camera.py's own handlers to the extracted use-case too.
3. Move `_api_prefix_from_request` to a shared helper (e.g. `app/core/http_prefix.py`).
4. Remove `device_camera.py` from `_ROUTE_COUPLING_ALLOWLIST` → **G1 becomes absolute.**

## Open decisions to gate (need GO before any code)
- **D1** — split 2A then 2B (recommended: 2A is contained; 2B touches camera.py — separate commits)?
- **D2** — HTML rendering stays in the route + a view-model use-case (recommended) vs move render out?
- **D3** — shared `CameraStreamConfig` home: `application/` model module vs a `services/` schema?
- **D4** — characterization tests as a separate prep commit first (recommended) vs folded into 2A?

## Red lines
Behavior-preserving: identical URLs, status codes, payloads, and audit events. Keep the
color→camera delegation semantics. No new features in device_camera. Each sub-phase:
characterization tests → move verbatim → re-point with identical assertions → suite green → one
gated commit.

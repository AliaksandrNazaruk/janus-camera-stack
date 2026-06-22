# DEVICE_CAMERA_2B_DECOUPLE — Phase 2B recon + plan (GATED, no code yet)

Part of [STRICT_ARCHITECTURE_HARDENING.md](STRICT_ARCHITECTURE_HARDENING.md) /
[DEVICE_CAMERA_THINNING.md](DEVICE_CAMERA_THINNING.md). Phase 2B breaks `routes/device_camera.py`'s
two couplings (to `routes.camera` and `routes.templates`) and **empties the G1 allowlist**. It
TOUCHES `camera.py` + `templates.py` (bigger blast radius than 2A). Behavior-preserving. No code yet.

## Recon — the couplings + blast radius (verified 2026-06-21)

### Coupling 1 — `routes.camera` (5 names, the G1 import)
| Name | kind | does |
|---|---|---|
| `CameraStreamConfig` | BaseModel | the config DTO (response_model + body) |
| `get_camera_modes` | sync `@router.get` | → `v4l2.list_v4l2_modes` |
| `get_realsense_sensors` | sync `@router.get` | → `realsense_catalog.query_catalog` |
| `get_camera_stream_config` | async `@router.get` | read **rs-color.tuning.env** → CameraStreamConfig |
| `update_camera_stream_config` | async `@router.post` | write **rs-color.tuning.env** + restart color |

device_camera calls these handlers directly (color path) at `:235/:241/:267/:280`.

**Color config IS the sensor_tuning_env path for sensor="color" — but with 3 differences to PRESERVE:**
1. **env path** — color uses `env_store.read_env()` (defaults to `settings.env_path` = rs-color.tuning.env,
   patchable in tests); `sensor_tuning_env` hardcodes `/etc/robot/rs-{sensor}.tuning.env`.
2. **restart** — color uses `restart_color_encoder()`; depth/IR uses generic `encoder-admin restart
   --instance <sensor>`.
3. **fields** — the color write sets `SNAPSHOT_FPS` + `PORT`; the depth/IR write omits them.

→ do NOT force-unify color into `sensor_tuning_env`; extract a **separate color-config use-case**.

`CameraStreamConfig` references: camera.py (def + 2×response_model + 2×return), device_camera.py
(import + 2×response_model + 2×return + 1×construct), tests (test_device_camera, test_camera_routes).

### Coupling 2 — `routes.templates._api_prefix_from_request`
Pure `Request → prefix` helper (templates.py:50, no service deps). Used by device_camera (2×) AND
templates.py itself (3×). Move to a shared helper → re-point 5 sites.

### Characterization status
- camera.py: `test_camera_routes.py` covers **/modes, /config GET, /config POST**. **GAPS:**
  `get_realsense_sensors` (/sensors) has NO test; `_api_prefix_from_request` only exercised indirectly.
- device_camera: Phase-2A `test_device_camera.py` already pins its 9 endpoints incl. the color
  delegation — those must stay green as the delegation target moves under them.

## Plan — sub-commits (tests-first, suite green between)
1. **2B-1 chars** — add characterization for `/sensors` (get_realsense_sensors) + `_api_prefix_from_request`.
2. **2B-2 model** — move `CameraStreamConfig` → a contract module; re-point camera.py + device_camera
   + tests (device_camera re-exports it so `dc.CameraStreamConfig` keeps working).
3. **2B-3 reads** — extract `get_camera_modes` + `get_realsense_sensors` so both routes call a shared
   seam (they already delegate to the v4l2 / realsense_catalog services — may need only a thin shared
   call, not a new use-case); re-point camera.py routes + device_camera /modes /sensors.
4. **2B-4 color-config** — extract `get/update_camera_stream_config` into a color-config use-case
   (preserving settings.env_path + restart_color_encoder + SNAPSHOT_FPS/PORT); re-point camera.py
   routes + device_camera's color path (it stops awaiting camera.py's handler).
5. **2B-5 prefix** — move `_api_prefix_from_request` → shared helper; re-point device_camera + templates.py.
6. **2B-6 G1** — drop `device_camera.py` from `_ROUTE_COUPLING_ALLOWLIST` → G1 absolute; confirm teeth.

## Open decisions to gate (GO before any code)
- **D1 — color config:** separate color-config use-case (recommended — the 3 differences above make
  unifying with sensor_tuning_env risky) vs parameterize sensor_tuning_env for color?
- **D2 — modes/sensors home:** they already delegate to services (v4l2 / realsense_catalog) — does
  device_camera just call those services directly (no new use-case), or a thin `application/camera_reads`?
- **D3 — CameraStreamConfig home:** `application/device_camera/contracts.py` (earlier D3) vs a neutral
  `application/camera/contracts.py` (it's shared by camera.py too, not device_camera-specific)?
- **D4 — `_api_prefix_from_request` home:** `app/core/http_prefix.py` vs `app/services/`?

## Acceptance (Phase 2B)
device_camera.py imports NEITHER app.routes.camera NOR app.routes.templates; CameraStreamConfig lives
outside routes; camera.py + device_camera both call the shared seam; `_api_prefix_from_request` moved;
`_ROUTE_COUPLING_ALLOWLIST` empty; G1 guard absolute (teeth-checked); full non-e2e suite PYTEST_EXIT=0.
THEN rebuild the canonical artifact.

## Red lines
Behavior-preserving: URLs, status codes, payloads, audit, AND the color specifics (settings.env_path,
restart_color_encoder, SNAPSHOT_FPS/PORT). camera.py's /color_camera routes stay byte-identical.
Tests-first per sub-commit; never edit a characterization assertion to make a refactor pass.

"""Parameterized per-(device, sensor) camera routes (Sprint X1).

URL shape:
    /api/v1/camera/{serial}/{sensor}/camera_config.html
    /api/v1/camera/{serial}/{sensor}/config         (GET/POST, admin)
    /api/v1/camera/{serial}/{sensor}/modes
    /api/v1/camera/{serial}/{sensor}/sensors

Currently only (first_realsense_serial, "color") is provisioned — it
delegates to the existing single-encoder logic in routes/camera.py. All
other (serial, sensor) combinations return 503 with an explainer pointing
the operator to the dashboard.

This lives in a separate file so the legacy `/color_camera/*` routes
in camera.py stay byte-identical and backcompat is automatic.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.core.admin import require_admin
from app.core.settings import get_settings
from app.middleware.rate_limit import require_admin_rate_limit
from app.application.camera.color_config import (
    ColorConfigWriteError,
    read_color_config,
    write_color_config,
)
from app.application.camera.contracts import CameraMode, CameraModesResponse, CameraStreamConfig
from app.services.realsense_catalog import query_catalog as rs_query_catalog
from app.services.v4l2 import list_v4l2_modes
from app.services import sensor_tuning_env
from app.services.sensor_lifecycle import LifecycleError, UnsupportedSensor
from app.application.device_camera import lifecycle as dc_lifecycle
from app.application.device_camera import resolve as dc_resolve

from jinja2 import Environment, FileSystemLoader, select_autoescape

# Mounted at /cameras/{serial}/{sensor}/* — matched by gateway service "cameras"
# (api_gateway_service docker-compose.yml SERVICE_MAP_JSON: prefix="/cameras").
# /cameras/{serial}/{sensor}/X is forwarded to /cameras/{serial}/{sensor}/X.
# Shares the prefix with devices.router (dashboard, registry static routes).
router = APIRouter(tags=["device-camera"], prefix="/cameras")

ADMIN_DEPENDENCY = Depends(require_admin)
ADMIN_RATE_LIMIT = Depends(require_admin_rate_limit)

_jinja_env = Environment(
    loader=FileSystemLoader(str(get_settings().templates_dir)),
    autoescape=select_autoescape(["html", "htm"]),
)


def _resolve_or_404(serial: str, sensor: str):
    """Thin HTTP mapper over the resolve use-case (logic in application/device_camera/resolve)."""
    try:
        return dc_resolve.resolve_or_raise(serial, sensor)
    except dc_resolve.SensorUnknown as e:
        raise HTTPException(status_code=404, detail=str(e))


def _require_running(serial: str, sensor: str):
    """For read/write of config — require pipeline up. Returns entry. Maps the resolve use-case's
    domain errors to 404 / 501 / 409 with the message unchanged."""
    try:
        return dc_resolve.resolve_running_sensor(serial, sensor)
    except dc_resolve.SensorUnknown as e:
        raise HTTPException(status_code=404, detail=str(e))
    except dc_resolve.SensorNotProvisionable as e:
        raise HTTPException(status_code=501, detail=str(e))
    except dc_resolve.SensorStopped as e:
        raise HTTPException(status_code=409, detail=str(e))


# ── HTML view ──────────────────────────────────────────────────────

@router.get("/{serial}/{sensor}/camera_config.html", include_in_schema=False)
def camera_config_html(serial: str, sensor: str, request: Request) -> HTMLResponse:
    entry = _require_running(serial, sensor)
    settings = get_settings()
    style_nonce = getattr(request.state, "style_nonce", "")
    tmpl = _jinja_env.get_template("camera_config.html")
    # Sensor-aware API prefix:
    #   color: root routes (/sensors /modes /config) live in camera.py — on
    #     cameras.* the direct prefix is empty, on the api host via gateway it's
    #     /api/v1/color_camera. _api_prefix_from_request auto-detects via
    #     X-Forwarded-Prefix header / URL path inspection.
    #   depth/ir1/ir2: per-sensor routes live under /cameras/{S}/{s}/* in
    #     device_camera.py — same prefix as viewer URLs.
    from app.core.http_prefix import _api_prefix_from_request
    api_prefix = (
        _api_prefix_from_request(request)
        if sensor == "color"
        else f"/cameras/{serial}/{sensor}"
    )
    return HTMLResponse(tmpl.render(
        cam_type=settings.camera_type,
        style_nonce=style_nonce,
        device_serial=serial,
        sensor=sensor,
        sensor_label=entry.label,
        api_prefix=api_prefix,
    ))


@router.get("/{serial}/{sensor}/viewer.html", include_in_schema=False)
def viewer_html(serial: str, sensor: str, request: Request) -> HTMLResponse:
    """Generic viewer URL. Sensor → template mapping:
       color/ir1/ir2 → color_view.html (per user: IR uses RGB viewer)
       depth         → depth_view.html (HUD + depth probe overlays)
    Uses dynamic mountpoint_id from allocator (sensor_lifecycle initialize
    populated it). 409 if pipeline stopped or mountpoint_id is missing.
    """
    entry = _require_running(serial, sensor)
    if not entry.mountpoint_id:
        raise HTTPException(
            status_code=409,
            detail=(
                f"sensor '{sensor}' running but mountpoint not allocated yet — "
                "POST /initialize first."
            ),
        )
    settings = get_settings()
    style_nonce = getattr(request.state, "style_nonce", "")
    tmpl_name = "depth_view.html" if sensor == "depth" else "color_view.html"
    try:
        tmpl = _jinja_env.get_template(tmpl_name)
    except Exception:
        tmpl = _jinja_env.get_template("color_view.html")

    # Cold-start tolerance: depth/IR pipelines warm up slower than color
    # (rs.pipeline.start ~2s + ffmpeg first H264 keyframe ~1s = ~3-5s after
    # Initialize). color warms with the mux pipeline (rs-stream@color).
    # Without these wider thresholds: watchdog fires on first connect, kicks
    # off a reconnect storm + cascading "Already watching mountpoint" errors.
    timings = {} if sensor == "color" else {
        "no_frame_threshold_ms":     15000,   # was 5000
        "connect_settle_ms":         10000,   # was 6000
        "fps_drop_threshold_ms":     8000,    # was 3000
        "ice_disconnected_grace_ms": 5000,    # was 3000
        "min_acceptable_fps":        3,       # was 5 — IR/depth can be 15fps + slow first frame
        "track_mute_ms":             6000,    # was 3000
    }

    from app.core.http_prefix import _api_prefix_from_request
    from app.core.settings import get_camera_rotation_deg

    # Rotation injection — depth_features.js combines two values for click-to-
    # sensor inverse:
    #   camera_rotation_deg : sysadmin-set CSS baseline (rs-mux.env) — for
    #                         compensating physical mount orientation
    #   ffmpeg_rotation_deg : operator-set ffmpeg transpose (rs-{sensor}.tuning
    #                         .env ROTATION via camera_config UI) — runtime
    #                         re-orient independent of mount
    return HTMLResponse(tmpl.render(
        cam_type=settings.camera_type,
        style_nonce=style_nonce,
        device_serial=serial,
        sensor=sensor,
        sensor_label=entry.label,
        stream_id=entry.mountpoint_id,
        stream_name=f"{entry.label} · {serial}",
        joystick_mode="off" if sensor != "color" else "always",
        # Phase 2.3: aligned click-to-depth probe on COLOR viewer. Display =
        # color frame, so click maps directly to color pixel; sample_aligned
        # (rs.align pre-computed) returns depth at that exact pixel. Semantically
        # correct unlike depth-viewer aligned probe (previously reverted). IR viewers
        # (color_view.html too) do NOT get probe — aligned depth doesn't map to IR module
        # geometry. depth viewer uses depth_view.html with its own native probe.
        depth_features_script=(sensor == "color"),
        depth_probe_aligned=(sensor == "color"),
        api_prefix=_api_prefix_from_request(request),
        camera_rotation_deg=get_camera_rotation_deg(),
        ffmpeg_rotation_deg=sensor_tuning_env.read_rotation_deg(sensor),
        **timings,
    ))


# ── Lifecycle endpoints (Sprint X2) ────────────────────────────────

@router.post(
    "/{serial}/{sensor}/initialize",
    dependencies=[ADMIN_DEPENDENCY, ADMIN_RATE_LIMIT],
    summary="Start the encoder pipeline for this (serial, sensor)",
)
def initialize(serial: str, sensor: str, request: Request):
    src_ip = request.client.host if request.client else None
    req_id = getattr(request.state, "request_id", None)
    try:
        result = dc_lifecycle.initialize_sensor(serial, sensor, source_ip=src_ip, request_id=req_id)
    except dc_resolve.SensorUnknown as e:
        raise HTTPException(status_code=404, detail=str(e))
    except UnsupportedSensor as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except LifecycleError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {
        "running": result.running,
        "message": result.message,
        "mountpoint_id": result.mountpoint_id,
        "rtp_port": result.rtp_port,
        # Sprint X4: canonical paths without /api/v1 prefix. Browser resolves
        # relative to the current host (cameras.example.com).
        "viewer_url": f"/cameras/{serial}/{sensor}/viewer.html",
        "config_url": f"/cameras/{serial}/{sensor}/camera_config.html",
    }


@router.post(
    "/{serial}/{sensor}/stop",
    dependencies=[ADMIN_DEPENDENCY, ADMIN_RATE_LIMIT],
    summary="Safely stop the encoder pipeline",
)
def stop(serial: str, sensor: str, request: Request):
    src_ip = request.client.host if request.client else None
    req_id = getattr(request.state, "request_id", None)
    try:
        result = dc_lifecycle.stop_sensor(serial, sensor, source_ip=src_ip, request_id=req_id)
    except dc_resolve.SensorUnknown as e:
        raise HTTPException(status_code=404, detail=str(e))
    except UnsupportedSensor as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except LifecycleError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"running": result.running, "message": result.message}


# ── JSON endpoints — delegate to single-encoder logic ───────────────
# color delegates to camera.py routes (now repointed to rs-color.tuning.env +
# rs-stream@color); depth/IR use the per-sensor rs-{sensor} helpers below.

@router.get("/{serial}/{sensor}/modes", summary="Parameterized /modes")
def get_modes(serial: str, sensor: str):
    _require_running(serial, sensor)
    # Build the same CameraModesResponse camera.py does, but call the v4l2 service directly (2B-3)
    # so device_camera no longer imports a sibling route's handler.
    raw = list_v4l2_modes()
    return CameraModesResponse(
        pixel_format=raw.get("pixel_format", "YUYV"),
        device=raw.get("device", get_settings().camera_device),
        modes=[CameraMode(**m) for m in raw.get("modes", [])],
    )


@router.get("/{serial}/{sensor}/sensors", summary="Parameterized /sensors")
def get_sensors(serial: str, sensor: str):
    _require_running(serial, sensor)
    try:
        return rs_query_catalog()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get(
    "/{serial}/{sensor}/rotation",
    summary="Lightweight no-auth rotation poll (for depth viewer auto-update)",
)
def get_rotation(serial: str, sensor: str) -> dict:
    """Current ffmpeg ROTATION value from sensor's tuning.env.

    Polled by depth_features.js on visibilitychange (when operator switches
    tabs back from camera_config UI) so click→depth math stays correct without
    a full page reload. No auth — rotation value is not sensitive.
    """
    return {"rotation": sensor_tuning_env.read_rotation_deg(sensor)}


@router.get(
    "/{serial}/{sensor}/config",
    response_model=CameraStreamConfig,
    dependencies=[ADMIN_DEPENDENCY, ADMIN_RATE_LIMIT],
    summary="Read encoder tuning config for this (serial, sensor)",
)
async def get_config(serial: str, sensor: str) -> CameraStreamConfig:
    _require_running(serial, sensor)
    if sensor == "color":
        return read_color_config()
    return _read_sensor_config(sensor)


@router.post(
    "/{serial}/{sensor}/config",
    response_model=CameraStreamConfig,
    dependencies=[ADMIN_DEPENDENCY, ADMIN_RATE_LIMIT],
    summary="Update encoder tuning config for this (serial, sensor)",
)
async def post_config(serial: str, sensor: str, cfg: CameraStreamConfig) -> CameraStreamConfig:
    _require_running(serial, sensor)
    if sensor == "color":
        try:
            return write_color_config(cfg)
        except ColorConfigWriteError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _write_sensor_config(sensor, cfg)


# ── rs-stream@.service config (depth/IR) ───────────────────────────
# Operator-tunable encoder params live in /etc/robot/rs-{sensor}.tuning.env. The file I/O + the
# encoder restart are the services/sensor_tuning_env adapter (Phase 2A — de-leaked: it raises
# TuningWriteError, mapped to 500 here). These helpers only map between that env and the
# CameraStreamConfig HTTP contract (which still lives in routes/camera.py until Phase 2B).


def _read_sensor_config(sensor: str) -> CameraStreamConfig:
    env = sensor_tuning_env.read_tuning(sensor)
    return CameraStreamConfig(
        width=int(env.get("WIDTH", "640")),
        height=int(env.get("HEIGHT", "480")),
        fps=int(env.get("FPS", "15")),
        bitrate_kbps=int(env.get("BITRATE_KBPS", "1000" if sensor == "depth" else "800")),
        gop=int(env["GOP"]) if env.get("GOP") else None,
        preset=env.get("PRESET", "veryfast"),
        tune=env.get("TUNE", "zerolatency"),
        snapshot_fps=int(env.get("SNAPSHOT_FPS", "0")),  # mux doesn't emit snapshots
        port=int(env.get("PORT", "5006")),
        rotation=int((env.get("ROTATION", "0") or "0").strip() or "0"),
    )


def _write_sensor_config(sensor: str, cfg: CameraStreamConfig) -> CameraStreamConfig:
    """Map cfg → tuning.env (operator-tunable fields only; contract.env's dynamic PORT is managed by
    lifecycle.initialize), then write + restart rs-stream@<sensor> via the adapter. The adapter
    raises TuningWriteError on failure → mapped to HTTP 500 (the de-leak boundary)."""
    env = sensor_tuning_env.read_tuning(sensor)
    env["WIDTH"]        = str(cfg.width)
    env["HEIGHT"]       = str(cfg.height)
    env["FPS"]          = str(cfg.fps)
    env["BITRATE_KBPS"] = str(cfg.bitrate_kbps)
    env["PRESET"]       = cfg.preset
    env["TUNE"]         = cfg.tune
    env["ROTATION"]     = str(cfg.rotation)
    if cfg.gop is not None:
        env["GOP"] = str(cfg.gop)
    else:
        env.pop("GOP", None)
    try:
        sensor_tuning_env.write_tuning(sensor, env)
    except sensor_tuning_env.TuningWriteError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return cfg

"""Color encoder tuning config (Phase 2B-4).

Read/write the COLOR tuning env (settings.env_path = rs-color.tuning.env) and restart the color
encoder. The color counterpart of services/sensor_tuning_env (depth/IR), kept DELIBERATELY SEPARATE
because the operational contract differs:
  - configurable settings.env_path (no-arg read_env), not a hardcoded per-sensor path;
  - a dedicated color restart (encoder-admin --instance color, timeout 60);
  - it persists SNAPSHOT_FPS + PORT (the depth/IR write omits them).

FastAPI-free: write failures raise ColorConfigWriteError (the route maps it to 500). env_store is
called module-qualified so a test patches it at the source; the color restart goes through the shared
encoder_admin.restart_unit (still driven by app.services.system.run, the test patch-point). Logic moved
verbatim from routes/camera.py's get/update_camera_stream_config + restart_color_encoder.
"""
from __future__ import annotations

from app.application.camera.contracts import CameraStreamConfig
from app.services import encoder_admin, env_store


class ColorConfigWriteError(RuntimeError):
    """Writing rs-color.tuning.env or restarting the color encoder failed (route maps to 500)."""


def _restart_color_encoder() -> None:
    """Restart the color RTP encoder (rs-stream@color) via the shared encoder-admin adapter. Explicit
    family/instance — we don't rely on encoder-admin defaults; timeout 60 (color counterpart of the
    depth/IR restart in sensor_tuning_env, now sharing encoder_admin.restart_unit)."""
    encoder_admin.restart_unit("rs-stream", "color", timeout=60)


def read_color_config() -> CameraStreamConfig:
    """Load the color tuning env (settings.env_path) → CameraStreamConfig, applying defaults."""
    env = env_store.read_env()
    rotation_raw = env.get("ROTATION", "0").strip() or "0"
    try:
        rotation = int(rotation_raw)
    except ValueError:
        rotation = 0
    gop_env = env.get("GOP")
    return CameraStreamConfig(
        width=int(env.get("WIDTH", "640")),
        height=int(env.get("HEIGHT", "480")),
        fps=int(env.get("FPS", "30")),
        bitrate_kbps=int(env.get("BITRATE_KBPS", "1800")),
        gop=int(gop_env) if gop_env is not None else None,
        preset=env.get("PRESET", "veryfast"),
        tune=env.get("TUNE", "zerolatency"),
        snapshot_fps=int(env.get("SNAPSHOT_FPS", "1")),
        port=int(env.get("PORT", "5004")),
        rotation=rotation,
    )


def write_color_config(cfg: CameraStreamConfig) -> CameraStreamConfig:
    """Write cfg → color tuning env (incl SNAPSHOT_FPS + PORT) atomically, then restart the color
    encoder. Raises ColorConfigWriteError on either failure."""
    env = env_store.read_env()
    env["WIDTH"] = str(cfg.width)
    env["HEIGHT"] = str(cfg.height)
    env["FPS"] = str(cfg.fps)
    env["BITRATE_KBPS"] = str(cfg.bitrate_kbps)
    env["PRESET"] = cfg.preset
    env["TUNE"] = cfg.tune
    env["SNAPSHOT_FPS"] = str(cfg.snapshot_fps)
    env["PORT"] = str(cfg.port)
    env["ROTATION"] = str(cfg.rotation)
    if cfg.gop is not None:
        env["GOP"] = str(cfg.gop)
    else:
        env.pop("GOP", None)
    try:
        env_store.write_env_atomic(env)
    except Exception as exc:  # noqa: BLE001 — surface as a domain error, not a leaked OSError
        raise ColorConfigWriteError(f"Failed to write env: {exc}") from exc
    try:
        _restart_color_encoder()
    except RuntimeError as exc:
        raise ColorConfigWriteError(str(exc)) from exc
    return cfg

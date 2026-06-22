"""rs-{sensor}.tuning.env adapter (Phase 2A).

Owns the operator-tunable encoder params for depth/IR sensors: read/write
`/etc/robot/rs-{sensor}.tuning.env` and restart the sensor's rs-stream ffmpeg consumer so the new
values take effect. Infrastructure adapter — file I/O + `encoder-admin`. It FAILS CLOSED with a
DOMAIN error (`TuningWriteError`); the route maps that to HTTP 500. No HTTP framework types here
(D3 de-leak — moved out of routes/device_camera.py).

Calls into env_store are module-qualified so a test can patch it at the source; the rs-stream restart
goes through the shared encoder_admin.restart_unit (still driven by app.services.system.run).
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.services import encoder_admin, env_store

log = logging.getLogger(__name__)


class TuningWriteError(RuntimeError):
    """Writing the tuning env or restarting the encoder failed (route maps this to HTTP 500)."""


def tuning_path(sensor: str) -> Path:
    return Path(f"/etc/robot/rs-{sensor}.tuning.env")


def read_tuning(sensor: str) -> dict:
    """Raw env dict from the sensor's tuning file (caller applies defaults / maps to its DTO)."""
    return env_store.read_env(tuning_path(sensor))


def read_rotation_deg(sensor: str) -> int:
    """ROTATION (deg) from the sensor's tuning env; 0 if missing / malformed / unreadable.

    Single read path shared by the viewer template inject + the /rotation poll endpoint so they
    never diverge."""
    try:
        env = env_store.read_env(tuning_path(sensor))
        return int((env.get("ROTATION", "0") or "0").strip() or "0")
    except Exception:  # noqa: BLE001 — rotation is best-effort cosmetic; never raise to the caller
        return 0


def write_tuning(sensor: str, env: dict) -> None:
    """Atomically write the tuning env, then restart rs-stream@<sensor> so ffmpeg picks up the new
    values. Raises TuningWriteError on either failure (the route maps it to HTTP 500)."""
    try:
        env_store.write_env_atomic(env, env_path=tuning_path(sensor))
    except Exception as exc:  # noqa: BLE001 — surface as a domain error, not a leaked OSError
        raise TuningWriteError(f"Failed to write env: {exc}") from exc
    try:
        encoder_admin.restart_unit("rs-stream", sensor, timeout=20)
    except RuntimeError as exc:
        raise TuningWriteError(str(exc)) from exc

"""encoder-admin port + readiness probes.

The single approved adapter for the scoped-sudo ``/usr/local/bin/encoder-admin`` CLI (status / start /
stop / restart of the realsense-mux + rs-stream@<sensor> units) plus the is_running / mux_running /
encoder_running status queries built on it. Extracted verbatim from sensor_lifecycle.py (Phase 4 / A-04).
"""
from __future__ import annotations

import json
from typing import Optional

from app.services.sensor_lifecycle.errors import LifecycleError
from app.services.system import run as run_cmd

# Encoder admin invocation prefix — always sudo (NOPASSWD scoped in /etc/sudoers.d/).
_ENCODER_ADMIN_CMD = ["sudo", "/usr/local/bin/encoder-admin"]


def _encoder_status(family: str, instance: Optional[str] = None) -> dict:
    cmd = list(_ENCODER_ADMIN_CMD) + ["status", "--family", family]
    if instance:
        cmd += ["--instance", instance]
    try:
        return json.loads(run_cmd(cmd, timeout=8))
    except (RuntimeError, json.JSONDecodeError, ValueError):
        return {}


def _encoder_action(action: str, family: str, instance: Optional[str] = None) -> None:
    cmd = list(_ENCODER_ADMIN_CMD) + [action, "--family", family]
    if instance:
        cmd += ["--instance", instance]
    try:
        run_cmd(cmd, timeout=30)
    except RuntimeError as e:
        raise LifecycleError(f"encoder-admin {action} {family}/{instance}: {e}") from e


def encoder_running() -> Optional[bool]:
    """Backward-compat: color encoder state (used by older device_registry)."""
    return is_running("color")


def is_running(sensor: str) -> Optional[bool]:
    """Return live encoder state for given sensor. None if probe failed.

    Phase 2: all sensors (color/depth/ir1/ir2) stream through mux + rs-stream@{sensor}.
    The legacy V4L2 color path (rtp-rgb@cam-rgb) was retired.
    """
    if sensor in ("color", "depth", "ir1", "ir2"):
        st = _encoder_status("rs-stream", sensor)
    else:
        return None
    return bool(st.get("active")) if st else None


def mux_running() -> Optional[bool]:
    st = _encoder_status("realsense-mux")
    return bool(st.get("active")) if st else None

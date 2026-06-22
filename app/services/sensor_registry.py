"""Plugin-extensible sensor type registry (Sprint B5).

Third-party plugins or operator configs can register custom sensor types
without modifying L4 code. Built-in types (color, depth, ir1, ir2) registered
in this module. Custom types added via `register_sensor_type()` SDK call —
either from a plugin module loaded at startup or directly from operator code.

Each sensor type carries metadata that lifecycle uses to provision the
pipeline: which encoder family to invoke, which producer process (if any),
default config values, presentation labels.

Example custom sensor registration (operator plugin file):

    # /etc/robot/plugins.d/my_thermal_camera.py
    from app.services.sensor_registry import SensorType, register_sensor_type

    register_sensor_type(SensorType(
        key="thermal",
        label="Thermal Camera (FLIR Lepton)",
        encoder_family="rtp-v4l2",        # generic V4L2 adapter
        encoder_instance_pattern="thermal-{index}",
        default_pix_fmt="gray",
        default_width=160, default_height=120, default_fps=9,
    ))

After registration, dashboard shows "thermal" sensor option and
lifecycle.initialize("thermal-0") works through the standard flow.
"""
from __future__ import annotations

import importlib.util
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SensorType:
    """Plugin-registered sensor type definition.

    Immutable — use copy + re-register to mutate.
    """
    # Identity
    key: str                     # "color", "depth", "thermal", "lidar", etc.
    label: str                   # Human-readable display name

    # Pipeline orchestration
    encoder_family: str          # "rs-stream" | "rtp-v4l2" | "rtp-rtsp" | etc.
    encoder_instance_pattern: str   # Template for instance name
                                    # Available substitutions: {serial}, {index}
                                    # Examples: "color", "depth", "thermal-{index}"

    # Optional: producer service that must run before encoder consumer
    requires_producer: Optional[str] = None   # systemd service name, e.g., "realsense-mux"

    # Defaults for tuning.env initial write (operator can override later)
    default_pix_fmt: Optional[str] = None
    default_width: int = 640
    default_height: int = 480
    default_fps: int = 30
    default_bitrate_kbps: int = 1000

    # Mountpoint allocation hints (for allocator)
    is_dynamic_mountpoint: bool = True    # False = use static jcfg mountpoint
    static_mp_id: Optional[int] = None
    static_rtp_port: Optional[int] = None

    # Optional: discoverability — plugin can provide a discover() callback returning
    # which instances of this sensor type are physically present. Used by registry
    # to auto-populate dashboard. Set to None for "operator manually configures".
    # Signature: () -> List[Dict[str, str]] returning [{"serial": "...", "index": "..."}]
    discover_fn: Optional[str] = None    # module:function string


# ── Registry ──────────────────────────────────────────────────────────

_REGISTRY: Dict[str, SensorType] = {}


def register_sensor_type(t: SensorType) -> None:
    """Add sensor type to registry. Idempotent — re-registering same key overrides."""
    if not isinstance(t, SensorType):
        raise TypeError(f"expected SensorType, got {type(t).__name__}")
    if not t.key or not t.encoder_family:
        raise ValueError("SensorType.key and .encoder_family are required")
    if t.key in _REGISTRY:
        log.info("sensor_type %s re-registered (override)", t.key)
    _REGISTRY[t.key] = t


def list_sensor_types() -> List[SensorType]:
    """Snapshot of all registered types (for dashboard / introspection)."""
    return list(_REGISTRY.values())


def list_sensor_keys() -> List[str]:
    return list(_REGISTRY.keys())


# ── Built-in sensor types (Sprint X3 RealSense) ───────────────────────

register_sensor_type(SensorType(
    key="color",
    label="RGB Camera (D435i color sensor)",
    # Phase 2: color streams through mux (rs-stream@color), not V4L2 rtp-rgb@cam-rgb.
    encoder_family="rs-stream",
    encoder_instance_pattern="color",
    default_pix_fmt="rgb24",
    default_width=640, default_height=480, default_fps=15,
    default_bitrate_kbps=900,
    is_dynamic_mountpoint=False,
    static_mp_id=1305,
    static_rtp_port=5004,
))

register_sensor_type(SensorType(
    key="depth",
    label="Depth (Z16 → colorized RGB)",
    encoder_family="rs-stream",
    encoder_instance_pattern="depth",
    requires_producer="realsense-mux",
    default_pix_fmt="rgb24",
    default_width=640, default_height=480, default_fps=15,
    default_bitrate_kbps=1000,
))

register_sensor_type(SensorType(
    key="ir1",
    label="Infrared 1 (D435i IR-left)",
    encoder_family="rs-stream",
    encoder_instance_pattern="ir1",
    requires_producer="realsense-mux",
    default_pix_fmt="gray",
    default_width=640, default_height=480, default_fps=15,
    default_bitrate_kbps=800,
))

register_sensor_type(SensorType(
    key="ir2",
    label="Infrared 2 (D435i IR-right)",
    encoder_family="rs-stream",
    encoder_instance_pattern="ir2",
    requires_producer="realsense-mux",
    default_pix_fmt="gray",
    default_width=640, default_height=480, default_fps=15,
    default_bitrate_kbps=800,
))


# ── Plugin loader ─────────────────────────────────────────────────────

PLUGIN_DIR = Path(os.environ.get("SENSOR_PLUGIN_DIR", "/etc/robot/plugins.d"))


def load_plugins(plugin_dir: Path = PLUGIN_DIR) -> int:
    """Discover *.py files in plugin_dir and import them. Each plugin is expected
    to call register_sensor_type() on import. Returns count of plugins loaded.

    Errors loading individual plugins are logged but don't abort startup —
    a broken plugin shouldn't take down the whole stack.
    """
    if not plugin_dir.exists() or not plugin_dir.is_dir():
        log.debug("plugin dir %s does not exist — skip", plugin_dir)
        return 0

    loaded = 0
    for py_file in sorted(plugin_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                f"sensor_plugin_{py_file.stem}", py_file,
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                loaded += 1
                log.info("loaded sensor plugin: %s", py_file.name)
        except Exception as e:
            log.error("failed loading plugin %s: %s", py_file.name, e)
    return loaded

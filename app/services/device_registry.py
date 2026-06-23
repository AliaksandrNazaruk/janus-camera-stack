"""Device registry — discovers attached RealSense devices and tracks
which (serial, sensor) pairs have a live encoder pipeline on this node.

Provisioning state derived from live encoder units (rs-stream@{sensor}):
- color maps to (first_device_serial, "color") via rs-stream@color, static mp 1305.
- depth / ir1 / ir2 use dynamic mountpoint allocation (Sprint X3).

This lives in a single module so that routes/dashboard and parameterized config
routes share one source of truth about device topology.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from app.core.settings import get_settings


SENSOR_LABELS = {
    "color": "RGB Camera",
    "depth": "Depth (Z16)",
    "ir1":   "Infrared 1",
    "ir2":   "Infrared 2",
}


@dataclass
class SensorEntry:
    sensor: str            # color | depth | ir1 | ir2
    label: str
    # provisioning_supported: pipeline implementation exists for this sensor.
    # Sprint X3: color (V4L2+ffmpeg), depth/ir1/ir2 (pyrealsense2 mux + FIFO ffmpeg).
    provisioning_supported: bool = False
    # running: encoder unit is currently active (read live via encoder-admin).
    running: Optional[bool] = None
    encoder_instance: Optional[str] = None   # systemd template arg
    encoder_unit: Optional[str] = None       # full unit name
    # mountpoint_id: dynamic for depth/IR (allocated at first Initialize),
    # static 1305 for color. Populated in registry from allocator or config.
    mountpoint_id: Optional[int] = None
    rtp_port: Optional[int] = None
    viewer_url: Optional[str] = None         # /cameras/{sn}/{sensor}/viewer.html
    config_url: Optional[str] = None         # /cameras/{sn}/{sensor}/camera_config.html


@dataclass
class DeviceEntry:
    serial: str
    name: str              # "Intel RealSense D435I"
    firmware: str
    sensors: List[SensorEntry]


def _discover_devices() -> List[Dict[str, str]]:
    """Return [{serial, name, firmware}, ...]. Empty list if SDK or
    device unavailable — caller treats as no-devices-attached.
    """
    try:
        import pyrealsense2 as rs
    except ImportError:
        return []
    out: List[Dict[str, str]] = []
    try:
        ctx = rs.context()
        for dev in ctx.query_devices():
            try:
                serial = dev.get_info(rs.camera_info.serial_number)
            except Exception:
                continue
            try:
                name = dev.get_info(rs.camera_info.name)
            except Exception:
                name = "RealSense"
            try:
                fw = dev.get_info(rs.camera_info.firmware_version)
            except Exception:
                fw = ""
            out.append({"serial": serial, "name": name, "firmware": fw})
    except Exception:
        # rs.context() can throw if libusb / udev permissions broken
        pass
    return out


_local_serial_cache: Optional[str] = None


def local_serial() -> Optional[str]:
    """Best-effort REAL serial of the gateway's own RealSense, or None.

    The probe imports pyrealsense2 + enumerates USB — slow, and can throw on
    bad udev/libusb perms (all swallowed in _discover_devices → []). NOT for the
    hot path; callers prefer the allocator (identity of record) first and fall
    back here only on a fresh box. Caches a positive result (the gateway camera
    is fixed); a None is retried so a later-attached camera is picked up."""
    global _local_serial_cache
    if _local_serial_cache:
        return _local_serial_cache
    devs = _discover_devices()
    if devs:
        _local_serial_cache = devs[0]["serial"]
    return _local_serial_cache


def get_registry() -> List[DeviceEntry]:
    """Public API — returns full device topology with provisioning state."""
    settings = get_settings()  # noqa: F841
    devices = _discover_devices()
    entries: List[DeviceEntry] = []

    # Color encoder is rs-stream@color (mux consumer, static mp 1305).
    # If there's a discovered device, we attribute that encoder to its serial.
    # If no device discovered, we still surface the encoder under
    # serial="unknown" so dashboard isn't blank if libusb perms fail.
    first_serial = devices[0]["serial"] if devices else "unknown"  # noqa: F841

    def _build_sensors(serial: str, is_first: bool) -> List[SensorEntry]:
        from app.services.sensor_lifecycle import (
            is_running as encoder_is_running,
            COLOR_MP_ID, COLOR_RTP_PORT, COLOR_ENCODER_INSTANCE,
        )
        from app.services.mountpoint_allocator import get_allocation

        if not is_first:
            # Multi-device support — only first D435i provisionable for now.
            return [SensorEntry(
                sensor=k, label=SENSOR_LABELS[k],
                provisioning_supported=False, running=False,
                config_url=f"/cameras/{serial}/{k}/camera_config.html",
            ) for k in ("color", "depth", "ir1", "ir2")]

        out: List[SensorEntry] = []
        # COLOR — static baseline: mux color.fifo → rs-stream@color → jcfg mp 1305.
        color_running = encoder_is_running("color")
        out.append(SensorEntry(
            sensor="color", label=SENSOR_LABELS["color"],
            provisioning_supported=True,
            running=color_running,
            encoder_instance=COLOR_ENCODER_INSTANCE,
            encoder_unit="rs-stream@color.service",
            mountpoint_id=COLOR_MP_ID,
            rtp_port=COLOR_RTP_PORT,
            viewer_url=f"/cameras/{serial}/color/viewer.html" if color_running else None,
            config_url=f"/cameras/{serial}/color/camera_config.html",
        ))

        # DEPTH | IR1 | IR2 — dynamic mountpoint allocation
        for key in ("depth", "ir1", "ir2"):
            running = encoder_is_running(key)
            alloc = get_allocation(serial, key)
            out.append(SensorEntry(
                sensor=key, label=SENSOR_LABELS[key],
                provisioning_supported=True,
                running=running,
                encoder_instance=key,
                encoder_unit=f"rs-stream@{key}.service",
                mountpoint_id=alloc.mp_id if alloc else None,
                rtp_port=alloc.rtp_port if alloc else None,
                viewer_url=(f"/cameras/{serial}/{key}/viewer.html"
                            if running and alloc else None),
                config_url=f"/cameras/{serial}/{key}/camera_config.html",
            ))
        return out

    if not devices:
        entries.append(DeviceEntry(
            serial="unknown",
            name="RealSense (libusb access failed)",
            firmware="",
            sensors=_build_sensors("unknown", is_first=True),
        ))
    else:
        for i, d in enumerate(devices):
            entries.append(DeviceEntry(
                serial=d["serial"], name=d["name"], firmware=d["firmware"],
                sensors=_build_sensors(d["serial"], is_first=(i == 0)),
            ))
    return entries


def resolve_sensor(serial: str, sensor: str) -> Optional[SensorEntry]:
    """Lookup (serial, sensor) — returns SensorEntry or None if device unknown."""
    for dev in get_registry():
        if dev.serial == serial:
            for s in dev.sensors:
                if s.sensor == sensor:
                    return s
            return None
    return None



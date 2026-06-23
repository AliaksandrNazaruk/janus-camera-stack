"""Probe Intel RealSense devices via pyrealsense2 (non-exclusive enumeration).

Used by admin_dashboard to show operator which RealSense hardware is plugged
in without stealing the device from realsense-mux (rs.context().query_devices()
is read-only — doesn't open streaming sessions).

Returns serial, model name, firmware, USB port, and per-sensor stream
profiles (depth ranges, IR resolutions, color formats).

Graceful degradation:
- pyrealsense2 not installed → ImportError caught, returns empty + reason
- No devices attached → returns empty list cleanly
- librealsense USB issue → exception caught, reason in response
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

log = logging.getLogger("realsense_probe")


@dataclass
class StreamProfile:
    stream: str        # "depth" | "color" | "ir" | "accel" | "gyro"
    format: str        # "Z16", "RGB8", "Y8", "YUYV", etc.
    width: int = 0
    height: int = 0
    fps: int = 0
    index: int = 0     # for IR1 vs IR2 etc.


@dataclass
class RealsenseDevice:
    serial: str
    name: str
    firmware: str
    product_id: str = ""
    usb_port: str = ""
    physical_port: str = ""
    # Top-3 stream profiles per sensor (otherwise output explodes — D435i has 100+)
    profiles: List[StreamProfile] = field(default_factory=list)


@dataclass
class ProbeResult:
    devices: List[RealsenseDevice]
    available: bool          # pyrealsense2 importable + queryable
    error: Optional[str] = None


def _safe_get_info(dev, info_attr: str) -> str:
    """rs.camera_info.<attr> sometimes raises if device doesn't support that attr."""
    try:
        import pyrealsense2 as rs   # local import — avoids hard dep at module load
        attr = getattr(rs.camera_info, info_attr, None)
        if attr is None:
            return ""
        if dev.supports(attr):
            return dev.get_info(attr) or ""
    except Exception:
        pass
    return ""


def probe(include_profiles: bool = True, profile_limit_per_stream: int = 3) -> ProbeResult:
    """Enumerate connected RealSense devices.

    include_profiles=False makes probe ~10× faster (skips per-sensor profile walk).
    """
    try:
        import pyrealsense2 as rs
    except ImportError as exc:
        return ProbeResult(devices=[], available=False, error=f"pyrealsense2 not installed: {exc}")

    try:
        ctx = rs.context()
    except Exception as exc:
        return ProbeResult(devices=[], available=False, error=f"rs.context() failed: {exc}")

    devices: List[RealsenseDevice] = []
    try:
        for dev in ctx.devices:
            serial = _safe_get_info(dev, "serial_number")
            name = _safe_get_info(dev, "name") or "RealSense"
            firmware = _safe_get_info(dev, "firmware_version")
            product_id = _safe_get_info(dev, "product_id")
            usb_port = _safe_get_info(dev, "usb_type_descriptor")
            physical_port = _safe_get_info(dev, "physical_port")

            profiles: List[StreamProfile] = []
            if include_profiles:
                # Walk sensors → top-N profiles per stream type
                try:
                    for sensor in dev.sensors:
                        for sp in sensor.get_stream_profiles():
                            try:
                                stream_type = sp.stream_type()
                                stream_name = stream_type.name if hasattr(stream_type, "name") else str(stream_type)
                                stream_name_l = stream_name.lower().replace("stream.", "")
                                fmt = sp.format().name if hasattr(sp.format(), "name") else str(sp.format())
                                fmt = fmt.replace("format.", "").upper()
                                idx = sp.stream_index()
                                if sp.is_video_stream_profile():
                                    vp = sp.as_video_stream_profile()
                                    width = vp.width()
                                    height = vp.height()
                                else:
                                    width = height = 0
                                fps = sp.fps()

                                # Limit to top-N per (stream, index)
                                key = (stream_name_l, idx)
                                count = sum(1 for p in profiles if (p.stream, p.index) == key)
                                if count >= profile_limit_per_stream:
                                    continue
                                profiles.append(StreamProfile(
                                    stream=stream_name_l, format=fmt,
                                    width=width, height=height, fps=fps, index=idx,
                                ))
                            except Exception:
                                continue
                except Exception as exc:
                    log.debug("sensor profile walk failed for %s: %s", serial, exc)

            devices.append(RealsenseDevice(
                serial=serial, name=name, firmware=firmware,
                product_id=product_id, usb_port=usb_port, physical_port=physical_port,
                profiles=profiles,
            ))
    except Exception as exc:
        return ProbeResult(devices=devices, available=True, error=f"device enumeration partial: {exc}")

    return ProbeResult(devices=devices, available=True)

"""Use-cases for the device-inventory routes (v4l2 + realsense enumeration).

Read-only orchestration over services/v4l2.py + services/realsense_probe.py. Extracted
from admin_dashboard (C-04 Phase 3A); response shapes + behavior preserved verbatim.
No Janus code here.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel

from app.services import v4l2


class V4l2Device(BaseModel):
    path: str             # /dev/video0
    label: str            # "Logitech HD Webcam C920"
    bus: Optional[str] = None
    capabilities: List[str] = []   # e.g. ["video_capture", "streaming"]
    formats: List[str] = []        # ["YUYV 640x480 @30", "MJPEG 1280x720 @30"]
    is_capture: bool = False


class RealsenseProfileModel(BaseModel):
    stream: str
    format: str
    width: int
    height: int
    fps: int
    index: int


class RealsenseDeviceModel(BaseModel):
    serial: str
    name: str
    firmware: str
    product_id: str = ""
    usb_port: str = ""
    physical_port: str = ""
    profiles: List[RealsenseProfileModel] = []


class RealsenseProbeResponse(BaseModel):
    devices: List[RealsenseDeviceModel]
    available: bool
    error: Optional[str] = None


def list_v4l2_devices(probe_formats: bool = False) -> List[V4l2Device]:
    return [V4l2Device(**d) for d in v4l2.enumerate_devices(probe_formats)]


def list_realsense_devices(include_profiles: bool = True) -> RealsenseProbeResponse:
    from app.services import realsense_probe
    result = realsense_probe.probe(include_profiles=include_profiles)
    return RealsenseProbeResponse(
        devices=[
            RealsenseDeviceModel(
                serial=d.serial, name=d.name, firmware=d.firmware,
                product_id=d.product_id, usb_port=d.usb_port, physical_port=d.physical_port,
                profiles=[
                    RealsenseProfileModel(
                        stream=p.stream, format=p.format,
                        width=p.width, height=p.height, fps=p.fps, index=p.index,
                    )
                    for p in d.profiles
                ],
            )
            for d in result.devices
        ],
        available=result.available,
        error=result.error,
    )

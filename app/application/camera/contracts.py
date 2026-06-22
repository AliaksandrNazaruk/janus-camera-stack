"""Camera HTTP contracts (Phase 2B). Shared request/response models for the camera + device_camera
routes, kept out of the route modules so neither route imports the other. FastAPI-free (pydantic only).
Moved verbatim from routes/camera.py."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class CameraMode(BaseModel):
    width: int
    height: int
    fps: List[int]


class CameraModesResponse(BaseModel):
    pixel_format: str
    device: str
    modes: List[CameraMode]


class CameraStreamConfig(BaseModel):
    width: int = Field(..., description="Video width in pixels, e.g. 640.")
    height: int = Field(..., description="Video height in pixels, e.g. 480.")
    fps: int = Field(..., description="Frames per second, e.g. 30.")

    bitrate_kbps: int = Field(1800, ge=100, description="H.264 bitrate in kbps.")
    gop: Optional[int] = Field(
        None,
        description="Keyframe interval (GOP). Defaults to FPS when omitted.",
    )
    preset: str = Field("veryfast", description="x264 preset, e.g. veryfast.")
    tune: str = Field("zerolatency", description="x264 tune, typically zerolatency.")

    snapshot_fps: int = Field(1, ge=0, description="JPEG snapshot cadence in FPS.")
    port: int = Field(5004, ge=1024, le=65535, description="RTP UDP port consumed by Janus.")

    rotation: int = Field(
        0,
        description="Image rotation in degrees. Allowed: 0 | 90 | 180 | 270.",
    )

    @field_validator("rotation")
    @classmethod
    def _rotation_must_be_quarter(cls, v: int) -> int:
        if v not in (0, 90, 180, 270):
            raise ValueError("rotation must be one of 0, 90, 180, 270")
        return v

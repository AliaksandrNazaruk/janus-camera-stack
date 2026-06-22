from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.application.camera.color_config import (
    ColorConfigWriteError,
    read_color_config,
    write_color_config,
)
from app.application.camera.contracts import (
    CameraMode,
    CameraModesResponse,
    CameraStreamConfig,
)
from app.core.admin import require_admin
from app.core.settings import get_settings
from app.middleware.rate_limit import require_admin_rate_limit
from app.services.realsense_catalog import query_catalog as rs_query_catalog
from app.services.v4l2 import list_v4l2_modes

router = APIRouter(tags=["camera"])

# Descriptive only (info field in API responses). Actual restart via encoder-admin
# CLI (in application/camera/color_config). Read from settings.service_name (single source).
# Phase 2: now rs-stream@color.service (was rtp-rgb@cam-rgb pre-mux-migration).
COLOR_ENCODER_SERVICE = get_settings().service_name

ADMIN_DEPENDENCY = Depends(require_admin)
ADMIN_RATE_LIMIT = Depends(require_admin_rate_limit)


# CameraMode / CameraModesResponse / CameraStreamConfig moved to
# app/application/camera/contracts.py (Phase 2B) — imported above.


# restart_color_encoder + the config read/write moved to application/camera/color_config (Phase 2B-4).



@router.get(
    "/sensors",
    summary="RealSense per-sensor mode catalog (pyrealsense2)",
    description=(
        "Enumerates all attached RealSense sensors (color/depth/IR) and their "
        "supported video stream profiles via pyrealsense2 SDK. Richer than "
        "`/modes` (which sees only one V4L2 sub-device). 503 if SDK or device "
        "unavailable — clients should fall back to `/modes`."
    ),
)
def get_realsense_sensors() -> dict:
    try:
        return rs_query_catalog()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get(
    "/modes",
    response_model=CameraModesResponse,
    summary="Available camera modes (V4L2)",
    description="Parses `v4l2-ctl --list-formats-ext` and returns supported YUYV resolutions/FPS combinations.",
)
def get_camera_modes() -> CameraModesResponse:
    raw = list_v4l2_modes()
    modes = [CameraMode(**mode) for mode in raw.get("modes", [])]
    return CameraModesResponse(
        pixel_format=raw.get("pixel_format", "YUYV"),
        device=raw.get("device", get_settings().camera_device),
        modes=modes,
    )

@router.get(
    "/config",
    response_model=CameraStreamConfig,
    dependencies=[ADMIN_DEPENDENCY, ADMIN_RATE_LIMIT],
    summary="Read applied RTP/ffmpeg configuration (admin)",
    description="Loads rs-color.tuning.env (operator-mutable runtime settings) from disk.",
)
async def get_camera_stream_config() -> CameraStreamConfig:
    return read_color_config()


@router.post(
    "/config",
    response_model=CameraStreamConfig,
    dependencies=[ADMIN_DEPENDENCY, ADMIN_RATE_LIMIT],
    summary="Update rs-color.tuning.env and restart the color encoder (admin)",
    description=(
        "Overwrites `/etc/robot/rs-color.tuning.env` and restarts rs-stream@color."
    ),
)
async def update_camera_stream_config(cfg: CameraStreamConfig) -> CameraStreamConfig:
    try:
        return write_color_config(cfg)
    except ColorConfigWriteError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/snapshot.jpg",
    summary="Latest JPEG snapshot",
    description="Returns the most recent still frame. HTTP clients should not cache the response beyond a single request.",
)
def get_snapshot() -> FileResponse:
    path = get_settings().snapshot_path
    if not os.path.exists(path):
        raise HTTPException(status_code=503, detail="snapshot not available")
    return FileResponse(
        path,
        media_type="image/jpeg",
        filename="snapshot.jpg",
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Content-Disposition": "inline; filename=snapshot.jpg",
            "X-Accel-Buffering": "no",
        },
    )





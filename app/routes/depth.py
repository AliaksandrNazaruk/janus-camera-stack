"""Depth camera routes — registered for both camera types (depth_map/load is universal).

Proxies depth queries and frame data to the local realsense_mux (port 8000).
Also provides the depth_map/load endpoint used by arm3d 3-D scene.

Route-purity Phase 6: the httpx client lives in services/depth_mux_client and the proxy +
error-mapping logic in services/depth_mux_proxy (an HTTP proxy adapter — it IS the boundary, so
it keeps FastAPI Response/HTTPException). These handlers are thin: parse/validate HTTP input,
delegate, return.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.middleware.rate_limit import require_rate_limit

from app.core.viewer_auth import require_viewer
from app.services import depth_mux_proxy
from app.services.depth_mux_proxy import DepthResponse  # re-exported for response_model=

_log = logging.getLogger(__name__)

router = APIRouter(tags=["depth"])

# P0-SEC-001: viewer gate. Dev mode (VIEWER_TOKENS unset) → no-op pass.
VIEWER_DEPENDENCY = Depends(require_viewer)

# realsense_mux client lifecycle (services/depth_mux_client) is closed on app shutdown
# from core/events; the proxy/error-mapping (services/depth_mux_proxy) is reached below.


# ── Depth query ──

depth_description = (
    "Returns the depth value at the given normalized coordinates (0..100), "
    "where (0,0) is the lower-left corner and (100,100) is the upper-right."
)


@router.get("/depth", response_model=DepthResponse, summary="Get depth at specified coordinates", description=depth_description, dependencies=[Depends(require_rate_limit), VIEWER_DEPENDENCY])
async def get_depth(
    x: Optional[float] = None,
    y: Optional[float] = None,
    message: Optional[str] = None,
    aligned: bool = False,
) -> DepthResponse:
    def parse_from_message(payload: str) -> tuple[Optional[float], Optional[float]]:
        try:
            data = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            return None, None
        try:
            return (
                float(data["x"]) if "x" in data else None,
                float(data["y"]) if "y" in data else None,
            )
        except (TypeError, ValueError):
            return None, None

    if (x is None or y is None) and message:
        msg_x, msg_y = parse_from_message(message)
        x = x if x is not None else msg_x
        y = y if y is not None else msg_y

    if x is None or y is None:
        raise HTTPException(status_code=422, detail="Parameters 'x' and 'y' are required")

    return await depth_mux_proxy.depth_at(x, y, aligned)


# ── Color frame ──

@router.get("/depth/color_frame", summary="Get D435 colour frame (RGB24)", dependencies=[VIEWER_DEPENDENCY])
async def get_depth_color_frame(format: str = "json"):
    return await depth_mux_proxy.proxy_realsense("/color_frame", format)


# ── Depth frame ──

@router.get("/depth/frame", summary="Get full depth frame (float32)", dependencies=[VIEWER_DEPENDENCY])
async def get_depth_frame(format: str = "json"):
    return await depth_mux_proxy.proxy_realsense("/depth_map", format)


# ── Aligned RGBD overlay ──

@router.get("/depth/frame_color_overlay", summary="Get aligned RGBD frame", dependencies=[VIEWER_DEPENDENCY])
async def get_depth_frame_color_overlay(format: str = "json"):
    return await depth_mux_proxy.frame_color_overlay()


# ── Depth map load (used by arm3d 3-D scene) ──

@router.get("/api/v1/depth_map/load", summary="Load full depth map")
@router.get("/depth_map/load", include_in_schema=False)
async def depth_map_load(format: str = "json"):
    return await depth_mux_proxy.depth_map_load(format)

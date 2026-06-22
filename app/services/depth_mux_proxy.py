"""Depth MUX proxy use-cases — proxy to the local realsense_mux and map httpx failures to
HTTP errors. (Distinct from routes/depth_proxy.py, the L5 cross-node depth-camera reverse
proxy — this one is the realsense_mux HTTP path: fallback /depth + arm3d frame endpoints.)

Extracted from routes/depth.py (route-purity Phase 6); behavior verbatim, including the
per-route status codes and detail strings (which intentionally differ between routes). Returns
FastAPI Response/JSONResponse directly (exact passthrough) and raises HTTPException; the httpx
client lifecycle lives in services/depth_mux_client. The DepthResponse model lives here.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app.core.settings import get_settings
from app.services import depth_mux_client

_log = logging.getLogger(__name__)


def _inc_depth_proxy_errors() -> None:
    try:
        from app.metrics import depth_proxy_errors_total
        depth_proxy_errors_total.inc()
    except Exception:
        _log.debug("depth proxy error metric increment failed", exc_info=True)


class DepthResponse(BaseModel):
    type: str = "depth"
    x: float
    y: float
    depth: float
    # P1-CV-001: present only when aligned=true was requested. Surfaces
    # whether mux had calibration AND the spatial-correction reason
    # (ok / nearest_neighbor / no_valid_depth / no_calibration).
    aligned: Optional[bool] = None
    reason: Optional[str] = None
    age_ms: Optional[int] = None
    stale: Optional[bool] = None


async def proxy_realsense(upstream_path: str, format: str = "json") -> Response | JSONResponse:
    """Common proxy logic for realsense_mux endpoints."""
    client = await depth_mux_client.get_client()
    params = {"format": format} if format else {}
    try:
        resp = await client.get(upstream_path, params=params)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        if format == "raw":
            return Response(
                content=resp.content,
                media_type="application/octet-stream",
                headers={
                    "X-Width": resp.headers.get("X-Width", ""),
                    "X-Height": resp.headers.get("X-Height", ""),
                    "X-Dtype": resp.headers.get("X-Dtype", ""),
                    "X-Timestamp": resp.headers.get("X-Timestamp", ""),
                },
            )
        try:
            return JSONResponse(content=resp.json())
        except (json.JSONDecodeError, ValueError) as e:
            _inc_depth_proxy_errors()
            raise HTTPException(status_code=502, detail=f"realsense_mux returned invalid JSON: {e}")
    except httpx.TimeoutException as e:
        _inc_depth_proxy_errors()
        raise HTTPException(status_code=504, detail=f"realsense_mux timeout: {e}")
    except httpx.ConnectError as e:
        _inc_depth_proxy_errors()
        raise HTTPException(status_code=502, detail=f"realsense_mux unreachable: {e}")
    except httpx.HTTPError as e:
        _inc_depth_proxy_errors()
        raise HTTPException(status_code=502, detail=f"realsense_mux proxy error: {e}")


async def depth_at(x: float, y: float, aligned: bool) -> DepthResponse:
    """Point depth query against mux /depth. Coords are clamped to 0..100."""
    x = max(0.0, min(100.0, x))
    y = max(0.0, min(100.0, y))

    client = await depth_mux_client.get_client()
    # P1-CV-001: passthrough aligned flag to mux. Default false keeps legacy
    # naive sampling (x_pct/y_pct interpreted as depth-frame fraction).
    params: Dict[str, Any] = {"x": x, "y": y}
    if aligned:
        params["aligned"] = "true"
    try:
        resp = await client.get("/depth", params=params)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        try:
            return DepthResponse(**resp.json())
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            _inc_depth_proxy_errors()
            raise HTTPException(status_code=502, detail=f"Invalid depth response: {e}")
    except httpx.TimeoutException as e:
        _inc_depth_proxy_errors()
        raise HTTPException(status_code=504, detail=f"Depth query timeout: {e}")
    except httpx.HTTPError as e:
        _inc_depth_proxy_errors()
        raise HTTPException(status_code=502, detail=f"Depth query error: {e}")


async def frame_color_overlay() -> JSONResponse:
    """Aligned RGBD overlay — color + depth fetched in parallel, merged into one JSON."""
    client = await depth_mux_client.get_client()
    try:
        color_resp, depth_resp = await asyncio.gather(
            client.get("/color_frame", params={"format": "json"}),
            client.get("/depth_map", params={"format": "json"}),
        )
        if color_resp.status_code != 200:
            raise HTTPException(status_code=color_resp.status_code, detail=color_resp.text)
        if depth_resp.status_code != 200:
            raise HTTPException(status_code=depth_resp.status_code, detail=depth_resp.text)
        try:
            cj = color_resp.json()
            dj = depth_resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            _inc_depth_proxy_errors()
            raise HTTPException(status_code=502, detail=f"RGBD invalid JSON: {e}")
        try:
            return JSONResponse(content={
                "width": dj["width"],
                "height": dj["height"],
                "timestamp": dj.get("timestamp", 0),
                "rgb_data": cj["data"],
                "rgb_dtype": cj.get("dtype", "uint8-rgb24"),
                "depth_data": dj["data"],
                "depth_dtype": dj.get("dtype", "float32"),
            })
        except KeyError as e:
            _inc_depth_proxy_errors()
            raise HTTPException(status_code=502, detail=f"RGBD response missing field: {e}")
    except httpx.TimeoutException as e:
        _inc_depth_proxy_errors()
        raise HTTPException(status_code=504, detail=f"Aligned RGBD timeout: {e}")
    except httpx.HTTPError as e:
        _inc_depth_proxy_errors()
        raise HTTPException(status_code=502, detail=f"Aligned RGBD proxy error: {e}")


async def depth_map_load(format: str = "json") -> Response | JSONResponse:
    """Load full depth map. depth_camera → local mux; color_camera → proxy to depth node."""
    settings = get_settings()
    if settings.camera_type == "depth_camera":
        return await proxy_realsense("/depth_map", format)

    # Color camera → proxy to depth camera node (reuses managed client pool)
    from app.services import depth_camera_proxy
    try:
        resp = await depth_camera_proxy.get("/depth_map/load", format=format)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        if format == "raw":
            return Response(
                content=resp.content,
                media_type="application/octet-stream",
                headers={
                    "X-Width": resp.headers.get("X-Width", ""),
                    "X-Height": resp.headers.get("X-Height", ""),
                    "X-Dtype": resp.headers.get("X-Dtype", "float32"),
                    "X-Timestamp": resp.headers.get("X-Timestamp", ""),
                },
            )
        try:
            return JSONResponse(content=resp.json())
        except (json.JSONDecodeError, ValueError) as exc:
            _inc_depth_proxy_errors()
            raise HTTPException(status_code=502, detail=f"Depth map invalid JSON: {exc}")
    except httpx.TimeoutException as exc:
        _inc_depth_proxy_errors()
        raise HTTPException(status_code=504, detail=f"Depth map timeout: {exc}")
    except httpx.HTTPError as exc:
        _inc_depth_proxy_errors()
        raise HTTPException(status_code=502, detail=f"Depth map proxy error: {exc}")

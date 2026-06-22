"""realsense_mux HTTP client adapter — lazy AsyncClient to REALSENSE_MUX_URL (:8000).

Extracted from routes/depth.py (route-purity Phase 6). Lifecycle is VERBATIM: lazy init
behind a double-checked asyncio lock, the same Timeout(connect=2,read=5,write=2,pool=5), and
NO connection Limits (deliberately distinct from depth_camera_proxy's proxy_base pool — see
docs/design/ROUTE_PURITY_CLOSEOUT.md). `close()` is called from core/events on app shutdown.
"""
from __future__ import annotations

import asyncio

import httpx

from app.core.settings import get_settings

_mux_client: httpx.AsyncClient | None = None
_mux_client_lock = asyncio.Lock()


async def get_client() -> httpx.AsyncClient:
    global _mux_client
    if _mux_client is None:
        async with _mux_client_lock:
            if _mux_client is None:
                _mux_client = httpx.AsyncClient(
                    base_url=get_settings().realsense_mux_url,
                    timeout=httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=5.0),
                )
    return _mux_client


async def close() -> None:
    """Close the realsense_mux HTTP client (called on app shutdown)."""
    global _mux_client
    async with _mux_client_lock:
        if _mux_client is not None:
            await _mux_client.aclose()
            _mux_client = None

"""Shared HTTP request helper (Phase 2B-5): resolve the asset-path prefix from a request.

Moved verbatim from routes/templates.py so both routes/templates.py and routes/device_camera.py
import it from a neutral home (neither route imports the other). core/ is the right layer for a
cross-route HTTP utility — it takes a fastapi Request and returns a path prefix string.
"""
from __future__ import annotations

from fastapi import Request


def _api_prefix_from_request(request: Request) -> str:
    """Detect prefix that must be prepended to asset paths.

    Sources in order of preference:
      1. `X-Forwarded-Prefix` header (api_gateway sets it when proxying —
         contains "/api/v1/color_camera" etc)
      2. request.url.path prefix match (direct L4 hit via legacy URL —
         /api/v1/{cam}/color_view.html still has prefix in path)
      3. "" — direct L4 access (paths resolve at root)
    """
    fwd = request.headers.get("x-forwarded-prefix", "").strip()
    if fwd and fwd.startswith("/api/v1/"):
        return fwd.rstrip("/")
    path = request.url.path
    for cam in ("color_camera", "depth_camera"):
        prefix = f"/api/v1/{cam}/"
        if path.startswith(prefix):
            return prefix.rstrip("/")
    return ""

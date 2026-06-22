"""Phase 2B-1 characterization for `_api_prefix_from_request` — the pure Request→prefix helper
currently in routes/templates.py (moves to app/core/http_prefix.py in 2B-5; only this import
re-points then). Pins the 3 resolution branches + the /api/v1-only guard before the move.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from app.core.http_prefix import _api_prefix_from_request


def _req(*, fwd: str = "", path: str = "/"):
    r = MagicMock()
    r.headers = {"x-forwarded-prefix": fwd} if fwd else {}
    r.url.path = path
    return r


def test_x_forwarded_prefix_wins_and_is_rstripped():
    assert _api_prefix_from_request(_req(fwd="/api/v1/color_camera")) == "/api/v1/color_camera"
    assert _api_prefix_from_request(_req(fwd="/api/v1/color_camera/")) == "/api/v1/color_camera"


def test_non_apiv1_forwarded_prefix_is_ignored():
    # Only /api/v1/* forwarded prefixes are honored; others fall through to path/default.
    assert _api_prefix_from_request(_req(fwd="/garbage", path="/")) == ""


def test_url_path_prefix_match():
    assert _api_prefix_from_request(
        _req(path="/api/v1/color_camera/color_view.html")) == "/api/v1/color_camera"
    assert _api_prefix_from_request(
        _req(path="/api/v1/depth_camera/foo")) == "/api/v1/depth_camera"


def test_default_empty_for_root_access():
    assert _api_prefix_from_request(_req(path="/color_view.html")) == ""

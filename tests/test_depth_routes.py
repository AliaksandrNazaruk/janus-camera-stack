"""Tests for app/routes/depth.py — depth camera mux proxy routes.

Validates that:
- Depth color frame proxies to realsense_mux
- Depth frame proxies to depth_map endpoint
- JSON parsing from message query param works
- Timeout returns 504

All tests mock the mux client (services/depth_mux_client.get_client) — no real realsense_mux
needed. (Route-purity Phase 6: client + proxy logic moved out of routes/depth.py; this oracle
was re-pointed to the new client location with the SAME assertions.)
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest


# ── Helpers ───────────────────────────────────────────────────────────

def _mux_json_response(json_body: dict, status: int = 200) -> httpx.Response:
    """Build a real httpx.Response with JSON body."""
    return httpx.Response(
        status_code=status,
        json=json_body,
        request=httpx.Request("GET", "http://localhost:8000/fake"),
    )


def _mux_raw_response(content: bytes, headers: dict, status: int = 200) -> httpx.Response:
    """Build a real httpx.Response with raw binary body."""
    return httpx.Response(
        status_code=status,
        content=content,
        headers=headers,
        request=httpx.Request("GET", "http://localhost:8000/fake"),
    )


def _make_async_client(return_value=None, side_effect=None) -> AsyncMock:
    """Return an AsyncMock pretending to be httpx.AsyncClient."""
    client = AsyncMock(spec=httpx.AsyncClient)
    if side_effect is not None:
        client.get = AsyncMock(side_effect=side_effect)
    else:
        client.get = AsyncMock(return_value=return_value)
    return client


# ── Depth color frame tests ──────────────────────────────────────────

class TestDepthColorFrame:
    """GET /depth/color_frame proxies to realsense_mux /color_frame."""

    @pytest.mark.asyncio
    async def test_depth_color_frame_proxies_to_mux(self, client):
        """Route calls mux client with /color_frame and returns JSON."""
        mux_resp = _mux_json_response(
            {"width": 640, "height": 480, "data": [1, 2, 3]},
        )
        mock_client = _make_async_client(return_value=mux_resp)

        with patch("app.services.depth_mux_client.get_client", return_value=mock_client):
            resp = await client.get("/depth/color_frame")

        assert resp.status_code == 200
        body = resp.json()
        assert body["width"] == 640
        mock_client.get.assert_awaited_once_with("/color_frame", params={"format": "json"})

    @pytest.mark.asyncio
    async def test_depth_color_frame_raw_format(self, client):
        """format=raw returns binary with X-Width/X-Height headers."""
        mux_resp = _mux_raw_response(
            content=b"\x00" * 100,
            headers={
                "X-Width": "640",
                "X-Height": "480",
                "X-Dtype": "uint8",
                "X-Timestamp": "12345",
            },
        )
        mock_client = _make_async_client(return_value=mux_resp)

        with patch("app.services.depth_mux_client.get_client", return_value=mock_client):
            resp = await client.get("/depth/color_frame?format=raw")

        assert resp.status_code == 200
        mock_client.get.assert_awaited_once_with("/color_frame", params={"format": "raw"})


# ── Depth frame tests ────────────────────────────────────────────────

class TestDepthFrame:
    """GET /depth/frame proxies to realsense_mux /depth_map."""

    @pytest.mark.asyncio
    async def test_depth_frame_proxies_to_depth_map(self, client):
        """Route calls mux client with /depth_map."""
        mux_resp = _mux_json_response(
            {"width": 640, "height": 480, "data": [0.5, 1.0]},
        )
        mock_client = _make_async_client(return_value=mux_resp)

        with patch("app.services.depth_mux_client.get_client", return_value=mock_client):
            resp = await client.get("/depth/frame")

        assert resp.status_code == 200
        body = resp.json()
        assert body["width"] == 640
        mock_client.get.assert_awaited_once_with("/depth_map", params={"format": "json"})


# ── Depth query with message param ───────────────────────────────────

class TestDepthEndpointMessageParam:
    """GET /depth?message={...} parses coordinates from JSON payload."""

    @pytest.mark.asyncio
    async def test_depth_endpoint_with_message_param(self, client):
        """x/y parsed from message JSON when not provided as query params."""
        mux_resp = _mux_json_response(
            {"type": "depth", "x": 50.0, "y": 75.0, "depth": 1.23},
        )
        mock_client = _make_async_client(return_value=mux_resp)
        msg = json.dumps({"x": 50, "y": 75})

        with patch("app.services.depth_mux_client.get_client", return_value=mock_client):
            resp = await client.get("/depth", params={"message": msg})

        assert resp.status_code == 200
        body = resp.json()
        assert body["type"] == "depth"
        assert body["depth"] == 1.23

    @pytest.mark.asyncio
    async def test_depth_endpoint_message_invalid_json(self, client):
        """Invalid JSON in message param with no x/y falls back to 422."""
        mock_client = _make_async_client(return_value=_mux_json_response({}))

        with patch("app.services.depth_mux_client.get_client", return_value=mock_client):
            resp = await client.get("/depth", params={"message": "not-json"})

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_depth_endpoint_missing_xy_returns_422(self, client):
        """No x, no y, no message returns 422."""
        mock_client = _make_async_client(return_value=_mux_json_response({}))

        with patch("app.services.depth_mux_client.get_client", return_value=mock_client):
            resp = await client.get("/depth")

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_depth_endpoint_explicit_xy_overrides_message(self, client):
        """Explicit x/y query params take precedence over message."""
        mux_resp = _mux_json_response(
            {"type": "depth", "x": 10.0, "y": 20.0, "depth": 0.5},
        )
        mock_client = _make_async_client(return_value=mux_resp)
        msg = json.dumps({"x": 99, "y": 99})

        with patch("app.services.depth_mux_client.get_client", return_value=mock_client):
            resp = await client.get("/depth", params={"x": 10, "y": 20, "message": msg})

        assert resp.status_code == 200
        # Verify the mux was called with the explicit x/y, not message values
        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert float(params["x"]) == 10.0
        assert float(params["y"]) == 20.0


# ── P1-CV-001 aligned passthrough ────────────────────────────────────

class TestDepthEndpointAlignedPassthrough:
    """GET /depth?aligned=true forwards to mux + surfaces aligned/reason fields."""

    @pytest.mark.asyncio
    async def test_aligned_default_false_not_sent_to_mux(self, client):
        """Default aligned=false → query NOT sent to mux (legacy contract)."""
        mux_resp = _mux_json_response(
            {"type": "depth", "x": 50.0, "y": 50.0, "depth": 1.2,
             "age_ms": 12, "stale": False},
        )
        mock_client = _make_async_client(return_value=mux_resp)
        with patch("app.services.depth_mux_client.get_client", return_value=mock_client):
            resp = await client.get("/depth", params={"x": 50, "y": 50})
        assert resp.status_code == 200
        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert "aligned" not in params  # passthrough only when explicitly requested

    @pytest.mark.asyncio
    async def test_aligned_true_forwarded_to_mux(self, client):
        """aligned=true → forwarded as string "true" in mux query."""
        mux_resp = _mux_json_response(
            {"type": "depth", "x": 50.0, "y": 50.0, "depth": 0.85,
             "age_ms": 5, "stale": False, "aligned": True, "reason": "ok"},
        )
        mock_client = _make_async_client(return_value=mux_resp)
        with patch("app.services.depth_mux_client.get_client", return_value=mock_client):
            resp = await client.get("/depth", params={"x": 50, "y": 50, "aligned": "true"})
        assert resp.status_code == 200
        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params.get("aligned") == "true"

    @pytest.mark.asyncio
    async def test_aligned_response_surfaces_reason(self, client):
        """When mux returns aligned/reason — proxy surfaces both to client."""
        mux_resp = _mux_json_response(
            {"type": "depth", "x": 50.0, "y": 50.0, "depth": 8.4,
             "age_ms": 5, "stale": False,
             "aligned": True, "reason": "nearest_neighbor"},
        )
        mock_client = _make_async_client(return_value=mux_resp)
        with patch("app.services.depth_mux_client.get_client", return_value=mock_client):
            resp = await client.get("/depth", params={"x": 50, "y": 50, "aligned": "true"})
        body = resp.json()
        assert body["aligned"] is True
        assert body["reason"] == "nearest_neighbor"
        assert body["depth"] == 8.4

    @pytest.mark.asyncio
    async def test_aligned_no_calibration_fallback_surfaced(self, client):
        """Mux without calibration → returns aligned=false, reason=no_calibration."""
        mux_resp = _mux_json_response(
            {"type": "depth", "x": 50.0, "y": 50.0, "depth": 1.0,
             "age_ms": 10, "stale": False,
             "aligned": False, "reason": "no_calibration"},
        )
        mock_client = _make_async_client(return_value=mux_resp)
        with patch("app.services.depth_mux_client.get_client", return_value=mock_client):
            resp = await client.get("/depth", params={"x": 50, "y": 50, "aligned": "true"})
        body = resp.json()
        assert body["aligned"] is False
        assert body["reason"] == "no_calibration"


# ── Timeout handling ─────────────────────────────────────────────────

class TestDepthProxyTimeout:
    """Upstream timeout from realsense_mux returns 504."""

    @pytest.mark.asyncio
    async def test_depth_proxy_timeout_returns_504(self, client):
        """httpx.TimeoutException from mux results in HTTP 504."""
        mock_client = _make_async_client(
            side_effect=httpx.TimeoutException("read timeout"),
        )

        with patch("app.services.depth_mux_client.get_client", return_value=mock_client):
            resp = await client.get("/depth/color_frame")

        assert resp.status_code == 504
        assert "timeout" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_depth_query_timeout_returns_504(self, client):
        """Timeout on /depth query also returns 504."""
        mock_client = _make_async_client(
            side_effect=httpx.TimeoutException("read timeout"),
        )

        with patch("app.services.depth_mux_client.get_client", return_value=mock_client):
            resp = await client.get("/depth", params={"x": 50, "y": 50})

        assert resp.status_code == 504

    @pytest.mark.asyncio
    async def test_depth_proxy_connect_error_returns_502(self, client):
        """httpx.ConnectError from mux results in HTTP 502."""
        mock_client = _make_async_client(
            side_effect=httpx.ConnectError("Connection refused"),
        )

        with patch("app.services.depth_mux_client.get_client", return_value=mock_client):
            resp = await client.get("/depth/color_frame")

        assert resp.status_code == 502
        assert "unreachable" in resp.json()["detail"].lower()

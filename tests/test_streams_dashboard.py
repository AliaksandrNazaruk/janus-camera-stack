"""Tests for GET /api/v1/cameras/streams (Sprint X4).

Per-stream toggle endpoints intentionally NOT covered here — they live
at /api/v1/cameras/{serial}/{sensor}/{initialize,stop} (device_camera.py)
since those already persist desired_active via sensor_lifecycle. Testing
those are already covered by device_camera tests + test_sensor_reconcile.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


_TEST_TOKEN = "test-token-streams-list"


@pytest.fixture
def app_with_token():
    with patch("app.core.events.register_event_handlers", lambda app: None), \
         patch.dict(os.environ, {"CAM_ADMIN_TOKEN": _TEST_TOKEN}):
        from app.core.app import create_app
        yield create_app()


@pytest.mark.asyncio
async def test_list_streams_returns_allocations(app_with_token):
    """GET /api/v1/cameras/streams returns allocation rows with desired+runtime."""
    from app.services.mountpoint_allocator import Allocation

    fake_allocs = {
        "141722072135:depth": Allocation(mp_id=1306, rtp_port=5006, desired_active=False),
        "local:color": Allocation(mp_id=1305, rtp_port=5004, desired_active=True),
    }
    with patch("app.services.mountpoint_allocator.list_allocations", return_value=fake_allocs), \
         patch("app.services.sensor_lifecycle.is_running", side_effect=lambda s: s == "color"):
        transport = ASGITransport(app=app_with_token)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get(
                "/cameras/streams",
                headers={"X-Admin-Token": _TEST_TOKEN},
            )
    assert r.status_code == 200
    body = r.json()
    streams = {(s["serial"], s["sensor"]): s for s in body["streams"]}
    assert ("141722072135", "depth") in streams
    assert ("local", "color") in streams
    assert streams[("local", "color")]["desired_active"] is True
    assert streams[("local", "color")]["runtime_active"] is True
    assert streams[("141722072135", "depth")]["desired_active"] is False
    assert streams[("141722072135", "depth")]["runtime_active"] is False


@pytest.mark.asyncio
async def test_list_streams_requires_admin_token(app_with_token):
    transport = ASGITransport(app=app_with_token)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/cameras/streams")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_list_streams_handles_empty_state(app_with_token):
    with patch("app.services.mountpoint_allocator.list_allocations", return_value={}):
        transport = ASGITransport(app=app_with_token)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get(
                "/cameras/streams",
                headers={"X-Admin-Token": _TEST_TOKEN},
            )
    assert r.status_code == 200
    body = r.json()
    assert body["streams"] == []
    assert "state_path" in body

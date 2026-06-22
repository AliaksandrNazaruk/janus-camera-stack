"""Tests for app/routes/system.py — system/health/static routes."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_SERVICE_ROOT), str(_SERVICE_ROOT.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class TestHealthz:
    @pytest.mark.asyncio
    async def test_returns_structured_response(self, client):
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        # Deep healthz: in test env Janus is unavailable, so ok may be False
        assert "ok" in body
        assert "mode" in body
        assert "janus_reachable" in body
        assert "stream_active" in body
        assert "details" in body
        assert isinstance(body["ok"], bool)


class TestRelayEndpoints:
    @pytest.mark.asyncio
    @patch("app.services.relay_proxy.relay_get", return_value={"time": 12345})
    async def test_relay_time(self, mock_get, client):
        resp = await client.get("/relay/time")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    @patch("app.services.relay_proxy.relay_get", side_effect=Exception("down"))
    async def test_relay_time_error(self, mock_get, client):
        resp = await client.get("/relay/time")
        assert resp.status_code == 502

    @pytest.mark.asyncio
    @patch("app.services.relay_proxy.relay_get", return_value={"pong": True})
    async def test_relay_pong(self, mock_get, client):
        resp = await client.get("/relay/pong")
        assert resp.status_code == 200


class TestRestartService:
    @pytest.mark.asyncio
    @patch("app.routes.system.service_restart")
    async def test_restart_ok(self, mock_restart, client):
        # Default settings has api_key=None → no auth required
        resp = await client.post("/action/restart")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        mock_restart.assert_called_once()


class TestJanusJsServing:
    @pytest.mark.asyncio
    async def test_serves_local_file(self, client, tmp_path):
        js_file = tmp_path / "janus.js"
        js_file.write_text("// janus lib")
        with patch("app.routes.templates.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir=str(tmp_path), camera_type="rgb_camera")
            resp = await client.get("/janus.js")
        assert resp.status_code == 200


class TestStreamerJsServing:
    @pytest.mark.asyncio
    async def test_missing_returns_404(self, client):
        with patch("app.routes.templates.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir="/nonexistent", camera_type="rgb_camera")
            resp = await client.get("/streamer.js")
        assert resp.status_code == 404


class TestDepthFeaturesJs:
    @pytest.mark.asyncio
    async def test_missing_returns_404(self, client):
        with patch("app.routes.templates.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir="/nonexistent", camera_type="rgb_camera")
            resp = await client.get("/depth_features.js")
        assert resp.status_code == 404


class TestGamepadJs:
    @pytest.mark.asyncio
    async def test_missing_returns_404(self, client):
        with patch("app.routes.templates.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir="/nonexistent", camera_type="rgb_camera")
            resp = await client.get("/gamepaddriver.js")
        assert resp.status_code == 404


class TestGamepadConfig:
    @pytest.mark.asyncio
    async def test_serves_json(self, client, tmp_path):
        cfg = tmp_path / "gamepad_config.json"
        cfg.write_text('{"axes": [0, 1]}')
        with patch("app.routes.templates.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir=str(tmp_path), camera_type="rgb_camera")
            resp = await client.get("/gamepad_config.json")
        assert resp.status_code == 200
        assert resp.json()["axes"] == [0, 1]

    @pytest.mark.asyncio
    async def test_invalid_json_500(self, client, tmp_path):
        cfg = tmp_path / "gamepad_config.json"
        cfg.write_text("{invalid")
        with patch("app.routes.templates.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir=str(tmp_path), camera_type="rgb_camera")
            resp = await client.get("/gamepad_config.json")
        assert resp.status_code == 500


class TestPlayerScript:
    @pytest.mark.asyncio
    async def test_serves_player_file(self, client, tmp_path):
        player_dir = tmp_path / "player"
        player_dir.mkdir()
        (player_dir / "app.js").write_text("// app")
        with patch("app.routes.templates.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir=str(tmp_path), camera_type="rgb_camera")
            resp = await client.get("/player/app.js")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_blocks_path_traversal(self, client, tmp_path):
        with patch("app.routes.templates.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir=str(tmp_path), camera_type="rgb_camera")
            resp = await client.get("/player/../../../etc/passwd")
        assert resp.status_code == 404


class TestFavicon:
    @pytest.mark.asyncio
    async def test_favicon_missing(self, client):
        with patch("app.routes.templates.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir="/nonexistent", camera_type="rgb_camera")
            resp = await client.get("/favicon.ico")
        # favicon() returns 204 when the file is missing
        assert resp.status_code == 204


class TestColorView:
    @pytest.mark.asyncio
    async def test_template_render(self, client):
        """GET /color_view.html serves the real Jinja-rendered RGB color view.

        The template-serving consolidation replaced the old __CAM_TYPE__ string-substitution and the
        per-request templates_dir: color_view.html is now rendered by a Jinja env whose
        FileSystemLoader is built at IMPORT time, so patching app.routes.templates.get_settings can no
        longer redirect it to a fixture. Assert the real rendered contract instead of the obsolete
        fixture path (recon-verified: route behavior is correct — this is a test-only fix)."""
        resp = await client.get("/color_view.html")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        body = resp.text
        assert "<!DOCTYPE html>" in body and '<video id="video"' in body   # the real color viewer
        assert "RealSense Viewer" in body                                  # RGB variant (not depth)
        assert "{{" not in body and "{%" not in body                       # Jinja fully rendered


class TestDepthMapLoad:
    """Tests for /api/v1/depth_map/load and /depth_map/load proxy routes."""

    @pytest.mark.asyncio
    @patch("app.services.depth_mux_proxy.get_settings")
    async def test_depth_camera_node_proxies_locally(self, mock_settings, client):
        """On a depth_camera node the request proxies to localhost:8000/depth_map."""
        mock_settings.return_value = MagicMock(
            camera_type="depth_camera",
            depth_cam_url="http://192.168.1.55:8900",
            realsense_mux_url="http://localhost:8000",
        )
        fake_payload = {"width": 480, "height": 848, "dtype": "float32", "timestamp": 1.0, "data": "AAAA"}

        # Reset the lazy mux client so it picks up new settings
        import app.services.depth_mux_client as depth_mod
        depth_mod._mux_client = None

        import respx
        with respx.mock:
            respx.get("http://localhost:8000/depth_map").respond(200, json=fake_payload)
            resp = await client.get("/depth_map/load")

        assert resp.status_code == 200
        assert resp.json()["width"] == 480
        depth_mod._mux_client = None  # cleanup

    @pytest.mark.asyncio
    @patch("app.services.depth_mux_proxy.get_settings")
    async def test_color_camera_node_proxies_remote(self, mock_settings, client):
        """On a color_camera node the request proxies to the depth camera URL."""
        mock_settings.return_value = MagicMock(
            camera_type="color_camera",
            depth_cam_url="http://192.168.1.55:8900",
            realsense_mux_url="http://localhost:8000",
        )
        fake_payload = {"width": 480, "height": 848, "dtype": "float32", "timestamp": 1.0, "data": "AAAA"}

        import respx
        with respx.mock:
            respx.get("http://192.168.1.55:8900/depth_map/load").respond(200, json=fake_payload)
            resp = await client.get("/depth_map/load")

        assert resp.status_code == 200
        assert resp.json()["dtype"] == "float32"

    @pytest.mark.asyncio
    @patch("app.services.depth_mux_proxy.get_settings")
    async def test_upstream_error_returns_502(self, mock_settings, client):
        """Network failure to upstream returns 502."""
        mock_settings.return_value = MagicMock(
            camera_type="color_camera",
            depth_cam_url="http://192.168.1.55:8900",
            realsense_mux_url="http://localhost:8000",
        )
        import respx
        with respx.mock:
            respx.get("http://192.168.1.55:8900/depth_map/load").mock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            resp = await client.get("/depth_map/load")

        assert resp.status_code == 502

    @pytest.mark.asyncio
    @patch("app.services.depth_mux_proxy.get_settings")
    async def test_upstream_503_forwarded(self, mock_settings, client):
        """Upstream 503 (no frame yet) is forwarded."""
        mock_settings.return_value = MagicMock(
            camera_type="depth_camera",
            depth_cam_url="http://192.168.1.55:8900",
            realsense_mux_url="http://localhost:8000",
        )
        import app.services.depth_mux_client as depth_mod
        depth_mod._mux_client = None

        import respx
        with respx.mock:
            respx.get("http://localhost:8000/depth_map").respond(503, text='{"detail":"no depth frame yet"}')
            resp = await client.get("/depth_map/load")

        assert resp.status_code == 503
        depth_mod._mux_client = None

    @pytest.mark.asyncio
    @patch("app.services.depth_mux_proxy.get_settings")
    async def test_raw_format_passthrough(self, mock_settings, client):
        """format=raw proxies binary content with headers."""
        mock_settings.return_value = MagicMock(
            camera_type="depth_camera",
            depth_cam_url="http://192.168.1.55:8900",
            realsense_mux_url="http://localhost:8000",
        )
        import app.services.depth_mux_client as depth_mod
        depth_mod._mux_client = None

        import respx
        with respx.mock:
            respx.get("http://localhost:8000/depth_map").respond(
                200,
                content=b"\x00" * 16,
                headers={
                    "X-Width": "4",
                    "X-Height": "1",
                    "X-Dtype": "float32",
                    "X-Timestamp": "1.0",
                },
            )
            resp = await client.get("/depth_map/load?format=raw")

        assert resp.status_code == 200
        assert resp.headers["x-width"] == "4"
        assert resp.headers["x-dtype"] == "float32"
        depth_mod._mux_client = None


# ── P1-NET-001 — TURN probe ephemeral creds integration ─────────────

def _settings_with(**overrides):
    """Build a MagicMock that mirrors current settings + applies overrides.

    Settings is a frozen dataclass, can't mutate directly. Patch the route's
    get_settings() reference to return this mock."""
    from app.core.settings import get_settings
    real = get_settings()
    fields = {f: getattr(real, f) for f in (
        "turn_host", "turn_port", "turn_user", "turn_pass", "turn_shared_secret",
        "turn_cred_ttl", "watchdog_stale_ms", "service_name",
        "camera_type", "janus_mount_id",
    )}
    fields.update(overrides)
    mock = MagicMock()
    for k, v in fields.items():
        setattr(mock, k, v)
    return mock


class TestTurnProbeHmacIntegration:
    """Verify /health/stream invokes turn_probe with HMAC ephemeral creds when
    TURN_SHARED_SECRET is set (production path), not static turn_pass."""

    @pytest.mark.asyncio
    async def test_uses_shared_secret_hmac_path(self, client):
        captured = {}

        def fake_probe(turn_host, turn_port, turn_user, turn_password, **_):
            captured["user"] = turn_user
            captured["password"] = turn_password
            return {
                "ok": True, "stun_ok": True, "turn_alloc_ok": True,
                "host": turn_host, "port": turn_port,
                "mapped_address": "1.2.3.4:5000", "tools_available": True,
                "error": None, "error_detail": None,
            }

        with patch("app.routes.system.get_settings", return_value=_settings_with(turn_shared_secret="secret-test-32")), \
             patch("app.services.turn_probe.probe_summary", side_effect=fake_probe), \
             patch("app.services.nat_config.load_nat_config") as load_nat:
            load_nat.return_value = MagicMock(turn_user="webrtc")
            resp = await client.get("/health/stream")

        body = resp.json()
        turn_check = body["checks"]["turn_server"]
        assert turn_check["cred_source"] == "shared_secret_hmac"
        assert ":" in captured["user"], f"expected ephemeral '<ts>:user', got {captured['user']!r}"
        assert captured["password"], "expected non-empty HMAC credential"

    @pytest.mark.asyncio
    async def test_falls_back_to_static_when_no_shared_secret(self, client):
        captured = {}

        def fake_probe(turn_host, turn_port, turn_user, turn_password, **_):
            captured["user"] = turn_user
            captured["password"] = turn_password
            return {"ok": True, "stun_ok": True, "turn_alloc_ok": True,
                    "host": turn_host, "port": turn_port, "tools_available": True,
                    "error": None, "error_detail": None}

        with patch("app.routes.system.get_settings", return_value=_settings_with(turn_shared_secret="", turn_pass="static-pw", turn_user="static-u")), \
             patch("app.services.turn_probe.probe_summary", side_effect=fake_probe):
            resp = await client.get("/health/stream")

        body = resp.json()
        turn_check = body["checks"]["turn_server"]
        assert turn_check["cred_source"] == "static"
        assert captured["user"] == "static-u"
        assert captured["password"] == "static-pw"

    @pytest.mark.asyncio
    async def test_reports_unset_when_no_creds(self, client):
        def fake_probe(turn_host, turn_port, turn_user, turn_password, **_):
            return {"ok": False, "stun_ok": True, "turn_alloc_ok": False,
                    "host": turn_host, "port": turn_port, "tools_available": True,
                    "error": "no credentials", "error_detail": None}

        with patch("app.routes.system.get_settings", return_value=_settings_with(turn_shared_secret="", turn_pass="")), \
             patch("app.services.turn_probe.probe_summary", side_effect=fake_probe):
            resp = await client.get("/health/stream")

        body = resp.json()
        turn_check = body["checks"]["turn_server"]
        assert turn_check["cred_source"] == "unset"

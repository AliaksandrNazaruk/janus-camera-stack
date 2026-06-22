"""Tests for app/routes/janus.py and app/services/nat_config.py — Janus health, NAT, proxy."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.routes.janus import JanusNatConfig
from app.services.nat_config import (
    load_nat_config,
    restart_depth_camera_janus,
    restart_janus,
)


class TestJanusHealthz:
    @pytest.mark.asyncio
    @patch("app.routes.janus.janus.streaming_info")
    async def test_healthy(self, mock_info, client):
        mock_info.return_value = {
            "data": {
                "info": {
                    "info": {
                        "id": 1,
                        "enabled": True,
                        "media": [{"age_ms": 50, "codec": "h264"}],
                    }
                }
            }
        }
        resp = await client.get("/janus/healthz")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    @patch("app.routes.janus.janus.streaming_info", return_value={})
    async def test_janus_empty_response(self, mock_info, client):
        resp = await client.get("/janus/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False


class TestJanusRestart:
    @pytest.mark.asyncio
    @patch("app.routes.janus.restart_janus")
    async def test_restart_ok(self, mock_restart, client):
        resp = await client.post("/janus/restart")
        # No admin token provided → require_admin raises 403
        assert resp.status_code == 403

    # CHAR (Cycle 13): pin the SYNCHRONOUS /janus/restart contract. The additive tracked endpoint
    # (POST /janus/restart-tracked) MUST NOT change this — the depth-peer machine client
    # (restart_depth_camera_janus → httpx.post + status_code==200 check) depends on the 200=done semantics.
    @pytest.mark.asyncio
    @patch("app.routes.janus.restart_janus")
    async def test_restart_sync_success_is_200(self, mock_restart, admin_client):
        resp = await admin_client.post("/janus/restart")
        assert resp.status_code == 200          # sync 200 = restart done
        mock_restart.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.routes.janus.restart_janus")
    async def test_restart_sync_failure_is_500_detail(self, mock_restart, admin_client):
        from app.services.nat_config import JanusAdminError
        mock_restart.side_effect = JanusAdminError("janus-admin restart exit=4", exit_code=4)
        resp = await admin_client.post("/janus/restart")
        assert resp.status_code == 500 and "detail" in resp.json()


class TestJanusNatUpdate:
    @pytest.mark.asyncio
    @patch("app.services.nat_config.restart_depth_camera_janus")
    @patch("app.services.nat_config.restart_janus")
    @patch("app.services.nat_config.patch_janus_cfg_with_nat")
    @patch("app.services.nat_config.save_nat_config")
    @patch("app.services.nat_config.load_nat_config")
    async def test_post_nat_preserves_masked_password(self, mock_load, mock_save, mock_patch, mock_rj, mock_rd, admin_client):
        # the console edits other fields and submits turn_pwd="***" (the GET mask) —
        # the stored secret must be preserved, not overwritten with the mask.
        mock_load.return_value = JanusNatConfig(turn_server="t", stun_server="s", turn_pwd="realsecret")
        body = JanusNatConfig(turn_server="t2", stun_server="s2", turn_pwd="***").model_dump()
        r = await admin_client.post("/janus/nat", json=body)
        assert r.status_code == 200, r.text
        saved = mock_save.call_args[0][0]
        assert saved.turn_pwd == "realsecret"        # masked → preserved
        assert saved.turn_server == "t2"             # other edits applied
        assert r.json()["turn_pwd"] == "***"         # response never echoes the secret

    @pytest.mark.asyncio
    @patch("app.services.nat_config.restart_depth_camera_janus")
    @patch("app.services.nat_config.restart_janus")
    @patch("app.services.nat_config.patch_janus_cfg_with_nat")
    @patch("app.services.nat_config.save_nat_config")
    @patch("app.services.nat_config.load_nat_config")
    async def test_post_nat_sets_new_password(self, mock_load, mock_save, mock_patch, mock_rj, mock_rd, admin_client):
        mock_load.return_value = JanusNatConfig(turn_pwd="old")
        body = JanusNatConfig(turn_server="t", stun_server="s", turn_pwd="brandnew").model_dump()
        r = await admin_client.post("/janus/nat", json=body)
        assert r.status_code == 200
        assert mock_save.call_args[0][0].turn_pwd == "brandnew"   # explicit new value applied


class TestJanusProxy:
    @pytest.mark.asyncio
    @patch("app.services.janus_proxy.forward_request")
    async def test_proxy_get(self, mock_fwd, client):
        from fastapi.responses import Response

        mock_fwd.return_value = Response(content=b'{"janus":"pong"}', media_type="application/json")
        resp = await client.get("/janus")
        assert resp.status_code == 200


class TestClientConfig:
    @pytest.mark.asyncio
    @patch("app.routes.janus.load_nat_config")
    @patch("app.routes.janus.get_settings")
    async def test_returns_config(self, mock_settings, mock_nat, client, settings):
        mock_settings.return_value = MagicMock(
            camera_type="color_camera",
            ice_policy="all",
            turn_shared_secret="",
            turn_cred_ttl=86400,
        )
        mock_nat.return_value = JanusNatConfig()
        resp = await client.get("/client-config")
        assert resp.status_code == 200
        body = resp.json()
        assert "iceServers" in body

    @pytest.mark.asyncio
    @patch("app.routes.janus.load_nat_config")
    @patch("app.routes.janus.get_settings")
    async def test_depth_camera_forces_relay_policy(self, mock_settings, mock_nat, client):
        """Depth camera behind double NAT must always return iceTransportPolicy=relay."""
        mock_settings.return_value = MagicMock(
            camera_type="depth_camera",
            ice_policy="all",  # env says "all", but depth must override to "relay"
            turn_shared_secret="",
            turn_cred_ttl=86400,
        )
        mock_nat.return_value = JanusNatConfig()
        resp = await client.get("/client-config")
        assert resp.status_code == 200
        assert resp.json()["iceTransportPolicy"] == "relay"

    @pytest.mark.asyncio
    @patch("app.routes.janus.load_nat_config")
    @patch("app.routes.janus.get_settings")
    async def test_color_camera_respects_env_policy(self, mock_settings, mock_nat, client):
        """Color camera uses the ICE_POLICY env var as-is."""
        mock_settings.return_value = MagicMock(
            camera_type="color_camera",
            ice_policy="all",
            turn_shared_secret="",
            turn_cred_ttl=86400,
        )
        mock_nat.return_value = JanusNatConfig()
        resp = await client.get("/client-config")
        assert resp.status_code == 200
        assert resp.json()["iceTransportPolicy"] == "all"

    @pytest.mark.asyncio
    @patch("app.routes.janus.load_nat_config")
    @patch("app.routes.janus.get_settings")
    async def test_ephemeral_turn_creds_when_shared_secret_set(self, mock_settings, mock_nat, client):
        """When TURN_SHARED_SECRET is set, /client-config returns time-limited HMAC credentials."""
        mock_settings.return_value = MagicMock(
            camera_type="color_camera",
            ice_policy="all",
            turn_shared_secret="test-secret-abc",
            turn_cred_ttl=3600,
        )
        mock_nat.return_value = JanusNatConfig()
        resp = await client.get("/client-config")
        assert resp.status_code == 200
        body = resp.json()
        # TURN server entry should have ephemeral username (timestamp:user format)
        turn_entry = [s for s in body["iceServers"] if s.get("username")]
        assert len(turn_entry) == 1
        assert ":" in turn_entry[0]["username"]  # "expiry:webrtc"
        assert len(turn_entry[0]["credential"]) > 10  # base64 HMAC


# ── Helper function tests (no HTTP client needed) ───────────────────


class TestLoadNatConfig:
    @patch("app.services.nat_config._janus_nat_json")
    @patch("app.services.nat_config.get_settings")
    def test_defaults_when_no_file(self, mock_settings, mock_path):
        mock_settings.return_value = MagicMock(camera_type="rgb_camera")
        mock_path.return_value.exists.return_value = False
        cfg = load_nat_config()
        assert isinstance(cfg, JanusNatConfig)
        assert cfg.stun_port == 3478

    @patch("app.services.nat_config._janus_nat_json")
    @patch("app.services.nat_config.get_settings")
    def test_reads_from_json_file(self, mock_settings, mock_path):
        mock_settings.return_value = MagicMock(camera_type="rgb_camera")
        mock_path.return_value.exists.return_value = True
        mock_path.return_value.read_text.return_value = json.dumps({"stun_server": "1.2.3.4", "stun_port": 9999})
        cfg = load_nat_config()
        assert cfg.stun_server == "1.2.3.4"
        assert cfg.stun_port == 9999

    @patch("app.services.nat_config.httpx.get")
    @patch("app.services.nat_config._janus_nat_json")
    @patch("app.services.nat_config.get_settings")
    def test_depth_camera_fetches_remote(self, mock_settings, mock_path, mock_get):
        mock_settings.return_value = MagicMock(camera_type="depth_camera")
        mock_path.return_value.exists.return_value = False
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"stun_server": "5.6.7.8"}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp
        cfg = load_nat_config()
        assert cfg.stun_server == "5.6.7.8"

    @patch.dict(os.environ, {"CAM_ADMIN_TOKEN": "test-admin-token-def01"})
    @patch("app.services.nat_config.httpx.get")
    @patch("app.services.nat_config._janus_nat_json")
    @patch("app.services.nat_config.get_settings")
    def test_depth_camera_sends_admin_token(self, mock_settings, mock_path, mock_get):
        """DEF-01: depth camera must send X-Admin-Token when fetching NAT config."""
        mock_settings.return_value = MagicMock(camera_type="depth_camera")
        mock_path.return_value.exists.return_value = False
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"stun_server": "1.2.3.4"}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp
        load_nat_config()
        _, kwargs = mock_get.call_args
        assert kwargs["headers"]["X-Admin-Token"] == "test-admin-token-def01"


class TestRestartDepthCameraJanus:
    """DEF-01: restart_depth_camera_janus must send X-Admin-Token."""

    @patch.dict(os.environ, {"CAM_ADMIN_TOKEN": "test-admin-token-def01"})
    @patch("app.services.nat_config.httpx.post")
    def test_sends_admin_token(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        restart_depth_camera_janus()
        _, kwargs = mock_post.call_args
        assert kwargs["headers"]["X-Admin-Token"] == "test-admin-token-def01"

    @patch.dict(os.environ, {"CAM_ADMIN_TOKEN": "test-admin-token-def01"})
    @patch("app.services.nat_config.httpx.post")
    def test_raises_on_failure(self, mock_post):
        mock_post.return_value = MagicMock(status_code=500, text="error")
        with pytest.raises(RuntimeError, match="Failed to restart janus"):
            restart_depth_camera_janus()


# (Cycle 10) TestRenderNatBlock removed — render_nat_block was a dead L4 duplicate of L3's renderer;
# the real jcfg NAT-block rendering is tested in host_infra/roles/janus/tests/test_janus_admin_cli.py.


class TestRestartJanus:
    """Verify restart_janus() shells out to janus-admin CLI and raises on non-zero.

    Refactor (boundary cleanup): nat_config.restart_janus() now invokes
    `sudo /usr/local/bin/janus-admin restart` via subprocess.run, replacing
    the earlier services.system.run_cmd path. Tests patched the old symbol
    and silently hit the real systemctl. Re-target tests to subprocess.run."""

    @patch("app.services.nat_config.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        restart_janus()
        mock_run.assert_called_once()
        # Verify it actually invokes janus-admin (boundary contract).
        called_cmd = mock_run.call_args[0][0]
        assert "/usr/local/bin/janus-admin" in called_cmd
        assert "restart" in called_cmd

    @patch("app.services.nat_config.subprocess.run")
    def test_failure(self, mock_run):
        """Non-zero exit code propagates as RuntimeError so callers can react."""
        mock_run.return_value = MagicMock(returncode=1, stderr="janus boot failed")
        with pytest.raises(RuntimeError, match="janus-admin restart"):
            restart_janus()


class TestJanusHealthzDefensive:
    """Test defensive parsing in janus_healthz."""

    @pytest.mark.asyncio
    async def test_healthz_malformed_response(self, client):
        with patch("app.routes.janus.janus.streaming_info", return_value="garbage"):
            resp = await client.get("/janus/healthz")
            assert resp.status_code == 200
            body = resp.json()
            assert body["ok"] is False

    @pytest.mark.asyncio
    async def test_healthz_valid_response(self, client):
        with patch("app.routes.janus.janus.streaming_info", return_value={
            "data": {"info": {"id": 1, "enabled": True}}
        }):
            resp = await client.get("/janus/healthz")
            assert resp.status_code == 200
            body = resp.json()
            assert body["ok"] is True

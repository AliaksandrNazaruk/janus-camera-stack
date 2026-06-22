"""Integration tests for P0-SEC-001 viewer gate.

Verifies wire-level behaviour:
- Gated route returns 401 without token when gate enabled.
- Same route returns 200 with valid X-Viewer-Token header.
- Same route returns 200 with valid ?token= query param.
- Dev mode (VIEWER_TOKENS unset) lets requests through unmodified —
  this is the contract the existing 176 regression tests rely on.

Each test builds its own app instance after patching env + reloading viewer_auth
so module-level VIEWER_TOKENS reflects test config — isolation per test, no
cross-test bleed.
"""
from __future__ import annotations

import importlib
import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient


_VIEWER_TOKEN = "integration-viewer-token-12345"
_ADMIN_TOKEN = "integration-admin-token-12345"


def _build_app_with_gate(viewer_tokens: str | None):
    """Build a fresh FastAPI app with specified VIEWER_TOKENS env.

    viewer_tokens=None → dev mode (gate disabled).
    viewer_tokens="x,y" → gate enabled with those tokens.
    """
    env_patch = {"CAM_ADMIN_TOKEN": _ADMIN_TOKEN}
    if viewer_tokens is not None:
        env_patch["VIEWER_TOKENS"] = viewer_tokens
    else:
        # Force-clear so that _load_tokens() returns []
        env_patch["VIEWER_TOKENS"] = ""

    with patch.dict(os.environ, env_patch):
        # Settings is a frozen @dataclass that resolves env via os.getenv
        # in field defaults — evaluated at class-body execution. Reloading
        # settings.py re-reads env. Required so that TURN_SHARED_SECRET / TURN_USER
        # test overrides actually reach get_client_rtc_config.
        import app.core.settings as _settings
        importlib.reload(_settings)
        import app.core.viewer_auth as va
        importlib.reload(va)
        # The router modules captured Depends(require_viewer) at import-time;
        # reloading viewer_auth swaps the underlying VIEWER_TOKENS check.
        # Same module identity is preserved → existing Depends references work.
        with patch("app.core.events.register_event_handlers", lambda app: None):
            from app.core.app import create_app
            return create_app()


@pytest.fixture
def gated_app():
    """App with gate enabled — single token configured."""
    return _build_app_with_gate(_VIEWER_TOKEN)


@pytest.fixture
def open_app():
    """App in dev mode — no gate."""
    return _build_app_with_gate(None)


async def _client(app, headers=None):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test", headers=headers or {})


# ── /client-config (critical TURN leak) ──────────────────────────────


class TestClientConfigGate:
    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, gated_app):
        async with await _client(gated_app) as ac:
            r = await ac.get("/client-config")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_with_valid_header_returns_200(self, gated_app):
        async with await _client(gated_app, {"X-Viewer-Token": _VIEWER_TOKEN}) as ac:
            r = await ac.get("/client-config")
        assert r.status_code == 200
        body = r.json()
        assert "iceServers" in body

    @pytest.mark.asyncio
    async def test_with_invalid_header_returns_401(self, gated_app):
        async with await _client(gated_app, {"X-Viewer-Token": "wrong"}) as ac:
            r = await ac.get("/client-config")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_with_valid_query_param_returns_200(self, gated_app):
        async with await _client(gated_app) as ac:
            r = await ac.get(f"/client-config?token={_VIEWER_TOKEN}")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_dev_mode_unauthenticated_returns_200(self, open_app):
        """Without VIEWER_TOKENS configured — existing UX preserved."""
        async with await _client(open_app) as ac:
            r = await ac.get("/client-config")
        assert r.status_code == 200


# ── P1-SEC-002 viewer-bound TURN credentials ─────────────────────────


def _patched_settings(monkeypatch, **overrides):
    """Build a Settings instance with overrides and patch the route's
    get_settings reference to return it. Avoids module-reload + dataclass
    bake-in pitfalls (see test fixture comment)."""
    import dataclasses
    from app.core.settings import get_settings as _real_get
    base = _real_get()
    patched = dataclasses.replace(base, **overrides)
    # Replace the symbol the route module captured at import.
    monkeypatch.setattr("app.routes.janus.get_settings", lambda: patched)
    return patched


class TestClientConfigViewerBoundTurn:
    """With configured TURN_SHARED_SECRET and a viewer token: returned TURN
    username must encode the viewer_id, and different tokens must yield
    different usernames."""

    @pytest.mark.asyncio
    async def test_per_viewer_turn_username(self, monkeypatch):
        """Two valid viewer tokens → two different TURN usernames."""
        app = _build_app_with_gate("tok-alpha,tok-beta")
        _patched_settings(
            monkeypatch,
            turn_shared_secret="test-shared-secret-1234567890ab",
            turn_host="turn.example.com",
        )

        async with await _client(app, {"X-Viewer-Token": "tok-alpha"}) as ac:
            r1 = await ac.get("/client-config")
        async with await _client(app, {"X-Viewer-Token": "tok-beta"}) as ac:
            r2 = await ac.get("/client-config")

        assert r1.status_code == 200 and r2.status_code == 200
        ice1 = r1.json()["iceServers"]
        ice2 = r2.json()["iceServers"]
        # Find the TURN entry (has username) — first non-STUN is TURN.
        turn1 = next(s for s in ice1 if s.get("username"))
        turn2 = next(s for s in ice2 if s.get("username"))
        # Usernames are coturn ephemeral format: <expiry>:<user>
        # Both should split to 2 parts; <user> parts must differ.
        user_part1 = turn1["username"].split(":", 1)[1]
        user_part2 = turn2["username"].split(":", 1)[1]
        assert user_part1 != user_part2, (
            f"Different tokens must yield different TURN usernames, "
            f"got both = {user_part1!r}"
        )
        # Format: <nat.turn_user>-<viewer_id_hex>
        assert user_part1.startswith("webrtc-")
        assert user_part2.startswith("webrtc-")

    @pytest.mark.asyncio
    async def test_same_viewer_stable_turn_username(self, monkeypatch):
        """Same token across reloads → same user portion (modulo expiry).
        Permits log correlation in coturn access logs."""
        app = _build_app_with_gate("tok-alpha")
        _patched_settings(
            monkeypatch,
            turn_shared_secret="test-shared-secret-1234567890ab",
            turn_host="turn.example.com",
        )

        async with await _client(app, {"X-Viewer-Token": "tok-alpha"}) as ac:
            r1 = await ac.get("/client-config")
            r2 = await ac.get("/client-config")

        ice1 = r1.json()["iceServers"]
        ice2 = r2.json()["iceServers"]
        turn1 = next(s for s in ice1 if s.get("username"))
        turn2 = next(s for s in ice2 if s.get("username"))
        user1 = turn1["username"].split(":", 1)[1]
        user2 = turn2["username"].split(":", 1)[1]
        assert user1 == user2  # viewer_id portion stable

    @pytest.mark.asyncio
    async def test_dev_mode_fallback_uses_default_user(self, monkeypatch):
        """No token → falls back to nat_cfg.turn_user (existing behaviour)."""
        app = _build_app_with_gate(None)
        _patched_settings(
            monkeypatch,
            turn_shared_secret="test-shared-secret-1234567890ab",
            turn_host="turn.example.com",
        )

        async with await _client(app) as ac:
            r = await ac.get("/client-config")
        assert r.status_code == 200
        ice = r.json()["iceServers"]
        turn = next(s for s in ice if s.get("username"))
        user_part = turn["username"].split(":", 1)[1]
        # No viewer-id suffix appended → equals the default user verbatim
        # (the JanusNatConfig default which is read from env TURN_USER or "webrtc").
        assert "-" not in user_part or user_part == "webrtc"


# ── /depth (P1-CV-001 endpoint, gated) ───────────────────────────────


class TestDepthGate:
    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, gated_app):
        async with await _client(gated_app) as ac:
            r = await ac.get("/depth", params={"x": 50, "y": 50})
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_with_token_proxies_to_mux(self, gated_app):
        """With valid token — request flows through to mux mock."""
        mux_resp = httpx.Response(
            200,
            json={"type": "depth", "x": 50.0, "y": 50.0, "depth": 1.5,
                  "age_ms": 10, "stale": False},
            request=httpx.Request("GET", "http://mux/depth"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mux_resp)

        async with await _client(gated_app, {"X-Viewer-Token": _VIEWER_TOKEN}) as ac:
            with patch("app.services.depth_mux_client.get_client", return_value=mock_client):
                r = await ac.get("/depth", params={"x": 50, "y": 50})
        assert r.status_code == 200
        assert r.json()["depth"] == 1.5


# ── /depth_events SSE (query-param transport path) ───────────────────


class TestDepthEventsGate:
    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, gated_app):
        async with await _client(gated_app) as ac:
            # SSE GET — gate runs before streaming so 401 returns immediately
            r = await ac.get("/depth_events", params={"session_id": "abc"})
        assert r.status_code == 401

    # Note: positive-path SSE test omitted — depth_events streams indefinitely
    # which deadlocks the ASGITransport mock. The 401-without-token test above
    # is sufficient proof the gate is wired correctly; positive path is the
    # same require_viewer dependency exercised by /client-config tests.


# ── /janus proxy gated ───────────────────────────────────────────────


class TestJanusProxyGate:
    @pytest.mark.asyncio
    async def test_unauthenticated_root_returns_401(self, gated_app):
        async with await _client(gated_app) as ac:
            r = await ac.get("/janus")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_unauthenticated_subpath_returns_401(self, gated_app):
        async with await _client(gated_app) as ac:
            r = await ac.get("/janus/some/handle")
        assert r.status_code == 401


# ── /preview/{mp_id} ─────────────────────────────────────────────────


class TestPreviewGate:
    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, gated_app):
        async with await _client(gated_app) as ac:
            r = await ac.get("/preview/1305")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_query_token_works_for_window_open(self, gated_app):
        """window.open() can't set headers — must accept ?token= for same-tab nav."""
        async with await _client(gated_app) as ac:
            r = await ac.get(f"/preview/1305?token={_VIEWER_TOKEN}")
        assert r.status_code == 200


# ── viewer_auth_bootstrap.js stays public (token discovery) ──────────


class TestBootstrapPublic:
    @pytest.mark.asyncio
    async def test_bootstrap_js_public_dev_mode(self, open_app):
        async with await _client(open_app) as ac:
            r = await ac.get("/viewer_auth_bootstrap.js")
        assert r.status_code == 200
        assert b"VIEWER_AUTH_BOOTSTRAPPED" in r.content

    @pytest.mark.asyncio
    async def test_bootstrap_js_public_with_gate(self, gated_app):
        """Bootstrap script MUST be reachable without auth — otherwise no way
        for browser to obtain token before making API calls."""
        async with await _client(gated_app) as ac:
            r = await ac.get("/viewer_auth_bootstrap.js")
        assert r.status_code == 200

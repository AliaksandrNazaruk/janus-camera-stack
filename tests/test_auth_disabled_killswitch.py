"""CAM_AUTH_DISABLED deploy-time kill switch.

Deliberate decision (owner): perimeter auth is delegated to the deployment EDGE (reverse proxy /
Cloudflare Access / firewall / VPN). When CAM_AUTH_DISABLED is truthy, all three app gates open:
require_admin / require_viewer / require_viewer_ws become pass-throughs — console + admin API +
viewer streams are reachable with no token. Default OFF: existing deployments/tests unaffected."""
from __future__ import annotations

import importlib
import os
from unittest.mock import patch

import pytest
from starlette.datastructures import Headers, QueryParams

from app.core import admin as admin_mod


class _FakeRequest:
    def __init__(self, headers=None, cookies=None):
        self.headers = Headers(headers or {})
        self.cookies = cookies or {}

        class _State:
            pass
        self.state = _State()


class _FakeWebSocket:
    def __init__(self, headers=None, query_params=None):
        self.headers = Headers(headers or {})
        self.query_params = QueryParams(query_params or [])


# ── auth_disabled() parsing ───────────────────────────────────────────

@pytest.mark.parametrize("val,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True), (" 1 ", True),
    ("0", False), ("false", False), ("", False), ("off", False), ("nope", False),
])
def test_auth_disabled_parsing(val, expected):
    with patch.dict(os.environ, {"CAM_AUTH_DISABLED": val}, clear=False):
        assert admin_mod.auth_disabled() is expected


def test_auth_disabled_unset_is_false(monkeypatch):
    monkeypatch.delenv("CAM_AUTH_DISABLED", raising=False)
    assert admin_mod.auth_disabled() is False


# ── require_admin opens with the flag (even with the default placeholder token) ──

@pytest.mark.asyncio
async def test_require_admin_open_when_disabled(monkeypatch):
    monkeypatch.setenv("CAM_AUTH_DISABLED", "1")
    monkeypatch.delenv("CAM_ADMIN_TOKEN", raising=False)   # default "change-me" → normally 503
    assert await admin_mod.require_admin(_FakeRequest()) is None   # no raise


@pytest.mark.asyncio
async def test_require_admin_still_enforces_when_unset(monkeypatch):
    monkeypatch.delenv("CAM_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("CAM_ADMIN_TOKEN", "a-strong-admin-token-1234")
    with pytest.raises(Exception):                          # 403, no token supplied
        await admin_mod.require_admin(_FakeRequest())


# ── require_viewer / require_viewer_ws open with the flag (gate otherwise ENABLED) ──

def _reload_viewer_with_tokens(value):
    with patch.dict(os.environ, {"VIEWER_TOKENS": value}, clear=False):
        import app.core.viewer_auth as va
        importlib.reload(va)
        return va


@pytest.mark.asyncio
async def test_require_viewer_open_when_disabled(monkeypatch):
    va = _reload_viewer_with_tokens("a-real-viewer-token-0001")   # gate ENABLED
    monkeypatch.setenv("CAM_AUTH_DISABLED", "1")
    await va.require_viewer(_FakeRequest())                        # no token, but no raise
    importlib.reload(va)                                          # restore clean module state


@pytest.mark.asyncio
async def test_require_viewer_ws_open_when_disabled(monkeypatch):
    va = _reload_viewer_with_tokens("a-real-viewer-token-0001")
    monkeypatch.setenv("CAM_AUTH_DISABLED", "1")
    assert await va.require_viewer_ws(_FakeWebSocket()) is True
    importlib.reload(va)


@pytest.mark.asyncio
async def test_require_viewer_ws_rejects_without_flag(monkeypatch):
    va = _reload_viewer_with_tokens("a-real-viewer-token-0001")
    monkeypatch.delenv("CAM_AUTH_DISABLED", raising=False)
    assert await va.require_viewer_ws(_FakeWebSocket()) is False   # no token, gate on
    importlib.reload(va)

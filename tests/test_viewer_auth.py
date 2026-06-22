"""Unit tests for app.core.viewer_auth — P0-SEC-001 MVP token gate.

Covers:
- _load_tokens parsing (comma-separated, whitespace, empties).
- _compare_to_any constant-time match.
- require_viewer behaviour: dev mode bypass, header path, query path,
  rejection on missing/invalid.
- require_viewer_ws variant.
"""
from __future__ import annotations

import importlib
import os
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers, QueryParams


# ── Helpers ───────────────────────────────────────────────────────────


class _FakeRequest:
    """Minimal Request stand-in — only the fields require_viewer touches."""
    def __init__(self, headers=None, query_params=None):
        self.headers = Headers(headers or {})
        self.query_params = QueryParams(query_params or [])

        class _State:
            pass
        self.state = _State()


class _FakeWebSocket:
    def __init__(self, headers=None, query_params=None):
        self.headers = Headers(headers or {})
        self.query_params = QueryParams(query_params or [])


def _reload_with_env(env_value):
    """Re-import viewer_auth so module-level VIEWER_TOKENS reflects current env.

    The module reads env at import. Tests need to vary the value, so we
    patch.dict + reload — clean isolation per test.
    """
    with patch.dict(os.environ, {"VIEWER_TOKENS": env_value} if env_value is not None else {}, clear=False):
        if env_value is None:
            os.environ.pop("VIEWER_TOKENS", None)
        import app.core.viewer_auth as va
        importlib.reload(va)
        return va


# ── _load_tokens parsing ──────────────────────────────────────────────


class TestLoadTokens:
    def test_unset_returns_empty_list(self):
        va = _reload_with_env(None)
        assert va.VIEWER_TOKENS == []
        assert va.is_gate_enabled() is False

    def test_empty_string_returns_empty_list(self):
        va = _reload_with_env("")
        assert va.VIEWER_TOKENS == []

    def test_single_token(self):
        va = _reload_with_env("abcdef0123456789")
        assert va.VIEWER_TOKENS == ["abcdef0123456789"]
        assert va.is_gate_enabled() is True

    def test_comma_separated_with_whitespace(self):
        va = _reload_with_env("tokenA, tokenB ,tokenC")
        assert va.VIEWER_TOKENS == ["tokenA", "tokenB", "tokenC"]

    def test_trailing_comma_ignored(self):
        va = _reload_with_env("tokenA,,")
        assert va.VIEWER_TOKENS == ["tokenA"]


# ── _compare_to_any ───────────────────────────────────────────────────


class TestCompareToAny:
    def test_match_first_token(self):
        va = _reload_with_env("alpha,beta,gamma")
        assert va._compare_to_any("alpha") is True

    def test_match_middle_token(self):
        va = _reload_with_env("alpha,beta,gamma")
        assert va._compare_to_any("beta") is True

    def test_no_match(self):
        va = _reload_with_env("alpha,beta")
        assert va._compare_to_any("delta") is False

    def test_empty_supplied_returns_false(self):
        va = _reload_with_env("alpha")
        assert va._compare_to_any("") is False


# ── require_viewer (HTTP) ─────────────────────────────────────────────


class TestRequireViewer:
    @pytest.mark.asyncio
    async def test_dev_mode_bypass(self):
        """No tokens configured → request passes without header (existing UX preserved)."""
        va = _reload_with_env(None)
        req = _FakeRequest()
        await va.require_viewer(req)
        # No exception = pass

    @pytest.mark.asyncio
    async def test_valid_header_passes(self):
        va = _reload_with_env("secret-token-1")
        req = _FakeRequest(headers={"X-Viewer-Token": "secret-token-1"})
        await va.require_viewer(req)
        assert getattr(req.state, "viewer_authenticated", False) is True

    @pytest.mark.asyncio
    async def test_valid_query_param_passes(self):
        """EventSource path — token in URL query."""
        va = _reload_with_env("secret-token-2")
        req = _FakeRequest(query_params=[("token", "secret-token-2")])
        await va.require_viewer(req)

    @pytest.mark.asyncio
    async def test_header_takes_precedence_over_query(self):
        va = _reload_with_env("real-token")
        req = _FakeRequest(
            headers={"X-Viewer-Token": "real-token"},
            query_params=[("token", "wrong-token")],
        )
        await va.require_viewer(req)

    @pytest.mark.asyncio
    async def test_missing_returns_401(self):
        va = _reload_with_env("real-token")
        req = _FakeRequest()
        with pytest.raises(HTTPException) as exc:
            await va.require_viewer(req)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_returns_401(self):
        va = _reload_with_env("real-token")
        req = _FakeRequest(headers={"X-Viewer-Token": "fake"})
        with pytest.raises(HTTPException) as exc:
            await va.require_viewer(req)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_multiple_tokens_any_works(self):
        """Multi-tenant setup — every configured token is accepted."""
        va = _reload_with_env("opA-token,opB-token,opC-token")
        for tok in ["opA-token", "opB-token", "opC-token"]:
            req = _FakeRequest(headers={"X-Viewer-Token": tok})
            await va.require_viewer(req)


# ── require_viewer_ws ─────────────────────────────────────────────────


class TestRequireViewerWebSocket:
    @pytest.mark.asyncio
    async def test_dev_mode_returns_true(self):
        va = _reload_with_env(None)
        ws = _FakeWebSocket()
        assert await va.require_viewer_ws(ws) is True

    @pytest.mark.asyncio
    async def test_query_token_accepted(self):
        va = _reload_with_env("ws-token")
        ws = _FakeWebSocket(query_params=[("token", "ws-token")])
        assert await va.require_viewer_ws(ws) is True

    @pytest.mark.asyncio
    async def test_header_token_accepted(self):
        """Non-browser clients can set X-Viewer-Token on WS upgrade."""
        va = _reload_with_env("ws-token")
        ws = _FakeWebSocket(headers={"X-Viewer-Token": "ws-token"})
        assert await va.require_viewer_ws(ws) is True

    @pytest.mark.asyncio
    async def test_invalid_returns_false(self):
        va = _reload_with_env("ws-token")
        ws = _FakeWebSocket(query_params=[("token", "wrong")])
        assert await va.require_viewer_ws(ws) is False

    @pytest.mark.asyncio
    async def test_missing_returns_false(self):
        va = _reload_with_env("ws-token")
        ws = _FakeWebSocket()
        assert await va.require_viewer_ws(ws) is False


# ── validate_viewer_config ────────────────────────────────────────────


class TestValidateViewerConfig:
    def test_warns_when_unset(self, caplog):
        import logging
        va = _reload_with_env(None)
        with caplog.at_level(logging.WARNING, logger="viewer_auth"):
            va.validate_viewer_config()
        assert any("DISABLED" in r.message for r in caplog.records)

    def test_warns_on_short_token(self, caplog):
        import logging
        va = _reload_with_env("short")
        with caplog.at_level(logging.WARNING, logger="viewer_auth"):
            va.validate_viewer_config()
        assert any("16 chars" in r.message for r in caplog.records)

    def test_no_warn_on_strong_token(self, caplog):
        import logging
        va = _reload_with_env("strong-token-with-enough-entropy-1234567890")
        with caplog.at_level(logging.WARNING, logger="viewer_auth"):
            va.validate_viewer_config()
        assert not any("token" in r.message.lower() for r in caplog.records)


# ── P1-SEC-002: viewer_id derivation ──────────────────────────────────


class TestViewerIdForToken:
    def test_stable_across_calls(self):
        va = _reload_with_env(None)
        a = va.viewer_id_for_token("token-A", key="secret")
        b = va.viewer_id_for_token("token-A", key="secret")
        assert a == b

    def test_different_tokens_yield_different_ids(self):
        va = _reload_with_env(None)
        a = va.viewer_id_for_token("token-A", key="secret")
        b = va.viewer_id_for_token("token-B", key="secret")
        assert a != b

    def test_different_keys_yield_different_ids(self):
        """Rotating turn_shared_secret rotates viewer_ids — that's intentional:
        coturn auth derivation uses the same secret, so a key rotation on
        coturn side invalidates old IDs anyway."""
        va = _reload_with_env(None)
        a = va.viewer_id_for_token("token-A", key="secret-1")
        b = va.viewer_id_for_token("token-A", key="secret-2")
        assert a != b

    def test_length_is_12_hex_chars(self):
        va = _reload_with_env(None)
        out = va.viewer_id_for_token("token-A", key="secret")
        assert len(out) == 12
        int(out, 16)  # all hex

    def test_does_not_leak_token(self):
        """Output must NOT contain the token substring even when token short."""
        va = _reload_with_env(None)
        out = va.viewer_id_for_token("abcd", key="secret")
        assert "abcd" not in out


# ── P1-SEC-002: extract_viewer_token ──────────────────────────────────


class TestExtractViewerToken:
    @pytest.mark.asyncio
    async def test_returns_cached_from_state(self):
        """After require_viewer cached in state — extract uses cache fast path."""
        va = _reload_with_env("real-token")
        req = _FakeRequest(headers={"X-Viewer-Token": "real-token"})
        await va.require_viewer(req)
        assert va.extract_viewer_token(req) == "real-token"

    def test_returns_header_in_dev_mode(self):
        """Dev mode — gate skipped, but extract still surfaces token if client
        passes one. Lets /client-config derive viewer_id even when gate is off."""
        va = _reload_with_env(None)
        req = _FakeRequest(headers={"X-Viewer-Token": "client-provided"})
        assert va.extract_viewer_token(req) == "client-provided"

    def test_returns_query_param(self):
        va = _reload_with_env(None)
        req = _FakeRequest(query_params=[("token", "from-query")])
        assert va.extract_viewer_token(req) == "from-query"

    def test_returns_empty_when_absent(self):
        va = _reload_with_env(None)
        req = _FakeRequest()
        assert va.extract_viewer_token(req) == ""

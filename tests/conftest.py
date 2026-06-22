"""Shared fixtures for janus_camera_page tests."""

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

# Capture the real recovery_ladder._default_ladder ONCE at session import, before
# any test can mock it. test_fdir_integration's `ladder` fixture starts a
# patch("..._default_ladder") before its yield; if setup raises before yield the
# patch never stops and the MagicMock leaks into later tests (recovery_ladder
# tests then see _default_ladder() as a mock). The autouse fixture below restores
# this known-good function before every test.
try:
    from app.services import recovery_ladder as _rl_for_capture
    _ORIG_DEFAULT_LADDER = _rl_for_capture._default_ladder
except Exception:
    _ORIG_DEFAULT_LADDER = None


@pytest.fixture(autouse=True)
def _isolate_revision_store(tmp_path, monkeypatch):
    """B2-0: point the runtime-config revision journal at a tmp dir so no test ever
    writes to the real /var/lib/camera-fdir/runtime_revisions."""
    try:
        from app.services import runtime_revision_store as _rev
        monkeypatch.setattr(_rev, "REVISION_DIR", tmp_path / "runtime_revisions")
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _isolate_recovery_ladder(tmp_path, monkeypatch):
    """Isolate the FDIR recovery-ladder per test. The ladder is a module-global
    singleton (``_ladder`` / ``_persistence_instance``) that persists to
    ``/run/camera/fdir_ladder.json`` + a reboot counter. Without resetting the
    singleton, any test that touches ``get_ladder()`` leaks ladder/reboot state
    into later tests — which is why ``test_recovery_ladder.py`` passes alone but
    fails in-suite. This runs before test-requested fixtures, so a test's own
    path fixture (e.g. ``tmp_state``) still overrides the paths afterwards; the
    critical addition here is the singleton reset."""
    try:
        from app.services import recovery_ladder as rl
        d = tmp_path / "fdir-ladder"
        if _ORIG_DEFAULT_LADDER is not None:
            monkeypatch.setattr(rl, "_default_ladder", _ORIG_DEFAULT_LADDER, raising=False)
        monkeypatch.setattr(rl, "_ladder", None, raising=False)
        monkeypatch.setattr(rl, "_persistence_instance", None, raising=False)
        monkeypatch.setattr(rl, "_LADDER_STATE_PATH", d / "fdir_ladder.json", raising=False)
        monkeypatch.setattr(rl, "_REBOOT_COUNT_DIR", d, raising=False)
        monkeypatch.setattr(rl, "_REBOOT_COUNT_PATH", d / "reboot_count", raising=False)
        monkeypatch.setattr(rl, "_REBOOT_MARKER_PATH", d / "last_reboot_request", raising=False)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Isolate the shared in-memory rate-limiter buckets per test. Without this,
    endpoint tests exhaust each other's admin burst budget (ADMIN_BURST=5) and the
    failures move around with test order (a test-infra flaw, not a product one)."""
    try:
        from app.middleware import rate_limit as _rl
        _rl._admin_buckets.clear()
        _rl._buckets.clear()
    except Exception:
        pass
    yield


@pytest.fixture
def settings():
    """Return test settings instance."""
    from app.core.settings import Settings
    return Settings()


_TEST_TOKEN = "test-token-conftest-default"


@pytest.fixture
def app():
    """Create a test-safe app instance with mocked event handlers.

    Yields WITHIN the env patch so CAM_ADMIN_TOKEN stays live at request time —
    admin_token() reads it at call time (no import-time freeze, no module-attr workaround)."""
    with patch("app.core.events.register_event_handlers", lambda app: None), \
         patch.dict(os.environ, {"CAM_ADMIN_TOKEN": _TEST_TOKEN}):
        from app.core.app import create_app
        yield create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def admin_client(app):
    """Client that sends X-Admin-Token on every request."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Admin-Token": _TEST_TOKEN},
    ) as ac:
        yield ac

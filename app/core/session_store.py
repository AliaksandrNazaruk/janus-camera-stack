"""Server-side admin session store (review P0-1 / R2).

The operator console authenticates ONCE with the admin token (X-Admin-Token
header), and the gateway hands back an opaque, short-lived **session id** that is
carried in the cam_admin cookie. The cookie therefore never contains the master
``CAM_ADMIN_TOKEN``, and the token is never persisted in the browser
(sessionStorage/localStorage) — it lives only for the duration of the login
request. require_admin / require_viewer accept a cookie whose value is a *valid,
unexpired session id* (looked up here), not the token itself.

In-memory (single-process L4): a gateway restart drops sessions → the operator
re-authenticates. Opaque ids are 256-bit url-safe random, so a dict lookup leaks
nothing useful. Expiry is enforced on every check; expired entries are pruned.
"""
from __future__ import annotations

import secrets
import threading
import time

DEFAULT_TTL_SECONDS = 12 * 3600

_SESSIONS: dict[str, float] = {}     # session_id -> expiry epoch
_LOCK = threading.Lock()


def _prune_locked(now: float) -> None:
    for sid in [s for s, exp in _SESSIONS.items() if exp <= now]:
        _SESSIONS.pop(sid, None)


def create_session(ttl: int = DEFAULT_TTL_SECONDS) -> str:
    """Mint a fresh opaque session id valid for ``ttl`` seconds."""
    sid = secrets.token_urlsafe(32)
    now = time.time()
    with _LOCK:
        _prune_locked(now)
        _SESSIONS[sid] = now + ttl
    return sid


def is_valid(sid: str) -> bool:
    """True iff ``sid`` is a known, unexpired session id."""
    if not sid:
        return False
    now = time.time()
    with _LOCK:
        exp = _SESSIONS.get(sid)
        if exp is None:
            return False
        if exp <= now:
            _SESSIONS.pop(sid, None)
            return False
        return True


def revoke(sid: str) -> None:
    """Invalidate a session id (logout)."""
    if not sid:
        return
    with _LOCK:
        _SESSIONS.pop(sid, None)


def _reset_for_tests() -> None:
    with _LOCK:
        _SESSIONS.clear()

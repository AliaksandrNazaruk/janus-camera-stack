"""Viewer authentication gate (P0-SEC-001 MVP).

Closes critical leaks identified in the P0-SEC-001 audit:
- /client-config (ephemeral TURN credentials)
- /depth, /depth/frame, /depth/color_frame, /depth/frame_color_overlay
- /depth_events (per-session SSE stream)
- /janus proxies (raw Janus core API + WebSocket)
- /preview/{mp_id} (arbitrary mountpoint enumeration)

Design mirrors require_admin (admin.py):
- Static shared secret(s) via env var (no JWT, no DB — minimum viable surgery).
- Constant-time compare via hmac.compare_digest.
- Dev mode: when VIEWER_TOKENS is unset/empty, gating is OFF — preserves
  current behaviour for local development, prevents lockout on first deploy.

Token transport:
- Browser session: `X-Viewer-Token` header (preferred — works for fetch/XHR).
- WebSocket / EventSource fallback: `?token=` query param (EventSource cannot
  set custom headers per spec).
- Same constant-time compare path for both, audited by audit_log + metrics.

VIEWER_TOKENS env format: comma-separated list. Multiple tokens enable
multi-tenant ops (one per operator) without DB. Whitespace trimmed.
"""
from __future__ import annotations

import hmac
import logging
import os
from typing import List, Optional

from fastapi import HTTPException, Request, WebSocket

logger = logging.getLogger("viewer_auth")

# Env-driven token list. Empty list / unset env → dev mode (gate disabled).
# Re-read on each Settings refresh — see _load_tokens(). Module-level cache
# kept lock-free; admin rotation requires service restart for now (Phase 2
# can hot-reload via admin endpoint).
def _load_tokens() -> List[str]:
    raw = os.getenv("VIEWER_TOKENS", "")
    return [t.strip() for t in raw.split(",") if t.strip()]


VIEWER_TOKENS: List[str] = _load_tokens()


def is_gate_enabled() -> bool:
    """True when at least one token is configured. Used by templates to decide
    whether to inject token into rendered HTML."""
    return len(VIEWER_TOKENS) > 0


def validate_viewer_config() -> None:
    """Warn on startup if viewer gate disabled (dev mode acceptable but
    risky in production). Mirrors validate_admin_config() ergonomics."""
    if not VIEWER_TOKENS:
        logger.warning(
            "VIEWER_TOKENS is unset — viewer auth gate is DISABLED. "
            "Critical endpoints (/client-config, /depth, /janus proxy, /preview) "
            "are publicly accessible. Set VIEWER_TOKENS=<comma,separated> "
            "in camera-secrets.env for production deployments."
        )
        return
    weak = [t for t in VIEWER_TOKENS if len(t) < 16]
    if weak:
        logger.warning(
            "VIEWER_TOKENS contains %d entries shorter than 16 chars — "
            "tokens must be at least 16 chars of crypto entropy.",
            len(weak),
        )


def _compare_to_any(supplied: str) -> bool:
    """Constant-time compare against each configured token. Returns True
    if match. Empty supplied → False even in dev mode (callers handle dev
    bypass before reaching this)."""
    if not supplied:
        return False
    supplied_b = supplied.encode()
    return any(hmac.compare_digest(supplied_b, t.encode()) for t in VIEWER_TOKENS)


def _record_failure(reason: str) -> None:
    """Increment metric without crashing if metrics module absent."""
    try:
        from app.metrics import viewer_auth_failures_total
        viewer_auth_failures_total.labels(reason=reason).inc()
    except Exception:  # pragma: no cover — metrics optional
        pass


async def require_viewer(request: Request) -> None:
    """Viewer gate for public-data endpoints.

    Two transport paths accepted:
    - Header `X-Viewer-Token: <token>` (preferred — fetch/XHR clients).
    - Query param `?token=<token>` (EventSource / SSE — cannot set headers).

    Dev mode (VIEWER_TOKENS unset): gate is disabled, request passes through
    with a warning emitted at startup. This preserves existing behaviour while
    operators roll out token distribution.

    On failure: 401 Unauthorized (per OWASP — 401 = missing/bad creds, 403 =
    authenticated but not authorised). Use 401 here because token-based.
    """
    # Deploy-time kill switch: perimeter auth delegated to the deployment edge.
    from app.core.admin import auth_disabled
    if auth_disabled():
        request.state.viewer_authenticated = True
        return
    # Admin supersedes viewer: an admin session — the X-Admin-Token header OR a
    # cam_admin cookie holding a valid opaque session id — grants viewer access too,
    # so the operator console's one login covers /preview navigations without a
    # separate viewer token exposed to JS (review P0-1).
    try:
        from app.core import session_store
        from app.core.admin import ADMIN_COOKIE, admin_token_ok
        sid = request.cookies.get(ADMIN_COOKIE, "")
        if admin_token_ok(request.headers.get("X-Admin-Token", "")) or \
                (sid and session_store.is_valid(sid)):
            request.state.viewer_authenticated = True
            return
    except Exception:  # pragma: no cover — never let the admin bridge break viewer auth
        pass

    if not VIEWER_TOKENS:
        return  # dev mode — gate disabled, see validate_viewer_config warning

    header_token = request.headers.get("X-Viewer-Token", "")
    query_token = request.query_params.get("token", "")
    supplied = header_token or query_token

    if not _compare_to_any(supplied):
        _record_failure("missing" if not supplied else "invalid")
        raise HTTPException(status_code=401, detail="Invalid or missing viewer token")

    request.state.viewer_authenticated = True
    # P1-SEC-002: stash token so downstream routes can derive viewer_id
    # (e.g. routes/janus.py /client-config → viewer-bound TURN username).
    request.state.viewer_token = supplied


def extract_viewer_token(request: Request) -> str:
    """Read viewer token from request (header or query). Returns empty string
    if absent — caller decides fallback behaviour.

    Useful in dev mode (when require_viewer was a no-op so request.state has
    nothing) AND in gated mode (returns the same value require_viewer already
    stashed). Idempotent — safe to call multiple times.
    """
    cached = getattr(request.state, "viewer_token", None)
    if cached:
        return cached
    return request.headers.get("X-Viewer-Token", "") or request.query_params.get("token", "")


def viewer_id_for_token(token: str, key: str = "") -> str:
    """Derive a stable short identifier from a viewer token.

    P1-SEC-002: used as `user` part of coturn ephemeral username so that
    TURN-server access logs correlate relay traffic to a specific viewer
    session. Same token → same viewer_id across reloads (predictable for
    log correlation, audit). Different tokens → different IDs.

    Properties:
    - **Stable:** deterministic on (token, key). Same input → same output.
    - **Short:** 12 hex chars = 48 bits. Collision-resistant for a fleet
      of <1000 viewers; coturn allow_loopback does not require stronger.
    - **Non-reversible:** HMAC, not plain hash. Key prevents pre-computation
      attack on the (small) viewer-token space.
    - **No leak:** id ≠ token; logs can include id without exposing creds.

    `key` defaults to "" — caller (typically routes/janus.py) passes
    `Settings.turn_shared_secret` so that the derivation reuses the secret
    coturn already knows (no new key management).
    """
    mac = hmac.new(key.encode(), token.encode(), "sha256")
    return mac.hexdigest()[:12]


async def require_viewer_ws(websocket: WebSocket) -> bool:
    """WebSocket variant of require_viewer.

    Returns True if authorized, False if rejected (caller must close with
    appropriate code). Same dev-mode bypass as HTTP variant.

    WebSocket auth handshake happens BEFORE accept(): we can only read query
    params (and the upgrade headers, which include browser-set cookies but
    NOT custom headers due to browser restrictions). EventSource has the
    same limitation, hence the unified query-param path.
    """
    from app.core.admin import auth_disabled
    if auth_disabled():
        return True  # deploy edge owns perimeter auth — see admin.auth_disabled()
    if not VIEWER_TOKENS:
        return True  # dev mode

    query_token = websocket.query_params.get("token", "")
    header_token = websocket.headers.get("X-Viewer-Token", "")
    supplied = header_token or query_token

    if not _compare_to_any(supplied):
        _record_failure("ws_invalid" if supplied else "ws_missing")
        return False
    return True

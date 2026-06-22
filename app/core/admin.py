from __future__ import annotations

import hmac
import logging
import os

from fastapi import HTTPException, Request

def admin_token() -> str:
    """Current admin token, read at CALL time (not import) so env/config load order can't
    freeze a stale value. (See docs/KNOWN_LIMITATIONS.md D4.)"""
    return os.getenv("CAM_ADMIN_TOKEN", "change-me")


def auth_disabled() -> bool:
    """Deploy-time kill switch (read at CALL time). When ``CAM_AUTH_DISABLED`` is truthy, ALL
    app gates (require_admin / require_viewer / require_viewer_ws) become pass-throughs and the
    whole surface — operator console + admin API + viewer streams — is open with no token.

    Deliberate decision: in a server deployment, auth/perimeter is delegated to the deployment
    EDGE (reverse proxy, Cloudflare Access, firewall, mTLS, VPN). The app does not implement
    perimeter auth; whoever exposes it to the internet owns that protection. Default OFF — the
    app keeps enforcing auth unless a deployer explicitly opts out, so nothing changes for
    existing deployments / tests."""
    return os.getenv("CAM_AUTH_DISABLED", "").strip().lower() in ("1", "true", "yes", "on")

# Cookie carrying the admin session after login (POST /api/v1/ui/session). Lets ONE
# login cover the whole app: the X-Admin-Token header for fetch/XHR, and this cookie
# for top-level navigations (opening /preview in a new tab) + fresh tabs. Set
# HttpOnly + Secure + SameSite=Lax so XSS can't read it and cross-site POSTs (CSRF)
# don't carry it (Lax still rides same-site fetch + top-level GET navigations).
ADMIN_COOKIE = "cam_admin"

logger = logging.getLogger("admin")


def admin_token_ok(supplied: str) -> bool:
    """Constant-time check of a supplied admin token (header or cookie value)."""
    token = admin_token()
    if not supplied or token.lower() == "change-me":
        return False
    return hmac.compare_digest(supplied.encode(), token.encode())


def validate_admin_config() -> None:
    """Warn on startup if admin token is still the default placeholder.

    Admin endpoints are independently guarded by ``require_admin``, which
    returns HTTP 503 when the token is unconfigured.  Crashing the whole
    service would also take down public pages (color_view, depth_view, etc.).
    """
    token = admin_token()
    if token.lower() == "change-me":
        logger.warning(
            "CAM_ADMIN_TOKEN is the default 'change-me'. "
            "Admin endpoints will return 503 until a strong token is set "
            "in camera-secrets.env."
        )
    elif len(token) < 16:
        logger.warning(
            "CAM_ADMIN_TOKEN is too short (%d chars). "
            "Admin endpoints may be insecure — use at least 16 characters.",
            len(token),
        )


async def require_admin(request: Request) -> None:
    """
    Admin gate for privileged endpoints.

    The client must present the admin token either as an ``X-Admin-Token`` header
    (fetch/XHR) OR the ``cam_admin`` session cookie (top-level navigations + fresh
    tabs, set by POST /api/v1/ui/session after one login). Set a strong
    ``CAM_ADMIN_TOKEN`` in ``camera-secrets.env`` for rover-grade deployments.
    """
    if auth_disabled():            # deploy edge owns perimeter auth — see auth_disabled()
        return None
    token = admin_token()
    if token.lower() == "change-me":
        raise HTTPException(
            status_code=503,
            detail="Admin endpoint disabled: CAM_ADMIN_TOKEN is still "
                   "the default placeholder. Set a strong token in "
                   "camera-secrets.env before using admin routes.",
        )

    # 1) X-Admin-Token header (fetch/XHR clients, curl). 2) cam_admin cookie whose
    # value is a VALID OPAQUE SESSION ID (never the token itself, review P0-1).
    if admin_token_ok(request.headers.get("X-Admin-Token", "")):
        request.state.admin_token = token
        return None
    from app.core import session_store
    sid = request.cookies.get(ADMIN_COOKIE, "")
    if sid and session_store.is_valid(sid):
        request.state.admin_session = sid
        return None

    try:
        from app.metrics import admin_auth_failures_total
        admin_auth_failures_total.inc()
    except Exception:
        pass
    raise HTTPException(status_code=403, detail="Invalid admin token")


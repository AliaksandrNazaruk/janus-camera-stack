"""`/api/v1/ui/*` — operator-console view-model (read-only).

The Gateway Operator Console (design_system/ ui kit) reads ONE aggregated
view-model instead of the raw admin endpoints. This router is read-only and
admin-gated; all mutations still go through the existing /api/v1/admin/* verbs
(restart/stop/maintenance/fdir/remove/provision/firewall) — the console wires its
action buttons to those, so there is one source of truth for state changes.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from app.core import session_store
from app.core.admin import ADMIN_COOKIE, require_admin
from app.services import mountpoint_allocator
from app.services import stream_binding_store as sbs
from app.services import ui_viewmodel

# Re-exported for tests to monkeypatch (mirror stream_bindings.py pattern).
BIND_STATE_PATH = sbs.DEFAULT_STATE_PATH
ALLOC_STATE_PATH = mountpoint_allocator.DEFAULT_STATE_PATH

router = APIRouter(prefix="/api/v1/ui", dependencies=[Depends(require_admin)])


@router.get("/fleet", summary="Operator-console view-model (nodes, streams, health, events)")
def fleet_view() -> dict:
    return ui_viewmodel.build_fleet(state_path=BIND_STATE_PATH, alloc_state_path=ALLOC_STATE_PATH)


_SESSION_MAX_AGE = session_store.DEFAULT_TTL_SECONDS   # operator session lifetime (re-login after)


@router.post("/session", summary="Open an admin session (opaque id cookie; one login covers nav + actions + /preview)")
def open_session(response: Response) -> dict:
    """require_admin (the router dep) validated the X-Admin-Token header. Mint an
    OPAQUE, short-lived session id (server-side store) and set it in the cam_admin
    cookie — the cookie never carries the master CAM_ADMIN_TOKEN (review P0-1).
    HttpOnly (XSS can't read it) + Secure + SameSite=Lax (cross-site POST/CSRF can't
    carry it). Subsequent fetch / top-level /preview navigations / fresh tabs are
    then authenticated by the cookie without re-sending the token."""
    sid = session_store.create_session(_SESSION_MAX_AGE)
    response.set_cookie(ADMIN_COOKIE, sid, max_age=_SESSION_MAX_AGE,
                        httponly=True, secure=True, samesite="lax", path="/")
    return {"ok": True, "max_age": _SESSION_MAX_AGE}


@router.delete("/session", summary="Log out — revoke the session id + clear the cookie")
def close_session(request: Request, response: Response) -> dict:
    session_store.revoke(request.cookies.get(ADMIN_COOKIE, ""))
    response.delete_cookie(ADMIN_COOKIE, path="/")
    return {"ok": True}

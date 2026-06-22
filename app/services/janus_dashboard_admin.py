"""Dashboard Janus admin adapter — the dashboard's OWN raw Janus HTTP client.

Deliberately SEPARATE from services/janus_admin.py: this is the dashboard's legacy
streaming-plugin client (STREAMING_ADMIN_KEY + httpx to ``{janus_url}/{sid}/{handle}``),
whereas janus_admin.py is the reconcile path's session/handle + admin_secret contract.
Keeping them apart means this de-dup (C-04 Phase 3B) cannot affect production reconcile.
Extracted verbatim from admin_dashboard — de-dup only, no retry/timeout/schema changes.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.core.settings import get_settings
from app.services import secret_store

log = logging.getLogger(__name__)


def streaming_admin_key() -> Optional[str]:
    """Pull admin_key from secret_store. Used for both create + destroy."""
    values = secret_store._load()
    return values.get("STREAMING_ADMIN_KEY") or values.get("JANUS_STREAMING_ADMIN_KEY")


def attach() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Create session + attach streaming plugin. Returns (sid, handle_id, error)."""
    settings = get_settings()
    try:
        with httpx.Client(timeout=3.0) as c:
            r = c.post(settings.janus_url, json={"janus": "create", "transaction": "cr-1"})
            if r.status_code != 200:
                return None, None, f"Janus /create rc={r.status_code}"
            sid = r.json().get("data", {}).get("id")
            if not sid:
                return None, None, "no session id"

            r = c.post(f"{settings.janus_url}/{sid}",
                       json={"janus": "attach", "plugin": "janus.plugin.streaming",
                             "transaction": "cr-2"})
            if r.status_code != 200:
                return str(sid), None, "attach failed"
            handle = r.json().get("data", {}).get("id")
            if not handle:
                return str(sid), None, "no handle id"
            return str(sid), str(handle), None
    except httpx.RequestError as exc:
        return None, None, f"Janus unreachable: {exc}"


def destroy_session(sid: str) -> None:
    """Best-effort cleanup."""
    settings = get_settings()
    try:
        with httpx.Client(timeout=2.0) as c:
            c.post(f"{settings.janus_url}/{sid}",
                   json={"janus": "destroy", "transaction": "cr-clean"})
    except Exception:
        pass


def streaming_message(sid: str, handle: str, body: Dict[str, Any], *,
                      transaction: str) -> Dict[str, Any]:
    """POST a streaming-plugin message on an attached handle; returns the raw Janus json."""
    settings = get_settings()
    with httpx.Client(timeout=5.0) as c:
        r = c.post(f"{settings.janus_url}/{sid}/{handle}",
                   json={"janus": "message", "body": body, "transaction": transaction})
        return r.json()


def list_mountpoints_raw() -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Query the streaming plugin for the mountpoint list (self-contained session).
    Returns (raw_mountpoint_dicts, error). Public read — no admin_key needed."""
    settings = get_settings()
    try:
        with httpx.Client(timeout=3.0) as c:
            r = c.post(settings.janus_url, json={"janus": "create", "transaction": "dash-1"})
            if r.status_code != 200:
                return [], f"Janus /create rc={r.status_code}"
            data = r.json()
            sid = data.get("data", {}).get("id")
            if not sid:
                return [], f"Janus /create no session ID: {data}"

            r = c.post(f"{settings.janus_url}/{sid}",
                       json={"janus": "attach", "plugin": "janus.plugin.streaming",
                             "transaction": "dash-2"})
            if r.status_code != 200:
                return [], "attach streaming plugin failed"
            data = r.json()
            handle = data.get("data", {}).get("id")
            if not handle:
                return [], f"no handle from attach: {data}"

            r = c.post(f"{settings.janus_url}/{sid}/{handle}",
                       json={"janus": "message", "body": {"request": "list"},
                             "transaction": "dash-3"})
            if r.status_code != 200:
                return [], f"list request rc={r.status_code}"
            data = r.json()
            plugindata = data.get("plugindata", {}).get("data", {})
            raw_mps = plugindata.get("list", [])

            try:
                c.post(f"{settings.janus_url}/{sid}",
                       json={"janus": "destroy", "transaction": "dash-4"})
            except Exception:
                pass
            return raw_mps, None
    except httpx.RequestError as exc:
        return [], f"Janus unreachable: {exc}"
    except Exception as exc:
        log.exception("list_mountpoints unexpected")
        return [], f"unexpected: {exc}"

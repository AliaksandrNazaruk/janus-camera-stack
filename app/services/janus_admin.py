"""Janus admin HTTP client — dynamic mountpoint CRUD without jcfg edit.

Speaks to Janus streaming plugin via session/handle attach + plugin
"create"/"destroy"/"list" messages. Uses admin_key from general jcfg block
(loaded from camera-secrets.env env var).

Endpoints (Janus default ports):
  Standard:   http://127.0.0.1:8088/janus            (sessions, attach, plugin messages)
  Admin API:  http://127.0.0.1:7088/admin            (engine introspection, NOT plugin CRUD)

Mountpoint CRUD goes through the standard endpoint via a streaming plugin
attach. The admin_key authorizes the plugin's create_permanent/destroy ops.

Concurrency: clients can be created freely; underlying HTTP calls are
synchronous + cheap (loopback).
"""
from __future__ import annotations

import logging
import os
import random
from typing import Optional

import httpx

log = logging.getLogger(__name__)


JANUS_HTTP = os.getenv("JANUS_HTTP_URL", "http://127.0.0.1:8088/janus")
JANUS_TIMEOUT = float(os.getenv("JANUS_HTTP_TIMEOUT", "8.0"))
JANUS_ADMIN_KEY = os.getenv("JANUS_STREAMING_ADMIN_KEY", "")


class JanusAdminError(RuntimeError):
    pass


def _tx() -> str:
    return f"x3-{random.randint(0, 1 << 32):08x}"


def _post(path: str, body: dict) -> dict:
    """Sync POST + parse JSON. Raises JanusAdminError on transport / non-2xx."""
    try:
        r = httpx.post(path, json=body, timeout=JANUS_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as e:
        raise JanusAdminError(f"janus HTTP error to {path}: {e}") from e


def _create_session() -> int:
    resp = _post(JANUS_HTTP, {"janus": "create", "transaction": _tx()})
    if resp.get("janus") != "success":
        raise JanusAdminError(f"session create failed: {resp}")
    return int(resp["data"]["id"])


def _attach_streaming(session_id: int) -> int:
    resp = _post(f"{JANUS_HTTP}/{session_id}",
                 {"janus": "attach", "plugin": "janus.plugin.streaming",
                  "transaction": _tx()})
    if resp.get("janus") != "success":
        raise JanusAdminError(f"plugin attach failed: {resp}")
    return int(resp["data"]["id"])


def _destroy_session(session_id: int) -> None:
    try:
        _post(f"{JANUS_HTTP}/{session_id}", {"janus": "destroy", "transaction": _tx()})
    except JanusAdminError:
        pass


def _plugin_message(session_id: int, handle_id: int, body: dict) -> dict:
    resp = _post(f"{JANUS_HTTP}/{session_id}/{handle_id}",
                 {"janus": "message", "transaction": _tx(), "body": body})
    if resp.get("janus") != "success":
        raise JanusAdminError(f"plugin message failed: {resp}")
    pdata = resp.get("plugindata", {}).get("data", {})
    if pdata.get("error_code"):
        raise JanusAdminError(
            f"streaming plugin error {pdata['error_code']}: {pdata.get('error')}"
        )
    return pdata


def _with_handle(fn):
    """Decorator: setup session+attach, call fn(handle), cleanup."""
    def wrapped(*args, **kwargs):
        sid = _create_session()
        try:
            hid = _attach_streaming(sid)
            return fn(sid, hid, *args, **kwargs)
        finally:
            _destroy_session(sid)
    wrapped.__name__ = fn.__name__
    return wrapped


@_with_handle
def list_mountpoints(session_id: int, handle_id: int) -> list:
    pdata = _plugin_message(session_id, handle_id, {"request": "list"})
    return pdata.get("list", [])


@_with_handle
def create_mountpoint(session_id: int, handle_id: int,
                      *,
                      mp_id: int,
                      rtp_port: int,
                      description: str,
                      mp_secret: str,
                      codec: str = "h264",
                      payload_type: int = 96,
                      fmtp: str = "profile-level-id=42e01f;packetization-mode=1;level-asymmetry-allowed=1",
                      rtcp_port: Optional[int] = None,
                      iface: str = "127.0.0.1") -> dict:
    """Create rtp mountpoint via streaming plugin. admin_key must be set.

    Returns plugin data dict on success. Raises JanusAdminError on failure
    (including "already exists" — caller can choose to catch + ignore).
    """
    if not JANUS_ADMIN_KEY:
        raise JanusAdminError("JANUS_STREAMING_ADMIN_KEY env not set")
    body = {
        "request": "create",
        "admin_key": JANUS_ADMIN_KEY,
        "type": "rtp",
        "id": int(mp_id),
        "description": description,
        "secret": mp_secret,
        # Audio/video flags
        "audio": False,
        "video": True,
        "media": [
            {
                "type": "video",
                "mid": "v",
                "label": "video",
                "port": int(rtp_port),
                "rtcpport": int(rtcp_port or rtp_port + 1),
                "pt": int(payload_type),
                "codec": codec,
                "fmtp": fmtp,
                "iface": iface,
            }
        ],
        "permanent": False,
    }
    return _plugin_message(session_id, handle_id, body)


@_with_handle
def destroy_mountpoint(session_id: int, handle_id: int,
                       *, mp_id: int, mp_secret: str) -> dict:
    """Destroy mountpoint. mp_secret must match the one used at create."""
    body = {
        "request": "destroy",
        "id": int(mp_id),
        "secret": mp_secret,
        "permanent": False,
    }
    return _plugin_message(session_id, handle_id, body)

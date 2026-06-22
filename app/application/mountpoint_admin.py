"""Use-cases for the dashboard mountpoint routes (list / create / destroy / info).

Orchestration over the dashboard Janus adapter (janus_dashboard_admin) + app.services.janus
(for info()). Extracted from admin_dashboard (C-04 Phase 3B); response shapes, HTTP status
codes, and audit strings preserved verbatim. The reconcile path (services/janus_admin.py)
is NOT touched.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.services import janus_dashboard_admin as jda
from app.services.audit_log import audit


# ── domain errors (the route maps these to HTTP; this layer stays FastAPI-free) ──
class StreamingAdminKeyMissing(Exception):
    """STREAMING_ADMIN_KEY not set. Route maps to 500 (message carried verbatim)."""


class JanusAttachFailed(Exception):
    """Janus admin attach failed. Route maps to 502 (message carried verbatim)."""


class InvalidMountpointId(Exception):
    """mp_id out of range. Route maps to 400 (message carried verbatim)."""


class JanusUnreachable(Exception):
    """Janus admin info() was unreachable. Route maps to 502 (message carried verbatim)."""


class JanusBadStructure(Exception):
    """Janus returned an unexpected structure. Route maps to 502 (message carried verbatim)."""


class MountpointInfo(BaseModel):
    id: int
    description: Optional[str] = None
    type: str
    enabled: bool
    is_private: bool
    video: bool = False
    audio: bool = False
    media: List[Dict[str, Any]] = []


class CreateMountpointRequest(BaseModel):
    id: int = Field(..., ge=1000, le=65535, description="Unique mountpoint ID (1000-65535)")
    description: str = Field("", max_length=200)
    rtp_port: int = Field(..., ge=1024, le=65535, description="UDP port that ffmpeg pushes RTP to")
    codec: str = Field("h264", pattern=r"^(h264|vp8|vp9|av1)$")
    payload_type: int = Field(96, ge=96, le=127)
    is_private: bool = Field(False)
    secret: Optional[str] = Field(None, max_length=128, description="Optional per-mountpoint secret")
    iface: Optional[str] = Field(
        None, max_length=45,
        description="RTP listen interface. Defaults to 127.0.0.1 (loopback). "
                    "Set to the gateway LAN IP only for remote producer sources.")


class CreateMountpointResponse(BaseModel):
    id: int
    rtp_port: int
    created: bool
    error: Optional[str] = None


def list_mountpoint_infos() -> Tuple[List[MountpointInfo], Optional[str]]:
    """The mountpoint list as MountpointInfo objects + error (was
    admin_dashboard._list_mountpoints_via_janus; also used by the dashboard snapshot)."""
    raw_mps, err = jda.list_mountpoints_raw()
    mps = [
        MountpointInfo(
            id=int(m.get("id", 0)),
            description=m.get("description"),
            type=str(m.get("type", "rtp")),
            enabled=bool(m.get("enabled", True)),
            is_private=bool(m.get("is_private", False)),
            video=bool(m.get("video", False)),
            audio=bool(m.get("audio", False)),
            media=m.get("media", []),
        )
        for m in raw_mps
    ]
    return mps, err


def list_mountpoints() -> Dict[str, Any]:
    mps, err = list_mountpoint_infos()
    return {"mountpoints": [m.model_dump() for m in mps], "error": err}


def create_mountpoint(req: CreateMountpointRequest) -> CreateMountpointResponse:
    admin_key = jda.streaming_admin_key()
    if not admin_key:
        raise StreamingAdminKeyMissing(
            "STREAMING_ADMIN_KEY not set — render Janus configs via /admin/config/apply first")

    sid, handle, err = jda.attach()
    if err:
        raise JanusAttachFailed(err)
    try:
        body: Dict[str, Any] = {
            "request": "create",
            "admin_key": admin_key,
            "type": "rtp",
            "id": req.id,
            "description": req.description or f"dynamic-{req.id}",
            "is_private": req.is_private,
            "media": [
                {
                    "type": "video",
                    "mid": "v",
                    "label": "video",
                    "port": req.rtp_port,
                    "pt": req.payload_type,
                    "codec": req.codec,
                    "iface": req.iface or "127.0.0.1",
                }
            ],
        }
        if req.codec == "h264":
            body["media"][0]["fmtp"] = "profile-level-id=42e01f;packetization-mode=1;level-asymmetry-allowed=1"
        if req.secret:
            body["secret"] = req.secret

        data = jda.streaming_message(sid, handle, body, transaction="create-mp")
        pd = data.get("plugindata", {}).get("data", {})
        if pd.get("streaming") == "created":
            audit("admin_dashboard.mountpoint.create", {"id": req.id, "port": req.rtp_port, "codec": req.codec})
            return CreateMountpointResponse(id=req.id, rtp_port=req.rtp_port, created=True)
        err_msg = pd.get("error", "unknown") + (f" (code={pd.get('error_code')})" if pd.get("error_code") else "")
        audit("admin_dashboard.mountpoint.create_failed", {"id": req.id, "error": err_msg[:200]})
        return CreateMountpointResponse(id=req.id, rtp_port=req.rtp_port, created=False, error=err_msg)
    finally:
        if sid:
            jda.destroy_session(sid)


def destroy_mountpoint(mp_id: int) -> Dict[str, Any]:
    admin_key = jda.streaming_admin_key()
    if not admin_key:
        raise StreamingAdminKeyMissing("STREAMING_ADMIN_KEY not set")

    sid, handle, err = jda.attach()
    if err:
        raise JanusAttachFailed(err)
    try:
        data = jda.streaming_message(
            sid, handle, {"request": "destroy", "admin_key": admin_key, "id": mp_id},
            transaction="destroy-mp")
        pd = data.get("plugindata", {}).get("data", {})
        if pd.get("streaming") == "destroyed":
            audit("admin_dashboard.mountpoint.destroy", {"id": mp_id})
            return {"id": mp_id, "destroyed": True}
        err_msg = pd.get("error", "unknown")
        audit("admin_dashboard.mountpoint.destroy_failed", {"id": mp_id, "error": err_msg[:200]})
        return {"id": mp_id, "destroyed": False, "error": err_msg}
    finally:
        if sid:
            jda.destroy_session(sid)


def mountpoint_info(mp_id: int) -> Dict[str, Any]:
    if mp_id < 1 or mp_id > 65535:
        raise InvalidMountpointId("mp_id must be 1-65535")
    from app.services import janus as _janus
    try:
        raw = _janus.streaming_info(mp_id)
    except Exception as exc:
        raise JanusUnreachable(f"janus unreachable: {exc}") from exc
    if not isinstance(raw, dict):
        raise JanusBadStructure("janus returned unexpected structure")
    data = raw.get("data") or {}
    info = data.get("info") or data
    return {"mp_id": mp_id, "raw": info, "summary": _janus.janus_summary(mp_id)}

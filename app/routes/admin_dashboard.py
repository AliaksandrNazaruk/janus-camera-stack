"""Admin operator dashboard — unified observability view.

Distinct from admin_config (which mutates state) — this router is read-only,
collects system health + service states + active mountpoints + recent
audit entries. Powers operator_dashboard.html.

All routes require_admin. No rate limit on dashboard reads (UI polls
every 5 sec — should not throttle).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.core.admin import require_admin
from app.core.settings import get_settings
from app.middleware.rate_limit import require_admin_rate_limit
from app.services import audit_log as audit_log_service
from app.services import jcfg_renderer, systemd  # noqa: F401

from app.services.audit_log import audit  # noqa: F401
from app.application import services_admin
# Models moved to the use-case module (C-04 split); imported here for response_model=.
from app.application.services_admin import ServiceState, RestartResponse
from app.services import encoder_env
from app.application import encoder_admin as enc_uc
from app.application import provision_stream as provision_uc
from app.application.encoder_admin import EncoderActionResponse, EncoderInstanceStatus
from app.services.encoder_env import EncoderEnvSpec
from app.application import device_inventory
from app.application.device_inventory import V4l2Device, RealsenseProbeResponse
from app.application import mountpoint_admin
from app.application.mountpoint_admin import MountpointInfo, CreateMountpointRequest, CreateMountpointResponse  # noqa: F401
from app.application import audit_view
from app.application import dashboard as dashboard_uc
from app.application.dashboard import DashboardSnapshot
from app.services import soak_files

log = logging.getLogger("admin_dashboard")

_CAM_TYPE = get_settings().camera_type
# Sprint X4 URL cleanup: cross-cutting admin endpoints do NOT depend on camera_type
# (services/mountpoints/audit are system-wide). Moved out of /{cam_type}/ prefix.
# Legacy callers: there were none — this router added in commit 0eb51e0.
router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin-dashboard"],
    dependencies=[Depends(require_admin)],
)

# Known stack services. install.sh creates standard names; legacy/prod alias also included.
# Audit log path delegated to services/audit_log.py (env-configured there).
AUDIT_LOG_FILE = audit_log_service.AUDIT_LOG_FILE


# ── Models ────────────────────────────────────────────────────────────

# MountpointInfo moved to app/application/mountpoint_admin.py (C-04 Phase 3B).


# AuditEntry moved to app/application/audit_view.py; DashboardSnapshot to
# app/application/dashboard.py (C-04 Phase 4). Imported above for response_model=.


# ── Helpers ───────────────────────────────────────────────────────────

# _systemctl_show / _service_state moved to app/application/services_admin.py (C-04 split).


# _janus_admin_url (dead) removed; _list_mountpoints_via_janus moved to
# app/services/janus_dashboard_admin.py + app/application/mountpoint_admin.py (C-04 Phase 3B).


# _read_audit_tail moved to app/application/audit_view.read_audit_tail;
# _primary_ip moved to app/services/netinfo.primary_ip (C-04 Phase 4).


# ── Endpoints ─────────────────────────────────────────────────────────

@router.get("/services", response_model=List[ServiceState], summary="systemd state of known stack services")
def list_services() -> List[ServiceState]:
    return services_admin.service_states()


# ── Service restart ─────────────────────────────────────────────────
# Mapping: encoder family services → encoder-admin CLI (boundary pattern,
# sudoers-scoped). Janus → sudo systemctl (explicit sudoers entry).
# Self (camera-page) NOT restartable from UI — operator must ssh (would
# kill the response handler before completing).

# _RESTART_DISPATCH / _SELF_SERVICE / parse_family_instance / RestartResponse moved
# to app/application/services_admin.py (C-04 split).


@router.post(
    "/services/{service}/restart",
    response_model=RestartResponse,
    summary="Restart a service (audit logged, rate limited)",
    dependencies=[Depends(require_admin_rate_limit)],
)
def restart_service(service: str) -> RestartResponse:
    try:
        return services_admin.restart_service(service)
    except (services_admin.RestartSelfRefused, services_admin.ServiceNotRestartable) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (services_admin.RestartMethodUnknown, services_admin.RestartExecFailed) as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mountpoints", summary="List Janus streaming mountpoints")
def list_mountpoints() -> Dict[str, Any]:
    return mountpoint_admin.list_mountpoints()


# ── Mountpoint CRUD ─────────────────────────────────────────────────
# Wraps Janus streaming plugin create/destroy. Requires STREAMING_ADMIN_KEY
# (admin_key in janus.plugin.streaming.jcfg). Operator authentication +
# rate limit applied via route dependencies.


# CreateMountpointRequest / CreateMountpointResponse moved to
# app/application/mountpoint_admin.py (C-04 Phase 3B).


# _streaming_admin_key / _streaming_attach / _streaming_destroy_session moved to
# app/services/janus_dashboard_admin.py (C-04 Phase 3B).


@router.post(
    "/mountpoints",
    response_model=CreateMountpointResponse,
    summary="Create a dynamic streaming mountpoint",
    dependencies=[Depends(require_admin_rate_limit)],
)
def create_mountpoint(req: CreateMountpointRequest) -> CreateMountpointResponse:
    try:
        return mountpoint_admin.create_mountpoint(req)
    except mountpoint_admin.StreamingAdminKeyMissing as e:
        raise HTTPException(status_code=500, detail=str(e))
    except mountpoint_admin.JanusAttachFailed as e:
        raise HTTPException(status_code=502, detail=str(e))


# _create_mountpoint_impl moved to app/application/mountpoint_admin.create_mountpoint (C-04 Phase 3B).


# ── Encoder management ──────────────────────────────────────────────
# Boundary to encoder-admin CLI (sudoers-scoped). Operator can
# start/stop arbitrary instances of allowed families. Used for both
# standalone encoder mgmt AND post-create chaining ("create mountpoint
# + start encoder in one call").

# Encoder constants (ENCODER_FAMILIES / INSTANCED_FAMILIES / INSTANCE_RE) moved to
# app/services/encoder_admin.py (C-04 split).


# EncoderActionResponse / _validate_encoder_target / _encoder_admin moved to
# app/services/encoder_admin.py + app/application/encoder_admin.py (C-04 split).


# ── Hardware discovery — V4L2 devices ───────────────────────────────


# V4l2Device moved to app/application/device_inventory.py (C-04 Phase 3A).


# _parse_v4l2_list_devices / _probe_v4l2_device_formats moved to app/services/v4l2.py (C-04 Phase 3A).


# RealsenseProfileModel / RealsenseDeviceModel / RealsenseProbeResponse moved to
# app/application/device_inventory.py (C-04 Phase 3A).


@router.get(
    "/devices/realsense",
    response_model=RealsenseProbeResponse,
    summary="Enumerate connected Intel RealSense devices (D435/D455/...)",
)
def list_realsense_devices(include_profiles: bool = True) -> RealsenseProbeResponse:
    return device_inventory.list_realsense_devices(include_profiles)


@router.get(
    "/devices/v4l2",
    response_model=List[V4l2Device],
    summary="Enumerate /dev/video* V4L2 devices with friendly labels + formats",
)
def list_v4l2_devices(probe_formats: bool = False) -> List[V4l2Device]:
    return device_inventory.list_v4l2_devices(probe_formats)


@router.post(
    "/encoders/{family}/start",
    response_model=EncoderActionResponse,
    summary="Start encoder (non-instanced family)",
    dependencies=[Depends(require_admin_rate_limit)],
)
def start_encoder_non_instanced(family: str) -> EncoderActionResponse:
    try:
        return enc_uc.start_encoder(family, None)
    except (enc_uc.UnknownEncoderFamily, enc_uc.BadEncoderInstance) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except enc_uc.EncoderExecFailed as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/encoders/{family}/{instance}/start",
    response_model=EncoderActionResponse,
    summary="Start a specific encoder instance",
    dependencies=[Depends(require_admin_rate_limit)],
)
def start_encoder(family: str, instance: str) -> EncoderActionResponse:
    try:
        return enc_uc.start_encoder(family, instance)
    except (enc_uc.UnknownEncoderFamily, enc_uc.BadEncoderInstance) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except enc_uc.EncoderExecFailed as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/encoders/{family}/{instance}/stop",
    response_model=EncoderActionResponse,
    summary="Stop a specific encoder instance",
    dependencies=[Depends(require_admin_rate_limit)],
)
def stop_encoder(family: str, instance: str) -> EncoderActionResponse:
    try:
        return enc_uc.stop_encoder(family, instance)
    except (enc_uc.UnknownEncoderFamily, enc_uc.BadEncoderInstance) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except enc_uc.EncoderExecFailed as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Per-encoder live status ─────────────────────────────────────────


# EncoderInstanceStatus moved to app/application/encoder_admin.py (C-04 split).


# _discover_encoder_units / _read_env_file / _encoder_instance_status moved to
# app/services/encoder_admin.py + app/services/encoder_env.py + app/application/encoder_admin.py.


@router.get(
    "/encoders/status",
    response_model=List[EncoderInstanceStatus],
    summary="Auto-discovered encoder instances + live status + config",
)
def list_encoder_instances() -> List[EncoderInstanceStatus]:
    return enc_uc.list_instances()


# ENV_DIR + EncoderEnvSpec moved to app/services/encoder_env.py (C-04 split).


# _write_env_files moved to app/services/encoder_env.py (C-04 split).


# ── Combined: create mountpoint + write env + start encoder ─────────


class ProvisionStreamRequest(BaseModel):
    """End-to-end: 'plug USB camera, get live stream' in 1 call."""
    mountpoint: CreateMountpointRequest
    encoder_family: str = Field(..., pattern=r"^(rtp-v4l2|rtp-rtsp|rs-stream)$")
    encoder_instance: str = Field(..., min_length=1, max_length=32)
    encoder_env: EncoderEnvSpec


class ProvisionStreamResponse(BaseModel):
    mountpoint: CreateMountpointResponse
    env_files: List[str] = []
    encoder: Optional[EncoderActionResponse] = None
    error: Optional[str] = None


@router.post(
    "/streams/provision",
    response_model=ProvisionStreamResponse,
    summary="Provision full stream: create mountpoint + write env + start encoder",
    dependencies=[Depends(require_admin_rate_limit)],
)
def provision_stream(req: ProvisionStreamRequest) -> ProvisionStreamResponse:
    try:
        result = provision_uc.provision_stream(
            mountpoint_req=req.mountpoint, encoder_family=req.encoder_family,
            encoder_instance=req.encoder_instance, encoder_env_spec=req.encoder_env,
            rtp_port=req.mountpoint.rtp_port, mp_id=req.mountpoint.id,
            create_mountpoint=mountpoint_admin.create_mountpoint)
    except encoder_env.InvalidEncoderInstanceName as e:     # write_env_files (D3.3C)
        raise HTTPException(status_code=400, detail=str(e))
    except mountpoint_admin.StreamingAdminKeyMissing as e:  # injected create_mountpoint (D3.3C)
        raise HTTPException(status_code=500, detail=str(e))
    except mountpoint_admin.JanusAttachFailed as e:
        raise HTTPException(status_code=502, detail=str(e))
    except enc_uc.EncoderExecFailed as e:                   # shared encoder_action (D3.1)
        raise HTTPException(status_code=500, detail=str(e))
    return ProvisionStreamResponse(**result)


@router.delete(
    "/mountpoints/{mp_id}",
    summary="Destroy a streaming mountpoint",
    dependencies=[Depends(require_admin_rate_limit)],
)
def destroy_mountpoint(mp_id: int) -> Dict[str, Any]:
    try:
        return mountpoint_admin.destroy_mountpoint(mp_id)
    except mountpoint_admin.StreamingAdminKeyMissing as e:
        raise HTTPException(status_code=500, detail=str(e))
    except mountpoint_admin.JanusAttachFailed as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/audit-log", summary="Recent audit log entries (newest first, optional filters)")
def get_audit_log(
    limit: int = Query(50, ge=1, le=500),
    action: Optional[str] = Query(None, max_length=128, description="Substring match on action field"),
    target: Optional[str] = Query(None, max_length=128, description="Substring match on target field"),
    outcome: Optional[str] = Query(None, max_length=32, description="Exact match: success|failure|denied|..."),
    since: Optional[str] = Query(None, max_length=32, description="ISO 8601 timestamp lower bound"),
) -> Dict[str, Any]:
    entries, truncated = audit_view.read_audit_tail(
        limit=limit, action_substr=action, target_substr=target,
        outcome=outcome, since_ts=since,
    )
    return {
        "entries": [e.model_dump() for e in entries],
        "truncated": truncated,
        "filters_applied": {"action": action, "target": target, "outcome": outcome, "since": since},
        "audit_log_file": str(AUDIT_LOG_FILE),
    }


# Sprint X4 note: per-stream toggle endpoints live in the generative
# /api/v1/cameras/{serial}/{sensor}/{initialize,stop} (device_camera.py)
# — those already persist desired_active via sensor_lifecycle. Cross-
# cutting list view of all streams: GET /api/v1/cameras/streams
# (devices.py). Don't duplicate here.


# ── Janus admin: per-mountpoint info ───────────────────────────────────
# Inspect modal shows raw Janus streaming.plugin info() for one mp_id —
# media list with codec/pt/fmtp/age_ms, viewers count, listeners, etc.

@router.get("/mountpoints/{mp_id}/info", summary="Janus admin info() for one mountpoint")
def mountpoint_info(mp_id: int) -> Dict[str, Any]:
    try:
        return mountpoint_admin.mountpoint_info(mp_id)
    except mountpoint_admin.InvalidMountpointId as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (mountpoint_admin.JanusUnreachable, mountpoint_admin.JanusBadStructure) as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Soak CSVs ─────────────────────────────────────────────────────────
# _SOAK_DIR + list/read (incl. basename whitelist + 1MB cap) moved to
# app/services/soak_files.py (C-04 Phase 4).


@router.get("/soak/files", summary="List soak metric CSV files")
def list_soak_files() -> Dict[str, Any]:
    return soak_files.list_files()


@router.get("/soak/file/{name}", summary="Read soak CSV file (basename, no path traversal)")
def get_soak_file(name: str) -> Response:
    try:
        return Response(content=soak_files.read_file_bytes(name), media_type="text/csv")
    except soak_files.InvalidSoakFilename as e:
        raise HTTPException(status_code=400, detail=str(e))
    except soak_files.SoakFileNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/dashboard", response_model=DashboardSnapshot, summary="Aggregate snapshot for operator dashboard")
def dashboard_snapshot(audit_limit: int = Query(20, ge=1, le=200)) -> DashboardSnapshot:
    return dashboard_uc.snapshot(audit_limit)

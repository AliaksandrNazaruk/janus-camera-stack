"""Use-case: aggregate the operator dashboard snapshot.
Extracted from admin_dashboard (C-04 Phase 4); behavior verbatim. The DashboardSnapshot
model lives here. Pure orchestration over the other use-cases + adapters.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel

from app.application import audit_view, mountpoint_admin, services_admin
from app.application.audit_view import AuditEntry
from app.application.mountpoint_admin import MountpointInfo
from app.application.services_admin import ServiceState
from app.services import jcfg_renderer, netinfo


class DashboardSnapshot(BaseModel):
    services: List[ServiceState]
    mountpoints: List[MountpointInfo]
    mountpoints_error: Optional[str] = None
    audit: List[AuditEntry]
    audit_truncated: bool
    janus_cfg_dir: Optional[str] = None
    primary_ip: Optional[str] = None


def snapshot(audit_limit: int = 20) -> DashboardSnapshot:
    services = services_admin.service_states()
    mps, mp_err = mountpoint_admin.list_mountpoint_infos()
    audit, truncated = audit_view.read_audit_tail(audit_limit)
    paths = jcfg_renderer.detect_janus_paths()
    return DashboardSnapshot(
        services=services,
        mountpoints=mps,
        mountpoints_error=mp_err,
        audit=audit,
        audit_truncated=truncated,
        janus_cfg_dir=str(paths.cfg_dir) if paths else None,
        primary_ip=netinfo.primary_ip(),
    )

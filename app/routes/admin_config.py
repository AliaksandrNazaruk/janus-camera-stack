"""Admin config routes — UI-driven safe secret rotation + jcfg re-render + restart.

All routes are admin-protected (require_admin) + rate-limited + audit-logged.
Service restart is destructive: rotating a secret invalidates all live sessions
using it. UI must confirm before calling /apply.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.admin import require_admin
from app.core.settings import get_settings
from app.middleware.rate_limit import require_admin_rate_limit
from app.services import jcfg_renderer, public_ip, secret_store
from app.application import config_apply, config_view
# Models moved to the use-case modules (route-purity Phase 5); imported for response_model=.
from app.application.config_apply import ApplyResponse
from app.application.config_view import ConfigSnapshot

from app.services.audit_log import audit  # Phase 2

log = logging.getLogger("admin_config")

_CAM_TYPE = get_settings().camera_type
# Sprint X4 URL cleanup: cross-cutting admin endpoints — system-wide.
router = APIRouter(
    prefix="/api/v1/admin/config",
    tags=["admin-config"],
    dependencies=[Depends(require_admin), Depends(require_admin_rate_limit)],
)


# ── Models ────────────────────────────────────────────────────────────

# SecretSnapshot + ConfigSnapshot moved to app/application/config_view.py (route-purity Phase 5).


class RotateResponse(BaseModel):
    key: str
    new_value: str
    rotated_at_ts: int
    must_apply: bool = True


class SetFieldRequest(BaseModel):
    key: str = Field(..., pattern=r"^[A-Z][A-Z0-9_]*$")
    value: str = Field(..., max_length=512)


# ApplyResponse moved to app/application/config_apply.py (route-purity Phase 5).


class DetectIpResponse(BaseModel):
    ip: Optional[str]
    method: str
    error: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────

# _humanize_age moved to application/config_view; _systemctl + _service_active moved to
# services/systemd.{systemctl_action,is_active} (bare, no sudo) — route-purity Phase 5.


# ── Endpoints ─────────────────────────────────────────────────────────

@router.get("", response_model=ConfigSnapshot, summary="Current config snapshot (secrets masked)")
def get_snapshot() -> ConfigSnapshot:
    return config_view.snapshot()


class RevealResponse(BaseModel):
    key: str
    value: str
    last_rotated_ts: Optional[int] = None


@router.post("/reveal/{key}", response_model=RevealResponse, summary="Reveal current secret (requires explicit confirm phrase)")
def reveal_secret(key: str, confirm: str = Body(..., embed=True, max_length=128)) -> RevealResponse:
    """Show plaintext secret value to admin.
    Requires confirm phrase exactly matching "reveal-<KEY>". This prevents
    accidental reveals via curl typos or replayed CSRF requests, and leaves
    an unambiguous trail in the audit log.

    Caller MUST already have admin token (route-level dependency). The
    confirm phrase adds intent verification on top of authentication.
    """
    if key not in secret_store.SENSITIVE_KEYS:
        raise HTTPException(status_code=400, detail=f"Key {key!r} is not in the sensitive set")
    expected = f"reveal-{key}"
    if confirm != expected:
        audit("admin_config.reveal.bad_confirm", {"key": key, "got_prefix": confirm[:16]})
        raise HTTPException(status_code=400, detail=f"confirm must be exactly {expected!r}")
    value = secret_store.reveal(key)
    if value is None:
        raise HTTPException(status_code=404, detail=f"Key {key!r} not set")
    audit("admin_config.reveal", {"key": key})
    log.warning("REVEAL key=%s (audit logged)", key)
    snap = secret_store.snapshot().get(key)
    return RevealResponse(
        key=key,
        value=value,
        last_rotated_ts=snap.last_rotated_ts if snap else None,
    )


@router.post("/rotate/{key}", response_model=RotateResponse, summary="Rotate a secret")
def rotate_secret(key: str) -> RotateResponse:
    if key not in secret_store.SENSITIVE_KEYS:
        raise HTTPException(status_code=400, detail=f"Key {key!r} is not rotatable (not in the sensitive set)")
    try:
        new_value = secret_store.rotate(key)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write secrets file: {exc}") from exc
    ts = int(time.time())
    audit("admin_config.rotate", {"key": key, "ts": ts})
    log.info("rotated secret %s (new value will need Apply to take effect)", key)
    return RotateResponse(key=key, new_value=new_value, rotated_at_ts=ts, must_apply=True)


@router.post("/set", summary="Set a non-secret config field")
def set_field(req: SetFieldRequest) -> Dict[str, Any]:
    try:
        secret_store.set_field(req.key, req.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to persist: {exc}") from exc
    audit("admin_config.set", {"key": req.key, "value": req.value[:64]})
    return {"ok": True, "key": req.key, "must_apply": True}


@router.post("/detect-public-ip", response_model=DetectIpResponse, summary="Probe public IP (STUN + HTTP fallback)")
def detect_public_ip() -> DetectIpResponse:
    result = public_ip.detect()
    audit("admin_config.detect_ip", {"method": result.method, "ip": result.ip or ""})
    return DetectIpResponse(ip=result.ip, method=result.method, error=result.error)


@router.post("/set-nat-mapping", summary="Set nat_1_1_mapping (persists immediately via re-render)")
def set_nat_mapping(ip: str = Body(..., embed=True, max_length=64)) -> Dict[str, Any]:
    # Basic sanity — IPv4 only here. (Janus also supports hostnames but not for nat_1_1.)
    parts = ip.split(".")
    if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        raise HTTPException(status_code=400, detail="Invalid IPv4 address")
    try:
        result = jcfg_renderer.render(nat_mapping=ip)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    audit("admin_config.set_nat", {"ip": ip, "rendered": len(result.rendered)})
    return {"ok": True, "rendered_files": [str(p) for p in result.rendered], "nat_1_1_mapping": ip}


@router.post("/apply", response_model=ApplyResponse, summary="Re-render jcfg + restart Janus + relay")
def apply_config(restart_janus: bool = Body(True, embed=True), restart_relay: bool = Body(True, embed=True)) -> ApplyResponse:
    try:
        return config_apply.apply(restart_janus, restart_relay)
    except config_apply.ConfigRenderFailed as e:
        raise HTTPException(status_code=500, detail=e.errors)   # detail stays a list[str]

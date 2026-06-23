"""runtime-config admin endpoints.

Prefix ``/api/v1/admin/runtime-config`` — deliberately distinct from the existing
``/api/v1/admin/config`` router so the two never collide. Admin-gated + rate-
limited + audit-logged.

Surface: ``GET /effective`` (read-only), ``POST /validate`` (dry-run), ``GET /capabilities``
(what apply is supported + why blocked), ``GET /revisions/{id}`` (journaled validated revision),
and ``POST /apply`` — **LIVE for the NEW_SESSIONS_ONLY class only** (AE-1: ice_policy /
turn_credential_ttl_seconds; writes rs-runtime.env + refreshes settings + verifies + rolls back; no
encoder/Janus/mountpoint/FDIR/reboot). Other impact classes are refused by apply.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.config.runtime_schema import ApplyResponse, ValidationResponse
from app.core.admin import require_admin
from app.middleware.rate_limit import require_admin_rate_limit
from app.services import runtime_revision_store as revstore
from app.services.runtime_config_apply import Outcome, apply_revision
from app.services.runtime_config_builder import EffectiveRuntimeConfig, build_effective
from app.services.runtime_config_validator import validate as validate_patch

# AE-1: orchestration Outcome → HTTP status.
_APPLY_HTTP = {
    Outcome.APPLIED: 200,
    Outcome.NOT_FOUND: 404,
    Outcome.CONFIRM_MISMATCH: 400,
    Outcome.REJECTED: 422,
    Outcome.DRIFT: 409,
    Outcome.CONFLICT: 409,
    Outcome.LOCK_HELD: 423,
    Outcome.WRITE_FAILED: 500,
    Outcome.ROLLED_BACK: 500,
    Outcome.ROLLBACK_FAILED: 500,
}

from app.services.audit_log import audit  # noqa: E402

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/runtime-config",
    tags=["runtime-config"],
    dependencies=[Depends(require_admin), Depends(require_admin_rate_limit)],
)


@router.get(
    "/effective",
    response_model=EffectiveRuntimeConfig,
    summary="Read-only effective runtime config (admin)",
    description=(
        "Assembles the effective runtime config from live state (settings, "
        "allocations, per-sensor running probe, color tuning). Read-only; no "
        "secrets (TURN password / shared secret excluded). NOTE: deriving "
        "runtime_active performs one `encoder-admin status` probe per sensor."
    ),
)
def get_effective() -> EffectiveRuntimeConfig:
    cfg = build_effective()
    audit("runtime_config.effective", {"sensors": list(cfg.stream_profiles.keys())})
    return cfg


@router.post(
    "/validate",
    response_model=ValidationResponse,
    summary="Dry-run validate a runtime-config patch (admin)",
    description=(
        "DRY-RUN ONLY — validates a partial runtime-config patch and returns a "
        "diff, per-change ApplyImpact classification, errors, and warnings. Writes "
        "and restarts NOTHING. To apply a validated NEW_SESSIONS_ONLY revision, use POST /apply."
    ),
)
def validate_config(patch: Dict[str, Any] = Body(default_factory=dict)) -> ValidationResponse:
    result = validate_patch(patch)
    # B2-0 (journal-only): persist a redacted "validated" revision and echo its id +
    # stable diff hash. Best-effort — journaling must NEVER break /validate, and it
    # writes ONLY the revision journal (no live-config mutation, no apply).
    if result.valid and result.diff:
        try:
            effective_before = build_effective().model_dump(mode="json")
            rev_id, diff_hash = revstore.persist_validated(patch, result, effective_before)
            result.revision_id, result.diff_hash = rev_id, diff_hash
        except Exception as e:  # pragma: no cover — defensive
            log.warning("runtime-config: revision journal failed (non-fatal): %s", e)
    audit("runtime_config.validate",
          {"valid": result.valid, "changes": len(result.diff), "errors": len(result.errors),
           "revision_id": result.revision_id})
    return result


@router.get(
    "/capabilities",
    summary="Apply capability report (admin) — what apply is supported + why blocked",
    description=(
        "READ-ONLY. Reports whether runtime apply is supported (LIVE for NEW_SESSIONS_ONLY once its "
        "field-level blockers clear) and, per ApplyImpact class, the grounded blockers. No mutation."
    ),
)
def get_capabilities() -> dict:
    report = revstore.capability_report()
    audit("runtime_config.capabilities", {"apply_supported": report.get("apply_supported")})
    return report


@router.get(
    "/revisions/{revision_id}",
    summary="Read a journaled validated revision (admin, redacted)",
    description=(
        "READ-ONLY. Returns a previously journaled 'validated' revision (intent + "
        "effective base + diff + stable hash), secret-redacted. 404 if unknown. "
        "Apply a validated NEW_SESSIONS_ONLY revision via POST /apply (rollback is internal to apply)."
    ),
)
def get_revision(revision_id: str) -> dict:
    record = revstore.get_revision(revision_id)
    if record is None:
        raise HTTPException(status_code=404, detail="revision not found")
    audit("runtime_config.revision_read", {"revision_id": revision_id})
    return record


@router.post(
    "/apply",
    summary="Apply a validated NEW_SESSIONS_ONLY revision (admin)",
    description=(
        "Applies a previously journaled, valid, NEW_SESSIONS_ONLY revision (ice_policy / "
        "turn_credential_ttl_seconds) by id, gated on confirm == 'apply-<diff_hash>'. Writes "
        "rs-runtime.env + the process env + a settings-cache refresh, verifies, and rolls "
        "back on failure. Refuses any other impact/field. No encoder/Janus/mountpoint/FDIR/reboot."
    ),
)
def apply_config(body: Dict[str, Any] = Body(default_factory=dict)):
    revision_id = (body or {}).get("revision_id")
    confirm = (body or {}).get("confirm", "")
    if not revision_id or not isinstance(revision_id, str):
        raise HTTPException(status_code=422, detail="revision_id (str) is required")
    result = apply_revision(revision_id, confirm)
    audit("runtime_config.apply",
          {"revision_id": result.revision_id, "outcome": result.outcome,
           "changed": result.changed, "verified": result.verified})
    resp = ApplyResponse(status=result.outcome, revision_id=result.revision_id,
                         changed=result.changed, verified=result.verified,
                         detail=result.detail, applied=result.applied or [])
    return JSONResponse(status_code=_APPLY_HTTP.get(result.outcome, 500),
                        content=resp.model_dump())

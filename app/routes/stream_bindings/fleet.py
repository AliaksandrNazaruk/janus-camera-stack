"""Fleet (config-as-code) + reconcile + firewall endpoints (Cycle 5 split): firewall reconcile,
fleet plan/reconcile, read-only drift, and the explicit run-once Janus reconcile. Thin adapters over
app.application.stream_bindings + binding_provision; shared anchors read back through ``_core.``."""
from __future__ import annotations

import app.routes.stream_bindings as _core
from fastapi import APIRouter, Depends, HTTPException

from app.core.admin import require_admin
from app.middleware.rate_limit import require_admin_rate_limit
from app.services import binding_provision, janus_admin
from app.services.audit_log import audit
from app.application.stream_bindings import (
    FirewallReconcileCommand,
    FleetPlanCommand,
    FleetReconcileCommand,
    JanusUnreachable,
    ManifestInvalid,
    ReconcileDriftCommand,
    firewall_reconcile as firewall_reconcile_uc,
    fleet_plan as fleet_plan_uc,
    fleet_reconcile as fleet_reconcile_uc,
    reconcile_drift as reconcile_drift_uc,
)

router = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(require_admin)])
_RL = Depends(require_admin_rate_limit)


@router.post("/firewall/reconcile", dependencies=[_RL],
             summary="Reconcile the per-node RTP firewall from the binding store (dry-run unless apply)")
def firewall_reconcile(apply: bool = False) -> dict:
    return firewall_reconcile_uc(FirewallReconcileCommand(
        apply=apply, bind_state_path=_core.BIND_STATE_PATH, alloc_state_path=_core.ALLOC_STATE_PATH))


# ── declarative fleet (config-as-code) ─────────────────────────────────

@router.get("/fleet/plan",
            summary="Drift of the actual fleet vs the declarative manifest (read-only, no creds)")
def fleet_plan() -> dict:
    try:
        return fleet_plan_uc(FleetPlanCommand(bind_state_path=_core.BIND_STATE_PATH,
                                              alloc_state_path=_core.ALLOC_STATE_PATH))
    except ManifestInvalid as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/fleet/reconcile", dependencies=[_RL],
             summary="Register manifest nodes missing from the store (creds-free, additive); "
                     "provision/activate stay operator-driven — see the returned plan")
def fleet_reconcile() -> dict:
    try:
        return fleet_reconcile_uc(FleetReconcileCommand(
            bind_state_path=_core.BIND_STATE_PATH, alloc_state_path=_core.ALLOC_STATE_PATH))
    except ManifestInvalid as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/reconcile/drift",
            summary="Read-only desired/actual drift: stored remote bindings vs live Janus "
                    "mountpoints + RTP. No mutations (no ensure-janus / firewall / systemctl).")
def reconcile_drift() -> dict:
    try:
        return reconcile_drift_uc(
            ReconcileDriftCommand(bind_state_path=_core.BIND_STATE_PATH, alloc_state_path=_core.ALLOC_STATE_PATH),
            rtp_age_fn=_core._rtp_age)
    except JanusUnreachable as e:
        raise HTTPException(status_code=503, detail=f"janus_unreachable: {e.reason}")


@router.post("/reconcile/janus/run-once", dependencies=[_RL],
             summary="Explicit RUN-ONCE Janus reconcile: create MISSING mountpoints for ACTIVE "
                     "remote bindings only. Skips stopped/maintenance; never restarts / applies "
                     "firewall / destroys / provisions. Idempotent; returns before/after drift.")
def reconcile_janus_run_once() -> dict:
    from app.services.sensor_lifecycle import MP_DEFAULT_SECRET
    # Corrupt desired store raises StoreCorruptionError BEFORE any Janus read -> the app
    # maps it to 503 topology_store_corrupt (R5). A Janus outage -> explicit 503 (no false
    # success). Both happen before any mutation.
    try:
        report = binding_provision.run_janus_reconcile_once(
            mp_secret=MP_DEFAULT_SECRET, state_path=_core.BIND_STATE_PATH,
            alloc_state_path=_core.ALLOC_STATE_PATH, rtp_age_fn=_core._rtp_age)
    except janus_admin.JanusAdminError as e:
        raise HTTPException(status_code=503, detail=f"janus_unreachable: {str(e)[:120]}")
    audit("stream_bindings.reconcile.janus_run_once",
          {"result": report["result"], "before_drift": report["before"]["drift"],
           "after_drift": report["after"]["drift"]})
    return report

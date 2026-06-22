"""Cycle 7B — the POST /janus/nat update as an explicit, observable operation.

FastAPI-free use-case (the route is a thin adapter). Stages, in order:
  persist desired  →  patch local jcfg (no restart)  →  restart local janus  →  restart depth (best-effort)

Closes the boundary gaps from docs/design/JANUS_NAT_OPERATION_BOUNDARY.md:
  * G7 — NO double restart: patch runs with ``no_restart`` (jcfg only); ONE explicit ``restart_janus``.
  * G3 — uniform error mapping: ``nat_config.JanusAdminError`` covers timeout/missing on BOTH patch and
    restart, so neither escapes the operation unmapped.
  * G4/G6 — structured ``NatUpdateResult``: ``failure_stage`` + applied-flags + the L3 ``exit_code`` the
    route used to collapse.
  * G2 — depth restart is BEST EFFORT: local success wins; a depth failure is a warning, not a 500
    (local is already applied + restarted, there is no rollback).
  * (G1 persist-before-apply drift → addressed by the staged store status in 7B.2.)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from app.application.operations import OperationStatus, status_from_ok
from app.services import nat_config
from app.services.nat_config import JanusNatConfig

log = logging.getLogger(__name__)


def _set_status(status: str, diff_hash: str, failure_stage: Optional[str] = None) -> None:
    """Update the apply-status sidecar BEST EFFORT — a status-write failure must never break the
    operation (it is an observability aid, not part of the apply itself)."""
    try:
        nat_config.write_apply_status(status, diff_hash=diff_hash, failure_stage=failure_stage)
    except Exception:  # noqa: BLE001 — best-effort status; the operation outcome is authoritative
        log.warning("nat update: failed to record apply-status=%s (non-fatal)", status, exc_info=True)


@dataclass
class NatUpdateResult:
    """Outcome of a NAT/TURN update operation. ``failure_stage`` is None on success; otherwise one of
    ``persist`` / ``patch_local`` / ``restart_local`` (depth failure is a non-fatal warning, not a
    failure_stage). The applied-flags let the operator see exactly how far the operation got."""
    ok: bool
    config: JanusNatConfig                 # effective (keep-password-resolved) config — route masks it
    failure_stage: Optional[str] = None
    desired_persisted: bool = False
    local_applied: bool = False            # jcfg patched
    local_restarted: bool = False
    depth_restarted: bool = False
    detail: str = ""
    exit_code: Optional[int] = None        # L3 janus-admin exit code where the binary ran (G6)
    warnings: List[str] = field(default_factory=list)

    @property
    def operation_status(self) -> OperationStatus:
        """The canonical admin-operation status (Cycle 8B) — a uniform read-model word shared with the
        node-op journal / runtime-apply / service-restart mechanisms."""
        return status_from_ok(self.ok)


def update_nat_config(requested: JanusNatConfig) -> NatUpdateResult:
    """Run the NAT/TURN update operation and return a structured result (never raises for a stage
    failure — the route maps the result to HTTP)."""
    # keep-password: GET masks turn_pwd as "***"; a client editing other fields submits it back
    # unchanged. Treat ""/"***" as "keep the stored secret" so an edit never clobbers it with the mask.
    cfg = requested
    if cfg.turn_pwd in ("", "***"):
        cfg = cfg.model_copy(update={"turn_pwd": nat_config.load_nat_config().turn_pwd})
    diff_hash = nat_config.config_diff_hash(cfg)

    # 1. persist desired
    try:
        nat_config.save_nat_config(cfg)
    except OSError as exc:
        _set_status("failed", diff_hash, "persist")
        return NatUpdateResult(ok=False, config=cfg, failure_stage="persist",
                               desired_persisted=False, detail=f"persist failed: {exc}")
    # desired is now on disk but NOT yet confirmed live → record 'pending' so a crash mid-apply (or a
    # later failure) leaves desired≠applied VISIBLE instead of silently claiming the new config is live.
    _set_status("pending", diff_hash)

    # 2. patch local jcfg — NO restart (G7: the single restart is stage 3)
    try:
        nat_config.patch_janus_cfg_with_nat(cfg, no_restart=True)
    except nat_config.JanusAdminError as exc:
        _set_status("failed", diff_hash, "patch_local")
        return NatUpdateResult(ok=False, config=cfg, failure_stage="patch_local",
                               desired_persisted=True, local_applied=False,
                               detail=str(exc), exit_code=exc.exit_code)

    # 3. restart local janus (the ONE explicit restart)
    try:
        nat_config.restart_janus()
    except nat_config.JanusAdminError as exc:
        _set_status("failed", diff_hash, "restart_local")
        return NatUpdateResult(ok=False, config=cfg, failure_stage="restart_local",
                               desired_persisted=True, local_applied=True, local_restarted=False,
                               detail=str(exc), exit_code=exc.exit_code)

    # 4. restart depth node — BEST EFFORT (G2: local already applied + restarted; a depth failure is a
    #    warning, not a hard failure, and there is no rollback to a previous config).
    depth_ok = True
    warnings: List[str] = []
    try:
        nat_config.restart_depth_camera_janus()
    except RuntimeError as exc:
        depth_ok = False
        warnings.append(f"depth-node Janus restart failed (best-effort; local already applied): {exc}")

    # local is applied + restarted → the persisted desired config IS now live (G1: desired==applied).
    _set_status("applied", diff_hash)
    return NatUpdateResult(ok=True, config=cfg, failure_stage=None,
                           desired_persisted=True, local_applied=True, local_restarted=True,
                           depth_restarted=depth_ok, detail="ok", warnings=warnings)

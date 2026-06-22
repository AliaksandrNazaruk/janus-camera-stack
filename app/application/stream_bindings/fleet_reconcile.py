"""Use-case: register manifest nodes missing from the store (creds-free, additive). Verbatim from
routes/stream_bindings.fleet_reconcile (Phase 12.3C). A manifest that fails to load surfaces as
ManifestInvalid (route -> 422); provision/activate stay operator-driven. Returns
{registered, **plan_dict}. fleet is called, never changed."""
from __future__ import annotations

from app.services.audit_log import audit

from app.application.stream_bindings.commands import FleetReconcileCommand
from app.application.stream_bindings.fleet_plan import plan_dict
from app.application.stream_bindings.results import ManifestInvalid


def fleet_reconcile(cmd: FleetReconcileCommand) -> dict:
    from app.services import fleet
    try:
        manifest = fleet.load_manifest()
    except fleet.ManifestError as e:
        raise ManifestInvalid(str(e))
    registered = fleet.reconcile_gateway(manifest, state_path=cmd.bind_state_path)
    audit("stream_bindings.fleet.reconcile", {"registered": registered})
    return {"registered": registered,
            **plan_dict(manifest, bind_state_path=cmd.bind_state_path,
                        alloc_state_path=cmd.alloc_state_path)}

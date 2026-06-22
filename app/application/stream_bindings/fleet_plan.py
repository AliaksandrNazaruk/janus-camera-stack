"""Use-case: drift of the actual fleet vs the declarative manifest (read-only, creds-free).
Verbatim from routes/stream_bindings.fleet_plan + its _fleet_plan_dict helper (Phase 12.3A).
A manifest that fails to load/validate surfaces as ManifestInvalid (route -> 422). plan_dict is
shared with the fleet_reconcile path (still inline in the route until 12.3C). fleet is called,
never changed."""
from __future__ import annotations

from app.application.stream_bindings.commands import FleetPlanCommand
from app.application.stream_bindings.results import ManifestInvalid


def plan_dict(manifest, *, bind_state_path, alloc_state_path) -> dict:
    from dataclasses import asdict
    from app.services import fleet
    p = fleet.plan(manifest, state_path=bind_state_path, alloc_state_path=alloc_state_path)
    return {"in_sync": p.in_sync, "extra_nodes": p.extra_nodes,
            "nodes": [asdict(n) for n in p.nodes]}


def fleet_plan(cmd: FleetPlanCommand) -> dict:
    from app.services import fleet
    try:
        manifest = fleet.load_manifest()
    except fleet.ManifestError as e:
        raise ManifestInvalid(str(e))
    return plan_dict(manifest, bind_state_path=cmd.bind_state_path,
                     alloc_state_path=cmd.alloc_state_path)

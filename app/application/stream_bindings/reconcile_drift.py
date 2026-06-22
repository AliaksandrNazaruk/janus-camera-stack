"""Use-case: read-only desired/actual drift — stored remote bindings vs live Janus mountpoints
+ RTP freshness. Verbatim from routes/stream_bindings.reconcile_drift (Phase 12.3A). No mutations.
A Janus outage surfaces as JanusUnreachable (route -> 503 degraded, not a false "all missing");
a corrupt desired store lets StoreCorruptionError propagate (app handler -> 503
topology_store_corrupt, never a fabricated empty report) [R5]. The RTP freshness probe is INJECTED
(rtp_age_fn) — route-owned, best-effort. sbs / janus_admin / reconcile_drift are called, never changed."""
from __future__ import annotations

from typing import Callable, Optional

from app.services import janus_admin
from app.services import stream_binding_store as sbs
from app.services.audit_log import audit

from app.application.stream_bindings.commands import ReconcileDriftCommand
from app.application.stream_bindings.results import JanusUnreachable


def reconcile_drift(cmd: ReconcileDriftCommand, *,
                    rtp_age_fn: Callable[[int], Optional[int]]) -> dict:
    from app.services import reconcile_drift as _drift
    # Desired: stored bindings. A corrupt store raises StoreCorruptionError -> the app
    # maps it to 503 {topology_store_corrupt} (never a fabricated empty report). [R5]
    bindings = sbs.list_bindings(state_path=cmd.bind_state_path, alloc_state_path=cmd.alloc_state_path)
    # Actual: live Janus mountpoints. A Janus outage -> 503 degraded, not a false "all missing".
    try:
        live = [int(m["id"]) for m in janus_admin.list_mountpoints()
                if isinstance(m, dict) and "id" in m]
    except Exception as e:
        raise JanusUnreachable(str(e)[:120])
    nodes = sbs.list_nodes(state_path=cmd.bind_state_path)
    maint = frozenset(nid for nid, n in nodes.items() if getattr(n, "maintenance", False))
    report = _drift.compute_drift(bindings, live, rtp_age_fn=rtp_age_fn, maintenance_node_ids=maint)
    audit("stream_bindings.reconcile.drift",
          {"drift": report["drift"], "counts": report["counts"]})
    return report

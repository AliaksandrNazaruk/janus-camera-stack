"""Use-case: pause/resume FDIR for a node while servicing its hardware. Verbatim from
routes/stream_bindings.set_node_maintenance (Phase 12.3B). The local node uses the cam10 watchdog
ladder, not node maintenance. Returns the updated NodeEntry; the route maps it to NodeOut.
sbs is called, never changed."""
from __future__ import annotations

from app.services import stream_binding_store as sbs
from app.services.audit_log import audit

from app.application.stream_bindings.commands import SetMaintenanceCommand
from app.application.stream_bindings.results import MaintenanceLocalRejected, NodeNotFound


def set_maintenance(cmd: SetMaintenanceCommand):
    node = sbs.get_node(cmd.node_id, state_path=cmd.bind_state_path)
    if node is None:
        raise NodeNotFound(cmd.node_id)
    if node.node_id == sbs.LOCAL_NODE_ID:
        raise MaintenanceLocalRejected()
    try:
        n = sbs.set_maintenance(cmd.node_id, cmd.enabled, state_path=cmd.bind_state_path)
    except KeyError:
        raise NodeNotFound(cmd.node_id)
    audit("stream_bindings.node.maintenance", {"node_id": cmd.node_id, "enabled": cmd.enabled})
    return n

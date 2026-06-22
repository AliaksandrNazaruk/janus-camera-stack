"""Use-case: probe a node's agent reachability + record the result. Verbatim from
routes/stream_bindings.check_node (Phase 12.3B). The local node is trivially reachable; a remote
node is probed via node_client.probe_agent and the reachability is best-effort recorded
(touch_checked, swallowing the KeyError race). Returns the plain status dict the route serves.
node_client / sbs are called, never changed."""
from __future__ import annotations

from app.services import node_client
from app.services import stream_binding_store as sbs
from app.services.audit_log import audit

from app.application.stream_bindings.commands import CheckNodeCommand
from app.application.stream_bindings.results import NodeNotFound


def check_node(cmd: CheckNodeCommand) -> dict:
    node = sbs.get_node(cmd.node_id, state_path=cmd.bind_state_path)
    if node is None:
        raise NodeNotFound(cmd.node_id)
    if node.node_id == sbs.LOCAL_NODE_ID:
        return {"node_id": node.node_id, "reachable": True, "reason": "local", "next_step": None}
    result = node_client.probe_agent(node.host)
    try:
        sbs.touch_checked(cmd.node_id, result["reachability"], state_path=cmd.bind_state_path)
    except KeyError:
        pass
    audit("stream_bindings.node.check", {"node_id": cmd.node_id, "reachable": result["reachable"]})
    return {"node_id": node.node_id, "reachable": result["reachable"],
            "reason": result["reason"], "next_step": result["next_step"]}

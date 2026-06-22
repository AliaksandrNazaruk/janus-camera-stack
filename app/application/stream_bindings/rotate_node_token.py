"""Use-case: rotate the node-agent token (push a new token, restart only the agent), async.

Sync orchestration extracted verbatim from routes/stream_bindings.rotate_node_token (Phase 3 / A-02):
resolve the node (unknown / local as domain errors), build the PINNED transport and spawn the durable
op via the route's injected forwarders, audit, and return the op handle the route shapes into the H1
response. No deploy bundle is needed (token-only). The async rotate work stays in services/
node_provisioner. sbs is read, never changed; ``build_transport``/``spawn_op`` are the route's
monkeypatchable boundary forwarders, so their HTTP mapping (412 / 409 / 503) stays at the edge.
"""
from __future__ import annotations

from app.services import node_provisioner
from app.services import stream_binding_store as sbs
from app.services.audit_log import audit

from app.application.stream_bindings.commands import RotateTokenCommand
from app.application.stream_bindings.results import (
    NodeNotFound,
    NodeOpStarted,
    RotateTokenLocalRejected,
)


def rotate_node_token(cmd: RotateTokenCommand, *, build_transport, spawn_op) -> NodeOpStarted:
    node = sbs.get_node(cmd.node_id, state_path=cmd.bind_state_path)
    if node is None:
        raise NodeNotFound(cmd.node_id)
    if node.node_id == sbs.LOCAL_NODE_ID:
        raise RotateTokenLocalRejected()
    transport = build_transport(node, cmd.sudo_password)
    op_id = spawn_op(cmd.node_id, "rotate-token", node_provisioner.rotate_token, cmd.node_id, transport,
                     state_path=cmd.bind_state_path)
    audit("stream_bindings.node.rotate_token", {"node_id": cmd.node_id, "host": node.host})
    return NodeOpStarted(host=node.host, operation_id=op_id)

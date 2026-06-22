"""Use-case: deploy the node pipe over SSH (probe -> mux), async.

Sync orchestration extracted verbatim from routes/stream_bindings.provision_node (Phase 3 / A-02):
resolve the node (unknown / local / bundle-missing as domain errors), build the PINNED transport and
spawn the durable op via the route's injected forwarders, audit, and return the op handle the route
shapes into the H1 response. The async deploy work itself stays in services/node_provisioner. sbs is
read, never changed; ``build_transport`` (host-key policy + 412) and ``spawn_op`` (durable runner +
409/503) are the route's monkeypatchable boundary forwarders, so their HTTP mapping stays at the edge.
"""
from __future__ import annotations

from app.services import node_provisioner
from app.services.audit_log import audit

from app.application.stream_bindings.commands import ProvisionNodeCommand
from app.application.stream_bindings.resolve_provision_target import resolve_provision_target
from app.application.stream_bindings.results import NodeOpStarted


def provision_node(cmd: ProvisionNodeCommand, *, build_transport, spawn_op) -> NodeOpStarted:
    node = resolve_provision_target(cmd.node_id, bind_state_path=cmd.bind_state_path,
                                    bundle_tar=cmd.bundle_tar)
    transport = build_transport(node, cmd.sudo_password, allow_tofu=cmd.allow_tofu)
    op_id = spawn_op(cmd.node_id, "provision", node_provisioner.provision, cmd.node_id, transport,
                     bundle_tar=cmd.bundle_tar, state_path=cmd.bind_state_path)
    audit("stream_bindings.node.provision", {"node_id": cmd.node_id, "host": node.host})
    return NodeOpStarted(host=node.host, operation_id=op_id)

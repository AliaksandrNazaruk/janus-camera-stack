"""Use-case: add a remote node by IP (the gateway mints an opaque node_id). Verbatim from
routes/stream_bindings.add_node (Phase 12.3D). Rejects the local gateway's own addresses (its
camera is the built-in cam10 host). gateway_lan_ip is INJECTED (route-owned, monkeypatchable).
Returns the NodeEntry; the route maps it to NodeOut. sbs is called, never changed."""
from __future__ import annotations

from app.services import stream_binding_store as sbs
from app.services.audit_log import audit

from app.application.stream_bindings.commands import AddNodeCommand
from app.application.stream_bindings.results import AddNodeIsLocalGateway, NodeRegistrationInvalid


def add_node(cmd: AddNodeCommand):
    # The local gateway's own camera is the built-in cam10 host (already listed),
    # not a remote node. Reject its addresses with a friendly hint instead of
    # minting a bogus remote node that SSH-loops back to ourselves (review L1).
    if cmd.host.strip() in (cmd.gateway_lan_ip, "127.0.0.1", "localhost", "::1", "0.0.0.0"):
        raise AddNodeIsLocalGateway(cmd.host, sbs.LOCAL_NODE_ID)
    try:
        n = sbs.add_node_by_host(cmd.host, display_name=cmd.display_name, state_path=cmd.bind_state_path)
    except sbs.BindingValidationError as e:
        raise NodeRegistrationInvalid(str(e))
    audit("stream_bindings.node.add_by_host", {"host": cmd.host, "node_id": n.node_id})
    return n

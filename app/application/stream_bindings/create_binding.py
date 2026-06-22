"""Use-case: create a remote stream binding. Verbatim from routes/stream_bindings.create_stream_binding
(Phase 12.3C). Allocates a mountpoint + RTP port (unless caller-specified), builds the
REMOTE_PRODUCER binding, and persists it. Local node ids are projections (rejected here). Returns the
StreamBinding; the route maps it to BindingOut. sbs / mountpoint_allocator are called, never changed."""
from __future__ import annotations

from app.services import mountpoint_allocator
from app.services import stream_binding_store as sbs
from app.services.audit_log import audit

from app.application.stream_bindings.commands import CreateBindingCommand
from app.application.stream_bindings.results import (
    AllocationConflict,
    BindingInvalid,
    BindingNodeNotFound,
    LocalBindingNotCreatable,
)


def create_binding(cmd: CreateBindingCommand):
    if cmd.node_id == sbs.LOCAL_NODE_ID:
        raise LocalBindingNotCreatable()
    node = sbs.get_node(cmd.node_id, state_path=cmd.bind_state_path)
    if node is None:
        raise BindingNodeNotFound(cmd.node_id)
    try:
        mp = cmd.mountpoint_id or sbs.allocate_mountpoint(
            cmd.node_id, state_path=cmd.bind_state_path, alloc_state_path=cmd.alloc_state_path)
        port = cmd.rtp_port or sbs.allocate_port(
            cmd.node_id, state_path=cmd.bind_state_path, alloc_state_path=cmd.alloc_state_path)
    except mountpoint_allocator.AllocationError as e:
        raise AllocationConflict(str(e))
    binding = sbs.StreamBinding(
        binding_id=sbs.remote_binding_id(node, cmd.sensor), node_id=cmd.node_id, sensor=cmd.sensor,
        mode=sbs.StreamMode.REMOTE_PRODUCER,
        transport=sbs.StreamTransport(rtp_port=port, payload_type=cmd.payload_type, codec=cmd.codec),
        janus=sbs.StreamJanusConfig(mountpoint_id=mp, rtp_iface=cmd.rtp_iface))
    try:
        sbs.upsert_binding(binding, state_path=cmd.bind_state_path, alloc_state_path=cmd.alloc_state_path)
    except sbs.BindingValidationError as e:
        raise BindingInvalid(str(e))
    audit("stream_bindings.binding.create",
          {"binding_id": binding.binding_id, "mp": mp, "port": port, "iface": cmd.rtp_iface})
    return binding

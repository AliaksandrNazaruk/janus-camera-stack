"""Use-case: register/update a remote node. Verbatim from routes/stream_bindings.register_node
(Phase 12.3D). Returns the NodeEntry; the route maps it to NodeOut. sbs is called, never changed."""
from __future__ import annotations

from app.services import stream_binding_store as sbs
from app.services.audit_log import audit

from app.application.stream_bindings.commands import RegisterNodeCommand
from app.application.stream_bindings.results import NodeRegistrationInvalid


def register_node(cmd: RegisterNodeCommand):
    try:
        n = sbs.upsert_node(cmd.node_id, host=cmd.host, role=cmd.role, state_path=cmd.bind_state_path)
    except sbs.BindingValidationError as e:
        raise NodeRegistrationInvalid(str(e))
    audit("stream_bindings.node.register",
          {"node_id": cmd.node_id, "host": cmd.host, "role": cmd.role})
    return n

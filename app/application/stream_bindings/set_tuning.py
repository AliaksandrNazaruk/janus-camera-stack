"""Use-case: set a REMOTE stream's encoder tuning (+ the node-agent restarts its encoder).
Verbatim from routes/stream_bindings.set_binding_tuning (Phase 10.5). Validates rotation +
non-empty here; returns the raw node-agent dict."""
from __future__ import annotations

from app.services import node_client
from app.services import stream_binding_store as sbs
from app.services.audit_log import audit

from app.application.stream_bindings.commands import SetTuningCommand
from app.application.stream_bindings.results import (
    BindingNotFound,
    InvalidRotation,
    LocalTuningRejected,
    NoTuningFields,
    NodeAgentError,
)


def set_tuning(cmd: SetTuningCommand) -> dict:
    b = sbs.get_binding(cmd.binding_id, state_path=cmd.bind_state_path,
                        alloc_state_path=cmd.alloc_state_path)
    if b is None:
        raise BindingNotFound(cmd.binding_id)
    if b.mode != sbs.StreamMode.REMOTE_PRODUCER:
        raise LocalTuningRejected()
    if "rotation" in cmd.tuning and cmd.tuning["rotation"] not in (0, 90, 180, 270):
        raise InvalidRotation()
    if not cmd.tuning:
        raise NoTuningFields()
    client = node_client.get_node_client(b.node_id, state_path=cmd.bind_state_path)
    try:
        res = client.set_tuning(b.sensor, cmd.tuning)
    except Exception as e:  # noqa: BLE001
        raise NodeAgentError(f"node agent tuning write failed: {e}")
    audit("stream_bindings.binding.tuning",
          {"binding_id": cmd.binding_id, "fields": list(cmd.tuning.keys())})
    return res

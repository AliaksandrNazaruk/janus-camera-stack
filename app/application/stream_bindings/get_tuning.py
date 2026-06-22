"""Use-case: read a REMOTE stream's encoder tuning via its node-agent. Verbatim from
routes/stream_bindings.get_binding_tuning (Phase 10.5). Local tuning lives at the camera
config endpoint. Returns the raw node-agent dict."""
from __future__ import annotations

from app.services import node_client
from app.services import stream_binding_store as sbs

from app.application.stream_bindings.commands import GetTuningCommand
from app.application.stream_bindings.results import (
    BindingNotFound,
    LocalTuningRejected,
    NodeAgentError,
)


def get_tuning(cmd: GetTuningCommand) -> dict:
    b = sbs.get_binding(cmd.binding_id, state_path=cmd.bind_state_path,
                        alloc_state_path=cmd.alloc_state_path)
    if b is None:
        raise BindingNotFound(cmd.binding_id)
    if b.mode != sbs.StreamMode.REMOTE_PRODUCER:
        raise LocalTuningRejected()
    client = node_client.get_node_client(b.node_id, state_path=cmd.bind_state_path)
    try:
        return client.get_tuning(b.sensor)
    except Exception as e:  # noqa: BLE001
        raise NodeAgentError(f"node agent tuning read failed: {e}")

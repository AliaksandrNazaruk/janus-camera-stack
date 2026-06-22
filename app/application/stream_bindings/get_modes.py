"""Use-case: read a REMOTE stream's supported encoder modes (resolution/fps) via its node-agent,
so the operator console can offer a real dropdown for a remote node instead of only the current
value. Mirrors get_tuning; reuses GetTuningCommand. Returns the raw node-agent dict
(``{"sensor": ..., "modes": [{"width","height","fps":[...]}]}``)."""
from __future__ import annotations

from app.services import node_client
from app.services import stream_binding_store as sbs

from app.application.stream_bindings.commands import GetTuningCommand
from app.application.stream_bindings.results import (
    BindingNotFound,
    LocalTuningRejected,
    NodeAgentError,
)


def get_modes(cmd: GetTuningCommand) -> dict:
    b = sbs.get_binding(cmd.binding_id, state_path=cmd.bind_state_path,
                        alloc_state_path=cmd.alloc_state_path)
    if b is None:
        raise BindingNotFound(cmd.binding_id)
    if b.mode != sbs.StreamMode.REMOTE_PRODUCER:
        raise LocalTuningRejected()
    client = node_client.get_node_client(b.node_id, state_path=cmd.bind_state_path)
    try:
        return client.get_modes(b.sensor)
    except Exception as e:  # noqa: BLE001
        raise NodeAgentError(f"node agent modes read failed: {e}")

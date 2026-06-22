"""Use-case: capture the node's SSH host-key fingerprint for out-of-band verification. Verbatim
from routes/stream_bindings.get_node_host_key + the _live_node guard (Phase 12.3B). INFORMATIONAL —
never pins. capture_host_key + fingerprint_fn are INJECTED (route-owned, so the existing oracle that
patches them on the route module stays untouched). sbs is called, never changed."""
from __future__ import annotations

from typing import Callable

from app.services import stream_binding_store as sbs

from app.application.stream_bindings.commands import GetHostKeyCommand
from app.application.stream_bindings.results import (
    HostKeyUnreachable,
    LocalNodeNoHostKey,
    NodeNotFound,
)


def get_host_key(cmd: GetHostKeyCommand, *, capture_host_key: Callable[[str], str],
                 fingerprint_fn: Callable[..., str]) -> dict:
    node = sbs.get_node(cmd.node_id, state_path=cmd.bind_state_path)
    if node is None:
        raise NodeNotFound(cmd.node_id)
    if node.node_id == sbs.LOCAL_NODE_ID:
        raise LocalNodeNoHostKey()
    hk = capture_host_key(node.host)
    if not hk:
        raise HostKeyUnreachable(node.host)
    return {"node_id": cmd.node_id, "host": node.host,
            "fingerprint": fingerprint_fn(hk),
            "pinned": bool(node.host_key),
            "hint": "verify out-of-band, then POST .../host-key/confirm {expected_fingerprint}"}

"""Use-case: pin the node's SSH host key after out-of-band fingerprint confirmation. Verbatim from
routes/stream_bindings.confirm_node_host_key + the _live_node guard (Phase 12.3B). Captures the key
FRESH (closing the capture->confirm TOCTOU), pins ONLY on a fingerprint match, and refuses to
silently replace an existing pin (force=true is deliberate key rotation). capture_host_key +
fingerprint_fn are INJECTED (route-owned, oracle-preserving). sbs is called, never changed."""
from __future__ import annotations

from typing import Callable

from app.services import stream_binding_store as sbs
from app.services.audit_log import audit

from app.application.stream_bindings.commands import ConfirmHostKeyCommand
from app.application.stream_bindings.results import (
    HostKeyFingerprintMismatch,
    HostKeyPinReplaceRejected,
    HostKeyUnreachable,
    LocalNodeNoHostKey,
    NodeNotFound,
)


def confirm_host_key(cmd: ConfirmHostKeyCommand, *, capture_host_key: Callable[[str], str],
                     fingerprint_fn: Callable[..., str]) -> dict:
    node = sbs.get_node(cmd.node_id, state_path=cmd.bind_state_path)
    if node is None:
        raise NodeNotFound(cmd.node_id)
    if node.node_id == sbs.LOCAL_NODE_ID:
        raise LocalNodeNoHostKey()
    hk = capture_host_key(node.host)
    if not hk:
        raise HostKeyUnreachable(node.host)
    seen = fingerprint_fn(hk)
    expected = cmd.expected_fingerprint.strip()
    if seen != expected:
        audit("stream_bindings.node.host_key_confirm",
              {"node_id": cmd.node_id, "expected": expected, "seen": seen}, outcome="rejected")
        raise HostKeyFingerprintMismatch(seen, expected)
    # Never silently REPLACE an existing pin: a matching fingerprint against the live
    # node is not enough to rotate away a previously-confirmed key (review MEDIUM).
    if node.host_key and node.host_key != hk and not cmd.force:
        audit("stream_bindings.node.host_key_confirm",
              {"node_id": cmd.node_id, "fingerprint": seen, "reason": "would_replace_existing_pin"},
              outcome="rejected")
        raise HostKeyPinReplaceRejected(cmd.node_id)
    sbs.set_host_key(node.node_id, hk, state_path=cmd.bind_state_path)
    audit("stream_bindings.node.host_key_confirm", {"node_id": cmd.node_id, "fingerprint": seen})
    return {"node_id": cmd.node_id, "host": node.host, "fingerprint": seen, "pinned": True}

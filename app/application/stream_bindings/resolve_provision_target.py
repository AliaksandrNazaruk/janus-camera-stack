"""Use-case helper: resolve a node that is eligible for provisioning / activation.

Shared by provision_node (3-3) and the activate path (the route's _node_for_provision forwarder), so
the "what makes a node provisionable" policy — it must exist, must not be the local cam10 host, and
the deploy bundle must be built — lives in ONE place and raises domain errors the route maps to
404 / 400 / 503. Extracted verbatim from routes/stream_bindings._node_for_provision (Phase 3 / A-02).
sbs is read, never changed.
"""
from __future__ import annotations

import os
from pathlib import Path

from app.services import stream_binding_store as sbs

from app.application.stream_bindings.results import (
    NodeBundleMissing,
    NodeNotFound,
    ProvisionLocalRejected,
)


def resolve_provision_target(node_id: str, *, bind_state_path: Path, bundle_tar: str):
    node = sbs.get_node(node_id, state_path=bind_state_path)
    if node is None:
        raise NodeNotFound(node_id)
    if node.node_id == sbs.LOCAL_NODE_ID:
        raise ProvisionLocalRejected()
    if not os.path.exists(bundle_tar):
        raise NodeBundleMissing(bundle_tar)
    return node

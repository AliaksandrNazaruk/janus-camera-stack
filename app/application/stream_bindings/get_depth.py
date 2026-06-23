"""Use-case: query depth from a node's camera (point or full frame), routed by node_id
through the node model — the LOCAL node (cam10) via its own mux directly, a REMOTE node via
its agent (:8901, X-Node-Token) which proxies that node's loopback mux. Universal across
nodes; mirrors get_modes but is NODE-keyed and serves local+remote (depth is a node-level
query, not per-binding). Returns the mux response shape; depth values are in METRES."""
from __future__ import annotations

from app.services import node_client

from app.application.stream_bindings.results import NodeAgentError


def get_depth(node_id: str, x: float, y: float, *, aligned: bool = False,
              bind_state_path) -> dict:
    client = node_client.get_node_client(node_id, state_path=bind_state_path)
    try:
        return client.get_depth(x, y, aligned=aligned)
    except Exception as e:  # noqa: BLE001
        raise NodeAgentError(f"node {node_id!r} depth query failed: {e}")


def get_depth_frame(node_id: str, *, bind_state_path) -> dict:
    client = node_client.get_node_client(node_id, state_path=bind_state_path)
    try:
        return client.get_depth_frame()
    except Exception as e:  # noqa: BLE001
        raise NodeAgentError(f"node {node_id!r} depth frame failed: {e}")

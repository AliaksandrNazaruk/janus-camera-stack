"""Node table (R5) for the stream_binding_store package (Phase 13E1, D2): CRUD over remote nodes
plus the implicit local 'cam10' sentinel — register/lookup/list, allocation-ordinal minting, the
provision-lifecycle / reachability / serial / host-key / maintenance / agent-token mutators, and the
forget-a-host cascade (remove_node deletes the node row AND every binding it owns under ONE lock —
a cross-entity transaction — then drops the 0600 token).

Depends on the leaf modules (models / state_file / secrets / validation); does NOT import bindings
(the binding side reaches IN via get_node for allocation). The facade re-exports every public name.
Moved verbatim from the original module; no behavior change."""
from __future__ import annotations

import ipaddress
import logging
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Dict, Optional

from app.services.stream_binding_store.models import (
    BindingValidationError,
    LOCAL_NODE,
    LOCAL_NODE_ID,
    NodeEntry,
)
from app.services.stream_binding_store.state_file import (
    DEFAULT_STATE_PATH,
    _flock_state,
    _load_state,
)
from app.services.stream_binding_store.secrets import (
    _read_secrets,
    _remove_node_secret,
    _set_node_secret,
    mint_agent_token,
)
from app.services.stream_binding_store.validation import (
    CAMERA_LAN_CIDR,
    GATEWAY_LAN_IP,
    _is_ipv4,
)

log = logging.getLogger(__name__)


def _with_token(entry: "NodeEntry", raw: dict, state_path: Path) -> "NodeEntry":
    """Overlay the agent token from the 0600 secret store (falling back to a
    legacy inline token in old combined state for one-time migration)."""
    tok = _read_secrets(state_path).get(entry.node_id) or raw.get("agent_token")
    return replace(entry, agent_token=tok)


def get_node(node_id: str, state_path: Path = DEFAULT_STATE_PATH) -> Optional[NodeEntry]:
    if node_id == LOCAL_NODE_ID:
        return LOCAL_NODE
    raw = _load_state(state_path)["nodes"].get(node_id)
    return _with_token(NodeEntry.from_raw(node_id, raw), raw, state_path) if raw else None


def _mint_ordinal(nodes: dict) -> int:
    """Lowest-free allocation-window ordinal (stable across other nodes' removal)."""
    used = {n.get("ordinal") for n in nodes.values() if n.get("ordinal") is not None}
    ordinal = 0
    while ordinal in used:
        ordinal += 1
    return ordinal


def upsert_node(node_id: str, *, host: str, role: str, reachability: str = "unknown",
                serial: Optional[str] = None, display_name: Optional[str] = None,
                state_path: Path = DEFAULT_STATE_PATH) -> NodeEntry:
    """Register/update a node. Assigns a stable allocation ordinal on first
    insert (stable across other nodes' removal). Preserves an existing serial/
    display_name unless explicitly overridden."""
    if node_id == LOCAL_NODE_ID:
        raise BindingValidationError("local node 'cam10' is implicit; do not store it")
    with _flock_state(state_path) as state:
        nodes = state["nodes"]
        existing = nodes.get(node_id, {})
        ordinal = existing.get("ordinal")
        if ordinal is None:
            ordinal = _mint_ordinal(nodes)
        entry = NodeEntry(
            node_id=node_id, host=host, role=role, reachability=reachability, ordinal=ordinal,
            serial=serial if serial is not None else existing.get("serial"),
            display_name=display_name if display_name is not None else existing.get("display_name"),
            provision_state=existing.get("provision_state"),
            host_key=existing.get("host_key"),
            maintenance=bool(existing.get("maintenance", False)),       # preserve operator state on re-register
            last_error=existing.get("last_error"),
            last_checked_at=existing.get("last_checked_at"),
            agent_token=(existing.get("agent_token") or _read_secrets(state_path).get(node_id)
                         or mint_agent_token()),       # per-node, mint if absent
        )
        nodes[node_id] = entry.to_dict()               # to_dict drops the token
        _set_node_secret(node_id, entry.agent_token, state_path)   # token -> 0600 secret store
        log.info("upsert node %s host=%s role=%s ordinal=%d", node_id, host, role, ordinal)
        return entry


def list_nodes(state_path: Path = DEFAULT_STATE_PATH) -> Dict[str, NodeEntry]:
    """All nodes, including the implicit local gateway node 'cam10'."""
    out: Dict[str, NodeEntry] = {LOCAL_NODE_ID: LOCAL_NODE}
    for nid, raw in _load_state(state_path)["nodes"].items():
        out[nid] = _with_token(NodeEntry.from_raw(nid, raw), raw, state_path)
    return out


def set_reachability(node_id: str, reachability: str,
                     state_path: Path = DEFAULT_STATE_PATH) -> NodeEntry:
    with _flock_state(state_path) as state:
        raw = state["nodes"].get(node_id)
        if not raw:
            raise KeyError(f"unknown node {node_id}")
        raw["reachability"] = reachability
        return NodeEntry.from_raw(node_id, raw)


def add_node_by_host(host: str, *, display_name: Optional[str] = None,
                     role: str = "remote_producer",
                     state_path: Path = DEFAULT_STATE_PATH) -> NodeEntry:
    """Add a remote node identified ONLY by IP — the operator supplies no name.

    Mints an opaque ``node_id`` (identity is never the IP nor a typed label;
    review I2). **Lookup-or-create:** an already-registered host returns its
    existing node rather than minting a duplicate (review I8). The camera serial
    is attached later by the provisioner via :func:`set_serial` (after probe).
    """
    host = host.strip()
    if not _is_ipv4(host):
        raise BindingValidationError(f"host must be an IPv4 address, got {host!r}")
    ip = ipaddress.IPv4Address(host)
    if ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        raise BindingValidationError(f"host {host} is not a valid remote camera-node address")
    # Service-layer LAN invariants (review P0-4): reject the gateway's own IP and
    # anything outside the camera LAN — a remote producer must be a DISTINCT host on
    # the camera subnet. Enforced here so fleet reconcile / future callers can't
    # bypass it. (Env-driven; empty CAMERA_LAN_CIDR = no subnet constraint for dev.)
    if GATEWAY_LAN_IP and host == GATEWAY_LAN_IP:
        raise BindingValidationError(
            f"{host} is the gateway's own LAN IP — the local camera is the implicit "
            f"'{LOCAL_NODE_ID}' node, not a remote producer")
    if CAMERA_LAN_CIDR:
        try:
            net = ipaddress.ip_network(CAMERA_LAN_CIDR, strict=False)
        except ValueError:
            net = None
        if net is not None and ip not in net:
            raise BindingValidationError(
                f"host {host} is outside the camera LAN {CAMERA_LAN_CIDR}")
    with _flock_state(state_path) as state:
        nodes = state["nodes"]
        for nid, raw in nodes.items():
            if str(raw.get("host")) == host:
                return _with_token(NodeEntry.from_raw(nid, raw), raw, state_path)   # lookup-or-create
        node_id = f"node-{uuid.uuid4().hex[:12]}"
        ordinal = _mint_ordinal(nodes)
        entry = NodeEntry(node_id=node_id, host=host, role=role,
                          reachability="unknown", ordinal=ordinal, display_name=display_name,
                          agent_token=mint_agent_token())     # per-node, minted once at enrollment
        nodes[node_id] = entry.to_dict()                      # to_dict drops the token
        _set_node_secret(node_id, entry.agent_token, state_path)   # token -> 0600 secret store
        log.info("add node by host %s -> %s ordinal=%d", host, node_id, ordinal)
        return entry


def set_serial(node_id: str, serial: str, state_path: Path = DEFAULT_STATE_PATH) -> NodeEntry:
    """Attach the probed camera serial (the device-identity anchor) to a node."""
    with _flock_state(state_path) as state:
        raw = state["nodes"].get(node_id)
        if not raw:
            raise KeyError(f"unknown node {node_id}")
        raw["serial"] = serial
        return NodeEntry.from_raw(node_id, raw)


def set_provision_state(node_id: str, provision_state: str,
                        state_path: Path = DEFAULT_STATE_PATH, *,
                        detail: Optional[str] = None) -> NodeEntry:
    """Persist the provisioning lifecycle state on the node row (write-ahead) so a
    restarted gateway can resume the provisioner (review L1). On a failed state,
    record ``detail`` as ``last_error`` for operator diagnostics; any non-failed
    state CLEARS it (the prior failure is resolved)."""
    with _flock_state(state_path) as state:
        raw = state["nodes"].get(node_id)
        if not raw:
            raise KeyError(f"unknown node {node_id}")
        raw["provision_state"] = provision_state
        if provision_state == "failed":
            raw["last_error"] = (detail or raw.get("last_error") or "")[:300]
        else:
            raw["last_error"] = None
        return NodeEntry.from_raw(node_id, raw)


def touch_checked(node_id: str, reachability: str,
                  state_path: Path = DEFAULT_STATE_PATH) -> NodeEntry:
    """Record a reachability probe result + its wall-clock timestamp (drives the
    UI's 'last seen Ns ago'). Single write so reachability and freshness agree."""
    with _flock_state(state_path) as state:
        raw = state["nodes"].get(node_id)
        if not raw:
            raise KeyError(f"unknown node {node_id}")
        raw["reachability"] = reachability
        raw["last_checked_at"] = time.time()
        return NodeEntry.from_raw(node_id, raw)


def set_maintenance(node_id: str, on: bool,
                    state_path: Path = DEFAULT_STATE_PATH) -> NodeEntry:
    """Toggle a node's maintenance pause. While on, the remote stream monitor skips
    this node's bindings — so servicing the camera/USB/cable raises NO false FDIR
    recovery (and no alert flood). Orthogonal to per-binding fdir."""
    with _flock_state(state_path) as state:
        raw = state["nodes"].get(node_id)
        if not raw:
            raise KeyError(f"unknown node {node_id}")
        raw["maintenance"] = bool(on)
        return NodeEntry.from_raw(node_id, raw)


def set_host_key(node_id: str, host_key: str, state_path: Path = DEFAULT_STATE_PATH) -> NodeEntry:
    """Pin a node's SSH host key (captured at enrollment) for strict verification."""
    with _flock_state(state_path) as state:
        raw = state["nodes"].get(node_id)
        if not raw:
            raise KeyError(f"unknown node {node_id}")
        raw["host_key"] = host_key
        return NodeEntry.from_raw(node_id, raw)


def set_agent_token(node_id: str, token: str, state_path: Path = DEFAULT_STATE_PATH) -> NodeEntry:
    """Set/rotate a node's per-node agent token. The value is never logged."""
    with _flock_state(state_path) as state:
        raw = state["nodes"].get(node_id)
        if not raw:
            raise KeyError(f"unknown node {node_id}")
        _set_node_secret(node_id, token, state_path)
        return replace(NodeEntry.from_raw(node_id, raw), agent_token=token)


def remove_node(node_id: str, state_path: Path = DEFAULT_STATE_PATH) -> dict:
    """Forget a remote host: atomically delete its node row AND every remote
    binding it owns (one lock — a half-removed node with orphan bindings would let
    FDIR keep monitoring a host the operator just removed). Also drops the node's
    0600 token secret. Returns {removed, binding_ids} so the caller can tear down
    each binding's Janus mountpoint + reconcile the firewall (drop stale rules).
    The local node 'cam10' is implicit and cannot be removed."""
    if node_id == LOCAL_NODE_ID:
        raise BindingValidationError("the local node 'cam10' is implicit and cannot be removed")
    removed_bindings: list = []
    with _flock_state(state_path) as state:
        present = node_id in state["nodes"]
        for bid in list(state["bindings"].keys()):
            if state["bindings"][bid].get("node_id") == node_id:
                del state["bindings"][bid]
                removed_bindings.append(bid)
        state["nodes"].pop(node_id, None)
        if present or removed_bindings:
            log.info("removed node %s (+%d bindings)", node_id, len(removed_bindings))
    _remove_node_secret(node_id, state_path)            # outside the topology lock; own atomic write
    return {"removed": present, "binding_ids": removed_bindings}

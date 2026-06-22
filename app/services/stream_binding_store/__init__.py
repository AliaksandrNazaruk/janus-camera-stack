"""StreamBinding store (Sprint G1) — universal gateway stream identity.

A StreamBinding describes one (node, sensor) → one Janus mountpoint,
transport-agnostic. Local and remote producers differ only in values.

Design: docs/design/STREAM_BINDING_MODEL.md (v2). Key decisions:

  • REMOTE bindings are AUTHORITATIVE stored rows (no local hardware backing).
  • LOCAL bindings are READ-ONLY PROJECTIONS computed from the existing
    serial-keyed mountpoint_allocator — never stored here. Every
    "{serial}:{sensor}" allocation belongs to the single local node
    ("cam10"), folding the serial. This removes the node_id→serial problem.
  • ONE free-list: local defers entirely to mountpoint_allocator; remote
    allocates STRICTLY ABOVE the legacy pool (mp ≥ 2000, port ≥ 5100) and
    uniqueness is checked against the UNION of this store + the allocator.
  • `nodes` is the single source of truth for a node's host (never copied
    into each binding).
  • `mode` is the structural safety cap (consumed by FDIR, not `policy`).

State file (versioned, backward-compat on read):
  /var/lib/camera-fdir/stream_bindings.json
  {
    "version": 1,
    "nodes":    { "<node_id>": { "host", "role", "reachability", "ordinal" } },
    "bindings": { "<binding_id>": { ...remote binding... } }
  }

Concurrency: flock on the file (same pattern as mountpoint_allocator). Cross-
store reads take the allocator state read-only; a binding mutation holds this
store's lock and re-validates against the allocator snapshot under it.

Package layout (Phase 13, D2 — this module is the FACADE; it re-exports the full public API so
`from app.services import stream_binding_store as sbs; sbs.*` is unchanged for all callers):
  models.py      R1  domain models / enums / errors
  state_file.py  R2+R3  JSON persistence + flock + fail-closed corruption
  secrets.py     R4  per-node 0600 agent-token store
  validation.py  R10  LAN-invariant helpers + config
  nodes.py       R5  node table CRUD (+ the remove_node cross-entity cascade)
  bindings.py    R6–R9  local projection · read · remote write · allocation
"""
from __future__ import annotations

from app.services.stream_binding_store.models import (
    BindingValidationError,
    LOCAL_NODE,
    LOCAL_NODE_ID,
    LOOPBACK,
    NodeEntry,
    StreamBinding,
    StreamFdirConfig,
    StreamJanusConfig,
    StreamMode,
    StreamStatus,
    StreamTransport,
)
from app.services.stream_binding_store.state_file import (
    DEFAULT_STATE_PATH,
    LOCK_SUFFIX,
    StoreCorruptionError,
    _flock_state,
    _load_state,
    store_corruption_status,
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
    _is_loopback,
)
from app.services.stream_binding_store.nodes import (
    add_node_by_host,
    get_node,
    list_nodes,
    remove_node,
    set_agent_token,
    set_host_key,
    set_maintenance,
    set_provision_state,
    set_reachability,
    set_serial,
    touch_checked,
    upsert_node,
)
from app.services.stream_binding_store.bindings import (
    MAX_REMOTE_NODES,
    NODE_MP_WINDOW,
    NODE_PORT_WINDOW,
    REMOTE_MP_MIN,
    REMOTE_PORT_MIN,
    allocate_mountpoint,
    allocate_port,
    get_binding,
    list_bindings,
    migrate_remote_binding_ids,
    remove_binding,
    remote_binding_id,
    set_fdir_enabled,
    set_desired_up,
    set_status,
    upsert_binding,
)

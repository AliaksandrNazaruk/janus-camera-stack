"""Stream bindings (R6–R9) for the stream_binding_store package (Phase 13E2, D2): the local
read-only projection (from the serial-keyed allocator), the merged local+remote read, the
remote-only writes (upsert/remove/status/fdir + serial-key rekey), and the remote allocation policy
(mountpoint/port windows STRICTLY above the legacy pool, uniqueness checked against the UNION of this
store + the allocator). These four responsibilities share `_used_sets`, the allocator dependency, and
the node lookup, so they live together (design note DB).

Depends on the leaf modules (models/state_file/validation) + nodes.get_node (for allocation) +
mountpoint_allocator. Moved verbatim from the original module; no behavior change."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, Optional, Tuple

from app.services import mountpoint_allocator as _alloc
from app.services.stream_binding_store.models import (
    BindingValidationError,
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
    _flock_state,
    _load_state,
)
from app.services.stream_binding_store.validation import _is_ipv4, _is_loopback
from app.services.stream_binding_store.nodes import get_node

log = logging.getLogger(__name__)

# Remote pools — STRICTLY above the legacy allocator pools
# (mountpoint_allocator.MP_ID_MAX=1999, PORT_MAX=5099) so the two free-lists
# can never collide. Per-node 100-wide windows keyed by node ordinal group a
# node's mountpoints into one readable band.
REMOTE_MP_MIN = 2000
REMOTE_PORT_MIN = 5100           # even base — RTP even / RTCP odd
NODE_MP_WINDOW = 100
NODE_PORT_WINDOW = 100
# Max remote nodes the gateway plans for — sizes the fail-closed firewall backstop
# so it covers EVERY node ordinal window, not just ordinal-0 (review M2).
MAX_REMOTE_NODES = 16


def _janus_mount_id() -> int:
    """Local watchdog target id (mirrors settings.janus_mount_id, decoupled)."""
    try:
        return int(os.environ.get("JANUS_MOUNT_ID", "1305"))
    except (TypeError, ValueError):
        return 1305


def _used_sets(bindings: dict, alloc_state_path: Path) -> Tuple[set, set]:
    """Union of mountpoint ids / RTP+RTCP ports across the bindings map AND the
    legacy serial-keyed allocator — the single shared free-list (R2-B2/M1)."""
    used_mp: set = set()
    used_port: set = set()
    for b in bindings.values():
        used_mp.add(int(b["janus"]["mountpoint_id"]))
        rtp = int(b["transport"]["rtp_port"])
        used_port.add(rtp)
        used_port.add(rtp + 1)
    for a in _alloc.list_allocations(alloc_state_path).values():
        used_mp.add(a.mp_id)
        used_port.add(a.rtp_port)
        used_port.add(a.rtp_port + 1)
    return used_mp, used_port


# ── projections (local) ───────────────────────────────────────────────

def _project_local(alloc_state_path: Path) -> Dict[str, "StreamBinding"]:
    """Build read-only local bindings from the serial-keyed allocator. Each
    (serial, sensor) allocation projects to its OWN serial-keyed binding —
    ``binding_id == the allocator key ('{serial}:{sensor}')`` — matching the
    by-serial camera identity used everywhere else (e.g. ``/cameras/{serial}/…``
    and remote serial-keyed binding_ids, SERIAL_KEYED_BINDING_ID.md). So two local
    cameras (e.g. two D435i on .10) are DISTINCT streams instead of colliding on a
    folded ``cam10:{sensor}`` key; ``node_id`` stays the implicit ``cam10`` local
    sentinel. (Pre-migration the legacy ``local:color`` sentinel key projects as-is
    until ``mountpoint_allocator.migrate_color_key`` folds it to the real serial.)"""
    out: Dict[str, StreamBinding] = {}
    for key, a in _alloc.list_allocations(alloc_state_path).items():
        sensor = key.rsplit(":", 1)[-1]
        out[key] = StreamBinding(
            binding_id=key, node_id=LOCAL_NODE_ID, sensor=sensor,
            mode=StreamMode.LOCAL_PRODUCER,
            transport=StreamTransport(rtp_port=a.rtp_port),
            janus=StreamJanusConfig(mountpoint_id=a.mp_id, rtp_iface=LOOPBACK),
            fdir=StreamFdirConfig(),
            status=(StreamStatus.ONLINE.value if a.desired_active
                    else StreamStatus.CONFIGURED_OFFLINE.value),
        )
    return out


# ── bindings: read ────────────────────────────────────────────────────

def list_bindings(*, state_path: Path = DEFAULT_STATE_PATH,
                  alloc_state_path: Path = _alloc.DEFAULT_STATE_PATH,
                  janus_mount_id: Optional[int] = None) -> Dict[str, "StreamBinding"]:
    """Merged view: projected local (from the allocator) + stored remote.
    (``janus_mount_id`` is retained for API compatibility; the local projection is
    now purely serial-keyed and no longer needs the canonical-color tie-break.)"""
    out = dict(_project_local(alloc_state_path))
    for bid, raw in _load_state(state_path)["bindings"].items():
        out[bid] = StreamBinding.from_raw(raw)
    return out


def get_binding(binding_id: str, *, state_path: Path = DEFAULT_STATE_PATH,
                alloc_state_path: Path = _alloc.DEFAULT_STATE_PATH,
                janus_mount_id: Optional[int] = None) -> Optional["StreamBinding"]:
    return list_bindings(state_path=state_path, alloc_state_path=alloc_state_path,
                         janus_mount_id=janus_mount_id).get(binding_id)


# ── bindings: write (remote only) ─────────────────────────────────────

def _validate_remote(binding: "StreamBinding", *, node: Optional[NodeEntry],
                     used_mp: set, used_port: set, janus_mount_id: int) -> None:
    if binding.mode != StreamMode.REMOTE_PRODUCER:
        raise BindingValidationError(
            "only remote_producer bindings are stored; local bindings are projections")
    if node is None:
        raise BindingValidationError(f"unknown node {binding.node_id!r}; register it first")
    # Accept either the pre-probe node-id-keyed id OR the serial-keyed id
    # (SERIAL_KEYED_BINDING_ID): make_gateway_binder uses remote_binding_id(),
    # which returns '{serial}:{sensor}' once the probe records a serial — so a
    # re-activation of an already-probed node legitimately upserts a serial-keyed
    # id. The old check only accepted '{node_id}:{sensor}' and rejected those.
    _valid_ids = {f"{binding.node_id}:{binding.sensor}", remote_binding_id(node, binding.sensor)}
    if binding.binding_id not in _valid_ids:
        raise BindingValidationError(
            f"binding_id {binding.binding_id!r} not in {sorted(_valid_ids)}")
    if not _is_ipv4(node.host) or _is_loopback(node.host):
        raise BindingValidationError(
            f"remote node host must be a non-loopback IPv4 LAN address, got {node.host!r}")
    iface = binding.janus.rtp_iface
    if not _is_ipv4(iface) or _is_loopback(iface):
        raise BindingValidationError(
            f"remote rtp_iface must be an explicit non-loopback .10 LAN IP, got {iface!r}")
    port = binding.transport.rtp_port
    if port % 2 != 0:
        raise BindingValidationError(f"rtp_port must be even (RTP/RTCP pair), got {port}")
    if port < REMOTE_PORT_MIN:
        raise BindingValidationError(
            f"remote rtp_port must be ≥ {REMOTE_PORT_MIN} (above the legacy pool), got {port}")
    mp = binding.janus.mountpoint_id
    if mp < REMOTE_MP_MIN:
        raise BindingValidationError(
            f"remote mountpoint_id must be ≥ {REMOTE_MP_MIN} (above the legacy pool), got {mp}")
    if mp == janus_mount_id:
        raise BindingValidationError(
            f"mountpoint_id {mp} collides with the local watchdog target (janus_mount_id)")
    if mp in used_mp:
        raise BindingValidationError(f"mountpoint_id {mp} already in use (union of both stores)")
    if port in used_port or (port + 1) in used_port:
        raise BindingValidationError(
            f"rtp port pair ({port},{port + 1}) already in use (union of both stores)")


def upsert_binding(binding: "StreamBinding", *, state_path: Path = DEFAULT_STATE_PATH,
                   alloc_state_path: Path = _alloc.DEFAULT_STATE_PATH,
                   janus_mount_id: Optional[int] = None) -> "StreamBinding":
    """Create/replace a REMOTE binding. Validates against the union of both
    stores under this store's lock. Local bindings are projections — rejected."""
    jmid = janus_mount_id if janus_mount_id is not None else _janus_mount_id()
    with _flock_state(state_path) as state:
        others = {k: v for k, v in state["bindings"].items() if k != binding.binding_id}
        used_mp, used_port = _used_sets(others, alloc_state_path)
        node_raw = state["nodes"].get(binding.node_id)
        node = NodeEntry.from_raw(binding.node_id, node_raw) if node_raw else None
        _validate_remote(binding, node=node, used_mp=used_mp,
                         used_port=used_port, janus_mount_id=jmid)
        state["bindings"][binding.binding_id] = binding.to_dict()
        log.info("upsert binding %s -> mp=%d port=%d iface=%s",
                 binding.binding_id, binding.janus.mountpoint_id,
                 binding.transport.rtp_port, binding.janus.rtp_iface)
        return binding


def remove_binding(binding_id: str, state_path: Path = DEFAULT_STATE_PATH) -> bool:
    with _flock_state(state_path) as state:
        if binding_id not in state["bindings"]:
            return False
        del state["bindings"][binding_id]
        log.info("removed binding %s", binding_id)
        return True


def remote_binding_id(node: NodeEntry, sensor: str) -> str:
    """Device-anchored id for a remote stream (SERIAL_KEYED_BINDING_ID.md). Prefers
    ``{serial}:{sensor}`` — stable across host re-IP and unique per camera, so one
    host can carry multiple cameras. Falls back to ``{node_id}:{sensor}`` only before
    the serial is known (pre-probe); those are folded to serial-keyed by
    :func:`migrate_remote_binding_ids` once the probe records a serial."""
    if node.serial:
        return f"{node.serial}:{sensor}"
    return f"{node.node_id}:{sensor}"


def migrate_remote_binding_ids(*, state_path: Path = DEFAULT_STATE_PATH) -> int:
    """One-shot, idempotent: rekey remote bindings ``{node_id}:{sensor}`` →
    ``{serial}:{sensor}`` once the node's serial is known. Safe because the remote
    allocation is DERIVED from each binding's stored (mp, port) — a rekey preserves
    both the values and the free-list, so no mountpoint/port churn. Returns the count
    migrated. Skips bindings already serial-keyed or whose target id would collide."""
    migrated = 0
    with _flock_state(state_path) as state:
        bindings = state["bindings"]
        nodes = state["nodes"]
        for old_id in list(bindings.keys()):
            b = bindings[old_id]
            if b.get("mode") != StreamMode.REMOTE_PRODUCER.value:
                continue
            node_raw = nodes.get(b.get("node_id"))
            serial = node_raw.get("serial") if node_raw else None
            if not serial:
                continue
            new_id = f"{serial}:{b.get('sensor')}"
            if new_id == old_id or new_id in bindings:
                continue
            b["binding_id"] = new_id            # the stored field …
            bindings[new_id] = b                # … and the dict key
            del bindings[old_id]
            migrated += 1
        if migrated:
            log.warning("migrated %d remote binding_id(s) to serial-keyed", migrated)
    return migrated


def set_status(binding_id: str, status: str,
               state_path: Path = DEFAULT_STATE_PATH) -> "StreamBinding":
    """Set runtime status on a STORED (remote) binding. Local projections derive
    status from the allocator and have no stored status (raises KeyError)."""
    StreamStatus(status)            # validate the value
    with _flock_state(state_path) as state:
        raw = state["bindings"].get(binding_id)
        if not raw:
            raise KeyError(f"no stored binding {binding_id} (local bindings are projections)")
        raw["status"] = status
        return StreamBinding.from_raw(raw)


def set_fdir_enabled(binding_id: str, enabled: bool,
                     state_path: Path = DEFAULT_STATE_PATH) -> "StreamBinding":
    """Enable/disable FDIR for ONE stored (remote) binding. Disabled → the remote
    monitor skips it (no auto-recovery, no alert). Local projections have no stored
    fdir config (raises KeyError) — local FDIR is the cam10 watchdog ladder."""
    with _flock_state(state_path) as state:
        raw = state["bindings"].get(binding_id)
        if not raw:
            raise KeyError(f"no stored binding {binding_id} (local bindings are projections)")
        fdir = dict(raw.get("fdir", {}))
        fdir["enabled"] = bool(enabled)
        raw["fdir"] = fdir
        return StreamBinding.from_raw(raw)


def set_desired_up(binding_id: str, up: bool,
                   state_path: Path = DEFAULT_STATE_PATH) -> "StreamBinding":
    """Set the operator Start/Stop intent (desired UP/down) for ONE stored binding — SEPARATE from
    fdir.enabled (auto-recovery). desired_up drives whether the gateway maintains the Janus
    mountpoint (and, with the node reconciler, whether the node brings its encoder up). Local
    projections have no stored binding (raises KeyError)."""
    with _flock_state(state_path) as state:
        raw = state["bindings"].get(binding_id)
        if not raw:
            raise KeyError(f"no stored binding {binding_id} (local bindings are projections)")
        raw["desired_up"] = bool(up)
        return StreamBinding.from_raw(raw)


# ── allocation (remote; above the legacy pool, union-checked) ─────────

def allocate_mountpoint(node_id: str, *, state_path: Path = DEFAULT_STATE_PATH,
                        alloc_state_path: Path = _alloc.DEFAULT_STATE_PATH) -> int:
    """Lowest-free mountpoint id in the node's window. Advisory — upsert_binding
    re-checks under lock (a concurrent racer's upsert fails on collision)."""
    state = _load_state(state_path)
    node = get_node(node_id, state_path)
    if node is None or node.ordinal is None:
        raise _alloc.AllocationError(f"node {node_id!r} unknown / has no ordinal; register it first")
    used_mp, _ = _used_sets(state["bindings"], alloc_state_path)
    lo = REMOTE_MP_MIN + node.ordinal * NODE_MP_WINDOW
    mp = next((i for i in range(lo, lo + NODE_MP_WINDOW) if i not in used_mp), None)
    if mp is None:
        raise _alloc.AllocationError(f"mountpoint window for {node_id} exhausted [{lo}..{lo + NODE_MP_WINDOW})")
    return mp


def allocate_port(node_id: str, *, state_path: Path = DEFAULT_STATE_PATH,
                  alloc_state_path: Path = _alloc.DEFAULT_STATE_PATH) -> int:
    """Lowest-free even RTP port (with RTCP port+1 free) in the node's window."""
    state = _load_state(state_path)
    node = get_node(node_id, state_path)
    if node is None or node.ordinal is None:
        raise _alloc.AllocationError(f"node {node_id!r} unknown / has no ordinal; register it first")
    _, used_port = _used_sets(state["bindings"], alloc_state_path)
    lo = REMOTE_PORT_MIN + node.ordinal * NODE_PORT_WINDOW
    for p in range(lo, lo + NODE_PORT_WINDOW, 2):
        if p not in used_port and (p + 1) not in used_port:
            return p
    raise _alloc.AllocationError(f"RTP port window for {node_id} exhausted [{lo}..{lo + NODE_PORT_WINDOW})")

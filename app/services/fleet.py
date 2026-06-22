"""P4 — declarative camera-fleet desired state (config-as-code).

A TOML manifest (``/etc/robot/camera-fleet.toml``, matching the ``cam-rgb.toml``
convention; parsed with stdlib ``tomllib``) declares the desired fleet: each node by
host + the sensors it should stream. :func:`plan` reports drift vs the actual store
(read-only, NO credentials); :func:`reconcile_gateway` performs only the creds-free
gateway-side convergence (register missing nodes). Provision + activate SSH to the
node and need a sudo password + an out-of-band-confirmed host key (P4-SEC), so they
stay operator-driven via the existing APIs — :func:`plan` surfaces exactly which
nodes need them. Pruning (removing extra nodes/streams) is reported but NEVER
auto-applied. Design: DECLARATIVE_FLEET.md.
"""
from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from app.services import stream_binding_store as sbs

log = logging.getLogger(__name__)

VALID_SENSORS = ("color", "depth", "ir1", "ir2")
DEFAULT_MANIFEST_PATH = Path("/etc/robot/camera-fleet.toml")
_READY = "ready"          # node_provisioner.PState.READY — "provisioned" (pipe deployed)


class ManifestError(ValueError):
    """Malformed fleet manifest."""


@dataclass(frozen=True)
class DesiredNode:
    host: str
    display_name: Optional[str]
    streams: List[str]


def load_manifest(path=DEFAULT_MANIFEST_PATH) -> List[DesiredNode]:
    """Parse + validate the fleet manifest. Raises :class:`ManifestError` on bad input.
    The manifest is non-secret (hosts + sensor names) — it carries no credentials."""
    p = Path(path)
    try:
        raw = tomllib.loads(p.read_text())
    except FileNotFoundError:
        raise ManifestError(f"manifest not found: {p}")
    except OSError as e:
        raise ManifestError(f"cannot read manifest {p}: {e}")
    except tomllib.TOMLDecodeError as e:
        raise ManifestError(f"invalid TOML in {p}: {e}")
    nodes_raw = raw.get("node", [])
    if not isinstance(nodes_raw, list):
        raise ManifestError("manifest 'node' must be an array of tables ([[node]])")
    out: List[DesiredNode] = []
    seen = set()
    for i, n in enumerate(nodes_raw):
        if not isinstance(n, dict):
            raise ManifestError(f"node[{i}] must be a table")
        host = str(n.get("host", "")).strip()
        if not host:
            raise ManifestError(f"node[{i}] missing 'host'")
        if host in seen:
            raise ManifestError(f"duplicate host in manifest: {host}")
        seen.add(host)
        streams = n.get("streams", [])
        if not isinstance(streams, list) or not streams:
            raise ManifestError(f"node {host}: 'streams' must be a non-empty array")
        bad = [s for s in streams if s not in VALID_SENSORS]
        if bad:
            raise ManifestError(f"node {host}: invalid sensors {bad} (valid: {list(VALID_SENSORS)})")
        out.append(DesiredNode(host=host, display_name=n.get("display_name"),
                               streams=list(dict.fromkeys(streams))))   # de-dup, keep order
    return out


@dataclass
class NodePlan:
    host: str
    desired_streams: List[str]
    node_id: Optional[str]          # None until registered
    registered: bool
    provisioned: bool
    active_streams: List[str]
    missing_streams: List[str]      # desired but no binding yet → needs activate
    extra_streams: List[str]        # bound but not desired → prune candidate (never auto-removed)
    actions: List[str]              # ordered: register / provision / activate:<csv>


@dataclass
class FleetPlan:
    nodes: List[NodePlan]
    extra_nodes: List[str]          # registered remote hosts absent from the manifest (prune candidates)

    @property
    def in_sync(self) -> bool:
        return not self.extra_nodes and all(not n.actions for n in self.nodes)


def _actual(state_path, alloc_state_path):
    """(host→NodeEntry for remote nodes, node_id→[active sensors]) from the store."""
    bkw = {"state_path": state_path}
    if alloc_state_path is not None:
        bkw["alloc_state_path"] = alloc_state_path
    nodes = sbs.list_nodes(state_path=state_path)               # includes the cam10 sentinel
    bindings = sbs.list_bindings(**bkw)
    by_host = {n.host: n for nid, n in nodes.items() if nid != sbs.LOCAL_NODE_ID}
    streams_by_node: Dict[str, List[str]] = {}
    for b in bindings.values():
        if b.mode == sbs.StreamMode.REMOTE_PRODUCER:
            streams_by_node.setdefault(b.node_id, []).append(b.sensor)
    return by_host, streams_by_node


def plan(manifest: List[DesiredNode], *, state_path=sbs.DEFAULT_STATE_PATH,
         alloc_state_path=None) -> FleetPlan:
    """Read-only drift of actual store vs the desired manifest. No creds, no SSH,
    no mutation — safe to run anytime (dashboards, CI drift checks)."""
    by_host, streams_by_node = _actual(state_path, alloc_state_path)
    plans: List[NodePlan] = []
    desired_hosts = set()
    for d in manifest:
        desired_hosts.add(d.host)
        node = by_host.get(d.host)
        if node is None:
            plans.append(NodePlan(
                host=d.host, desired_streams=d.streams, node_id=None, registered=False,
                provisioned=False, active_streams=[], missing_streams=list(d.streams),
                extra_streams=[], actions=["register", "provision",
                                           f"activate:{','.join(d.streams)}"]))
            continue
        provisioned = node.provision_state == _READY
        active = sorted(set(streams_by_node.get(node.node_id, [])))
        missing = [s for s in d.streams if s not in active]
        extra = [s for s in active if s not in d.streams]
        actions: List[str] = []
        if not provisioned:
            actions.append("provision")
        if missing:
            actions.append(f"activate:{','.join(missing)}")
        plans.append(NodePlan(
            host=d.host, desired_streams=d.streams, node_id=node.node_id, registered=True,
            provisioned=provisioned, active_streams=active, missing_streams=missing,
            extra_streams=extra, actions=actions))
    extra_nodes = sorted(h for h in by_host if h not in desired_hosts)
    return FleetPlan(nodes=plans, extra_nodes=extra_nodes)


def reconcile_gateway(manifest: List[DesiredNode], *,
                      state_path=sbs.DEFAULT_STATE_PATH) -> List[str]:
    """Creds-free convergence: register every manifest node that isn't in the store
    yet (``add_node_by_host`` — mints node_id + per-node token + ordinal). Idempotent
    (an already-registered host is a no-op via lookup-or-create). NEVER provisions,
    activates, SSHes, or removes anything. Returns the node_ids that were registered."""
    existing_hosts = {n.host for nid, n in sbs.list_nodes(state_path=state_path).items()
                      if nid != sbs.LOCAL_NODE_ID}
    registered: List[str] = []
    for d in manifest:
        if d.host in existing_hosts:
            continue
        node = sbs.add_node_by_host(d.host, display_name=d.display_name, state_path=state_path)
        registered.append(node.node_id)
        log.info("fleet: registered %s (%s) from manifest", node.node_id, d.host)
    return registered

"""G5 — NodeClient: recovery indirection keyed by node_id.

Routes `recover(binding)` to the right client by node:
  • LocalNodeClientAdapter (cam10): may run the existing local encoder recovery.
  • RealNodeClient (provisioned remote): drives the node-agent (:8901) over HTTP —
    asks the node to restart its OWN encoder. No local command, no gateway action.
  • RemoteNodeClientStub (unprovisioned remote, no host): OFFLINE/inert.

The FDIR safety property holds either way: a remote binding's recovery is confined
to "ask the remote node to restart its stream" (HTTP to the node, or a no-op) —
there is NO code path from a remote binding to a local-destructive action. P2
makes that ask actually happen. Design: UNIFIED_FDIR_OVER_STREAM_BINDINGS.md §3/§4.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import httpx

from app.services import stream_binding_store as sbs

log = logging.getLogger(__name__)

# Port for the per-node agent (/healthz + /restart_stream + /probe_devices).
NODE_AGENT_PORT = int(os.getenv("NODE_AGENT_PORT", "8901"))
# (G5: the dead global NODE_AGENT_TOKEN import-time capture was removed — review H1 dropped the global
# fallback; node_client uses the PER-NODE token from the secret store, never a process-wide env token.)


class NodeReachability(str, Enum):
    LOCAL = "local"
    REACHABLE = "reachable"
    UNREACHABLE = "unreachable"
    BOOTSTRAP_REQUIRED = "bootstrap_required"


@dataclass(frozen=True)
class RestartResult:
    ok: bool
    detail: str


class LocalNodeClientAdapter:
    """cam10 — the local gateway camera. restart_stream delegates to the existing
    encoder recovery path (local IS allowed to run recovery). Not on cam10's hot
    path in G5.1 (cam10 keeps the existing watchdog ladder); provided for the
    unified recovery interface + future per-binding routing."""

    def status(self, node_id: str) -> NodeReachability:
        return NodeReachability.LOCAL

    def stream_status(self, node_id: str, sensor: str) -> str:
        try:
            from app.services import sensor_lifecycle
            running = sensor_lifecycle.is_running(sensor)
            return "online" if running else "offline"
        except Exception as e:  # pragma: no cover - defensive
            return f"unknown ({e})"

    def restart_stream(self, node_id: str, sensor: str) -> RestartResult:
        try:
            from app.services import sensor_lifecycle
            sensor_lifecycle._encoder_action("restart", "rs-stream", sensor)
            return RestartResult(True, f"restarted rs-stream@{sensor}")
        except Exception as e:
            return RestartResult(False, f"local restart failed: {e}")

    def stop_stream(self, node_id: str, sensor: str) -> RestartResult:
        # Encoder-only stop for the unified interface. The route prefers the
        # serial-aware sensor_lifecycle.stop() for local bindings (it also flips the
        # allocation's desired_active so the projection reflects offline).
        try:
            from app.services import sensor_lifecycle
            sensor_lifecycle._encoder_action("stop", "rs-stream", sensor)
            return RestartResult(True, f"stopped rs-stream@{sensor}")
        except Exception as e:
            return RestartResult(False, f"local stop failed: {e}")


class RemoteNodeClientStub:
    """cam55 (any remote producer) — OFFLINE stub. NEVER runs a local command and
    cannot reach the remote node. Every method is a pure return. This is by
    construction, not policy: no process execution, no network call, no shell."""

    def __init__(self, node: sbs.NodeEntry):
        self.node = node

    def status(self, node_id: str) -> NodeReachability:
        r = (self.node.reachability or "").lower()
        if r == "reachable":
            return NodeReachability.REACHABLE
        if r == "bootstrap_required":
            return NodeReachability.BOOTSTRAP_REQUIRED
        return NodeReachability.UNREACHABLE

    def stream_status(self, node_id: str, sensor: str) -> str:
        return "unreachable"

    def restart_stream(self, node_id: str, sensor: str) -> RestartResult:
        # No node agent exists. This is the terminal "recovery" for a remote
        # binding and it is intentionally inert — never a local action.
        return RestartResult(
            False, f"remote node {node_id!r} agent unreachable (bootstrap_required)")

    def stop_stream(self, node_id: str, sensor: str) -> RestartResult:
        return RestartResult(
            False, f"remote node {node_id!r} agent unreachable (bootstrap_required)")

    def get_tuning(self, sensor: str) -> dict:
        raise RuntimeError(f"node {self.node.node_id!r} agent unreachable (bootstrap_required)")

    def set_tuning(self, sensor: str, body: dict) -> dict:
        raise RuntimeError(f"node {self.node.node_id!r} agent unreachable (bootstrap_required)")

    def get_modes(self, sensor: str) -> dict:
        raise RuntimeError(f"node {self.node.node_id!r} agent unreachable (bootstrap_required)")


class RealNodeClient:
    """Reachable remote node — drives recovery via the node-agent (:8901) over HTTP.

    Runs NO local command and takes NO gateway action: recovery is confined to an
    HTTP call asking the node to restart its OWN encoder. The FDIR safety boundary
    (a remote fault can never drive a local-destructive action) is preserved — this
    just makes the previously-inert 'ask the node to restart' actually happen (P2)."""

    def __init__(self, node: sbs.NodeEntry, *, port: Optional[int] = None, token: str = ""):
        self.node = node
        self._port = port or NODE_AGENT_PORT
        # Per-node token ONLY — no global NODE_AGENT_TOKEN fallback (review H1): a node
        # without its own token is unmanageable (the agent 403s) until re-provision/
        # rotate mints one, so one leaked shared token can't drive the whole fleet.
        self._token = token

    def _headers(self) -> dict:
        return {"X-Node-Token": self._token} if self._token else {}

    def status(self, node_id: str) -> NodeReachability:
        return NodeReachability(probe_agent(self.node.host, port=self._port)["reachability"])

    def stream_status(self, node_id: str, sensor: str) -> str:
        return "reachable" if probe_agent(self.node.host, port=self._port)["reachable"] else "unreachable"

    def restart_stream(self, node_id: str, sensor: str) -> RestartResult:
        return self._agent_action("restart_stream", node_id, sensor)

    def stop_stream(self, node_id: str, sensor: str) -> RestartResult:
        """Operator stop — HTTP-ask the node-agent to stop its OWN encoder. Distinct
        from recovery: stop is a deliberate, no-auto-restart action (the binding
        stays configured but goes offline). Pair with maintenance/disable-FDIR so the
        monitor does not immediately try to recover it back. No local/gateway action."""
        return self._agent_action("stop_stream", node_id, sensor)

    def _agent_action(self, action: str, node_id: str, sensor: str) -> RestartResult:
        url = f"http://{self.node.host}:{self._port}/{action}?sensor={sensor}"
        try:
            r = httpx.post(url, headers=self._headers(), timeout=10.0)
            if r.status_code == 200 and r.json().get("ok"):
                return RestartResult(True, f"agent {action} rs-stream@{sensor} on {node_id}")
            return RestartResult(False, f"agent {action} {sensor}: HTTP {r.status_code} {r.text[:120]}")
        except (httpx.HTTPError, OSError) as e:
            return RestartResult(False, f"node {node_id!r} agent unreachable: {e}")

    def get_tuning(self, sensor: str) -> dict:
        """Read the node's current encoder tuning (rs-{sensor}.tuning.env)."""
        url = f"http://{self.node.host}:{self._port}/tuning?sensor={sensor}"
        r = httpx.get(url, headers=self._headers(), timeout=10.0)
        r.raise_for_status()
        return r.json()

    def set_tuning(self, sensor: str, body: dict) -> dict:
        """Write tuning on the node + restart its encoder. Raises on HTTP error."""
        url = f"http://{self.node.host}:{self._port}/tuning?sensor={sensor}"
        r = httpx.post(url, headers=self._headers(), json=body, timeout=40.0)
        r.raise_for_status()
        return r.json()

    def get_modes(self, sensor: str) -> dict:
        """Read the node's supported encoder modes (resolution/fps) for this sensor — so the
        console can offer a real dropdown for a remote node. Raises on HTTP error."""
        url = f"http://{self.node.host}:{self._port}/modes?sensor={sensor}"
        r = httpx.get(url, headers=self._headers(), timeout=20.0)
        r.raise_for_status()
        return r.json()


def probe_agent(host: str, *, port: Optional[int] = None) -> dict:
    """Best-effort node-agent health probe (HTTP /healthz). The agent does not
    exist on remote nodes yet, so this normally returns unreachable /
    bootstrap_required. Used by the G6 /nodes/check endpoint."""
    p = port or NODE_AGENT_PORT
    try:
        r = httpx.get(f"http://{host}:{p}/healthz", timeout=2.0)
        if r.status_code == 200:
            return {"reachable": True, "reason": "healthz_ok",
                    "next_step": None, "reachability": "reachable"}
        return {"reachable": False, "reason": f"healthz_status_{r.status_code}",
                "next_step": "bootstrap_required", "reachability": "bootstrap_required"}
    except (httpx.HTTPError, OSError):
        return {"reachable": False, "reason": "node_agent_unreachable",
                "next_step": "bootstrap_required", "reachability": "unreachable"}


def get_node_client(node_id: str, *, state_path=sbs.DEFAULT_STATE_PATH):
    """Return the recovery client for a node. cam10 → local; a provisioned remote
    node (host known) → RealNodeClient (drives its agent, HTTP-only); an
    unprovisioned remote node (no host) → the inert stub. A remote node can NEVER
    get the local adapter."""
    if node_id == sbs.LOCAL_NODE_ID:
        return LocalNodeClientAdapter()
    node = sbs.get_node(node_id, state_path=state_path)
    if node is None:
        node = sbs.NodeEntry(node_id=node_id, host="", role="remote_producer",
                             reachability="unreachable")
    if node.host:
        # P4-SEC / H1: authenticate with the node's OWN token only. A node without one
        # is unmanageable (agent 403s) until re-provision/rotate-token mints it — no
        # shared global-token fallback.
        if not node.agent_token:
            log.warning("node %s has no per-node agent token — control calls will be "
                        "rejected until you re-provision or rotate-token", node_id)
        return RealNodeClient(node, token=node.agent_token or "")
    return RemoteNodeClientStub(node)

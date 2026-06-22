"""Node provisioner — drives onboarding over a pluggable Transport (CI-testable
with a fake; review O6). Two uniform phases, no stream is special:

  provision(node)         deploy the PIPE: reachable -> push -> probe -> serial
                          -> bootstrap deploy (mux up) -> ready
  activate_streams(node,  per chosen sensor (uniform): gateway bind (allocate +
    sensors)              ensure-janus) -> bootstrap activate --sensor (contract
                          + encoder). FDIR then monitors each (node,sensor) alike.

Provision persists node.provision_state write-ahead (review L1). The gateway bind
is INJECTED (on_bind) so the SSH machine is testable; make_gateway_binder is the
live impl and returns the ALLOCATED rtp_port so the node targets the right port.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
from dataclasses import dataclass
from typing import Callable, List, Optional

from app.services import mountpoint_allocator
from app.services import stream_binding_store as sbs
from app.services.ssh_transport import Transport

log = logging.getLogger(__name__)

BUNDLE_REMOTE = "/tmp/camera-node-bundle.tar.gz"
REMOTE_DIR = "/tmp/camera-node-bundle"
PROBE_CLI = f"{REMOTE_DIR}/probe/realsense_probe_cli.py"
BOOTSTRAP = f"{REMOTE_DIR}/bootstrap.sh"

# Provisioner config (env-tunable). Lives in the service layer so routes don't
# scatter os.getenv (architecture fitness; CONTRACT.md "Configuration").
NODE_BUNDLE_TAR = os.getenv("NODE_BUNDLE_TAR", "/tmp/camera-node-bundle.tar.gz")  # built bundle, gateway side
GATEWAY_LAN_IP = os.getenv("GATEWAY_LAN_IP", "192.168.1.10")
NODE_SSH_USER = os.getenv("NODE_SSH_USER", "boris")
NODE_SSH_KEY = os.getenv("NODE_SSH_KEY", "/opt/.ssh/id_ed25519")
# (G5: removed the dead global NODE_AGENT_TOKEN import-time capture — unused; per-node tokens are minted
# into the secret store during provisioning, never read from a process-wide env var.)


class PState:
    REACHABLE = "reachable"
    PROBING = "probing"
    NO_CAMERA = "no_camera"
    READY = "ready"          # pipe deployed (mux up); no streams yet
    FAILED = "failed"


# provision_state values that mean a provision op is actively MID-FLIGHT — the only states the
# restart reaper resets on an orphaned op. Terminal states (ready / failed / no_camera) are left
# untouched. (provision() writes reachable → probing → ready|no_camera; rotate/activate don't
# touch provision_state, so an orphaned rotate/activate leaves a terminal state = not reset.)
_IN_PROGRESS_STATES = frozenset({PState.REACHABLE, PState.PROBING})


def is_in_progress(provision_state) -> bool:
    return provision_state in _IN_PROGRESS_STATES


@dataclass(frozen=True)
class BindOutcome:
    binding_id: str
    rtp_port: int            # gateway-allocated port the node must target


@dataclass
class ProvisionResult:
    node_id: str
    state: str
    serial: Optional[str] = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state == PState.READY


@dataclass
class StreamResult:
    sensor: str
    ok: bool
    binding_id: Optional[str] = None
    detail: str = ""


# on_bind(node, sensor) -> BindOutcome : gateway-side allocate + ensure-janus (uniform per sensor)
BindCallback = Callable[[sbs.NodeEntry, str], BindOutcome]


def provision(node_id: str, transport: Transport, *, bundle_tar: str,
              state_path=sbs.DEFAULT_STATE_PATH) -> ProvisionResult:
    """Deploy the node PIPE (mux + agent) — sensor-agnostic. No streams activated.
    The node's OWN per-node agent token (minted at enrollment; minted here if a
    legacy node lacks one) is installed into the node-agent's env so its control
    endpoints require it (P4-SEC: no shared fleet token)."""
    node = sbs.get_node(node_id, state_path=state_path)
    if node is None:
        raise ValueError(f"unknown node {node_id}")
    agent_token = node.agent_token
    if not agent_token:                         # legacy node enrolled before per-node tokens
        agent_token = sbs.mint_agent_token()
        sbs.set_agent_token(node_id, agent_token, state_path=state_path)

    def mark(state: str) -> None:
        sbs.set_provision_state(node_id, state, state_path=state_path)
        log.info("provision %s -> %s", node_id, state)

    def fail(detail: str) -> ProvisionResult:
        # Persist the failure detail as node.last_error (operator diagnostics — no
        # journalctl needed); a later successful mark() clears it.
        sbs.set_provision_state(node_id, PState.FAILED, state_path=state_path, detail=detail)
        log.warning("provision %s FAILED: %s", node_id, detail)
        return ProvisionResult(node_id, PState.FAILED, detail=detail[:200])

    if not transport.run("echo provision-ok").ok:
        return fail("node unreachable over SSH")
    mark(PState.REACHABLE)

    if not transport.push(bundle_tar, BUNDLE_REMOTE).ok:
        return fail("bundle push failed")
    transport.run(f"rm -rf {REMOTE_DIR} && tar xzf {BUNDLE_REMOTE} -C /tmp")

    mark(PState.PROBING)
    pr = transport.run(f"python3 {PROBE_CLI} --json")
    try:
        info = json.loads(pr.stdout or "{}")
    except json.JSONDecodeError:
        return fail(f"probe returned non-JSON: {pr.stdout[:120]!r}")
    if not info.get("available"):
        mark(PState.NO_CAMERA)
        transport.run(f"rm -rf {REMOTE_DIR} {BUNDLE_REMOTE}")   # leave host clean
        return ProvisionResult(node_id, PState.NO_CAMERA, detail="no camera found")
    serial = (info.get("devices") or [{}])[0].get("serial")
    if serial:
        sbs.set_serial(node_id, serial, state_path=state_path)

    dep = transport.run(f"{shlex.quote(BOOTSTRAP)} deploy --agent-token {shlex.quote(agent_token)}",
                        sudo=True, timeout=300.0)
    if not dep.ok:
        return fail(f"pipe deploy failed: {dep.stderr[:160]}")
    mark(PState.READY)
    return ProvisionResult(node_id, PState.READY, serial=serial)


def rotate_token(node_id: str, transport: Transport, *,
                 state_path=sbs.DEFAULT_STATE_PATH) -> bool:
    """Mint a new per-node agent token, push it to the node (rewrite node-agent.env
    + restart ONLY the agent, not the mux), then persist it. The old token dies when
    the agent reloads its env. Returns True on success; the token value is never
    returned or logged. Requires the node to be provisioned (agent present)."""
    node = sbs.get_node(node_id, state_path=state_path)
    if node is None:
        raise ValueError(f"unknown node {node_id}")
    token = sbs.mint_agent_token()
    r = transport.run(f"{shlex.quote(BOOTSTRAP)} set-token --agent-token {shlex.quote(token)}", sudo=True, timeout=60.0)
    if not r.ok:
        log.warning("rotate_token %s: set-token failed: %s", node_id, r.stderr[:160])
        return False
    sbs.set_agent_token(node_id, token, state_path=state_path)
    log.info("rotated agent token for %s (value not logged)", node_id)
    return True


def activate_streams(node_id: str, transport: Transport, *, sensors: List[str],
                     gateway_host: str, on_bind: BindCallback,
                     state_path=sbs.DEFAULT_STATE_PATH) -> List[StreamResult]:
    """Activate a chosen set of streams — UNIFORM across sensors. Per sensor:
    gateway bind (allocate + ensure-janus) then node activate (contract + encoder)."""
    node = sbs.get_node(node_id, state_path=state_path)
    if node is None:
        raise ValueError(f"unknown node {node_id}")
    results: List[StreamResult] = []
    for sensor in sensors:
        try:
            outcome = on_bind(node, sensor)
        except Exception as e:  # noqa: BLE001
            results.append(StreamResult(sensor, False, detail=f"gateway bind failed: {e}"))
            continue
        act = transport.run(
            f"{shlex.quote(BOOTSTRAP)} activate --sensor {shlex.quote(sensor)} "
            f"--rtp-target-host {shlex.quote(gateway_host)} --rtp-port {int(outcome.rtp_port)}",
            sudo=True, timeout=120.0)
        if not act.ok:
            results.append(StreamResult(sensor, False, binding_id=outcome.binding_id,
                                        detail=f"node activate failed: {act.stderr[:120]}"))
            continue
        log.info("activated %s:%s -> %s:%d", node_id, sensor, gateway_host, outcome.rtp_port)
        results.append(StreamResult(sensor, True, binding_id=outcome.binding_id))
    return results


def make_gateway_binder(gateway_host: str, *, payload_type: int = 96, codec: str = "h264",
                        state_path=sbs.DEFAULT_STATE_PATH,
                        alloc_state_path=mountpoint_allocator.DEFAULT_STATE_PATH) -> BindCallback:
    """Live gateway bind for ANY sensor: allocate mp+port, create the remote
    StreamBinding, ensure the Janus mountpoint on the gateway LAN iface. Returns
    BindOutcome with the allocated rtp_port. Mirrors the create + ensure-janus routes."""

    def bind(node: sbs.NodeEntry, sensor: str) -> BindOutcome:
        # Idempotent re-activation (Bug C): reuse an existing binding's mp+port
        # instead of allocating fresh. Re-allocating on re-onboard creates a
        # DUPLICATE Janus mountpoint and (since the new port differs) leaves the
        # per-node firewall rule pinned to the OLD port → RTP silently dropped.
        # Stable mp/port across re-activation keeps the mountpoint + firewall valid.
        bid = sbs.remote_binding_id(node, sensor)
        existing = sbs.get_binding(bid, state_path=state_path, alloc_state_path=alloc_state_path)
        if existing is not None and existing.mode == sbs.StreamMode.REMOTE_PRODUCER:
            mp, port = existing.janus.mountpoint_id, existing.transport.rtp_port
        else:
            mp = sbs.allocate_mountpoint(node.node_id, state_path=state_path,
                                         alloc_state_path=alloc_state_path)
            port = sbs.allocate_port(node.node_id, state_path=state_path,
                                     alloc_state_path=alloc_state_path)
        binding = sbs.StreamBinding(
            binding_id=bid, node_id=node.node_id, sensor=sensor,
            mode=sbs.StreamMode.REMOTE_PRODUCER,
            transport=sbs.StreamTransport(rtp_port=port, payload_type=payload_type, codec=codec),
            janus=sbs.StreamJanusConfig(mountpoint_id=mp, rtp_iface=gateway_host))
        sbs.upsert_binding(binding, state_path=state_path, alloc_state_path=alloc_state_path)
        from app.services import binding_provision
        from app.services.sensor_lifecycle import MP_DEFAULT_SECRET
        outcome = binding_provision.ensure_janus(binding, mp_secret=MP_DEFAULT_SECRET)
        if not outcome.ok:
            raise RuntimeError(f"ensure_janus {outcome.status}: {outcome.detail}")
        sbs.set_status(binding.binding_id, sbs.StreamStatus.WAITING_FOR_RTP.value, state_path=state_path)
        log.info("bound %s -> mp=%d port=%d iface=%s", binding.binding_id, mp, port, gateway_host)
        return BindOutcome(binding_id=binding.binding_id, rtp_port=port)

    return bind

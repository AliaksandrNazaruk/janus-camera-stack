"""Node lifecycle endpoints (Cycle 5 split): register / add-by-host / check / provision /
rotate-token / maintenance / delete / host-key (get + confirm) / activate-streams.

Handlers stay thin adapters over app.application.stream_bindings use-cases; shared anchors + helpers
are read back through the package object (``_core.``) so the test patch-anchors are preserved verbatim.
"""
from __future__ import annotations

import app.routes.stream_bindings as _core
from fastapi import APIRouter, Depends, HTTPException

from app.core.admin import require_admin
from app.middleware.rate_limit import require_admin_rate_limit
from app.services import node_provisioner, stream_binding_store as sbs
from app.services.audit_log import audit
from app.application.stream_bindings import (
    ActivateLocalCommand,
    AddNodeCommand,
    AddNodeIsLocalGateway,
    CheckNodeCommand,
    ConfirmHostKeyCommand,
    DeleteNodeCommand,
    GetHostKeyCommand,
    HostKeyFingerprintMismatch,
    HostKeyPinReplaceRejected,
    HostKeyUnreachable,
    ListNodesCommand,
    LocalNodeNoHostKey,
    LocalNodeNotRemovable,
    MaintenanceLocalRejected,
    NodeBundleMissing,
    NodeNotFound,
    NodeRegistrationInvalid,
    ProvisionLocalRejected,
    ProvisionNodeCommand,
    RegisterNodeCommand,
    RotateTokenCommand,
    RotateTokenLocalRejected,
    SetMaintenanceCommand,
    activate_local,
    activate_remote,
    add_node as add_node_uc,
    check_node as check_node_uc,
    confirm_host_key as confirm_host_key_uc,
    delete_node as delete_node_uc,
    get_host_key as get_host_key_uc,
    list_nodes as list_nodes_uc,
    provision_node as provision_node_uc,
    register_node as register_node_uc,
    rotate_node_token as rotate_node_token_uc,
    set_maintenance as set_maintenance_uc,
)

from .contracts import (
    HostKeyConfirmRequest,
    MaintenanceRequest,
    NodeAddByHostRequest,
    NodeCheckRequest,
    NodeOut,
    NodeRegisterRequest,
    ProvisionRequest,
    StreamsRequest,
)

router = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(require_admin)])
_RL = Depends(require_admin_rate_limit)   # per-route rate-limit on mutations (same as the old module)


@router.get("/nodes", summary="List nodes (incl. implicit local gateway camera)")
def get_nodes() -> dict:
    nodes = list_nodes_uc(ListNodesCommand(bind_state_path=_core.BIND_STATE_PATH))
    return {"nodes": [_core._node_out(n).model_dump() for n in nodes.values()]}


@router.post("/nodes/register", dependencies=[_RL], summary="Register/update a remote node")
def register_node(req: NodeRegisterRequest) -> NodeOut:
    try:
        n = register_node_uc(RegisterNodeCommand(node_id=req.node_id, host=req.host, role=req.role,
                                                 bind_state_path=_core.BIND_STATE_PATH))
    except NodeRegistrationInvalid as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _core._node_out(n)


@router.post("/nodes", dependencies=[_RL],
             summary="Add a remote node by IP (gateway mints an opaque node_id)")
def add_node(req: NodeAddByHostRequest) -> NodeOut:
    cmd = AddNodeCommand(host=req.host, display_name=req.display_name,
                         gateway_lan_ip=_core.GATEWAY_LAN_IP, bind_state_path=_core.BIND_STATE_PATH)
    try:
        n = add_node_uc(cmd)
    except AddNodeIsLocalGateway as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NodeRegistrationInvalid as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _core._node_out(n)


@router.post("/nodes/check", dependencies=[_RL], summary="Probe a node's agent reachability")
def check_node(req: NodeCheckRequest) -> dict:
    try:
        return check_node_uc(CheckNodeCommand(node_id=req.node_id, bind_state_path=_core.BIND_STATE_PATH))
    except NodeNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/nodes/{node_id}/provision", dependencies=[_RL],
             summary="Deploy the node pipe over SSH (probe -> mux), async")
def provision_node(node_id: str, req: ProvisionRequest) -> dict:
    try:
        res = provision_node_uc(
            ProvisionNodeCommand(node_id=node_id, sudo_password=req.sudo_password,
                                 allow_tofu=req.allow_tofu, bind_state_path=_core.BIND_STATE_PATH,
                                 bundle_tar=_core.NODE_BUNDLE_TAR),
            build_transport=_core._transport_for, spawn_op=_core._spawn_node_op)
    except NodeNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ProvisionLocalRejected as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NodeBundleMissing as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"node_id": node_id, "host": res.host, "started": True, "operation_id": res.operation_id,
            "poll": "GET /api/v1/admin/nodes (watch provision_state)",
            "operation": f"GET /api/v1/admin/operations/{res.operation_id}"}


@router.post("/nodes/{node_id}/rotate-token", dependencies=[_RL],
             summary="Rotate the node-agent token (push new token, restart only the agent), async")
def rotate_node_token(node_id: str, req: ProvisionRequest) -> dict:
    try:
        res = rotate_node_token_uc(
            RotateTokenCommand(node_id=node_id, sudo_password=req.sudo_password,
                               bind_state_path=_core.BIND_STATE_PATH),
            build_transport=_core._transport_for, spawn_op=_core._spawn_node_op)
    except NodeNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RotateTokenLocalRejected as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"node_id": node_id, "host": res.host, "started": True, "operation_id": res.operation_id,
            "operation": f"GET /api/v1/admin/operations/{res.operation_id}"}


@router.post("/nodes/{node_id}/maintenance", dependencies=[_RL],
             summary="Pause/resume FDIR for a node while servicing its hardware")
def set_node_maintenance(node_id: str, req: MaintenanceRequest) -> NodeOut:
    try:
        n = set_maintenance_uc(SetMaintenanceCommand(node_id=node_id, enabled=req.enabled,
                                                     bind_state_path=_core.BIND_STATE_PATH))
    except NodeNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except MaintenanceLocalRejected as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _core._node_out(n)


@router.delete("/nodes/{node_id}", dependencies=[_RL],
               summary="Forget a host: tear down its bindings/mountpoints/firewall + drop its key/token")
def delete_node(node_id: str, deprovision: bool = False) -> dict:
    """Remove a remote host from the GATEWAY (the MVP scope): destroy each of its
    Janus mountpoints, delete the node row + all its bindings, drop the pinned host
    key + the 0600 agent token, then reconcile the firewall so the now-stale
    per-node ACCEPT rules are removed. ``deprovision=true`` ADDITIONALLY best-effort
    asks the node-agent to stop its encoders first (the node keeps the bundle; this
    just stops live streams) — never fatal if the node is unreachable."""
    cmd = DeleteNodeCommand(node_id=node_id, deprovision=deprovision,
                            bind_state_path=_core.BIND_STATE_PATH, alloc_state_path=_core.ALLOC_STATE_PATH)
    try:
        result = delete_node_uc(cmd)
    except NodeNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except LocalNodeNotRemovable as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"node_id": result.node_id, "removed": result.removed,
            "removed_bindings": result.removed_bindings,
            "destroyed_mountpoints": result.destroyed_mountpoints,
            "firewall_reconciled": result.firewall_reconciled, "deprovisioned": result.deprovisioned}


@router.get("/nodes/{node_id}/host-key",
            summary="Capture the node's SSH host-key fingerprint for out-of-band verification")
def get_node_host_key(node_id: str) -> dict:
    """Capture (ssh-keyscan) the node's host key and return its SHA256 fingerprint
    so the operator can compare it against `ssh-keygen -lf
    /etc/ssh/ssh_host_ed25519_key.pub` on the node console. INFORMATIONAL — never
    pins; pinning happens only via the confirm endpoint on a verified match."""
    try:
        return get_host_key_uc(GetHostKeyCommand(node_id=node_id, bind_state_path=_core.BIND_STATE_PATH),
                               capture_host_key=_core.capture_host_key,
                               fingerprint_fn=_core.host_key_fingerprint)
    except NodeNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except LocalNodeNoHostKey as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HostKeyUnreachable as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/nodes/{node_id}/host-key/confirm", dependencies=[_RL],
             summary="Pin the node's SSH host key after out-of-band fingerprint confirmation")
def confirm_node_host_key(node_id: str, req: HostKeyConfirmRequest) -> dict:
    """Capture the node's host key FRESH, compute its fingerprint, and pin it ONLY
    if it equals the operator-supplied (out-of-band-verified) fingerprint. No match
    → reject, nothing pinned. Capturing fresh at confirm closes the capture→confirm
    TOCTOU; the operator's fingerprint — not first contact — is the trust anchor."""
    try:
        return confirm_host_key_uc(
            ConfirmHostKeyCommand(node_id=node_id, expected_fingerprint=req.expected_fingerprint,
                                  force=req.force, bind_state_path=_core.BIND_STATE_PATH),
            capture_host_key=_core.capture_host_key, fingerprint_fn=_core.host_key_fingerprint)
    except NodeNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except LocalNodeNoHostKey as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HostKeyUnreachable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except (HostKeyFingerprintMismatch, HostKeyPinReplaceRejected) as e:
        raise HTTPException(status_code=409, detail=str(e))


# ── local (cam10) sensor activation ───────────────────────────────────
# The local gateway camera activates through the SAME verb as remote nodes
# (POST /nodes/{id}/streams) but SYNCHRONOUSLY: cam10 has no SSH/provision/poll
# store, and a fire-and-forget task would lose LifecycleErrors + render a failed
# color stream as online (review C1). Mode stays LOCAL_PRODUCER — only the
# allocator projection changes; no REMOTE_PRODUCER binding is ever created.


@router.post("/nodes/{node_id}/streams", dependencies=[_RL],
             summary="Activate streams on a node (remote: async SSH; local cam10: sync)")
def activate_node_streams(node_id: str, req: StreamsRequest) -> dict:
    bad = [s for s in req.sensors if s not in ("color", "depth", "ir1", "ir2")]
    if bad:
        raise HTTPException(status_code=400, detail=f"invalid sensors: {bad}")
    # Local gateway camera: same verb, SYNCHRONOUS, and BEFORE _node_for_provision
    # (which 400s cam10 + 503s on a bundle local does not need — review M3).
    if node_id == sbs.LOCAL_NODE_ID:
        return activate_local(ActivateLocalCommand(sensors=req.sensors, alloc_state_path=_core.ALLOC_STATE_PATH))

    # Remote: build the SSH transport (shared host-key/LAN helpers — 400/404/412/503 stay here),
    # then fire the activate+firewall op through the durable runner (async, journal-tracked).
    gw = _core._require_lan_ipv4(req.gateway_host, "gateway_host")   # review H1
    node = _core._node_for_provision(node_id)
    transport = _core._transport_for(node, req.sudo_password)
    binder = node_provisioner.make_gateway_binder(
        gw, state_path=_core.BIND_STATE_PATH, alloc_state_path=_core.ALLOC_STATE_PATH)
    op_id = _core._spawn_node_op(node_id, "activate", activate_remote, node_id,
                                 transport=transport, sensors=req.sensors, gateway_host=gw, binder=binder,
                                 bind_state_path=_core.BIND_STATE_PATH, alloc_state_path=_core.ALLOC_STATE_PATH)
    audit("stream_bindings.node.activate_streams", {"node_id": node_id, "sensors": req.sensors})
    return {"node_id": node_id, "sensors": req.sensors, "started": True, "operation_id": op_id,
            "poll": "GET /api/v1/admin/stream-bindings",
            "operation": f"GET /api/v1/admin/operations/{op_id}"}

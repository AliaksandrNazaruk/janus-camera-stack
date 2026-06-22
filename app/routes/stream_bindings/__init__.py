"""G6 — Gateway admin API for nodes + stream bindings.

CRUD over the StreamBinding topology (docs/design/STREAM_BINDING_MODEL.md,
GATEWAY_REMOTE_RTP_MODE.md). Admin-gated (X-Admin-Token via the router
dependency) + rate-limited on mutations + audited.

`ensure-janus` only PREPARES Janus to receive RTP. For a remote binding the
host-scoped, fail-closed firewall (G2-sec) is a SEPARATE prerequisite before
live LAN exposure — this API does not open any firewall rule.

Cycle 5 split: the 765-line module became this package. THIS module (`__init__`) is the SHARED
core — the anchors, helpers, and the assembled `router`; the route handlers live in cohesive
submodules (`nodes`, `bindings`, `operations`, `fleet`) and read the patchable anchors back through
this package object (e.g. ``_core.BIND_STATE_PATH``) so a test's
``monkeypatch.setattr(stream_bindings, "BIND_STATE_PATH", tmp)`` still redirects every handler.
DTOs live in `contracts`. The public router + URLs + auth + audit + journal behavior are unchanged.
"""
from __future__ import annotations

import ipaddress
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.middleware.rate_limit import require_admin_rate_limit
from app.services import binding_provision, janus_admin, mountpoint_allocator  # noqa: F401 — re-exported anchors
from app.services import node_client  # noqa: F401 — patch anchor: use-case oracles monkeypatch stream_bindings.node_client (shared module)
from app.services import node_provisioner, node_operation_runner
from app.services import operation_journal
from app.services.operation_journal import OperationConflict
from app.services import stream_binding_store as sbs
from app.services.ssh_transport import SSHTransport, capture_host_key, host_key_fingerprint
from app.services.node_transport import build_transport, HostKeyNotConfirmed

from app.services.audit_log import audit
from app.application.stream_bindings import (
    NodeBundleMissing,
    NodeNotFound,
    ProvisionLocalRejected,
    resolve_provision_target,
)

from .contracts import (  # noqa: F401 — re-exported so stream_bindings.<DTO> still resolves
    BindingCreateRequest,
    BindingOut,
    EnsureJanusResponse,
    FdirToggleRequest,
    HostKeyConfirmRequest,
    MaintenanceRequest,
    NodeAddByHostRequest,
    NodeCheckRequest,
    NodeOut,
    NodeRegisterRequest,
    ProvisionRequest,
    StreamsRequest,
    TuningRequest,
)

log = logging.getLogger(__name__)

# Resolved at call time via module globals → tests monkeypatch these (the submodule handlers read
# them back through this package object, so a rebind here reaches every handler).
BIND_STATE_PATH = sbs.DEFAULT_STATE_PATH
ALLOC_STATE_PATH = mountpoint_allocator.DEFAULT_STATE_PATH
# Provisioner config is owned by the service layer (no os.getenv in routes — see
# test_architecture_fitness); re-exported here so they stay monkeypatchable in tests.
NODE_BUNDLE_TAR = node_provisioner.NODE_BUNDLE_TAR
GATEWAY_LAN_IP = node_provisioner.GATEWAY_LAN_IP
NODE_SSH_USER = node_provisioner.NODE_SSH_USER
NODE_SSH_KEY = node_provisioner.NODE_SSH_KEY

_RL = Depends(require_admin_rate_limit)
_SENSOR_RE = r"^(color|depth|ir1|ir2)$"

# A corrupt operations.json fails CLOSED (H3): the journal quarantines the bad file and raises
# JournalCorrupt; the route surfaces 503 rather than proceed on a silently-empty (guard-less) journal.
_OPS_JOURNAL_CORRUPT_503 = "operation journal was corrupt and has been quarantined; retry"


# Long node ops (provision / activate / rotate) run via services/node_operation_runner: a daemon
# thread (immune to the response lifecycle — Starlette BackgroundTasks silently dropped the work,
# Bug A) with a DURABLE per-node guard + journal (operations.json). One op per node → 409;
# restart-orphaned ops are reaped on startup. (Replaces the old in-memory _inflight dict.)


def _require_lan_ipv4(host: str, what: str = "host") -> str:
    """Validate + normalise a LAN IPv4 (review H1 — defence-in-depth on top of
    shlex.quote, so admin input can't smuggle shell metachars into the node SSH
    command even if quoting regresses). Rejects non-IPv4, loopback, multicast,
    unspecified. Returns the canonical string."""
    try:
        ip = ipaddress.ip_address((host or "").strip())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{what} must be a valid IPv4, got {host!r}")
    if ip.version != 4 or ip.is_loopback or ip.is_multicast or ip.is_unspecified:
        raise HTTPException(status_code=400,
                            detail=f"{what} must be a non-loopback IPv4 LAN address, got {host!r}")
    return str(ip)


def _operations_path() -> Path:
    """The durable operations journal lives beside the binding store, so the test path-redirect that
    points BIND_STATE_PATH at a tmp dir also covers operations.json (writes AND the read API)."""
    return BIND_STATE_PATH.parent / "operations.json"


def _spawn_node_op(node_id: str, op: str, fn, *args, **kwargs) -> str:
    """Thin wrapper over node_operation_runner.run — maps the durable OperationConflict to 409 and
    RETURNS the durable operation_id (uuid4) so the handler can surface it to the client (H1).
    The journal lives beside the store in use (BIND_STATE_PATH dir) so tests' path-redirect covers it."""
    try:
        return node_operation_runner.run(node_id, op, fn, *args, ops_path=_operations_path(), **kwargs)
    except OperationConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except operation_journal.JournalCorrupt:
        raise HTTPException(status_code=503, detail=_OPS_JOURNAL_CORRUPT_503)


def _node_out(n: sbs.NodeEntry) -> NodeOut:
    return NodeOut(node_id=n.node_id, host=n.host, role=n.role,
                   reachability=n.reachability, ordinal=n.ordinal,
                   serial=n.serial, display_name=n.display_name,
                   provision_state=n.provision_state,
                   host_key_pinned=bool(getattr(n, "host_key", None)),
                   maintenance=bool(getattr(n, "maintenance", False)),
                   last_error=getattr(n, "last_error", None),
                   last_checked_at=getattr(n, "last_checked_at", None))


def _binding_out(b: sbs.StreamBinding, *, rtp_age_ms: Optional[int] = None) -> BindingOut:
    return BindingOut(
        binding_id=b.binding_id, node_id=b.node_id, sensor=b.sensor, mode=b.mode.value,
        mountpoint_id=b.janus.mountpoint_id, rtp_port=b.transport.rtp_port,
        rtp_iface=b.janus.rtp_iface, codec=b.transport.codec,
        payload_type=b.transport.payload_type, status=b.status,
        fdir_enabled=b.fdir.enabled, rtp_age_ms=rtp_age_ms)


def _node_for_provision(node_id: str) -> sbs.NodeEntry:
    """HTTP-boundary forwarder over the shared resolve_provision_target use-case (A-02): maps the
    provisionability policy's domain errors → 404 / 400 / 503. Kept as a module-level function for
    the activate path + the local_activate ``_boom`` patch anchor."""
    try:
        return resolve_provision_target(node_id, bind_state_path=BIND_STATE_PATH,
                                        bundle_tar=NODE_BUNDLE_TAR)
    except NodeNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ProvisionLocalRejected as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NodeBundleMissing as e:
        raise HTTPException(status_code=503, detail=str(e))


def _transport_for(node: sbs.NodeEntry, sudo_password, *, allow_tofu: bool = False) -> SSHTransport:
    """HTTP-boundary forwarder over services.node_transport.build_transport (A-02).

    Forwards this module's monkeypatchable host-key collaborators (test oracles:
    ``capture_host_key``, ``host_key_fingerprint``, ``sbs``, ``audit``) and maps the domain
    ``HostKeyNotConfirmed`` → 412. The host-key-confirmation policy + transport construction
    (including the audited TOFU pin) now live in the adapter."""
    try:
        return build_transport(
            node, sudo_password, allow_tofu=allow_tofu,
            capture_host_key=capture_host_key, fingerprint_fn=host_key_fingerprint,
            store=sbs, state_path=BIND_STATE_PATH, audit_fn=audit,
            ssh_user=NODE_SSH_USER, ssh_key=NODE_SSH_KEY)
    except HostKeyNotConfirmed as e:
        raise HTTPException(status_code=412, detail=str(e))


def _rtp_age(mp_id: int) -> Optional[int]:
    """Media freshness for a mountpoint (None if Janus can't tell). Best-effort —
    a Janus hiccup must never 500 the topology list."""
    try:
        from app.services import janus
        v = janus.janus_summary(mp_id).get("video_age_ms")
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    except Exception:
        return None


def _get_binding_or_404(binding_id: str) -> sbs.StreamBinding:
    b = sbs.get_binding(binding_id, state_path=BIND_STATE_PATH, alloc_state_path=ALLOC_STATE_PATH)
    if b is None:
        raise HTTPException(status_code=404, detail=f"unknown binding {binding_id}")
    return b


# ── assemble the public router from the cohesive submodules ────────────────────
# Submodules are imported AFTER the anchors/helpers above exist (they read those back through this
# package object at call time). The aggregator carries no prefix/deps; each submodule router holds
# the /api/v1/admin prefix + require_admin so the mounted surface is byte-for-byte the old one.
router = APIRouter()

from . import nodes, operations, fleet, bindings  # noqa: E402  (after shared core is defined)

router.include_router(nodes.router)
router.include_router(operations.router)
router.include_router(fleet.router)
router.include_router(bindings.router)

# Re-export the handlers tests call directly (test_reconcile_*: sb.reconcile_drift / run_once).
from .fleet import reconcile_drift, reconcile_janus_run_once  # noqa: E402,F401

"""G2 — provision a Janus mountpoint from a StreamBinding (local or remote).

Bridges stream_binding_store → janus_admin. The binding's `rtp_iface` is
threaded into `janus_admin.create_mountpoint` (the `iface` param already
exists; default 127.0.0.1) so a remote binding binds the RTP listener to the
gateway LAN IP instead of loopback. Design: GATEWAY_REMOTE_RTP_MODE.md §2.

Idempotency is a STATE CONTRACT, not a string-match (review R4-M7):
  • mountpoint absent            → create it          → CREATED
  • mountpoint exists, same port → no-op              → EXISTS
  • mountpoint exists, diff port → refuse, surface it → CONFLICT
  • any other Janus error                              → FAILED

NB: this only PREPARES Janus to receive RTP. It does NOT open any firewall
rule — for remote bindings the host-scoped, fail-closed firewall step
(GATEWAY §4.2) is a separate, ordered operation that must precede live
exposure. ensure_janus never widens network reachability by itself beyond the
socket bind implied by `iface`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict

from app.services import janus_admin
from app.services import stream_binding_store as sbs
from app.services.stream_binding_store import StreamBinding

log = logging.getLogger(__name__)


class ProvisionStatus(str, Enum):
    CREATED = "created"
    EXISTS = "exists"
    CONFLICT = "conflict"
    FAILED = "failed"


@dataclass(frozen=True)
class ProvisionOutcome:
    status: ProvisionStatus
    mountpoint_id: int
    iface: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (ProvisionStatus.CREATED, ProvisionStatus.EXISTS)


def is_already_exists(err: object) -> bool:
    """Robust 'mountpoint id already in use' detection (replaces the brittle
    precedence-shaped check). Janus reports it as error 456 / 'already exists'."""
    m = str(err).lower()
    return "already exists" in m or ("exist" in m and "456" in m)


def _reconcile_existing(binding: StreamBinding, *, detail: str) -> ProvisionOutcome:
    """An mountpoint with this id already exists. Best-effort divergence check:
    matching RTP port → benign EXISTS; a different port → CONFLICT (reject)."""
    j, t = binding.janus, binding.transport
    try:
        for mp in janus_admin.list_mountpoints():
            if int(mp.get("id", -1)) != j.mountpoint_id:
                continue
            existing_port = mp.get("video_port", mp.get("port"))
            if existing_port is not None and int(existing_port) != t.rtp_port:
                return ProvisionOutcome(
                    ProvisionStatus.CONFLICT, j.mountpoint_id, j.rtp_iface,
                    f"mountpoint {j.mountpoint_id} exists on port {existing_port}, "
                    f"binding wants {t.rtp_port}")
            break
    except janus_admin.JanusAdminError as e:
        log.info("ensure_janus: could not introspect existing mp %d (%s) — treating as EXISTS",
                 j.mountpoint_id, e)
    return ProvisionOutcome(ProvisionStatus.EXISTS, j.mountpoint_id, j.rtp_iface, detail)


def ensure_janus(binding: StreamBinding, *, mp_secret: str) -> ProvisionOutcome:
    """Create (or confirm) the Janus mountpoint described by `binding`,
    binding the RTP listener to `binding.janus.rtp_iface`."""
    j, t = binding.janus, binding.transport
    try:
        janus_admin.create_mountpoint(
            mp_id=j.mountpoint_id,
            rtp_port=t.rtp_port,
            description=binding.binding_id,
            mp_secret=mp_secret,
            codec=t.codec,
            payload_type=t.payload_type,
            iface=j.rtp_iface,
        )
        log.info("ensure_janus: created mp %d iface=%s port=%d for %s",
                 j.mountpoint_id, j.rtp_iface, t.rtp_port, binding.binding_id)
        return ProvisionOutcome(ProvisionStatus.CREATED, j.mountpoint_id, j.rtp_iface)
    except janus_admin.JanusAdminError as e:
        if is_already_exists(e):
            return _reconcile_existing(binding, detail=str(e))
        log.warning("ensure_janus: create failed for %s: %s", binding.binding_id, e)
        return ProvisionOutcome(ProvisionStatus.FAILED, j.mountpoint_id, j.rtp_iface, str(e))


@dataclass(frozen=True)
class ReconcileSummary:
    created: int
    existing: int
    failed: int
    skipped: int
    outcomes: Dict[str, ProvisionOutcome]

    @property
    def ok(self) -> bool:
        return self.failed == 0


def reconcile_janus(*, mp_secret: str, state_path=sbs.DEFAULT_STATE_PATH,
                    alloc_state_path=None) -> ReconcileSummary:
    """Ensure every ENABLED stored `remote_producer` binding has its Janus mountpoint.

    Idempotent (existing mountpoints are no-op EXISTS), so this is safe to run on
    every startup and on demand. It is the gateway-side recovery for the case the
    per-binding monitor cannot reach on its own: a Janus restart drops all runtime
    mountpoints, and a fresh L4 has no `ever_healthy` memory to drive the monitor's
    edge — without this sweep those bindings would sit WAITING_FOR_RTP forever
    (UNIFIED_FDIR §4.7). A remote binding whose mountpoint falls in the local-owned
    range (< REMOTE_MP_MIN) is refused fail-closed — a hand-edited file must never
    make us touch a cam10-reserved id (mirrors the monitor's §4.6 guard, widened to
    the whole local range). A binding with FDIR disabled (`fdir.enabled=False` — the
    operator-Stop marker) IS excluded (skipped): a stopped stream's Janus listener is
    not re-created, mirroring the remote monitor's evaluate() gate so an operator Stop
    is honored uniformly by both this startup sweep and the live monitor.
    Per-binding failures are isolated — one bad binding never aborts the sweep."""
    kwargs = {"state_path": state_path}
    if alloc_state_path is not None:
        kwargs["alloc_state_path"] = alloc_state_path
    bindings = sbs.list_bindings(**kwargs)
    created = existing = failed = skipped = 0
    outcomes: Dict[str, ProvisionOutcome] = {}
    for bid, b in bindings.items():
        if b.mode != sbs.StreamMode.REMOTE_PRODUCER:
            continue
        if not b.desired_up:
            # Operator Stop (desired_up=False) -> leave the stream down: do NOT re-create its Janus
            # listener. desired_up is the Start/Stop intent, now SEPARATE from fdir.enabled
            # (recovery): a desired-up binding's mountpoint is maintained even with FDIR off, so it
            # survives a gateway restart. (Legacy rows derive desired_up from fdir.enabled, so this
            # is unchanged until Start/Stop is split.)
            skipped += 1
            continue
        if b.janus.mountpoint_id < sbs.REMOTE_MP_MIN:
            log.critical("reconcile_janus: remote binding %s has mountpoint %d in the "
                         "local-owned range (< %d) — skipping (fail-closed)",
                         bid, b.janus.mountpoint_id, sbs.REMOTE_MP_MIN)
            skipped += 1
            continue
        try:
            out = ensure_janus(b, mp_secret=mp_secret)
        except Exception as e:  # isolate: a single binding must not abort the sweep
            log.warning("reconcile_janus: ensure %s raised: %s", bid, e)
            out = ProvisionOutcome(ProvisionStatus.FAILED, b.janus.mountpoint_id,
                                   b.janus.rtp_iface, str(e))
        outcomes[bid] = out
        if out.status == ProvisionStatus.CREATED:
            created += 1
        elif out.status == ProvisionStatus.EXISTS:
            existing += 1
        else:
            failed += 1
    if created or failed or skipped:
        log.warning("reconcile_janus: created=%d existing=%d failed=%d skipped=%d",
                    created, existing, failed, skipped)
    return ReconcileSummary(created=created, existing=existing, failed=failed,
                            skipped=skipped, outcomes=outcomes)


def run_janus_reconcile_once(*, mp_secret: str,
                             state_path=sbs.DEFAULT_STATE_PATH,
                             alloc_state_path=None,
                             rtp_age_fn=None) -> dict:
    """Explicit, operator-triggered RUN-ONCE Janus convergence (ADR R1/R2/R3/R4/R6/R9).

    Creates the missing Janus mountpoint for every ACTIVE remote binding, and does
    NOTHING else: never restarts Janus / rs-stream / a node-agent, never applies a
    firewall rule, never destroys an unexpected mountpoint, never provisions/removes a
    node, never touches secrets. The ensure set is driven by the read-only drift report's
    ``missing_janus_mountpoint`` classification, so the skip predicate (fdir-disabled /
    configured_offline / maintenance) is SHARED with the report — operator-stopped and
    maintenance bindings are skipped; orphan mountpoints are reported but left intact.
    Idempotent (R9): a second call with no new drift creates 0. Returns before/after drift.
    """
    from app.services import reconcile_drift as drift
    kw = {"state_path": state_path}
    if alloc_state_path is not None:
        kw["alloc_state_path"] = alloc_state_path
    bindings = sbs.list_bindings(**kw)              # corrupt store -> StoreCorruptionError -> 503
    nodes = sbs.list_nodes(state_path=state_path)
    maint = frozenset(nid for nid, n in nodes.items() if getattr(n, "maintenance", False))

    def _live_ids():
        return [int(m["id"]) for m in janus_admin.list_mountpoints()
                if isinstance(m, dict) and "id" in m]

    before = drift.compute_drift(bindings, _live_ids(), rtp_age_fn=rtp_age_fn,
                                 maintenance_node_ids=maint)   # janus down -> JanusAdminError -> 503

    created = existing = failed = skipped = 0
    outcomes: Dict[str, str] = {}
    for it in before["bindings"]:
        c = it["classification"]
        if c == drift.STOPPED_BY_OPERATOR:
            skipped += 1
            continue
        if c in (drift.IN_SYNC, drift.STALE_RTP):    # mountpoint already present -> no-op
            existing += 1
            continue
        if c != drift.MISSING_JANUS_MOUNTPOINT:
            continue
        b = bindings[it["binding_id"]]
        if b.janus.mountpoint_id < sbs.REMOTE_MP_MIN:   # R6 ownership guard — fail closed
            failed += 1
            outcomes[it["binding_id"]] = "refused_local_range"
            continue
        try:
            out = ensure_janus(b, mp_secret=mp_secret)
        except Exception as e:                       # per-binding isolation (R8)
            failed += 1
            outcomes[it["binding_id"]] = f"error:{e}"
            continue
        if out.status == ProvisionStatus.CREATED:
            created += 1
            outcomes[it["binding_id"]] = "created"
        elif out.status == ProvisionStatus.EXISTS:
            existing += 1
            outcomes[it["binding_id"]] = "exists"
        else:
            failed += 1
            outcomes[it["binding_id"]] = out.status.value

    after = drift.compute_drift(bindings, _live_ids(), rtp_age_fn=rtp_age_fn,
                                maintenance_node_ids=maint)
    return {
        "ok": failed == 0,
        "action": "janus_reconcile_run_once",
        "dry_run": False,
        "before": {"drift": before["drift"], "counts": before["counts"]},
        "result": {"created": created, "existing": existing,
                   "skipped": skipped, "failed": failed},
        "after": {"drift": after["drift"], "counts": after["counts"]},
        "outcomes": outcomes,
    }

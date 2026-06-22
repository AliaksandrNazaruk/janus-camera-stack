"""G5 — isolated monitor for REMOTE producer bindings. CONVERGES (bring up / restart when the node
is reachable — replacing node autostart) and ESCALATES (alert) a stream, both gated on
`desired_up AND fdir.enabled`: FDIR is the autonomous keep-alive switch — it OWNS recovery (see
docs/design/FDIR_RECOVERY_SEMANTICS.md). The mountpoint itself is maintained on `desired_up` alone
(binding_provision.reconcile_janus). The node-lifecycle contract (local + remote) is
docs/NODE_CONTRACT.md.

Separate from the global watchdog/ladder BY CONSTRUCTION. Its only escalation is
{mark binding degraded, emit a Domain.PRODUCER alert, NodeClient.restart_stream}.
It must NOT reference recovery_ladder.get_ladder, the reboot counter, the global
fdir_quiesce arm, or any systemctl/reboot — so a stale/fake/hostile remote stream
can never drive a local destructive action (UNIFIED_FDIR_OVER_STREAM_BINDINGS.md
§4.4). A unit test asserts this module references none of those symbols.

Detection reuses janus.janus_summary(mountpoint_id)["video_age_ms"] per remote
binding. The binding's `mode` is known from the store BEFORE the age check, so
remote staleness is never misrouted to Domain.JANUS (the undecidable-classification
hazard, review B2).

Alerting is edge-triggered with a heartbeat: a binding that was never healthy
(configured-offline / waiting for the producer to start) is NOT a fault and is
not alerted; only a regression (online → stale) alerts, then re-alerts at most
once per heartbeat — so a permanently-dead remote binding cannot flood the event
ring and evict cam10's events (review m4).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

from app.core.settings import get_settings
from app.services import janus, node_client
from app.services import stream_binding_store as sbs
from app.services.fdir_events import Domain, RecoveryAction, Severity, emit

log = logging.getLogger("remote_stream_monitor")

# Re-alert cadence while a binding stays degraded (after the initial edge alert).
HEARTBEAT_SEC = float(os.getenv("REMOTE_MONITOR_HEARTBEAT_SEC", "300"))
# Convergence (bring-up / restart of a managed stream) retries on a SHORT cadence — independent
# of the 300s fault-escalation heartbeat — so a node that becomes reachable again has its streams
# brought up within seconds, not minutes. Bounded so an un-startable node is not hammered.
BRINGUP_THROTTLE_SEC = float(os.getenv("REMOTE_MONITOR_BRINGUP_SEC", "20"))

_stop_event = threading.Event()
_state: Dict[str, "_BindingState"] = {}
_state_lock = threading.Lock()


@dataclass
class _BindingState:
    ever_healthy: bool = False
    healthy: Optional[bool] = None
    last_alert_mono: float = 0.0
    last_converge_mono: float = 0.0


@dataclass(frozen=True)
class MonitorDecision:
    status: str        # StreamStatus value to persist
    alert: bool        # emit Domain.PRODUCER + attempt remote restart this tick
    healthy: bool


def evaluate(video_age_ms: object, *, stale_ms: int, now_mono: float,
             prev: Optional[_BindingState], heartbeat_sec: float = HEARTBEAT_SEC) -> MonitorDecision:
    """Pure decision core (no I/O) — the unit-tested heart of the monitor.

    healthy           → ONLINE, no alert
    never-yet-healthy → WAITING_FOR_RTP, no alert (configured-offline is not a fault)
    regressed         → DEGRADED, alert on the online→stale edge + once per heartbeat
    """
    healthy = isinstance(video_age_ms, (int, float)) and not isinstance(video_age_ms, bool) \
        and video_age_ms <= stale_ms
    if healthy:
        return MonitorDecision(sbs.StreamStatus.ONLINE.value, alert=False, healthy=True)

    ever_healthy = bool(prev and prev.ever_healthy)
    if not ever_healthy:
        # Producer has never delivered — expected for a configured-but-offline
        # remote binding (e.g. .55 not started). Not a fault; no alert.
        return MonitorDecision(sbs.StreamStatus.WAITING_FOR_RTP.value, alert=False, healthy=False)

    just_regressed = bool(prev and prev.healthy is True)
    last_alert = prev.last_alert_mono if prev else 0.0
    heartbeat_due = (now_mono - last_alert) >= heartbeat_sec
    return MonitorDecision(sbs.StreamStatus.DEGRADED.value,
                           alert=just_regressed or heartbeat_due, healthy=False)


def _apply(binding: sbs.StreamBinding, video_age_ms: object, decision: MonitorDecision,
           *, state_path, do_recover: bool = False, do_alert: bool = False) -> None:
    """Side effects for one binding. Status is persisted ALWAYS. Two axes (unified node lifecycle):
    ``do_recover`` runs the recovery action (ensure the gateway mountpoint, then restart the node
    encoder) and ``do_alert`` emits the PRODUCER escalation event — BOTH gated by the caller on
    ``desired_up AND fdir.enabled``: FDIR is the autonomous keep-alive switch and OWNS recovery. So
    FDIR off means "not auto-managed — keep the mountpoint, restart by hand", not "still recover
    silently". The mountpoint is maintained on ``desired_up`` alone elsewhere. NEVER
    local-destructive."""
    try:
        sbs.set_status(binding.binding_id, decision.status, state_path=state_path)
    except Exception as e:  # binding may have been removed mid-tick
        log.debug("set_status(%s) skipped: %s", binding.binding_id, e)
    # Hard guard: only remote bindings reach here, and only the remote stub is used.
    assert binding.mode == sbs.StreamMode.REMOTE_PRODUCER, "non-remote binding in remote monitor"
    if do_alert:
        signal = f"rtp_age_ms={video_age_ms}"
        emit(Domain.PRODUCER, Severity.WARN, signal, RecoveryAction.NONE, "degraded",
             binding_id=binding.binding_id, node_id=binding.node_id, sensor=binding.sensor)
    if not do_recover:
        return
    # G5.3 (UNIFIED_FDIR §4.7): reconcile to desired — ensure the gateway
    # mountpoint FIRST. A Janus restart drops the runtime mountpoint, so the node
    # encoder restart_stream() targets is healthy but its RTP lands nowhere; only a
    # gateway-side ensure recovers that. ensure_janus is additive + self-targeted
    # (the binding's own pre-allocated id from the store) — never a Janus *process*
    # restart, never touches cam10. If the mountpoint had to be CREATED it was the
    # fault and the node is fine, so skip the disruptive node restart; if it already
    # EXISTED but is still stale, the fault is upstream → restart the node. Best
    # effort: a re-ensure error must not block the node restart.
    # Both-down (mountpoint absent AND encoder dead) still recovers: a *crashed*
    # encoder is auto-restarted by the node's own systemd (rs-stream@ Restart=always,
    # RestartSec=2); only a *hung* encoder coinciding with an absent mountpoint waits
    # for the next heartbeat — and even that is strictly better than pre-G5.3, which
    # never recreated the mountpoint at all.
    mp_recreated = False
    try:
        from app.services import binding_provision
        from app.services.sensor_lifecycle import MP_DEFAULT_SECRET
        out = binding_provision.ensure_janus(binding, mp_secret=MP_DEFAULT_SECRET)
        mp_recreated = out.status == binding_provision.ProvisionStatus.CREATED
        log.info("remote re-ensure %s: %s (mp %s)", binding.binding_id,
                 out.status.value, binding.janus.mountpoint_id)
    except Exception as e:
        log.warning("remote re-ensure %s failed: %s", binding.binding_id, e)
    if mp_recreated:
        log.info("remote %s: mountpoint was missing and is recreated — "
                 "skipping node restart (node was not the fault)", binding.binding_id)
        return
    client = node_client.get_node_client(binding.node_id, state_path=state_path)
    res = client.restart_stream(binding.node_id, binding.sensor)
    log.info("remote restart %s: ok=%s — %s", binding.binding_id, res.ok, res.detail)


def _node_reachable(node) -> bool:
    """Cheap live agent health probe (HTTP /healthz on :8901) — gates gateway-driven bring-up so the
    monitor never hammers/blocks on an unreachable node. False for an unknown node or any error."""
    if node is None:
        return False
    try:
        return bool(node_client.probe_agent(node.host)["reachable"])
    except Exception:  # noqa: BLE001 — a probe failure must never break the tick
        return False


def tick(*, state_path=sbs.DEFAULT_STATE_PATH,
         alloc_state_path=None) -> int:
    """One monitor pass over all remote bindings. Returns the count examined.
    Public for tests; the loop just calls this on an interval."""
    settings = get_settings()
    stale_ms = settings.watchdog_stale_ms
    kwargs = {"state_path": state_path}
    if alloc_state_path is not None:
        kwargs["alloc_state_path"] = alloc_state_path
    bindings = sbs.list_bindings(**kwargs)
    nodes = sbs.list_nodes(state_path=state_path)
    now = time.monotonic()
    examined = 0
    for bid, b in bindings.items():
        if b.mode != sbs.StreamMode.REMOTE_PRODUCER:
            continue
        node = nodes.get(b.node_id)
        if node is not None and node.maintenance:
            # Operator is servicing this host (camera/USB/cable) — pause observation AND
            # recovery so a deliberately-down stream raises no recovery + no alert flood
            # (review: maintenance mode). Node-level cousin of the fdir.enabled gate.
            continue
        if b.janus.mountpoint_id < sbs.REMOTE_MP_MIN:
            # Defense-in-depth (UNIFIED_FDIR §4.6, widened): the store rejects any
            # remote mp < REMOTE_MP_MIN at upsert, but a hand-edited file must never
            # let a remote binding hold an id in the local-owned range (cam10 owns
            # the whole pool below REMOTE_MP_MIN, not just janus_mount_id) and be
            # probed/recovered as if local.
            log.critical("remote binding %s has mountpoint %d in the local-owned range "
                         "(< %d) — refusing to monitor (fail-closed)",
                         bid, b.janus.mountpoint_id, sbs.REMOTE_MP_MIN)
            continue
        # Status is OBSERVED + persisted for EVERY remote binding so the view reflects reality.
        # Two axes (unified node lifecycle). FDIR is the autonomous keep-alive switch and OWNS
        # recovery (docs/design/FDIR_RECOVERY_SEMANTICS.md), so BOTH actions gate on
        # `desired_up AND fdir.enabled`:
        #   CONVERGE (ensure mountpoint + restart node) — bring up a never-started one / restart a
        #     degraded one whenever the node is REACHABLE. This is what replaces node autostart.
        #   ESCALATE (emit the PRODUCER alert event) — on a genuine regression.
        # FDIR off means "not auto-managed" (mountpoint kept by reconcile_janus on desired_up;
        # operator restarts by hand), NOT "recover silently".
        examined += 1
        summary = janus.janus_summary(b.janus.mountpoint_id)
        age = summary.get("video_age_ms")
        prev = _state.get(bid)
        healthy_now = (isinstance(age, (int, float)) and not isinstance(age, bool)
                       and age <= stale_ms)
        just_regressed = bool(prev and prev.healthy is True)
        # Convergence retries on the SHORT bring-up cadence (prompt once a node is back); escalation
        # uses the long fault heartbeat. just_regressed converges immediately (online→stale edge).
        converge_due = just_regressed or (now - (prev.last_converge_mono if prev else 0.0)
                                          ) >= BRINGUP_THROTTLE_SEC
        # Probe reachability OUTSIDE the lock, only when convergence could act (don't hammer/block
        # on an unreachable node — it comes up once the node IP is available again).
        converge = (b.desired_up and b.fdir.enabled and not healthy_now and converge_due
                    and _node_reachable(nodes.get(b.node_id)))
        with _state_lock:
            decision = evaluate(age, stale_ms=stale_ms, now_mono=now, prev=prev)
            st = _state.setdefault(bid, _BindingState())
            # decision.alert already carries evaluate()'s heartbeat throttle.
            escalate = b.desired_up and b.fdir.enabled and decision.alert
            if escalate:
                st.last_alert_mono = now
            if converge:
                st.last_converge_mono = now
            st.ever_healthy = st.ever_healthy or decision.healthy
            st.healthy = decision.healthy
        _apply(b, age, decision, state_path=state_path, do_recover=converge, do_alert=escalate)
    return examined


def start_remote_stream_monitor() -> None:
    settings = get_settings()
    if not getattr(settings, "watchdog_enabled", True):
        return
    _stop_event.clear()
    threading.Thread(target=_loop, daemon=True).start()


def _loop() -> None:
    interval = max(1, get_settings().watchdog_interval_sec)
    while not _stop_event.is_set():
        try:
            tick()
        except Exception:
            log.exception("remote_stream_monitor tick error")
        _stop_event.wait(interval)


def stop() -> None:
    _stop_event.set()


def _reset_state_for_tests() -> None:
    with _state_lock:
        _state.clear()

"""G5.1c — RemoteStreamMonitor: pure decision core, edge-triggered alerting,
and the safety invariant (no local-destructive references)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.services import janus
from app.services import remote_stream_monitor as rsm
from app.services import stream_binding_store as sbs
from app.services.fdir_events import Domain
from app.services.stream_binding_store import (
    StreamBinding, StreamJanusConfig, StreamMode, StreamTransport,
)

STALE = 10000


@pytest.fixture(autouse=True)
def _reset():
    rsm._reset_state_for_tests()
    yield
    rsm._reset_state_for_tests()


@pytest.fixture(autouse=True)
def _no_real_recovery(monkeypatch):
    # _apply() routes a degraded remote binding to (1) binding_provision.ensure_janus
    # (gateway-side mountpoint re-ensure) then (2) RealNodeClient.restart_stream (HTTP
    # to the node agent). Keep unit tests off the network on BOTH paths — what's under
    # test is the monitor's status/alert behaviour. Default ensure → EXISTS so the
    # node-restart path still runs (tests override it to CREATED to assert the skip).
    class _Inert:
        def restart_stream(self, *a, **k):
            return rsm.node_client.RestartResult(True, "test-stub")

    monkeypatch.setattr(rsm.node_client, "get_node_client", lambda *a, **k: _Inert())

    from app.services import binding_provision as bp
    monkeypatch.setattr(bp, "ensure_janus",
                        lambda b, **k: bp.ProvisionOutcome(
                            bp.ProvisionStatus.EXISTS, b.janus.mountpoint_id, b.janus.rtp_iface))
    # Reachability is a live agent probe — stub it deterministically (node reachable) so convergence
    # is exercised without network. Tests that need an unreachable node override this.
    monkeypatch.setattr(rsm, "_node_reachable", lambda node: True)


@pytest.fixture
def bind_path(tmp_path):
    return tmp_path / "stream_bindings.json"


@pytest.fixture
def alloc_path(tmp_path):
    return tmp_path / "allocations.json"


def _setup_remote(bind_path, alloc_path, mp=2000, port=5100):
    sbs.upsert_node("cam55", host="192.168.1.55", role="remote_producer", state_path=bind_path)
    b = StreamBinding(
        binding_id="cam55:color", node_id="cam55", sensor="color",
        mode=StreamMode.REMOTE_PRODUCER,
        transport=StreamTransport(rtp_port=port),
        janus=StreamJanusConfig(mountpoint_id=mp, rtp_iface="192.168.1.10"))
    sbs.upsert_binding(b, state_path=bind_path, alloc_state_path=alloc_path, janus_mount_id=1305)
    return b


# ── pure decision core ────────────────────────────────────────────────

def test_evaluate_healthy_is_online_no_alert():
    d = rsm.evaluate(120, stale_ms=STALE, now_mono=0.0, prev=None)
    assert d.healthy and d.status == "online" and d.alert is False


def test_evaluate_never_healthy_is_waiting_no_alert():
    # configured-offline remote (never received RTP) is NOT a fault
    d = rsm.evaluate(None, stale_ms=STALE, now_mono=0.0, prev=None)
    assert d.status == "waiting_for_rtp" and d.alert is False


def test_evaluate_regression_degrades_and_alerts():
    prev = rsm._BindingState(ever_healthy=True, healthy=True, last_alert_mono=0.0)
    d = rsm.evaluate(25000, stale_ms=STALE, now_mono=1.0, prev=prev)
    assert d.status == "degraded" and d.alert is True


def test_evaluate_degraded_respects_heartbeat():
    prev = rsm._BindingState(ever_healthy=True, healthy=False, last_alert_mono=0.0)
    soon = rsm.evaluate(25000, stale_ms=STALE, now_mono=10.0, prev=prev)
    late = rsm.evaluate(25000, stale_ms=STALE, now_mono=rsm.HEARTBEAT_SEC + 1, prev=prev)
    assert soon.alert is False          # within heartbeat — no spam
    assert late.alert is True           # heartbeat elapsed — re-alert


def test_evaluate_bool_is_not_a_valid_age():
    # guard against True/False sneaking in as an int-like age
    d = rsm.evaluate(True, stale_ms=STALE, now_mono=0.0, prev=None)
    assert d.healthy is False


# ── tick integration (mocked Janus) ───────────────────────────────────

def test_tick_online_then_stale_alerts_producer(bind_path, alloc_path, monkeypatch):
    _setup_remote(bind_path, alloc_path)
    captured = []
    monkeypatch.setattr(rsm, "emit", lambda *a, **k: captured.append((a, k)))

    monkeypatch.setattr(janus, "janus_summary", lambda mid: {"video_age_ms": 100})
    rsm.tick(state_path=bind_path, alloc_state_path=alloc_path)
    assert captured == []   # healthy → online, no alert
    assert sbs.get_binding("cam55:color", state_path=bind_path,
                           alloc_state_path=alloc_path).status == "online"

    monkeypatch.setattr(janus, "janus_summary", lambda mid: {"video_age_ms": 25000})
    rsm.tick(state_path=bind_path, alloc_state_path=alloc_path)
    assert len(captured) == 1
    args, kwargs = captured[0]
    assert args[0] is Domain.PRODUCER
    assert kwargs["binding_id"] == "cam55:color"
    assert sbs.get_binding("cam55:color", state_path=bind_path,
                           alloc_state_path=alloc_path).status == "degraded"


# ── FDIR gates recovery + alert (it owns recovery) — status is observed for every remote binding ──

def test_tick_observes_status_when_fdir_disabled(bind_path, alloc_path, monkeypatch):
    """FDIR off must NOT freeze the status: a live remote stream is still examined and
    persisted ONLINE (the '.55 up but shown configured_offline' bug), with NO recovery."""
    _setup_remote(bind_path, alloc_path)
    sbs.set_fdir_enabled("cam55:color", False, state_path=bind_path)
    captured = []
    monkeypatch.setattr(rsm, "emit", lambda *a, **k: captured.append((a, k)))
    rec = _RecordingClient()
    monkeypatch.setattr(rsm.node_client, "get_node_client", lambda *a, **k: rec)
    monkeypatch.setattr(janus, "janus_summary", lambda mid: {"video_age_ms": 50})

    n = rsm.tick(state_path=bind_path, alloc_state_path=alloc_path)
    assert n == 1                                                 # examined despite FDIR off
    got = sbs.get_binding("cam55:color", state_path=bind_path, alloc_state_path=alloc_path)
    assert got.status == "online"                                 # status reflects reality
    assert captured == [] and rec.restarts == []                 # observe only — no alert/recovery


def test_tick_fdir_disabled_neither_recovers_nor_escalates(bind_path, alloc_path, monkeypatch):
    """FDIR off + a regression (model B — FDIR OWNS recovery): the gateway does NEITHER recover NOR
    escalate. FDIR off = 'not auto-managed' (mountpoint kept elsewhere; operator restarts by hand),
    NOT 'recover silently'. Status is still persisted DEGRADED honestly (observed for every
    binding)."""
    _setup_remote(bind_path, alloc_path)
    sbs.set_fdir_enabled("cam55:color", False, state_path=bind_path)   # FDIR off; desired_up True
    captured = []
    monkeypatch.setattr(rsm, "emit", lambda *a, **k: captured.append((a, k)))
    rec = _RecordingClient()
    monkeypatch.setattr(rsm.node_client, "get_node_client", lambda *a, **k: rec)

    monkeypatch.setattr(janus, "janus_summary", lambda mid: {"video_age_ms": 50})
    rsm.tick(state_path=bind_path, alloc_state_path=alloc_path)   # healthy first
    monkeypatch.setattr(janus, "janus_summary", lambda mid: {"video_age_ms": 25000})
    rsm.tick(state_path=bind_path, alloc_state_path=alloc_path)   # regressed
    assert sbs.get_binding("cam55:color", state_path=bind_path,
                           alloc_state_path=alloc_path).status == "degraded"
    assert captured == []                          # FDIR off → NO escalation alert
    assert rec.restarts == []                      # FDIR off → NO auto-recovery (manual only)


def test_tick_desired_down_no_recovery_even_with_fdir_on(bind_path, alloc_path, monkeypatch):
    """A Stopped binding (desired_up=False) is NOT auto-restarted even with FDIR ON — recovery is
    gated on `desired_up AND fdir.enabled`. This is what lets Stop drop the FDIR-coupling."""
    _setup_remote(bind_path, alloc_path)                         # FDIR enabled by default
    sbs.set_desired_up("cam55:color", False, state_path=bind_path)
    captured = []
    monkeypatch.setattr(rsm, "emit", lambda *a, **k: captured.append((a, k)))
    rec = _RecordingClient()
    monkeypatch.setattr(rsm.node_client, "get_node_client", lambda *a, **k: rec)

    monkeypatch.setattr(janus, "janus_summary", lambda mid: {"video_age_ms": 50})
    rsm.tick(state_path=bind_path, alloc_state_path=alloc_path)  # healthy
    monkeypatch.setattr(janus, "janus_summary", lambda mid: {"video_age_ms": 25000})
    rsm.tick(state_path=bind_path, alloc_state_path=alloc_path)  # stale, but desired_down
    assert captured == [] and rec.restarts == []                # FDIR on, but Stopped → no recovery


# ── G5.3: Janus-restart mountpoint recovery (UNIFIED_FDIR §4.7) ─────────

class _RecordingClient:
    def __init__(self):
        self.restarts = []

    def restart_stream(self, node_id, sensor):
        self.restarts.append((node_id, sensor))
        return rsm.node_client.RestartResult(True, "test-stub")


def test_tick_janus_restart_reensures_mp_and_skips_node_restart(
        bind_path, alloc_path, monkeypatch):
    """A Janus restart drops the runtime mountpoint: the monitor re-ensures it
    (CREATED) and does NOT restart the node (the node was never the fault)."""
    from app.services import binding_provision as bp
    _setup_remote(bind_path, alloc_path)
    monkeypatch.setattr(janus, "janus_summary", lambda mid: {"video_age_ms": 100})
    rsm.tick(state_path=bind_path, alloc_state_path=alloc_path)        # was healthy

    # mountpoint gone — janus_summary has no age (mountpoint_id None)
    monkeypatch.setattr(janus, "janus_summary",
                        lambda mid: {"mountpoint_id": None, "video_age_ms": None})
    ensured = []
    monkeypatch.setattr(bp, "ensure_janus",
                        lambda b, **k: ensured.append(b.binding_id) or bp.ProvisionOutcome(
                            bp.ProvisionStatus.CREATED, b.janus.mountpoint_id, b.janus.rtp_iface))
    rec = _RecordingClient()
    monkeypatch.setattr(rsm.node_client, "get_node_client", lambda *a, **k: rec)

    rsm.tick(state_path=bind_path, alloc_state_path=alloc_path)
    assert ensured == ["cam55:color"]      # gateway mountpoint re-ensured
    assert rec.restarts == []              # node not the fault → not restarted


def test_tick_stale_with_present_mp_restarts_node(bind_path, alloc_path, monkeypatch):
    """Stale RTP while the mountpoint still EXISTS → fault is upstream (node),
    so the monitor re-ensures (no-op EXISTS) AND restarts the node."""
    from app.services import binding_provision as bp
    _setup_remote(bind_path, alloc_path)
    monkeypatch.setattr(janus, "janus_summary", lambda mid: {"video_age_ms": 100})
    rsm.tick(state_path=bind_path, alloc_state_path=alloc_path)        # was healthy

    monkeypatch.setattr(janus, "janus_summary", lambda mid: {"video_age_ms": 25000})
    ensured = []
    monkeypatch.setattr(bp, "ensure_janus",
                        lambda b, **k: ensured.append(b.binding_id) or bp.ProvisionOutcome(
                            bp.ProvisionStatus.EXISTS, b.janus.mountpoint_id, b.janus.rtp_iface))
    rec = _RecordingClient()
    monkeypatch.setattr(rsm.node_client, "get_node_client", lambda *a, **k: rec)

    rsm.tick(state_path=bind_path, alloc_state_path=alloc_path)
    assert ensured == ["cam55:color"]                 # mountpoint confirmed present
    assert rec.restarts == [("cam55", "color")]       # upstream fault → node restarted


def test_tick_never_healthy_unreachable_waits_without_action(bind_path, alloc_path, monkeypatch):
    """A never-yet-healthy desired_up binding on an UNREACHABLE node just waits — the gateway does
    not hammer a node it cannot reach (bring-up fires only once the node IP is available)."""
    _setup_remote(bind_path, alloc_path)
    monkeypatch.setattr(rsm, "_node_reachable", lambda node: False)   # node not reachable
    captured = []
    monkeypatch.setattr(rsm, "emit", lambda *a, **k: captured.append((a, k)))
    rec = _RecordingClient()
    monkeypatch.setattr(rsm.node_client, "get_node_client", lambda *a, **k: rec)
    monkeypatch.setattr(janus, "janus_summary",
                        lambda mid: {"video_age_ms": None, "status": "janus_unreachable"})
    rsm.tick(state_path=bind_path, alloc_state_path=alloc_path)
    assert captured == [] and rec.restarts == []                     # nothing while unreachable
    assert sbs.get_binding("cam55:color", state_path=bind_path,
                           alloc_state_path=alloc_path).status == "waiting_for_rtp"


def test_tick_brings_up_never_healthy_when_node_reachable(bind_path, alloc_path, monkeypatch):
    """Phase 2 / gateway-driven bring-up: a desired_up+FDIR stream that has never produced RTP is
    started by the gateway once the node is reachable — replacing node autostart."""
    from app.services import binding_provision as bp
    _setup_remote(bind_path, alloc_path)                    # desired_up + FDIR on by default
    monkeypatch.setattr(rsm, "_node_reachable", lambda node: True)   # node reachable now
    monkeypatch.setattr(bp, "ensure_janus",
                        lambda b, **k: bp.ProvisionOutcome(
                            bp.ProvisionStatus.EXISTS, b.janus.mountpoint_id, b.janus.rtp_iface))
    rec = _RecordingClient()
    monkeypatch.setattr(rsm.node_client, "get_node_client", lambda *a, **k: rec)
    monkeypatch.setattr(janus, "janus_summary", lambda mid: {"video_age_ms": None})
    rsm.tick(state_path=bind_path, alloc_state_path=alloc_path)
    assert rec.restarts == [("cam55", "color")]                      # gateway started it


def test_tick_ignores_local_projections(bind_path, alloc_path, monkeypatch):
    """A local projection (cam10:*) must never be touched by the remote monitor."""
    from app.services import mountpoint_allocator as _alloc
    _alloc.ensure(_alloc.LOCAL_SERIAL, "color", 1305, 5004, state_path=alloc_path)
    called = []
    monkeypatch.setattr(janus, "janus_summary", lambda mid: called.append(mid) or {"video_age_ms": 100})
    n = rsm.tick(state_path=bind_path, alloc_state_path=alloc_path)
    assert n == 0               # only local projection exists, not remote
    assert called == []         # never probed Janus for the local mountpoint


def test_tick_skips_remote_binding_squatting_local_mount(bind_path, alloc_path, monkeypatch):
    """Defense-in-depth (UNIFIED_FDIR §4.6, widened): a hand-edited remote binding
    holding ANY id in the local-owned range (< REMOTE_MP_MIN) must be refused, never
    probed as if it were the local stream. Uses 1306 — a cam10-owned id that is NOT
    janus_mount_id, which the old `== janus_mount_id` guard would have missed."""
    import json
    sbs.upsert_node("cam55", host="192.168.1.55", role="remote_producer", state_path=bind_path)
    state = json.loads(bind_path.read_text())
    state["bindings"]["cam55:color"] = {
        "binding_id": "cam55:color", "node_id": "cam55", "sensor": "color",
        "mode": "remote_producer",
        "transport": {"rtp_port": 5100, "payload_type": 96, "codec": "h264", "srtp": None},
        "janus": {"mountpoint_id": 1306, "rtp_iface": "192.168.1.10"},  # cam10-owned range
        "fdir": {"enabled": True, "policy": "stream_default"}, "status": "configured_offline"}
    bind_path.write_text(json.dumps(state))

    probed = []
    monkeypatch.setattr(janus, "janus_summary", lambda mid: probed.append(mid) or {"video_age_ms": 100})
    n = rsm.tick(state_path=bind_path, alloc_state_path=alloc_path)
    assert n == 0           # refused
    assert probed == []     # never probed the local mountpoint


def test_remote_tick_does_not_perturb_global_quiesce(bind_path, alloc_path, monkeypatch):
    """TB-C6 regression: the remote monitor never touches cam10's global quiesce
    gate (it is deliberately not wired to it)."""
    from app.services import fdir_quiesce as q
    _setup_remote(bind_path, alloc_path)
    monkeypatch.setattr(janus, "janus_summary", lambda mid: {"video_age_ms": 25000})

    while q._arms > 0:          # start from a clean gate (other tests share the module)
        q.unquiesce()
    q.quiesce(60, "cam10 restart_pipeline", {Domain.PIPELINE, Domain.SENSOR})
    try:
        before = (q._until, set(q._domains), q._arms)
        rsm.tick(state_path=bind_path, alloc_state_path=alloc_path)
        after = (q._until, set(q._domains), q._arms)
        assert before == after
    finally:
        while q._arms > 0:
            q.unquiesce()


# ── safety invariant ──────────────────────────────────────────────────

def test_monitor_module_has_no_local_destructive_references():
    """UNIFIED_FDIR §4.4: the remote monitor must not reach the global ladder,
    reboot counter, or quiesce gate. Check imports + code (excl. the docstring
    that explains *why* they are absent)."""
    refs = set(dir(rsm))
    for sym in ("get_ladder", "recovery_ladder", "fdir_quiesce"):
        assert sym not in refs, f"remote monitor must not import {sym}"
    code = Path(rsm.__file__).read_text().split('"""', 2)[-1]   # drop module docstring
    for sym in ("get_ladder", "recovery_ladder", "fdir_quiesce",
                "REBOOT_NODE", "systemctl", "reboot"):
        assert sym not in code, f"remote monitor code must not reference {sym}"

"""TB-C1 — FDIR quiesce gate + recovery-executor self-quiesce. Proves the live
self-amplification bug is closed: the executor's own restart no longer re-escalates the
staleness it causes, while a real JANUS fault still escalates. Safety: deadline fail-safe,
hard ceiling, refcount, domain-scope."""
import time

import pytest

from app.services import fdir_quiesce as Q
from app.services.fdir_events import Domain


@pytest.fixture(autouse=True)
def _reset_quiesce():
    def _clear():
        with Q._lock:
            Q._until = 0.0; Q._domains = set(); Q._reason = ""; Q._arms = 0
    _clear(); yield; _clear()


# ── mechanism ────────────────────────────────────────────────────────────────

def test_default_not_quiesced():
    assert Q.is_quiesced(Domain.PIPELINE) is False

def test_quiesce_is_domain_scoped():
    Q.quiesce(60, "r", {Domain.PIPELINE, Domain.SENSOR})
    assert Q.is_quiesced(Domain.PIPELINE) is True
    assert Q.is_quiesced(Domain.SENSOR) is True
    assert Q.is_quiesced(Domain.JANUS) is False     # a real JANUS fault is NOT suppressed

def test_deadline_is_fail_safe():
    Q.quiesce(60, "r", {Domain.PIPELINE})
    with Q._lock:
        Q._until = time.monotonic() - 1            # simulate the deadline lapsing
    assert Q.is_quiesced(Domain.PIPELINE) is False  # FDIR reclaims itself (TB-C5)

def test_zero_ttl_does_not_arm():
    Q.quiesce(0, "r", {Domain.PIPELINE})
    assert Q.is_quiesced(Domain.PIPELINE) is False

def test_ttl_ceiling_caps_window():
    Q.quiesce(9999, "r", {Domain.PIPELINE})
    with Q._lock:
        assert Q._until <= time.monotonic() + Q.QUIESCE_TTL_CEILING_SEC + 0.5

def test_refcount_last_out_clears():
    Q.quiesce(60, "a", {Domain.PIPELINE})
    Q.quiesce(60, "b", {Domain.PIPELINE})
    Q.unquiesce()
    assert Q.is_quiesced(Domain.PIPELINE) is True    # one arm still live
    Q.unquiesce()
    assert Q.is_quiesced(Domain.PIPELINE) is False    # last out clears

def test_nested_arm_does_not_extend_deadline(monkeypatch):
    Q.quiesce(1, "first", {Domain.PIPELINE})          # short first window
    with Q._lock:
        first_until = Q._until
    Q.quiesce(9999, "second", {Domain.SENSOR})        # nested longer arm
    with Q._lock:
        assert Q._until == first_until                # deadline NOT pushed forward (TB-C6)
        assert Domain.SENSOR in Q._domains            # but domains widened

def test_context_manager_arms_and_clears():
    with Q.quiesced(60, "r", {Domain.PIPELINE}):
        assert Q.is_quiesced(Domain.PIPELINE) is True
    assert Q.is_quiesced(Domain.PIPELINE) is False
    # clears on exception too
    with pytest.raises(ValueError):
        with Q.quiesced(60, "r", {Domain.PIPELINE}):
            raise ValueError("x")
    assert Q.is_quiesced(Domain.PIPELINE) is False

def test_note_suppressed_observable_no_raise():
    Q.quiesce(60, "recovery: restart_janus", {Domain.PIPELINE})
    Q.note_suppressed("video_age_ms=99999", Domain.PIPELINE)   # must not raise


# ── the gate in _try_escalate ────────────────────────────────────────────────

class _FakeLadder:
    def __init__(self): self.calls = []
    def escalate(self, signal, domain): self.calls.append((signal, domain))

def test_gate_suppresses_quiesced_domain_but_not_janus():
    from app.services import watchdogs
    watchdogs._last_escalation_ts = time.monotonic() - 100   # dedup not blocking
    ladder = _FakeLadder()
    Q.quiesce(60, "recovery: restart_janus", {Domain.PIPELINE, Domain.SENSOR})
    # the staleness the restart causes → suppressed, ladder NOT advanced
    assert watchdogs._try_escalate(ladder, "video_age_ms=99999", Domain.PIPELINE) is False
    assert watchdogs._try_escalate(ladder, "snapshot_missing", Domain.SENSOR) is False
    assert ladder.calls == []
    # a genuine JANUS fault during the window STILL escalates (domain-scope)
    assert watchdogs._try_escalate(ladder, "watchdog_exception", Domain.JANUS) is True
    assert ladder.calls == [("watchdog_exception", Domain.JANUS)]

def test_core_regression_restart_does_not_self_amplify():
    # the live bug: during a ~75s restart the watchdog would re-escalate ~15x (every 5s).
    # With the gate armed, EVERY re-check is suppressed → zero ladder escalations.
    from app.services import watchdogs
    ladder = _FakeLadder()
    with Q.quiesced(90, "recovery: restart_janus", {Domain.PIPELINE, Domain.SENSOR}):
        for _ in range(15):
            watchdogs._last_escalation_ts = time.monotonic() - 100   # past dedup each tick
            watchdogs._try_escalate(ladder, "video_age_ms=99999", Domain.PIPELINE)
    assert ladder.calls == []   # the executor's own restart never climbs the ladder


# ── recovery-executor self-quiesce ───────────────────────────────────────────

def _executor(run_cmd_fn):
    from app.services.recovery_executor import RecoveryExecutor
    return RecoveryExecutor(
        read_reboot_count=lambda: 0, write_reboot_count=lambda *_: None,
        atomic_increment_reboot_count=lambda: 1, reboot_marker_path=None,
        subprocess_module=None, run_cmd_fn=run_cmd_fn,
        emit_fn=lambda *a, **k: None, get_settings_fn=lambda: None)

def test_restart_pipeline_quiesces_pipeline_sensor_not_janus():
    seen = {}
    def run_cmd(cmd, timeout=None):
        seen["pipeline"] = Q.is_quiesced(Domain.PIPELINE)
        seen["sensor"] = Q.is_quiesced(Domain.SENSOR)
        seen["janus"] = Q.is_quiesced(Domain.JANUS)
    _executor(run_cmd)._restart_pipeline(None)
    assert seen == {"pipeline": True, "sensor": True, "janus": False}
    assert Q.is_quiesced(Domain.PIPELINE) is False        # cleared after

def test_restart_janus_quiesces_all_three_domains_incl_janus():
    # TB-C1.1: restart_janus is a planned Janus restart → JANUS is suppressed too (so the
    # self-amplification can't move to the JANUS domain), across BOTH _run_cmd calls.
    during = []
    def run_cmd(cmd, timeout=None):
        during.append((Q.is_quiesced(Domain.PIPELINE),
                       Q.is_quiesced(Domain.SENSOR),
                       Q.is_quiesced(Domain.JANUS)))
    _executor(run_cmd)._restart_janus(None)
    assert during == [(True, True, True), (True, True, True)]   # all three, both commands

def test_restart_janus_janus_escalates_normally_after_window():
    from app.services import watchdogs
    def run_cmd(cmd, timeout=None):
        pass
    _executor(run_cmd)._restart_janus(None)                     # window opens + closes (ctx mgr)
    assert Q.is_quiesced(Domain.JANUS) is False                 # cleared after
    watchdogs._last_escalation_ts = time.monotonic() - 100
    ladder = _FakeLadder()
    assert watchdogs._try_escalate(ladder, "watchdog_exception", Domain.JANUS) is True
    assert ladder.calls == [("watchdog_exception", Domain.JANUS)]   # JANUS escalates normally again

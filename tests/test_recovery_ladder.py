"""Unit tests for recovery_ladder.RecoveryLadder.

Covers the state machine, persistence, cooldown/dedup, escalation,
reboot circuit breaker, and corrupted-state recovery. Mocks subprocess
execution + system_mode side effects.

recovery_ladder.py is the largest L4 module (593 LOC) and until today
had zero isolated tests — only integration coverage in
test_concurrent_races.py.
"""
from __future__ import annotations

import itertools
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import app.services.janus as janus_mod
from app.services import recovery_ladder
from app.services.fdir_events import Domain, RecoveryAction


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    """Redirect persistence paths into tmp_path."""
    state_path = tmp_path / "ladder.json"
    reboot_dir = tmp_path / "fdir-persist"
    monkeypatch.setattr(recovery_ladder, "_LADDER_STATE_PATH", state_path)
    monkeypatch.setattr(recovery_ladder, "_REBOOT_COUNT_DIR", reboot_dir)
    monkeypatch.setattr(recovery_ladder, "_REBOOT_COUNT_PATH", reboot_dir / "reboot_count")
    monkeypatch.setattr(recovery_ladder, "_REBOOT_MARKER_PATH", reboot_dir / "last_reboot_request")
    # Process start = now (so reboot counter reset gate has no uptime built up)
    monkeypatch.setattr(recovery_ladder, "_PROCESS_START_MONO", time.monotonic())
    return {"state": state_path, "reboot_dir": reboot_dir}


@pytest.fixture
def mock_settings(monkeypatch):
    """Mock get_settings() for controllable values."""
    settings = MagicMock()
    settings.camera_type = "color_camera"
    settings.service_name = "rs-stream@color.service"
    settings.max_fdir_reboots = 3
    settings.watchdog_reboot_enabled = False  # Tests must NOT trigger a real reboot!
    settings.watchdog_stale_ms = 5000
    settings.janus_mount_id = 1305
    monkeypatch.setattr(recovery_ladder, "get_settings", lambda: settings)
    return settings


@pytest.fixture
def mock_subprocess(monkeypatch):
    """Mock subprocess.run + run_cmd so we don't call real systemctl."""
    sp_result = MagicMock()
    sp_result.returncode = 0
    sp_result.stdout = b""
    sp_result.stderr = b""

    sp_run = MagicMock(return_value=sp_result)
    monkeypatch.setattr(recovery_ladder.subprocess, "run", sp_run)

    rc_run = MagicMock()
    monkeypatch.setattr(recovery_ladder, "run_cmd", rc_run)

    return {"subprocess_run": sp_run, "run_cmd": rc_run}


@pytest.fixture
def mock_emit_and_mode(monkeypatch):
    """Suppress fdir_events.emit + system_mode side effects."""
    emit = MagicMock()
    monkeypatch.setattr(recovery_ladder, "emit", emit)

    transition = MagicMock()
    degrade = MagicMock()
    monkeypatch.setattr(recovery_ladder.system_mode, "transition", transition)
    monkeypatch.setattr(recovery_ladder.system_mode, "degrade", degrade)
    return {"emit": emit, "transition": transition, "degrade": degrade}


@pytest.fixture
def ladder(tmp_state, mock_settings, mock_subprocess, mock_emit_and_mode, monkeypatch):
    """Fresh RecoveryLadder with clean state."""
    # janus_summary used by RETRY_HANDLE — return healthy default
    # Patch attribute on already-imported module (late `from app.services import janus`
    # rebinds name but uses cached module).
    monkeypatch.setattr(janus_mod, "janus_summary", lambda mid: {"video_age_ms": 50})
    return recovery_ladder.RecoveryLadder()


# ── Initial state ──────────────────────────────────────────────────────

def test_fresh_ladder_starts_at_level_zero(ladder):
    assert ladder.status()["current_level"] == 0
    assert ladder.status()["current_level_name"] == "retry_handle"
    assert ladder.status()["total_recoveries"] == 0


def test_color_camera_has_no_usb_reset_level(mock_settings, ladder):
    """USB reset only applicable depth_camera. Color must have 4 levels (without USB)."""
    names = [lvl["name"] for lvl in ladder.status()["levels"]]
    assert "usb_reset" not in names
    assert names == ["retry_handle", "restart_pipeline", "restart_janus", "reboot_node"]


def test_depth_camera_includes_usb_reset(mock_settings, tmp_state, mock_subprocess, mock_emit_and_mode):
    mock_settings.camera_type = "depth_camera"
    ladder = recovery_ladder.RecoveryLadder()
    names = [lvl["name"] for lvl in ladder.status()["levels"]]
    assert "usb_reset" in names


# ── Single escalation execution ────────────────────────────────────────

def test_escalate_at_level_0_executes_retry_handle(ladder, mock_emit_and_mode):
    """Healthy retry → success path executes without escalation."""
    result = ladder.escalate("watchdog_stale_test")
    # RETRY_HANDLE on healthy returns success — outcome was "janus_ok, ..."
    assert result["action"] == "retry_handle"
    assert result["level"] == "retry_handle"
    assert result["attempt"] == 1
    assert ladder.status()["current_level"] == 0


def test_escalate_increments_attempts(ladder):
    """Each successful escalate increments attempts."""
    ladder.escalate("t1")
    assert ladder.status()["total_recoveries"] == 1


# ── Escalation after exhausting budget ─────────────────────────────────

def test_escalates_after_max_attempts(ladder, mock_emit_and_mode, mock_subprocess):
    """When attempts == max_attempts → next escalate moves to next level."""
    # Fast-forward time: each call returns progress > all cooldowns.
    counter = itertools.count(start=100.0, step=1000.0)
    with patch.object(recovery_ladder.time, "monotonic", lambda: next(counter)):
        # RETRY_HANDLE max=1 → exhausts on first call
        ladder.escalate("t1")
        # Next call → escalate to restart_pipeline
        r2 = ladder.escalate("t2")
        assert r2["level"] == "restart_pipeline"


def test_all_levels_exhausted_triggers_safe_mode(ladder, mock_emit_and_mode):
    """When all levels exhausted → SAFE mode dict returned."""
    # Manually set current_level past end
    ladder._current_level = len(ladder._levels)
    result = ladder.escalate("exhausted")
    assert result == {"action": "safe_mode", "reason": "ladder_exhausted"}
    mock_emit_and_mode["transition"].assert_called()


# ── Cooldown / dedup ───────────────────────────────────────────────────

def test_dedup_window_skips_immediate_duplicate(ladder):
    """Two escalates in _DEDUP_WINDOW_SEC (default 3s) → second returns dedup_skip."""
    # monotonic constant: both calls see same ts → dedup window triggers
    with patch.object(recovery_ladder.time, "monotonic", return_value=100.0):
        ladder.escalate("watchdog1")
        r2 = ladder.escalate("watchdog2")
        assert r2["action"] == "dedup_skip"


def test_cooldown_after_attempt(ladder, mock_subprocess):
    """After an attempt, within cooldown_sec — next escalate (not dedup'd) → 'cooldown'."""
    # Settable clock: t=100 for the first escalate (+ inner duration call), t=105 for the second.
    # _execute calls monotonic for duration → needs an extra inner call.
    clock = {"now": 100.0}
    with patch.object(recovery_ladder.time, "monotonic", lambda: clock["now"]):
        ladder.escalate("t1")
        clock["now"] = 105.0   # 5s later: past dedup window (3s), within cooldown (10s)
        r2 = ladder.escalate("t2")
        assert r2["action"] == "cooldown"
        assert r2["level"] == "retry_handle"


# ── Reset behavior ─────────────────────────────────────────────────────

def test_reset_clears_attempts_and_returns_to_level_0(ladder, mock_subprocess):
    """After escalations, reset() → level 0, attempts cleared."""
    # Force level escalation
    ladder._current_level = 1
    ladder._levels[1].attempts = 3
    ladder.reset()
    assert ladder.status()["current_level"] == 0
    for lvl in ladder.status()["levels"]:
        assert lvl["attempts"] == 0


def test_reset_persists_to_disk(ladder, tmp_state):
    """After reset() — state file exists and contains level=0."""
    ladder._current_level = 2
    ladder.reset()
    data = json.loads(tmp_state["state"].read_text())
    assert data["level"] == 0


# ── Persistence (state survive instance recreation) ────────────────────

def test_state_persists_across_instances(tmp_state, mock_settings, mock_subprocess, mock_emit_and_mode):
    """A new RecoveryLadder loads state from disk."""
    # Pre-populate state file
    state = {
        "level": 2,
        "attempts": [1, 5, 1, 0],
        "last_attempt": [100, 200, 300, 0],
        "total_recoveries": 7,
        "ts": time.time(),
    }
    tmp_state["state"].write_text(json.dumps(state))

    ladder = recovery_ladder.RecoveryLadder()
    assert ladder.status()["current_level"] == 2
    assert ladder.status()["total_recoveries"] == 7


def test_corrupted_state_file_resets_to_level_0(tmp_state, mock_settings, mock_subprocess, mock_emit_and_mode):
    """If state file corrupt → reset to level 0, emit warning event."""
    tmp_state["state"].write_text("this is not json {{{")
    ladder = recovery_ladder.RecoveryLadder()
    assert ladder.status()["current_level"] == 0
    assert ladder.status()["total_recoveries"] == 0
    # Should emit warning
    calls = mock_emit_and_mode["emit"].call_args_list
    assert any("ladder_state_corrupt" in str(c) for c in calls)


def test_state_with_out_of_bounds_level_clamped(tmp_state, mock_settings, mock_subprocess, mock_emit_and_mode):
    """If saved level > len(levels) → clamped (treated as exhausted)."""
    state = {
        "level": 999,
        "attempts": [],
        "last_attempt": [],
        "total_recoveries": 0,
        "ts": time.time(),
    }
    tmp_state["state"].write_text(json.dumps(state))
    ladder = recovery_ladder.RecoveryLadder()
    # Clamped to len(levels) = exhausted (current_level_obj returns None)
    assert ladder.status()["current_level_name"] == "exhausted"


# ── Reboot circuit breaker ─────────────────────────────────────────────

def test_fresh_reboot_count_is_zero(ladder, tmp_state):
    assert ladder.status()["reboot_count"] == 0


def test_atomic_increment_reboot_count(tmp_state):
    """Increment helper — concurrent-safe, returns new count."""
    n1 = recovery_ladder._atomic_increment_reboot_count()
    n2 = recovery_ladder._atomic_increment_reboot_count()
    n3 = recovery_ladder._atomic_increment_reboot_count()
    assert (n1, n2, n3) == (1, 2, 3)
    assert recovery_ladder._read_reboot_count() == 3


def test_reboot_circuit_breaker_trips_at_max(tmp_state, mock_settings, mock_subprocess, mock_emit_and_mode):
    """If reboot_count >= max → SAFE mode on init."""
    mock_settings.max_fdir_reboots = 3
    # Pre-populate count
    (tmp_state["reboot_dir"]).mkdir(parents=True, exist_ok=True)
    (tmp_state["reboot_dir"] / "reboot_count").write_text("3\n")

    ladder = recovery_ladder.RecoveryLadder()
    # All levels marked exhausted
    assert ladder._current_level == len(ladder._levels)
    # system_mode.transition(SAFE) was called
    transition_calls = mock_emit_and_mode["transition"].call_args_list
    assert any("SAFE" in str(c) for c in transition_calls)


def test_reboot_action_blocked_when_disabled(tmp_state, mock_settings, mock_subprocess, mock_emit_and_mode):
    """REBOOT_NODE action: if watchdog_reboot_enabled=False → SAFE mode, no reboot."""
    mock_settings.watchdog_reboot_enabled = False
    ladder = recovery_ladder.RecoveryLadder()

    # Direct invoke _execute with reboot action
    reboot_level = ladder._levels[-1]  # last is reboot_node
    assert reboot_level.name == "reboot_node"
    success = ladder._execute(reboot_level, "test", Domain.PIPELINE)
    assert success is True  # returns True even when skipped
    # systemctl reboot was NOT invoked
    assert not any(
        "reboot" in str(call) for call in mock_subprocess["run_cmd"].call_args_list
    )


def test_reboot_action_increments_counter_when_enabled(tmp_state, mock_settings, mock_subprocess, mock_emit_and_mode):
    """REBOOT_NODE with enabled=True → increment counter + invoke systemctl reboot."""
    mock_settings.watchdog_reboot_enabled = True
    mock_settings.max_fdir_reboots = 5
    ladder = recovery_ladder.RecoveryLadder()
    assert recovery_ladder._read_reboot_count() == 0

    reboot_level = ladder._levels[-1]
    ladder._execute(reboot_level, "test", Domain.PIPELINE)

    assert recovery_ladder._read_reboot_count() == 1
    # systemctl reboot was invoked
    assert any(
        "reboot" in str(call) for call in mock_subprocess["run_cmd"].call_args_list
    )


def test_reboot_counter_rollback_on_failed_reboot(tmp_state, mock_settings, mock_subprocess, mock_emit_and_mode):
    """If systemctl reboot fails → counter rolled back (does not consume budget)."""
    mock_settings.watchdog_reboot_enabled = True
    mock_settings.max_fdir_reboots = 5
    # Make run_cmd raise
    mock_subprocess["run_cmd"].side_effect = RuntimeError("reboot blocked")
    ladder = recovery_ladder.RecoveryLadder()

    reboot_level = ladder._levels[-1]
    success = ladder._execute(reboot_level, "test", Domain.PIPELINE)
    # Counter must be 0 (rolled back)
    assert recovery_ladder._read_reboot_count() == 0
    # _execute returned False because exception caught
    assert success is False


# ── Execute action: restart pipeline ──────────────────────────────────

def test_restart_pipeline_invokes_encoder_admin(tmp_state, mock_settings, mock_subprocess, mock_emit_and_mode):
    """RESTART_PIPELINE goes through L2-owned encoder-admin CLI (not raw systemctl).

    Boundary contract: L4 doesn't know rs-stream@color.service exists.
    """
    ladder = recovery_ladder.RecoveryLadder()
    pipeline_level = ladder._levels[1]
    assert pipeline_level.name == "restart_pipeline"
    success = ladder._execute(pipeline_level, "test", Domain.PIPELINE)
    assert success is True
    calls = mock_subprocess["run_cmd"].call_args_list
    assert any(
        "encoder-admin" in str(c) and "restart" in str(c) for c in calls
    ), f"expected encoder-admin restart, got: {calls}"


def test_restart_janus_does_ordered_restart_via_admin_clis(tmp_state, mock_settings, mock_subprocess, mock_emit_and_mode):
    """RESTART_JANUS: stop encoder FIRST (via encoder-admin), then janus-admin restart.

    Avoids v4l2 buffer corruption (ffmpeg pushing RTP into a restarting Janus). Both
    operations through L2/L3 admin CLIs — L4 does not know unit names.
    """
    ladder = recovery_ladder.RecoveryLadder()
    janus_level = ladder._levels[2]
    assert janus_level.name == "restart_janus"
    success = ladder._execute(janus_level, "test", Domain.PIPELINE)
    assert success is True
    calls = [str(c) for c in mock_subprocess["run_cmd"].call_args_list]
    # First: encoder-admin stop
    assert any("encoder-admin" in c and "stop" in c for c in calls), \
        f"expected encoder-admin stop, got: {calls}"
    # Then: janus-admin restart
    assert any("janus-admin" in c and "restart" in c for c in calls), \
        f"expected janus-admin restart, got: {calls}"
    # Order: encoder stop should come BEFORE janus restart
    encoder_idx = next(i for i, c in enumerate(calls) if "encoder-admin" in c and "stop" in c)
    janus_idx = next(i for i, c in enumerate(calls) if "janus-admin" in c and "restart" in c)
    assert encoder_idx < janus_idx, "encoder must stop BEFORE janus restart"


# ── Singleton getter ──────────────────────────────────────────────────

def test_get_ladder_returns_singleton(tmp_state, mock_settings, mock_subprocess, mock_emit_and_mode, monkeypatch):
    """get_ladder() returns same instance."""
    monkeypatch.setattr(recovery_ladder, "_ladder", None)
    l1 = recovery_ladder.get_ladder()
    l2 = recovery_ladder.get_ladder()
    assert l1 is l2

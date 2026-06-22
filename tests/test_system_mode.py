"""Unit tests for system_mode.py — operating mode lattice.

Modes form a lattice:
    NOMINAL (0) → DEGRADED (1) → LOCAL_ONLY (2) → SAFE (3)

Tests cover:
- SystemMode enum + level ordering
- ModePolicy per mode (streams_enabled, fps, bitrate caps, requires)
- transition() — idempotent if already in target
- degrade() — one level worse, clamps at SAFE
- promote() — only if better, no-op otherwise
- mode_info() / current_policy() / mode_uptime_sec()
- Listener callbacks fire on transition
- Module state reset between tests (singleton _state)
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from app.services import system_mode
from app.services.system_mode import SystemMode, MODE_POLICIES, ModePolicy


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    """Reset module singleton _state between tests."""
    fresh = system_mode._ModeState()
    monkeypatch.setattr(system_mode, "_state", fresh)
    # Suppress emit + metrics side effects
    monkeypatch.setattr(system_mode, "emit", MagicMock())
    monkeypatch.setattr(system_mode, "_system_mode_gauge", None)
    monkeypatch.setattr(system_mode, "_mode_transitions_counter", None)


# ── SystemMode enum + level ordering ──────────────────────────────────

def test_mode_levels_ordered():
    """NOMINAL=0, DEGRADED=1, LOCAL_ONLY=2, SAFE=3."""
    assert SystemMode.NOMINAL.level == 0
    assert SystemMode.DEGRADED.level == 1
    assert SystemMode.LOCAL_ONLY.level == 2
    assert SystemMode.SAFE.level == 3


def test_mode_string_values_match_enum_value():
    """Mode string values used in API/logs."""
    assert SystemMode.NOMINAL.value == "nominal"
    assert SystemMode.SAFE.value == "safe"


def test_mode_str_compatibility():
    """SystemMode inherits str — comparisons with strings work."""
    assert SystemMode.NOMINAL == "nominal"


# ── ModePolicy per mode ───────────────────────────────────────────────

def test_nominal_policy_full_capacity():
    """NOMINAL: streams on, max fps/bitrate, TURN + uplink required."""
    p = MODE_POLICIES[SystemMode.NOMINAL]
    assert p.streams_enabled is True
    assert p.max_fps == 30
    assert p.max_bitrate_kbps == 4000
    assert p.require_turn is True
    assert p.require_uplink is True


def test_degraded_policy_reduced():
    """DEGRADED: streams on, reduced quality."""
    p = MODE_POLICIES[SystemMode.DEGRADED]
    assert p.streams_enabled is True
    assert p.max_fps < MODE_POLICIES[SystemMode.NOMINAL].max_fps
    assert p.max_bitrate_kbps < MODE_POLICIES[SystemMode.NOMINAL].max_bitrate_kbps


def test_local_only_does_not_require_turn_or_uplink():
    """LOCAL_ONLY: no internet, LAN viewers only."""
    p = MODE_POLICIES[SystemMode.LOCAL_ONLY]
    assert p.streams_enabled is True
    assert p.require_turn is False
    assert p.require_uplink is False


def test_safe_policy_streams_off():
    """SAFE: all streaming disabled, control plane stays up."""
    p = MODE_POLICIES[SystemMode.SAFE]
    assert p.streams_enabled is False
    assert p.max_fps == 0
    assert p.max_bitrate_kbps == 0


def test_all_modes_have_policy():
    """Every mode in enum has corresponding policy (no KeyError possible)."""
    for mode in SystemMode:
        assert mode in MODE_POLICIES
        assert isinstance(MODE_POLICIES[mode], ModePolicy)


# ── Initial state ─────────────────────────────────────────────────────

def test_initial_mode_is_nominal():
    assert system_mode.current_mode() == SystemMode.NOMINAL


def test_initial_uptime_near_zero():
    uptime = system_mode.mode_uptime_sec()
    assert 0.0 <= uptime < 1.0


def test_initial_policy_is_nominal_policy():
    p = system_mode.current_policy()
    assert p == MODE_POLICIES[SystemMode.NOMINAL]


def test_mode_info_returns_expected_shape():
    info = system_mode.mode_info()
    assert info["mode"] == "nominal"
    assert "since" in info
    assert "uptime_s" in info
    assert "reason" in info
    assert info["policy"]["streams_enabled"] is True
    assert info["policy"]["max_fps"] == 30


# ── transition() ──────────────────────────────────────────────────────

def test_transition_to_different_mode_returns_true():
    result = system_mode.transition(SystemMode.DEGRADED, "test")
    assert result is True
    assert system_mode.current_mode() == SystemMode.DEGRADED


def test_transition_to_same_mode_is_noop():
    """Already in target → return False, no transition."""
    system_mode.transition(SystemMode.DEGRADED, "first")
    result = system_mode.transition(SystemMode.DEGRADED, "second")
    assert result is False


def test_transition_updates_reason():
    system_mode.transition(SystemMode.DEGRADED, "watchdog_stale")
    assert system_mode.mode_info()["reason"] == "watchdog_stale"


def test_transition_updates_uptime():
    """After transition uptime resets to ~0."""
    system_mode.transition(SystemMode.DEGRADED, "test")
    time.sleep(0.05)
    uptime = system_mode.mode_uptime_sec()
    assert 0.0 < uptime < 0.5


def test_transition_to_safe():
    system_mode.transition(SystemMode.SAFE, "emergency")
    assert system_mode.current_mode() == SystemMode.SAFE
    assert system_mode.current_policy().streams_enabled is False


def test_transition_emits_fdir_event(monkeypatch):
    emit_mock = MagicMock()
    monkeypatch.setattr(system_mode, "emit", emit_mock)
    system_mode.transition(SystemMode.SAFE, "test")
    emit_mock.assert_called_once()


# ── degrade() ─────────────────────────────────────────────────────────

def test_degrade_from_nominal_goes_to_degraded():
    system_mode.degrade("test")
    assert system_mode.current_mode() == SystemMode.DEGRADED


def test_degrade_from_degraded_goes_to_local_only():
    system_mode.transition(SystemMode.DEGRADED, "init")
    system_mode.degrade("more_problems")
    assert system_mode.current_mode() == SystemMode.LOCAL_ONLY


def test_degrade_from_local_only_goes_to_safe():
    system_mode.transition(SystemMode.LOCAL_ONLY, "init")
    system_mode.degrade("worse")
    assert system_mode.current_mode() == SystemMode.SAFE


def test_degrade_from_safe_stays_at_safe():
    """SAFE — lowest level, degrade — noop."""
    system_mode.transition(SystemMode.SAFE, "init")
    system_mode.degrade("already_safe")
    assert system_mode.current_mode() == SystemMode.SAFE


# ── promote() ─────────────────────────────────────────────────────────

def test_promote_from_safe_to_nominal_succeeds():
    system_mode.transition(SystemMode.SAFE, "init")
    result = system_mode.promote(SystemMode.NOMINAL, "recovered")
    assert result is True
    assert system_mode.current_mode() == SystemMode.NOMINAL


def test_promote_to_same_or_worse_returns_false():
    """promote() only improves; same-or-worse → noop."""
    system_mode.transition(SystemMode.DEGRADED, "init")
    result = system_mode.promote(SystemMode.DEGRADED, "same")
    assert result is False
    result = system_mode.promote(SystemMode.SAFE, "worse")
    assert result is False
    assert system_mode.current_mode() == SystemMode.DEGRADED


def test_promote_from_degraded_to_nominal():
    system_mode.transition(SystemMode.DEGRADED, "init")
    result = system_mode.promote(SystemMode.NOMINAL, "healthy")
    assert result is True
    assert system_mode.current_mode() == SystemMode.NOMINAL


def test_promote_from_local_only_to_degraded():
    """Partial promotion."""
    system_mode.transition(SystemMode.LOCAL_ONLY, "init")
    result = system_mode.promote(SystemMode.DEGRADED, "uplink_back")
    assert result is True
    assert system_mode.current_mode() == SystemMode.DEGRADED


# ── Listeners ─────────────────────────────────────────────────────────

def test_listener_fires_on_transition():
    callback = MagicMock()
    system_mode.on_transition(callback)
    system_mode.transition(SystemMode.DEGRADED, "trigger")
    callback.assert_called_once()
    args = callback.call_args[0]
    assert args[0] == SystemMode.NOMINAL   # previous
    assert args[1] == SystemMode.DEGRADED  # target
    assert args[2] == "trigger"            # reason


def test_listener_not_fired_on_noop_transition():
    """If already in target — transition() returns False, listener does NOT fire."""
    callback = MagicMock()
    system_mode.on_transition(callback)
    system_mode.transition(SystemMode.NOMINAL, "noop")  # already nominal
    callback.assert_not_called()


def test_listener_exception_does_not_break_transition():
    """Failing listener does not block transition + other listeners."""
    failing = MagicMock(side_effect=RuntimeError("boom"))
    good = MagicMock()
    system_mode.on_transition(failing)
    system_mode.on_transition(good)

    # Transition should succeed
    result = system_mode.transition(SystemMode.DEGRADED, "test")
    assert result is True
    assert system_mode.current_mode() == SystemMode.DEGRADED
    # Good listener still called
    good.assert_called_once()


def test_listener_fires_on_degrade():
    callback = MagicMock()
    system_mode.on_transition(callback)
    system_mode.degrade("watchdog")
    callback.assert_called_once()


def test_listener_fires_on_promote():
    system_mode.transition(SystemMode.SAFE, "init")
    callback = MagicMock()
    system_mode.on_transition(callback)
    system_mode.promote(SystemMode.NOMINAL, "recovered")
    callback.assert_called_once()


# ── Concurrency: state mutation thread-safe ───────────────────────────

def test_concurrent_transitions_dont_corrupt_state():
    """Many threads call transition — final state consistent."""
    import threading
    targets = [SystemMode.DEGRADED, SystemMode.LOCAL_ONLY, SystemMode.SAFE,
               SystemMode.NOMINAL, SystemMode.DEGRADED]

    def worker(t):
        system_mode.transition(t, f"thread_{t.value}")

    threads = [threading.Thread(target=worker, args=(t,)) for t in targets]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    # Final mode in valid range
    assert system_mode.current_mode() in SystemMode


# ── mode_info() snapshot ──────────────────────────────────────────────

def test_mode_info_after_transition_reflects_new_policy():
    system_mode.transition(SystemMode.SAFE, "emergency")
    info = system_mode.mode_info()
    assert info["mode"] == "safe"
    assert info["reason"] == "emergency"
    assert info["policy"]["streams_enabled"] is False
    assert info["policy"]["max_fps"] == 0


def test_mode_info_uptime_increases():
    system_mode.transition(SystemMode.DEGRADED, "test")
    info1 = system_mode.mode_info()
    time.sleep(0.05)
    info2 = system_mode.mode_info()
    assert info2["uptime_s"] >= info1["uptime_s"]


# ── Lattice invariants (provable) ───────────────────────────────

def test_safe_level_strictly_greater_than_all_other_modes():
    """SAFE — highest level."""
    safe_level = SystemMode.SAFE.level
    for mode in SystemMode:
        if mode != SystemMode.SAFE:
            assert mode.level < safe_level


def test_nominal_level_strictly_less_than_all_other_modes():
    """NOMINAL — lowest level."""
    nominal_level = SystemMode.NOMINAL.level
    for mode in SystemMode:
        if mode != SystemMode.NOMINAL:
            assert mode.level > nominal_level

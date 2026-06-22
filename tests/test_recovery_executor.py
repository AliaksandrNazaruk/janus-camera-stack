"""Tests for RecoveryExecutor — isolated per-action handlers.

DI-friendly construction: each test passes mock callables. No global
state, no module-level monkeypatching. Complementary to test_recovery_ladder
(which tests integration through RecoveryLadder.escalate()).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_SERVICE_ROOT), str(_SERVICE_ROOT.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.services.fdir_events import Domain, RecoveryAction, Severity
from app.services.recovery_executor import RecoveryExecutor, _REBOOT_HANDLED_INTERNALLY
from app.services.recovery_policy import LadderLevel


def _make_level(action: RecoveryAction, name: str = "x", attempts: int = 1) -> LadderLevel:
    return LadderLevel(name=name, action=action, max_attempts=3, cooldown_sec=10, attempts=attempts)


def _make_executor(**overrides):
    """Build executor with safe defaults — each test overrides what it cares about."""
    settings = MagicMock(
        watchdog_stale_ms=1000,
        watchdog_reboot_enabled=False,  # safe default — no real reboots
        max_fdir_reboots=3,
        janus_mount_id=1305,
    )
    subprocess_mod = MagicMock()
    subprocess_mod.run.return_value = MagicMock(returncode=0, stdout='{"active": true}', stderr="")
    defaults = dict(
        read_reboot_count=MagicMock(return_value=0),
        write_reboot_count=MagicMock(),
        atomic_increment_reboot_count=MagicMock(return_value=1),
        reboot_marker_path="/tmp/test-marker",
        subprocess_module=subprocess_mod,
        run_cmd_fn=MagicMock(),
        emit_fn=MagicMock(),
        get_settings_fn=lambda: settings,
    )
    defaults.update(overrides)
    return RecoveryExecutor(**defaults), defaults


# ── RESTART_PIPELINE ──────────────────────────────────────────────────

def test_restart_pipeline_invokes_encoder_admin():
    ex, deps = _make_executor()
    level = _make_level(RecoveryAction.RESTART_PIPELINE)
    ok = ex.execute(level, "test_signal", Domain.PIPELINE)
    assert ok is True
    deps["run_cmd_fn"].assert_called_once()
    args = deps["run_cmd_fn"].call_args[0][0]
    assert args == ["sudo", "/usr/local/bin/encoder-admin", "restart"]


def test_restart_pipeline_emit_warn_with_outcome():
    ex, deps = _make_executor()
    level = _make_level(RecoveryAction.RESTART_PIPELINE, attempts=2)
    ex.execute(level, "watchdog_stale", Domain.PIPELINE)
    call = deps["emit_fn"].call_args
    assert call.kwargs["severity"] == Severity.WARN
    assert "encoder" in call.kwargs["outcome"]
    assert call.kwargs["details"]["attempt"] == 2


# ── RESTART_JANUS — ordered ───────────────────────────────────────────

def test_restart_janus_stops_encoder_first_then_restarts_janus():
    ex, deps = _make_executor()
    level = _make_level(RecoveryAction.RESTART_JANUS)
    ex.execute(level, "test", Domain.PIPELINE)
    calls = [c[0][0] for c in deps["run_cmd_fn"].call_args_list]
    assert calls[0] == ["sudo", "/usr/local/bin/encoder-admin", "stop"]
    assert calls[1] == ["sudo", "/usr/local/bin/janus-admin", "restart"]


# ── USB_RESET ─────────────────────────────────────────────────────────

def test_usb_reset_via_camera_admin():
    ex, deps = _make_executor()
    level = _make_level(RecoveryAction.USB_RESET)
    ex.execute(level, "test", Domain.PIPELINE)
    deps["run_cmd_fn"].assert_called_with(
        ["sudo", "/usr/local/bin/camera-admin", "reset-usb"], timeout=90
    )


# ── REBOOT_NODE — circuit breaker + disabled paths ───────────────────

def test_reboot_skipped_when_disabled_emits_critical_and_safe_mode():
    # System mode patched (real transition would fail on test settings)
    from unittest.mock import patch
    settings = MagicMock(watchdog_reboot_enabled=False, max_fdir_reboots=10, janus_mount_id=1305)
    ex, deps = _make_executor(get_settings_fn=lambda: settings)
    level = _make_level(RecoveryAction.REBOOT_NODE)
    with patch("app.services.recovery_executor.system_mode.transition"):
        ok = ex.execute(level, "test", Domain.PIPELINE)
    assert ok is True
    # No reboot invocation
    for call in deps["run_cmd_fn"].call_args_list:
        assert "reboot" not in call[0][0][-1]
    # Critical emit issued
    critical_emits = [c for c in deps["emit_fn"].call_args_list
                      if c.kwargs.get("severity") == Severity.CRITICAL]
    assert any("disabled" in c.kwargs["outcome"] for c in critical_emits)


def test_reboot_circuit_breaker_blocks_at_threshold():
    from unittest.mock import patch
    settings = MagicMock(watchdog_reboot_enabled=True, max_fdir_reboots=3, janus_mount_id=1305)
    ex, deps = _make_executor(
        get_settings_fn=lambda: settings,
        read_reboot_count=MagicMock(return_value=3),  # already at limit
    )
    level = _make_level(RecoveryAction.REBOOT_NODE)
    with patch("app.services.recovery_executor.system_mode.transition"):
        ok = ex.execute(level, "test", Domain.PIPELINE)
    assert ok is True  # circuit-broken path reports success (handled internally)
    # systemctl reboot NOT called
    deps["run_cmd_fn"].assert_not_called()
    # increment NOT called
    deps["atomic_increment_reboot_count"].assert_not_called()
    # Critical emit referencing circuit breaker
    critical = [c for c in deps["emit_fn"].call_args_list
                if c.kwargs.get("severity") == Severity.CRITICAL
                and "circuit breaker" in c.kwargs.get("outcome", "")]
    assert len(critical) >= 1


def test_reboot_increments_counter_when_allowed():
    from unittest.mock import patch
    settings = MagicMock(watchdog_reboot_enabled=True, max_fdir_reboots=5, janus_mount_id=1305)
    ex, deps = _make_executor(
        get_settings_fn=lambda: settings,
        read_reboot_count=MagicMock(return_value=0),
    )
    level = _make_level(RecoveryAction.REBOOT_NODE)
    with patch("app.services.recovery_executor.atomic_write_text"):
        ex.execute(level, "test", Domain.PIPELINE)
    deps["atomic_increment_reboot_count"].assert_called_once()
    # systemctl reboot invoked
    assert any(
        "reboot" in str(call[0][0]) for call in deps["run_cmd_fn"].call_args_list
    )


def test_reboot_rollback_counter_when_systemctl_fails():
    from unittest.mock import patch
    settings = MagicMock(watchdog_reboot_enabled=True, max_fdir_reboots=5, janus_mount_id=1305)
    run_cmd_failing = MagicMock(side_effect=RuntimeError("systemctl gone"))
    ex, deps = _make_executor(
        get_settings_fn=lambda: settings,
        read_reboot_count=MagicMock(return_value=2),  # simulate that it was 2
        run_cmd_fn=run_cmd_failing,
    )
    level = _make_level(RecoveryAction.REBOOT_NODE)
    with patch("app.services.recovery_executor.atomic_write_text"):
        ok = ex.execute(level, "test", Domain.PIPELINE)
    assert ok is False  # exception bubbled to executor → caught → False
    # Counter rolled back to pre-increment value (2)
    deps["write_reboot_count"].assert_called_with(2)


# ── RETRY_HANDLE ──────────────────────────────────────────────────────

def test_retry_handle_raises_when_data_plane_stale():
    """If video_age_ms > watchdog_stale_ms, retry returns failure (escalates)."""
    from unittest.mock import patch
    settings = MagicMock(watchdog_stale_ms=1000, janus_mount_id=1305)
    ex, deps = _make_executor(get_settings_fn=lambda: settings)
    level = _make_level(RecoveryAction.RETRY_HANDLE)
    # Mock janus_summary to return stale frame
    with patch("app.services.janus.janus_summary",
               return_value={"video_age_ms": 9999}):
        ok = ex.execute(level, "test", Domain.PIPELINE)
    assert ok is False  # data plane stale → execute returns False
    # Error event emitted
    error_emits = [c for c in deps["emit_fn"].call_args_list
                   if c.kwargs.get("severity") == Severity.ERROR]
    assert any("data_plane_stale" in c.kwargs.get("outcome", "") for c in error_emits)


def test_retry_handle_succeeds_when_healthy():
    from unittest.mock import patch
    settings = MagicMock(watchdog_stale_ms=10000, janus_mount_id=1305)
    ex, deps = _make_executor(get_settings_fn=lambda: settings)
    level = _make_level(RecoveryAction.RETRY_HANDLE)
    with patch("app.services.janus.janus_summary",
               return_value={"video_age_ms": 50}):
        ok = ex.execute(level, "test", Domain.PIPELINE)
    assert ok is True
    warn_emits = [c for c in deps["emit_fn"].call_args_list
                  if c.kwargs.get("severity") == Severity.WARN]
    assert any("janus_ok" in c.kwargs.get("outcome", "") for c in warn_emits)


# ── Unknown action — defensive ─────────────────────────────────────────

def test_unknown_action_returns_true_with_message():
    ex, deps = _make_executor()
    level = _make_level(RecoveryAction.NONE)  # NONE → unknown branch
    ok = ex.execute(level, "test", Domain.PIPELINE)
    assert ok is True
    # Emitted with "unknown action" outcome
    warn = [c for c in deps["emit_fn"].call_args_list
            if c.kwargs.get("severity") == Severity.WARN]
    assert any("unknown" in c.kwargs.get("outcome", "") for c in warn)

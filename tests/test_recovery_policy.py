"""Tests for recovery_policy — pure ladder structure verification.

Isolated from RecoveryLadder execution path. Verifies:
  • default_ladder() ordering and thresholds
  • Color vs depth ladder size differs by usb_reset level
  • LadderLevel dataclass defaults
  • Knobs (DEDUP, REBOOT_RESET) match documented values
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_SERVICE_ROOT), str(_SERVICE_ROOT.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.services.fdir_events import RecoveryAction
from app.services.recovery_policy import (
    DEDUP_WINDOW_SEC,
    REBOOT_COUNTER_RESET_SEC,
    LadderLevel,
    default_ladder,
)


# ── LadderLevel dataclass ─────────────────────────────────────────────

def test_ladder_level_starts_with_zero_attempts():
    lvl = LadderLevel(name="x", action=RecoveryAction.NONE, max_attempts=3, cooldown_sec=10)
    assert lvl.attempts == 0
    assert lvl.last_attempt == 0.0


def test_ladder_level_attempts_mutable():
    lvl = LadderLevel(name="x", action=RecoveryAction.NONE, max_attempts=3, cooldown_sec=10)
    lvl.attempts += 1
    assert lvl.attempts == 1


# ── Color ladder structure ────────────────────────────────────────────

def test_color_ladder_has_4_levels_no_usb_reset():
    levels = default_ladder(camera_type="color_camera")
    names = [lvl.name for lvl in levels]
    assert names == ["retry_handle", "restart_pipeline", "restart_janus", "reboot_node"]


def test_color_ladder_no_usb_reset_action():
    levels = default_ladder(camera_type="color_camera")
    actions = [lvl.action for lvl in levels]
    assert RecoveryAction.USB_RESET not in actions


# ── Depth ladder structure ────────────────────────────────────────────

def test_depth_ladder_has_5_levels_includes_usb_reset():
    levels = default_ladder(camera_type="depth_camera")
    names = [lvl.name for lvl in levels]
    assert names == ["retry_handle", "restart_pipeline", "restart_janus", "usb_reset", "reboot_node"]


def test_depth_ladder_usb_reset_before_reboot():
    levels = default_ladder(camera_type="depth_camera")
    idx_usb = next(i for i, lvl in enumerate(levels) if lvl.name == "usb_reset")
    idx_reboot = next(i for i, lvl in enumerate(levels) if lvl.name == "reboot_node")
    assert idx_usb < idx_reboot, "USB reset must precede reboot in escalation order"


# ── Threshold values match documented contract ───────────────────────

def test_retry_handle_single_attempt():
    levels = default_ladder(camera_type="color_camera")
    retry = next(lvl for lvl in levels if lvl.name == "retry_handle")
    assert retry.max_attempts == 1
    assert retry.cooldown_sec == 10


def test_restart_pipeline_has_5_attempts_short_cooldown():
    levels = default_ladder(camera_type="color_camera")
    rp = next(lvl for lvl in levels if lvl.name == "restart_pipeline")
    assert rp.max_attempts == 5
    assert rp.cooldown_sec == 45


def test_restart_janus_3_attempts():
    levels = default_ladder(camera_type="color_camera")
    rj = next(lvl for lvl in levels if lvl.name == "restart_janus")
    assert rj.max_attempts == 3
    assert rj.cooldown_sec == 90


def test_reboot_node_single_attempt_long_cooldown():
    levels = default_ladder(camera_type="color_camera")
    rb = next(lvl for lvl in levels if lvl.name == "reboot_node")
    assert rb.max_attempts == 1
    assert rb.cooldown_sec == 300, "Reboot cooldown must be 5 minutes to prevent boot loops"


# ── Idempotency / isolation ──────────────────────────────────────────

def test_ladder_returns_fresh_list_each_call():
    """Each call produces fresh levels with zero attempts — prevents shared
    mutable state between RecoveryLadder instances."""
    a = default_ladder(camera_type="color_camera")
    b = default_ladder(camera_type="color_camera")
    assert a is not b
    a[0].attempts = 99
    assert b[0].attempts == 0, "Mutation on one shouldn't leak through"


# ── Knob constants ────────────────────────────────────────────────────

def test_dedup_window_has_safe_default():
    assert DEDUP_WINDOW_SEC > 0
    assert DEDUP_WINDOW_SEC < 60, "Dedup window unreasonably long would mask real faults"


def test_reboot_counter_reset_is_long_enough_to_detect_loops():
    # If reset window too short, system reboots → 5min stable → counter resets →
    # eligible to reboot again. Need at least 15min stable to prove "no boot loop".
    assert REBOOT_COUNTER_RESET_SEC >= 900, (
        f"REBOOT_COUNTER_RESET_SEC={REBOOT_COUNTER_RESET_SEC} too short — "
        "boot loop detection requires >= 15 min stable uptime"
    )

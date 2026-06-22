"""FDIR ladder policy — level definitions + threshold config.

Sprint D extraction (Phase 1): the *configuration* of the recovery ladder
— what levels exist, how many attempts each gets, how long the cooldown
between attempts — lives here separately from execution (recovery_ladder.py)
and persistence (recovery_persistence.py).

Why separated: policy is pure data. Future config-driven ladders (per-camera
overrides, A/B-tested cooldowns) plug in here without touching execution path.
Tests for "ladder structure correct for camera_type X" cover this file
alone, no FDIR side effects required.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

from app.services.fdir_events import RecoveryAction


# ── Threshold knobs (env-overridable) ─────────────────────────────────

# Minimum seconds between escalation calls that consume an attempt.
# Prevents bursty signals from exhausting a level's budget on a single fault.
DEDUP_WINDOW_SEC = float(os.getenv("FDIR_DEDUP_SEC", "3"))

# Seconds of stable uptime before the reboot counter resets. Prevents boot
# loops: system reboots → briefly stable → fails again → reboots... A long
# reset window (default 1h) ensures FDIR doesn't loop reboot decisions.
REBOOT_COUNTER_RESET_SEC = int(os.getenv("FDIR_REBOOT_COUNTER_RESET_SEC", "3600"))


# ── Level definition ──────────────────────────────────────────────────

@dataclass
class LadderLevel:
    """Configuration for a single recovery level."""
    name: str
    action: RecoveryAction
    max_attempts: int
    cooldown_sec: float
    # runtime state — mutated by RecoveryLadder.escalate(); not policy config.
    attempts: int = 0
    last_attempt: float = 0.0


# ── Default ladder builder ────────────────────────────────────────────

def default_ladder(camera_type: Optional[str] = None) -> List[LadderLevel]:
    """Build the default 4- or 5-level ladder for the given node.

    Color nodes skip USB reset (no RealSense → no librealsense-failsafe).
    Depth nodes get the full 5 levels.

    Pure construction — no I/O, no logging, no side effects. camera_type
    parameter explicit (does not read settings here) so policy is testable
    in isolation. recovery_ladder.py resolves settings via its own patched
    get_settings() and passes the value.
    """
    if camera_type is None:
        # Backward-compat: callers that didn't pass arg get current settings.
        from app.core.settings import get_settings
        camera_type = get_settings().camera_type
    levels: List[LadderLevel] = [
        LadderLevel(
            name="retry_handle",
            action=RecoveryAction.RETRY_HANDLE,
            max_attempts=1,
            cooldown_sec=10,
        ),
        LadderLevel(
            name="restart_pipeline",
            action=RecoveryAction.RESTART_PIPELINE,
            max_attempts=5,
            cooldown_sec=45,
        ),
        LadderLevel(
            name="restart_janus",
            action=RecoveryAction.RESTART_JANUS,
            max_attempts=3,
            cooldown_sec=90,
        ),
    ]
    # USB reset only applicable on depth camera nodes (RealSense hardware).
    if camera_type == "depth_camera":
        levels.append(LadderLevel(
            name="usb_reset",
            action=RecoveryAction.USB_RESET,
            max_attempts=2,
            cooldown_sec=90,
        ))
    levels.append(LadderLevel(
        name="reboot_node",
        action=RecoveryAction.REBOOT_NODE,
        max_attempts=1,
        cooldown_sec=300,
    ))
    return levels

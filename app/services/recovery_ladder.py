"""Hierarchical FDIR recovery ladder for camera streaming.

Implements the 5-level escalation required by deep-research-report
§Autonomy / ECSS-style FDIR:

    Level 0: retry / verify Janus + pipeline health
    Level 1: restart pipeline process (ffmpeg/realsense-mux)
    Level 2: restart Janus gateway
    Level 3: USB reset (for RealSense nodes — skipped on color)
    Level 4: reboot node (last resort, bounded by reboot counter)

Each level has a bounded attempt count and cooldown.  If a level
exhausts its budget, the ladder escalates to the next level.

Persistence:
  - Process-restart state: ``/run/camera/fdir_ladder.json`` (tmpfs)
  - Reboot-surviving state: ``/var/lib/camera-fdir/`` (reboot counter)

The reboot counter prevents infinite reboot loops: after MAX_FDIR_REBOOTS
FDIR-initiated reboots the ladder enters SAFE mode instead of rebooting again.

Every action is logged via fdir_events.emit().
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import subprocess
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.settings import get_settings
from app.services.fdir_events import (
    Domain,
    RecoveryAction,
    Severity,
    emit,
)
from app.services import system_mode
from app.services.system import atomic_write_text, run as run_cmd

logger = logging.getLogger("fdir.ladder")

# ── Persistence paths ─────────────────────────────────────────────────
_LADDER_STATE_PATH = Path(os.getenv(
    "FDIR_LADDER_STATE", "/run/camera/fdir_ladder.json",
))
_REBOOT_COUNT_DIR = Path(os.getenv(
    "FDIR_PERSIST_DIR", "/var/lib/camera-fdir",
))
_REBOOT_COUNT_PATH = _REBOOT_COUNT_DIR / "reboot_count"
_REBOOT_MARKER_PATH = _REBOOT_COUNT_DIR / "last_reboot_request"

# Sprint D extraction (Phase 1): policy constants moved to recovery_policy.py.
# Re-exported here for backward compat — tests + callers monkeypatch
# _DEDUP_WINDOW_SEC and similar via this module.
from app.services.recovery_policy import (
    DEDUP_WINDOW_SEC as _DEDUP_WINDOW_SEC,
    REBOOT_COUNTER_RESET_SEC as _REBOOT_COUNTER_RESET_SEC_POLICY,
)

# Lazy metric references — loaded once outside the lock to avoid import deadlock
_rl_gauge = None
_esc_counter = None
_ladder_metrics_loaded = False


def _ensure_ladder_metrics():
    global _ladder_metrics_loaded, _rl_gauge, _esc_counter
    if _ladder_metrics_loaded:
        return
    try:
        from app.metrics import recovery_ladder_level, watchdog_escalations_total
        _rl_gauge = recovery_ladder_level
        _esc_counter = watchdog_escalations_total
    except Exception:
        pass
    _ladder_metrics_loaded = True

_REBOOT_COUNTER_RESET_SEC = _REBOOT_COUNTER_RESET_SEC_POLICY
_PROCESS_START_MONO = time.monotonic()


# ── Ladder configuration ──────────────────────────────────────────────
# Sprint D extraction (Phase 1): LadderLevel + builder live in recovery_policy.
# Re-exported here for backward compat — callers import from recovery_ladder.

from app.services.recovery_policy import LadderLevel, default_ladder as _policy_default_ladder  # noqa: E402,F401


def _default_ladder() -> List[LadderLevel]:
    """Adapter — resolves settings via this module's get_settings (which
    tests monkeypatch) then delegates to policy module's pure builder."""
    return _policy_default_ladder(camera_type=get_settings().camera_type)


# ── Persistence ───────────────────────────────────────────────────────
# Sprint D extraction: persistence logic moved to RecoveryPersistence class
# (recovery_persistence.py). These module-level wrappers preserve backward
# compatibility — existing tests monkeypatch module-level paths AND functions.
# Persistence instance constructed lazily (paths may be monkeypatched
# BEFORE first call).

from app.services.recovery_persistence import RecoveryPersistence

_persistence_instance: Optional[RecoveryPersistence] = None


def _emit_corruption(exc: Exception) -> None:
    emit(
        domain=Domain.SYSTEM,
        severity=Severity.WARN,
        detection_signal="ladder_state_corrupt",
        recovery_action=RecoveryAction.NONE,
        outcome=f"ladder state lost ({exc}), reset to level 0",
    )


def _get_persistence() -> RecoveryPersistence:
    """Lazy persistence instance — picks up CURRENT module-level paths
    (which tests may have monkeypatched).
    """
    return RecoveryPersistence(
        ladder_state_path=_LADDER_STATE_PATH,
        reboot_count_dir=_REBOOT_COUNT_DIR,
        atomic_write_text=atomic_write_text,
        emit_event=_emit_corruption,
    )


def _read_reboot_count() -> int:
    return _get_persistence().read_reboot_count()


def _write_reboot_count(n: int) -> None:
    _get_persistence().write_reboot_count(n)


def _atomic_increment_reboot_count() -> int:
    return _get_persistence().atomic_increment_reboot_count()


def _save_ladder_state(level: int, levels: List[LadderLevel], total: int) -> None:
    _get_persistence().save_ladder_state(level, levels, total)


def _load_ladder_state(levels: List[LadderLevel]) -> tuple[int, int]:
    return _get_persistence().load_ladder_state(levels)


class RecoveryLadder:
    """
    Stateful escalating recovery controller with persistence.

    Call ``escalate(signal)`` when a fault is detected.
    Call ``reset()`` when the system returns to nominal.

    State is persisted to /run/camera/ (survives process restarts).
    A reboot counter in /var/lib/camera-fdir/ prevents infinite reboot loops.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._levels = _default_ladder()
        self._current_level, self._total_recoveries = _load_ladder_state(self._levels)
        self._last_escalation_ts: float = 0.0

        # Check reboot circuit breaker
        settings = get_settings()
        reboots = _read_reboot_count()
        if reboots >= settings.max_fdir_reboots:
            logger.critical(
                "Reboot circuit breaker: %d FDIR reboots (max %d) — entering SAFE mode",
                reboots, settings.max_fdir_reboots,
            )
            self._current_level = len(self._levels)  # mark all levels exhausted
            system_mode.transition(
                system_mode.SystemMode.SAFE,
                f"reboot_circuit_breaker:{reboots}_reboots",
            )
            emit(
                domain=Domain.SYSTEM,
                severity=Severity.CRITICAL,
                detection_signal=f"fdir_reboots={reboots}",
                recovery_action=RecoveryAction.NONE,
                outcome=f"reboot circuit breaker tripped after {reboots} reboots → SAFE mode",
            )

    # ── Public API ────────────────────────────────────────────────

    def escalate(self, detection_signal: str, domain: Domain = Domain.PIPELINE) -> Dict[str, Any]:
        """
        Attempt recovery at the current ladder level.

        If the level's budget is exhausted ⇒ escalate.
        Returns a dict describing what was done.
        Thread-safe: all state mutations are protected by _lock.
        """
        with self._lock:
            return self._escalate_locked(detection_signal, domain)

    def _escalate_locked(self, detection_signal: str, domain: Domain) -> Dict[str, Any]:
        """Sprint D Phase 3: loop driven by pure decide_escalation() —
        decision logic separated from side effects (persistence, emit,
        execution)."""
        from app.services.recovery_state_machine import decide_escalation
        while True:
            now = time.monotonic()
            decision = decide_escalation(
                current_level=self._current_level,
                levels=self._levels,
                last_escalation_ts=self._last_escalation_ts,
                now=now,
                dedup_window_sec=_DEDUP_WINDOW_SEC,
            )

            if decision.kind == "exhausted":
                # All levels used → SAFE mode
                system_mode.transition(system_mode.SystemMode.SAFE, detection_signal)
                emit(
                    domain=domain,
                    severity=Severity.CRITICAL,
                    detection_signal=detection_signal,
                    recovery_action=RecoveryAction.NONE,
                    outcome="all recovery levels exhausted → SAFE mode",
                )
                return {"action": "safe_mode", "reason": "ladder_exhausted"}

            if decision.kind == "skip_dedup":
                return {"action": "dedup_skip", "level": self._levels[decision.level_idx].name}

            if decision.kind == "cooldown":
                return {
                    "action": "cooldown",
                    "remaining_sec": decision.cooldown_remaining_sec,
                    "level": self._levels[decision.level_idx].name,
                }

            if decision.kind == "escalate":
                self._escalate_to_next_locked(detection_signal, domain)
                continue

            # decision.kind == "execute"
            level = self._levels[decision.level_idx]
            level.attempts += 1
            level.last_attempt = now
            self._total_recoveries += 1
            self._last_escalation_ts = now

            # Prometheus gauge (loaded outside lock via _ensure_ladder_metrics)
            _ensure_ladder_metrics()
            if _rl_gauge is not None:
                _rl_gauge.set(self._current_level)

            # Persist BEFORE executing — if the process crashes during _execute()
            # (e.g. during systemctl restart or reboot), the attempt is still recorded.
            # Without this, the ladder reloads stale state and retries the same action.
            _save_ladder_state(self._current_level, self._levels, self._total_recoveries)
            success = self._execute(level, detection_signal, domain)

            return {
                "action": level.action.value,
                "level": level.name,
                "attempt": level.attempts,
                "max_attempts": level.max_attempts,
                "success": success,
            }

    def reset(self) -> None:
        """Reset ladder to level 0 (system recovered to nominal). Thread-safe."""
        with self._lock:
            self._reset_locked()

    def _reset_locked(self) -> None:
        _ensure_ladder_metrics()
        if _rl_gauge is not None:
            _rl_gauge.set(0)
        if self._current_level > 0:
            logger.info("Recovery ladder reset to level 0")
            emit(
                domain=Domain.SYSTEM,
                severity=Severity.INFO,
                detection_signal="system_nominal",
                recovery_action=RecoveryAction.NONE,
                outcome=f"ladder reset from level {self._current_level}",
            )
        self._current_level = 0
        for lvl in self._levels:
            lvl.attempts = 0
            lvl.last_attempt = 0.0
        self._last_escalation_ts = 0.0
        _save_ladder_state(0, self._levels, self._total_recoveries)
        # Clear reboot counter only after sustained stability (>= 1 hour uptime)
        # to prevent boot loops where system reboots, recovers briefly, then fails again.
        uptime_sec = time.monotonic() - _PROCESS_START_MONO
        if uptime_sec >= _REBOOT_COUNTER_RESET_SEC:
            _write_reboot_count(0)
        else:
            logger.info(
                "Reboot counter NOT reset: uptime %.0fs < %ds threshold",
                uptime_sec, _REBOOT_COUNTER_RESET_SEC,
            )

    def status(self) -> Dict[str, Any]:
        """Return ladder status for diagnostics. Thread-safe."""
        with self._lock:
            return self._status_locked()

    def _status_locked(self) -> Dict[str, Any]:
        return {
            "current_level": self._current_level,
            "current_level_name": self._current_level_obj().name if self._current_level_obj() else "exhausted",
            "total_recoveries": self._total_recoveries,
            "reboot_count": _read_reboot_count(),
            "max_fdir_reboots": get_settings().max_fdir_reboots,
            "levels": [
                {
                    "name": lvl.name,
                    "action": lvl.action.value,
                    "attempts": lvl.attempts,
                    "max_attempts": lvl.max_attempts,
                    "cooldown_sec": lvl.cooldown_sec,
                }
                for lvl in self._levels
            ],
        }

    # ── Private ───────────────────────────────────────────────────

    def _current_level_obj(self) -> Optional[LadderLevel]:
        if self._current_level >= len(self._levels):
            return None
        return self._levels[self._current_level]

    def _escalate_to_next_locked(self, signal: str, domain: Domain) -> None:
        old_name = self._levels[self._current_level].name
        self._current_level += 1
        _save_ladder_state(self._current_level, self._levels, self._total_recoveries)
        if self._current_level < len(self._levels):
            new_name = self._levels[self._current_level].name
            logger.warning("Escalating: %s → %s  (signal: %s)", old_name, new_name, signal)
            _ensure_ladder_metrics()
            if _esc_counter is not None:
                _esc_counter.labels(level=new_name).inc()
            emit(
                domain=domain,
                severity=Severity.WARN,
                detection_signal=signal,
                recovery_action=RecoveryAction.NONE,
                outcome=f"escalate: {old_name} → {new_name}",
            )
            system_mode.degrade(f"fdir_escalate:{new_name}")

    def _execute(self, level: LadderLevel, signal: str, domain: Domain) -> bool:
        """Execute the actual recovery action. Returns success flag.

        Sprint D Phase 2: per-action logic moved to RecoveryExecutor; this
        method preserves the public name + sequencing for test compatibility.
        Executor wired through module-level helpers _read_reboot_count etc
        so existing monkeypatch hooks continue working.
        """
        import app.services.recovery_ladder as _self
        from app.services.recovery_executor import RecoveryExecutor
        executor = RecoveryExecutor(
            read_reboot_count=_read_reboot_count,
            write_reboot_count=_write_reboot_count,
            atomic_increment_reboot_count=_atomic_increment_reboot_count,
            reboot_marker_path=_REBOOT_MARKER_PATH,
            # I/O surface injected from this module so existing tests that
            # patch `recovery_ladder.subprocess` / `run_cmd` / `emit`
            # transparently still affect the action handlers.
            subprocess_module=_self.subprocess,
            run_cmd_fn=_self.run_cmd,
            emit_fn=_self.emit,
            get_settings_fn=_self.get_settings,
        )
        return executor.execute(level, signal, domain)


# ── Module-level lazy singleton ───────────────────────────────────────
_ladder: Optional[RecoveryLadder] = None
_ladder_lock = threading.Lock()


def get_ladder() -> RecoveryLadder:
    global _ladder
    if _ladder is None:
        with _ladder_lock:
            if _ladder is None:
                _ladder = RecoveryLadder()
    return _ladder

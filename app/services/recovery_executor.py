"""Recovery action executors.

Sprint D extraction (Phase 2): the *execution* of each FDIR ladder level —
RETRY_HANDLE health probe, RESTART_PIPELINE encoder restart, RESTART_JANUS
ordered restart, USB_RESET reset cycle, REBOOT_NODE circuit-broken reboot —
lives here separated from the state machine (recovery_ladder.RecoveryLadder).

Why separated: actions are imperative shell-outs to admin CLIs. Each can be
unit-tested by mocking subprocess. State machine logic (when to escalate,
when to reset) is orthogonal — better a separate module per concern.

Compatibility: this module preserves the exact emit() signal sequence and
metric observation timing of the previous inline `_execute()` method, so
existing recovery_ladder tests pass without modification.
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from typing import TYPE_CHECKING

from app.services import fdir_quiesce, system_mode
from app.services.fdir_events import Domain, RecoveryAction, Severity

# TB-C1: a restart makes the stream stale; quiesce the stream-staleness watchdogs for the
# restart's timeout + a settle margin so they don't re-escalate the staleness we just caused.
_QUIESCE_SETTLE_SEC = 15.0
from app.services.system import atomic_write_text

if TYPE_CHECKING:
    from app.services.recovery_policy import LadderLevel

logger = logging.getLogger("fdir.executor")


class RecoveryExecutor:
    """Per-action executor — pure dispatcher to action handlers.

    Constructor takes the persistence accessors as functions so tests can
    swap them with in-memory equivalents without monkeypatching module state."""

    def __init__(
        self,
        *,
        read_reboot_count,
        write_reboot_count,
        atomic_increment_reboot_count,
        reboot_marker_path,
        # I/O surface injected so existing recovery_ladder tests that patch
        # `recovery_ladder.subprocess` / `run_cmd` / `emit` / `get_settings`
        # transparently still affect action handlers. Caller passes the
        # module-bound references that tests already monkeypatch.
        subprocess_module,
        run_cmd_fn,
        emit_fn,
        get_settings_fn,
    ):
        self._read_reboot_count = read_reboot_count
        self._write_reboot_count = write_reboot_count
        self._atomic_increment_reboot_count = atomic_increment_reboot_count
        self._reboot_marker_path = reboot_marker_path
        self._subprocess = subprocess_module
        self._run_cmd = run_cmd_fn
        self._emit = emit_fn
        self._get_settings = get_settings_fn

    # ── Public dispatch ───────────────────────────────────────────────

    def execute(self, level: "LadderLevel", signal: str, domain: Domain) -> bool:
        """Run the action for this level. Returns success flag.

        Exact behavior preserved from former `RecoveryLadder._execute()`:
        catches all exceptions, emits failure event with the action enum,
        observes recovery_action_duration_seconds metric in both branches.
        """
        action = level.action
        _start = time.monotonic()
        try:
            if action == RecoveryAction.RETRY_HANDLE:
                outcome = self._retry_handle(level)
            elif action == RecoveryAction.RESTART_PIPELINE:
                outcome = self._restart_pipeline(level)
            elif action == RecoveryAction.RESTART_JANUS:
                outcome = self._restart_janus(level)
            elif action == RecoveryAction.USB_RESET:
                outcome = self._usb_reset(level)
            elif action == RecoveryAction.REBOOT_NODE:
                # Reboot has bespoke emit path (CRITICAL events at multiple
                # decision points) — delegated method returns optional
                # "skip default emit" marker via raising _RebootEmitted.
                handled = self._reboot_node(level, signal, domain)
                if handled is _REBOOT_HANDLED_INTERNALLY:
                    self._observe_metric(level, _start)
                    return True
                outcome = handled
            else:
                outcome = f"unknown action: {action}"

            self._emit(
                domain=domain,
                severity=Severity.WARN,
                detection_signal=signal,
                recovery_action=action,
                outcome=outcome,
                details={"attempt": level.attempts, "level": level.name},
            )
            self._observe_metric(level, _start)
            return True

        except Exception as exc:
            self._emit(
                domain=domain,
                severity=Severity.ERROR,
                detection_signal=signal,
                recovery_action=action,
                outcome=f"FAILED: {exc}",
                details={"attempt": level.attempts, "level": level.name},
            )
            logger.exception("Recovery action %s failed", action.value)
            self._observe_metric(level, _start)
            return False

    # ── Per-action handlers ───────────────────────────────────────────

    def _retry_handle(self, _level: "LadderLevel") -> str:
        """Verify Janus reachable + data plane healthy + pipeline active.

        Raises if data plane stale — caller treats as failed retry,
        triggering escalation to next level."""
        settings = self._get_settings()
        from app.services import janus  # imported lazily — avoid cycle on startup
        summary = janus.janus_summary(settings.janus_mount_id)
        age = summary.get("video_age_ms")
        data_plane_ok = (
            age is not None
            and isinstance(age, (int, float))
            and age <= settings.watchdog_stale_ms
        )
        # Pipeline state via L2-owned encoder-admin (boundary: L4 doesn't know unit).
        pipeline_active = False
        try:
            result = self._subprocess.run(
                ["sudo", "/usr/local/bin/encoder-admin", "status"],
                timeout=5, capture_output=True, text=True,
            )
            if result.returncode == 0:
                pipeline_active = bool(json.loads(result.stdout).get("active"))
        except Exception:
            pass
        if not data_plane_ok:
            raise RuntimeError(
                f"handle_retry: data_plane_stale (age={age}), escalating"
            )
        return f"handle_retry: janus_ok, data_plane_ok, pipeline_active={pipeline_active}"

    def _restart_pipeline(self, _level: "LadderLevel") -> str:
        """L2-owned encoder-admin CLI (boundary: L4 doesn't know unit name)."""
        # TB-C1: quiesce the stream-staleness watchdogs while WE restart the encoder.
        with fdir_quiesce.quiesced(45 + _QUIESCE_SETTLE_SEC, "recovery: restart_pipeline",
                                   {Domain.PIPELINE, Domain.SENSOR}):
            self._run_cmd(["sudo", "/usr/local/bin/encoder-admin", "restart"], timeout=45)
        return "restarted encoder via encoder-admin"

    def _restart_janus(self, _level: "LadderLevel") -> str:
        """Ordered restart: stop encoder first to avoid pushing RTP into a
        restarting Janus (causes v4l2 buffer corruption). Both stops/
        restarts via L2/L3 admin CLIs."""
        # TB-C1/.1: quiesce while WE stop the encoder + restart Janus. This handler is itself
        # a planned Janus restart, so a JANUS-domain disturbance (Janus briefly unavailable /
        # a watchdog exception) during it is ALSO an expected side-effect — include JANUS so the
        # self-amplification can't simply move to that domain. Still deadline-bounded + scoped;
        # after the window JANUS escalates normally. (restart_pipeline keeps JANUS armed — a real
        # Janus fault during an *encoder* restart is genuine.)
        with fdir_quiesce.quiesced((15 + 60) + _QUIESCE_SETTLE_SEC, "recovery: restart_janus",
                                   {Domain.PIPELINE, Domain.SENSOR, Domain.JANUS}):
            self._run_cmd(["sudo", "/usr/local/bin/encoder-admin", "stop"], timeout=15)
            self._run_cmd(["sudo", "/usr/local/bin/janus-admin", "restart"], timeout=60)
        # ffmpeg auto-restarts via Restart=always after Janus is up
        return "restarted janus via janus-admin (ordered: encoder stopped first)"

    def _usb_reset(self, _level: "LadderLevel") -> str:
        """L0-owned camera-admin CLI — no direct systemctl unit name."""
        self._run_cmd(["sudo", "/usr/local/bin/camera-admin", "reset-usb"], timeout=90)
        return "usb_reset via camera-admin"

    def _reboot_node(self, level: "LadderLevel", signal: str, domain: Domain):
        """Reboot path with bespoke emit sequence (CRITICAL events).

        Returns either a string outcome (caller emits standard event) or
        the _REBOOT_HANDLED_INTERNALLY sentinel (caller skips emit because
        we already emitted CRITICAL for policy/circuit-breaker decisions).
        """
        settings = self._get_settings()
        if not settings.watchdog_reboot_enabled:
            logger.warning("Reboot disabled by CAM_WATCHDOG_REBOOT_ENABLED=0 → SAFE mode")
            system_mode.transition(
                system_mode.SystemMode.SAFE,
                "reboot_disabled_by_config",
            )
            self._emit(
                domain=domain,
                severity=Severity.CRITICAL,
                detection_signal=signal,
                recovery_action=level.action,
                outcome="reboot skipped (disabled) → SAFE mode",
            )
            return _REBOOT_HANDLED_INTERNALLY

        # Circuit breaker
        reboots = self._read_reboot_count()
        if reboots >= settings.max_fdir_reboots:
            logger.critical("Reboot circuit breaker: %d reboots → SAFE mode", reboots)
            system_mode.transition(
                system_mode.SystemMode.SAFE,
                f"reboot_circuit_breaker:{reboots}",
            )
            self._emit(
                domain=domain,
                severity=Severity.CRITICAL,
                detection_signal=signal,
                recovery_action=level.action,
                outcome=f"reboot blocked (circuit breaker: {reboots} reboots) → SAFE mode",
            )
            return _REBOOT_HANDLED_INTERNALLY

        # Write reboot marker + increment counter BEFORE reboot
        # (process won't survive a successful reboot).
        self._atomic_increment_reboot_count()
        try:
            atomic_write_text(
                self._reboot_marker_path,
                json.dumps({"ts": time.time(), "signal": signal}) + "\n",
            )
        except OSError:
            pass

        self._emit(
            domain=domain,
            severity=Severity.CRITICAL,
            detection_signal=signal,
            recovery_action=level.action,
            outcome=f"initiating node reboot (count={reboots + 1})",
        )
        try:
            self._run_cmd(["sudo", "-n", "/usr/local/bin/service-admin", "reboot"], timeout=10)
        except RuntimeError:
            # Reboot command failed — roll back counter so the circuit
            # breaker budget is not consumed by a failed attempt.
            logger.error("Reboot command failed, rolling back reboot counter")
            self._write_reboot_count(reboots)
            raise
        return "reboot initiated"

    # ── Metric observation (preserves exact timing) ───────────────────

    def _observe_metric(self, level: "LadderLevel", start: float) -> None:
        duration = time.monotonic() - start
        try:
            from app.metrics import recovery_action_duration_seconds
            recovery_action_duration_seconds.labels(action=level.name).observe(duration)
        except Exception:
            pass


# Sentinel — used because returning None could be confused with "outcome string
# is empty". The _reboot_node path emits CRITICAL events directly at several
# decision branches, so the dispatcher skips the default WARN emit.
_REBOOT_HANDLED_INTERNALLY = object()

"""Mode policy enforcement for rover-grade camera streaming.

Listens to system mode transitions and enforces the declared policies:
- SAFE mode: stop the camera pipeline service (safety-critical halt)
- DEGRADED / LOCAL_ONLY: write fps_profile signal for pipeline adjustment
- NOMINAL: ensure pipeline is running, clear fps_profile

Registered during app startup via ``register()``.
"""
from __future__ import annotations

import json
import logging
import subprocess
import time

from app.core.settings import get_settings
from app.services.fdir_events import Domain, RecoveryAction, Severity, emit
from app.services.system import atomic_write_text
from app.services.system_mode import (
    MODE_POLICIES,
    SystemMode,
    on_transition,
)

logger = logging.getLogger("mode_enforcer")

_SYSTEMCTL_TIMEOUT = 30
_registered = False


def register() -> None:
    """Register the enforcer as a mode transition listener. Idempotent."""
    global _registered
    if _registered:
        return
    on_transition(_on_mode_transition)
    _registered = True
    logger.info("Mode enforcer registered")


def _on_mode_transition(
    previous: SystemMode, target: SystemMode, reason: str,
) -> None:
    """Callback fired on every mode transition — enforces the target policy."""
    policy = MODE_POLICIES[target]
    settings = get_settings()
    service = settings.service_name

    logger.warning(
        "ENFORCE mode=%s streams_enabled=%s max_fps=%d reason=%s",
        target.value, policy.streams_enabled, policy.max_fps, reason,
    )

    try:
        if target == SystemMode.SAFE:
            _stop_pipeline(service, reason)
        elif target == SystemMode.NOMINAL:
            _clear_fps_profile()
            # Restart encoder if it was running with a profile override — otherwise
            # fps_profile delete does not take effect (ffmpeg continues with old args).
            if previous in (SystemMode.DEGRADED, SystemMode.LOCAL_ONLY) and _is_service_active(service):
                _restart_pipeline(reason)
            else:
                _ensure_pipeline_running(service, reason)
        elif target in (SystemMode.DEGRADED, SystemMode.LOCAL_ONLY):
            _write_fps_profile(target, policy.max_fps, policy.max_bitrate_kbps)
            # Restart encoder so the new profile is picked up (closes DEGRADED loop —
            # previously fps_profile was a dead-code path, P1-PERF-001 in external review).
            if _is_service_active(service):
                _restart_pipeline(reason)
            else:
                _ensure_pipeline_running(service, reason)
    except Exception:
        logger.exception("Mode enforcement failed for %s", target.value)

    _persist_history(previous, target, reason)


def _stop_pipeline(service: str, reason: str) -> None:
    """Stop the camera pipeline — SAFE mode critical halt.

    `service` param kept for backward-compat in log messages, but the operation
    goes via encoder-admin CLI (L2 owns unit name). See ADR in host_infra.
    """
    if _is_service_active(service):
        logger.critical("SAFE MODE: stopping pipeline via encoder-admin")
        try:
            subprocess.run(
                ["sudo", "/usr/local/bin/encoder-admin", "stop"],
                timeout=_SYSTEMCTL_TIMEOUT,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            logger.critical(
                "encoder-admin stop failed (rc=%d): %s",
                exc.returncode, exc.stderr.decode(errors="replace").strip(),
            )
        except subprocess.TimeoutExpired:
            logger.critical("encoder-admin stop timed out after %ds", _SYSTEMCTL_TIMEOUT)

        # Verify the service actually stopped
        if _is_service_active(service):
            logger.critical(
                "SAFETY VIOLATION: pipeline %s still active after stop command", service,
            )
            emit(
                domain=Domain.SYSTEM,
                severity=Severity.CRITICAL,
                detection_signal=reason,
                recovery_action=RecoveryAction.RESTART_PIPELINE,
                outcome=f"pipeline stop FAILED — service still active: {service}",
            )
        else:
            emit(
                domain=Domain.SYSTEM,
                severity=Severity.CRITICAL,
                detection_signal=reason,
                recovery_action=RecoveryAction.RESTART_PIPELINE,
                outcome=f"pipeline stopped: {service}",
            )
    else:
        logger.info("Pipeline %s already stopped", service)


def _restart_pipeline(reason: str) -> None:
    """Restart encoder via L2 admin CLI — picks up new fps_profile / env values.

    Used at mode transitions DEGRADED↔NOMINAL so the new profile actually
    takes effect (ffmpeg reads fps_profile only on startup).
    """
    logger.warning("Restarting encoder via encoder-admin (mode change: %s)", reason)
    try:
        subprocess.run(
            ["sudo", "/usr/local/bin/encoder-admin", "restart"],
            timeout=_SYSTEMCTL_TIMEOUT,
            check=True,
            capture_output=True,
        )
        emit(
            domain=Domain.SYSTEM,
            severity=Severity.INFO,
            detection_signal=reason,
            recovery_action=RecoveryAction.RESTART_PIPELINE,
            outcome="encoder restarted to apply mode profile",
        )
    except subprocess.CalledProcessError as exc:
        logger.error("encoder-admin restart failed (rc=%d): %s",
                     exc.returncode, exc.stderr.decode(errors="replace").strip())
    except subprocess.TimeoutExpired:
        logger.error("encoder-admin restart timed out")


def _ensure_pipeline_running(service: str, reason: str) -> None:
    """Start the pipeline if it is not running. Uses encoder-admin (L2 boundary)."""
    if not _is_service_active(service):
        logger.warning("Starting pipeline via encoder-admin (mode requires streams)")
        try:
            subprocess.run(
                ["sudo", "/usr/local/bin/encoder-admin", "start"],
                timeout=_SYSTEMCTL_TIMEOUT,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            logger.error(
                "encoder-admin start failed (rc=%d): %s",
                exc.returncode, exc.stderr.decode(errors="replace").strip(),
            )
        except subprocess.TimeoutExpired:
            logger.error("encoder-admin start timed out")

        # Verify the service actually started
        if not _is_service_active(service):
            logger.error("Pipeline %s failed to start after start command", service)

        emit(
            domain=Domain.SYSTEM,
            severity=Severity.INFO,
            detection_signal=reason,
            recovery_action=RecoveryAction.RESTART_PIPELINE,
            outcome=f"pipeline started: {service}",
        )


def _is_service_active(service: str) -> bool:
    """Check active state via encoder-admin status (L2 boundary)."""
    try:
        result = subprocess.run(
            ["sudo", "/usr/local/bin/encoder-admin", "status"],
            timeout=5, capture_output=True, text=True,
        )
        if result.returncode != 0:
            return False
        import json as _json
        return bool(_json.loads(result.stdout).get("active"))
    except Exception:
        return False


def _write_fps_profile(mode: SystemMode, max_fps: int, max_bitrate_kbps: int) -> None:
    """Write the fps/bitrate profile signal file for the pipeline to read."""
    fps_profile_path = get_settings().fps_profile_path
    profile = "low" if mode == SystemMode.DEGRADED else "local"
    try:
        atomic_write_text(
            fps_profile_path,
            json.dumps({
                "profile": profile,
                "max_fps": max_fps,
                "max_bitrate_kbps": max_bitrate_kbps,
                "mode": mode.value,
                "ts": time.time(),
            }),
        )
        logger.info("fps_profile written: profile=%s fps=%d bitrate=%d", profile, max_fps, max_bitrate_kbps)
    except OSError:
        logger.exception("Failed to write fps_profile to %s", fps_profile_path)


def _clear_fps_profile() -> None:
    """Remove the fps_profile signal (return to defaults)."""
    fps_profile_path = get_settings().fps_profile_path
    try:
        if fps_profile_path.exists():
            fps_profile_path.unlink()
            logger.info("fps_profile cleared (nominal)")
    except OSError:
        logger.exception("Failed to clear fps_profile")


def _persist_history(previous: SystemMode, target: SystemMode, reason: str) -> None:
    """Append transition to mode history (ring buffer of last 50 entries)."""
    mode_history_path = get_settings().mode_history_path
    try:
        mode_history_path.parent.mkdir(parents=True, exist_ok=True)
        history = []
        if mode_history_path.exists():
            try:
                history = json.loads(mode_history_path.read_text())
            except Exception:
                history = []
        history.append({
            "from": previous.value,
            "to": target.value,
            "reason": reason,
            "ts": time.time(),
        })
        # Keep last 50 entries
        history = history[-50:]
        atomic_write_text(mode_history_path, json.dumps(history, indent=1))
    except OSError:
        logger.debug("Failed to persist mode history")

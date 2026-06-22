"""Structured audit log for admin actions (Phase 2).

Every admin endpoint logs an audit entry with:
- timestamp (ISO 8601)
- source_ip
- action (verb + endpoint)
- target (resource being modified)
- outcome (success / failure / denied)
- request_id (correlation across logs)
- session/user identifier (best-effort from X-Admin-Token presence)

Output: structured JSON lines to /var/log/camera-audit/audit.jsonl with rotation.
Operator-readable: jq queries for compliance/forensics.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

# Audit log location — bound and rotated.
AUDIT_LOG_DIR = Path(os.environ.get("AUDIT_LOG_DIR", "/var/log/camera-audit"))
AUDIT_LOG_FILE = AUDIT_LOG_DIR / "audit.jsonl"
ROTATE_AT_BYTES = int(os.environ.get("AUDIT_ROTATE_BYTES", str(10 * 1024 * 1024)))   # 10MB
KEEP_BACKUPS = int(os.environ.get("AUDIT_KEEP_BACKUPS", "5"))

_write_lock = threading.Lock()


def _rotate_if_needed() -> None:
    """Size-based rotation: audit.jsonl → audit.jsonl.1 .. .5 → drop oldest."""
    try:
        if not AUDIT_LOG_FILE.exists():
            return
        if AUDIT_LOG_FILE.stat().st_size < ROTATE_AT_BYTES:
            return
        # Drop oldest beyond KEEP_BACKUPS
        for i in range(KEEP_BACKUPS, 0, -1):
            src = AUDIT_LOG_DIR / f"audit.jsonl.{i}"
            if i == KEEP_BACKUPS and src.exists():
                src.unlink()
                continue
            if src.exists():
                dst = AUDIT_LOG_DIR / f"audit.jsonl.{i + 1}"
                src.rename(dst)
        AUDIT_LOG_FILE.rename(AUDIT_LOG_DIR / "audit.jsonl.1")
    except OSError as e:
        log.warning("audit log rotation failed: %s", e)


def emit(
    *,
    action: str,
    target: str,
    outcome: str,
    source_ip: Optional[str] = None,
    request_id: Optional[str] = None,
    user: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Emit one audit log entry.

    Args:
        action: HTTP verb + endpoint (e.g., "POST /api/v1/cameras/X/depth/initialize")
        target: resource being acted upon (e.g., "depth-sensor:X:depth")
        outcome: "success" | "failure" | "denied" | "error"
        source_ip: client IP
        request_id: correlation ID
        user: user identifier (e.g. "admin" if X-Admin-Token valid, else "anon")
        details: extra fields (status code, error message, parameters)
    """
    entry: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "target": target,
        "outcome": outcome,
        "source_ip": source_ip or "unknown",
        "request_id": request_id or "-",
        "user": user or "anon",
    }
    if details:
        entry["details"] = details

    with _write_lock:
        try:
            AUDIT_LOG_DIR.mkdir(parents=True, exist_ok=True)
            _rotate_if_needed()
            with open(AUDIT_LOG_FILE, "a") as f:
                f.write(json.dumps(entry, separators=(",", ":")) + "\n")
        except OSError as e:
            log.warning("audit log write failed: %s", e)


# ── convenience wrapper ────────────────────────────────────────────────
# Markers in a dotted action verb that imply a non-success outcome.
_FAILURE_MARKERS = ("_failed", ".failed", "refused", "bad_", ".denied", "_error", ".unknown")


def audit(
    action: str,
    details: Optional[Dict[str, Any]] = None,
    *,
    outcome: Optional[str] = None,
    target: Optional[str] = None,
    request: Any = None,
) -> None:
    """Convenience wrapper over :func:`emit` for admin route call-sites.

    Call-sites use ``audit("verb.noun", {details})``; this maps that form to a
    full audit entry. ``outcome`` is inferred from the action verb when not
    supplied. Pass ``request`` (a Starlette/FastAPI ``Request``) to capture
    source_ip + user.

    History: four route modules previously imported a *missing* ``audit`` symbol
    behind a no-op ``except`` fallback (this module exports ``emit``), so every
    call silently wrote nothing. This makes them real — do NOT reintroduce the
    no-op fallback (see tests/test_audit_log.py).
    """
    if outcome is None:
        low = action.lower()
        outcome = "failure" if any(m in low for m in _FAILURE_MARKERS) else "success"
    source_ip = request_id = user = None
    if request is not None:
        try:
            source_ip = request.client.host if request.client else None
            request_id = request.headers.get("x-request-id")
            user = "admin" if request.headers.get("x-admin-token") else "anon"
        except Exception:  # pragma: no cover - auditing must never break a request
            pass
    emit(
        action=action,
        target=target or action,
        outcome=outcome,
        source_ip=source_ip,
        request_id=request_id,
        user=user,
        details=details,
    )

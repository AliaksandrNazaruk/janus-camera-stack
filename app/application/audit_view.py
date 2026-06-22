"""Use-case: read the audit log tail with filters (newest-first).
Extracted from admin_dashboard (C-04 Phase 4); behavior verbatim. The AuditEntry model
lives here (route response shape unchanged).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

from app.services import audit_log as audit_log_service

log = logging.getLogger(__name__)
AUDIT_LOG_FILE = audit_log_service.AUDIT_LOG_FILE


class AuditEntry(BaseModel):
    ts: str
    action: str
    outcome: Optional[str] = None
    target: Optional[str] = None
    source_ip: Optional[str] = None
    request_id: Optional[str] = None
    extra: Dict[str, Any] = {}


def read_audit_tail(
    limit: int = 50,
    action_substr: Optional[str] = None,
    target_substr: Optional[str] = None,
    outcome: Optional[str] = None,
    since_ts: Optional[str] = None,   # ISO 8601 string, e.g. "2026-06-15T00:00:00"
) -> Tuple[List[AuditEntry], bool]:
    """Read newest-first audit entries with optional filters.

    Scans from the tail of the file backwards so the whole log isn't loaded into RAM
    for very long files (audit can grow).
    """
    if not AUDIT_LOG_FILE.exists():
        return [], False
    try:
        all_lines = AUDIT_LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        log.warning("audit log read failed: %s", exc)
        return [], False

    action_l = action_substr.lower() if action_substr else None
    target_l = target_substr.lower() if target_substr else None
    outcome_l = outcome.lower() if outcome else None

    entries: List[AuditEntry] = []
    examined = 0
    for line in reversed(all_lines):
        examined += 1
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        if action_l and action_l not in str(obj.get("action", "")).lower():
            continue
        if target_l and target_l not in str(obj.get("target", "")).lower():
            continue
        if outcome_l and outcome_l != str(obj.get("outcome", "")).lower():
            continue
        if since_ts:
            ts_str = str(obj.get("ts", ""))
            if ts_str and ts_str < since_ts:
                break

        extra = {k: v for k, v in obj.items() if k not in (
            "ts", "action", "outcome", "target", "source_ip", "request_id"
        )}
        entries.append(AuditEntry(
            ts=str(obj.get("ts", "")),
            action=str(obj.get("action", "")),
            outcome=obj.get("outcome"),
            target=obj.get("target"),
            source_ip=obj.get("source_ip"),
            request_id=obj.get("request_id"),
            extra=extra,
        ))
        if len(entries) >= limit:
            break

    truncated = examined < len(all_lines)
    return entries, truncated

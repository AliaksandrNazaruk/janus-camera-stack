"""Cycle 8B — the canonical admin/runtime operation-status vocabulary.

The four operation mechanisms keep their own machinery but MAP their domain status onto this shared
``OperationStatus`` so the operator's read model is uniform across them:
  * node ops          (operation_journal):     running / succeeded / failed / interrupted
  * runtime-cfg apply (runtime_revision_store): validated / applying / applied / rolled_back / rollback_failed
  * NAT update        (janus-nat.status.json):  pending / applied / failed / unknown
  * service restart   (services_admin):         ok (bool)

This is a VOCABULARY, not an executor — see docs/design/ADMIN_OPERATION_MODEL.md (Cycle 8A recon). It
adds NOTHING to those mechanisms' storage; they project onto it for a uniform read surface.
"""
from __future__ import annotations

from enum import Enum


class OperationStatus(str, Enum):
    """Canonical outcome of an admin/runtime operation (the operator-facing read model). Recovery /
    rollback states collapse to FAILED — the detail string carries the specifics."""
    PENDING = "pending"      # accepted/persisted, not yet confirmed-applied
    RUNNING = "running"      # in flight (async node op, or a mid-apply state seen post-crash)
    SUCCEEDED = "succeeded"  # terminal OK
    FAILED = "failed"        # terminal not-OK (incl. interrupted / rolled_back / rollback_failed)


# Every persistent DOMAIN status string the four mechanisms write, mapped to the canonical status.
# Keep this COMPLETE — guard #24 fails if a known domain status is missing (so the read model can't
# silently mis-bucket a new status). Synonyms are intentional (apply 'applied' == NAT 'ok' == SUCCEEDED).
_DOMAIN_TO_CANONICAL = {
    # node ops (operation_journal)
    "running": OperationStatus.RUNNING,
    "succeeded": OperationStatus.SUCCEEDED,
    "failed": OperationStatus.FAILED,
    "interrupted": OperationStatus.FAILED,
    # runtime-config apply / revision store
    "validated": OperationStatus.PENDING,
    "applying": OperationStatus.RUNNING,
    "applied": OperationStatus.SUCCEEDED,
    "rolling_back": OperationStatus.RUNNING,   # in-flight recovery; terminal will be rolled_back/failed
    "rolled_back": OperationStatus.FAILED,
    "rollback_failed": OperationStatus.FAILED,
    # NAT update sidecar
    "pending": OperationStatus.PENDING,
    "unknown": OperationStatus.PENDING,   # no record / lost → not-confirmed-applied (operator re-checks)
    "ok": OperationStatus.SUCCEEDED,       # service restart RestartResponse.ok=True
}

# The domain statuses each mechanism is known to emit — the guard checks these are all mapped above.
KNOWN_DOMAIN_STATUSES = frozenset(_DOMAIN_TO_CANONICAL)


def canonical_status(domain_status: str) -> OperationStatus:
    """Map a mechanism's domain status string to the canonical OperationStatus. An UNRECOGNIZED status
    fails CLOSED to FAILED — an unknown status must never read as success."""
    return _DOMAIN_TO_CANONICAL.get((domain_status or "").lower(), OperationStatus.FAILED)


def status_from_ok(ok: bool) -> OperationStatus:
    """Map a boolean-outcome operation (service restart, NAT result) onto the canonical status."""
    return OperationStatus.SUCCEEDED if ok else OperationStatus.FAILED

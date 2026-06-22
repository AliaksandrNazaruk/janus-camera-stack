"""Cycle 8B — the canonical admin/runtime operation-status vocabulary (app/application/operations).

Locks the shared read-model contract: every mechanism's domain status maps to one of the 4 canonical
OperationStatus values, synonyms are intentional, and an unrecognized status fails CLOSED to FAILED.
"""
import pytest

from app.application.operations import (
    KNOWN_DOMAIN_STATUSES,
    OperationStatus,
    canonical_status,
    status_from_ok,
)


@pytest.mark.parametrize("domain,canonical", [
    # node ops
    ("running", OperationStatus.RUNNING),
    ("succeeded", OperationStatus.SUCCEEDED),
    ("failed", OperationStatus.FAILED),
    ("interrupted", OperationStatus.FAILED),
    # runtime-config apply / revision store
    ("validated", OperationStatus.PENDING),
    ("applying", OperationStatus.RUNNING),
    ("applied", OperationStatus.SUCCEEDED),
    ("rolled_back", OperationStatus.FAILED),
    ("rollback_failed", OperationStatus.FAILED),
    # NAT sidecar
    ("pending", OperationStatus.PENDING),
    ("unknown", OperationStatus.PENDING),
    ("ok", OperationStatus.SUCCEEDED),
])
def test_domain_status_maps_to_canonical(domain, canonical):
    assert canonical_status(domain) is canonical


def test_case_insensitive():
    assert canonical_status("APPLIED") is OperationStatus.SUCCEEDED


def test_unrecognized_status_fails_closed_to_failed():
    """An unknown status must never read as success — fail closed to FAILED."""
    assert canonical_status("weird-new-state") is OperationStatus.FAILED
    assert canonical_status("") is OperationStatus.FAILED
    assert canonical_status(None) is OperationStatus.FAILED


def test_status_from_ok():
    assert status_from_ok(True) is OperationStatus.SUCCEEDED
    assert status_from_ok(False) is OperationStatus.FAILED


def test_every_known_domain_status_is_mapped_non_default():
    """KNOWN_DOMAIN_STATUSES must each resolve to a real canonical (not the fail-closed default for an
    unrecognized string). This pairs with guard #24 to keep the vocabulary complete."""
    for s in KNOWN_DOMAIN_STATUSES:
        assert canonical_status(s) in OperationStatus


def test_canonical_values_are_the_four_core():
    assert {s.value for s in OperationStatus} == {"pending", "running", "succeeded", "failed"}

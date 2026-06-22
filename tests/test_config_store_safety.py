"""Cycle 1 Tier 2: runtime config/revision stores fail CLOSED on CONTENT corruption.

A corrupt revision must NOT be hidden as "revision not found" (apply would silently mislead) — it is
quarantined + raises StoreCorrupt (→ the app-level 503 handler). Missing / bad-id stay a legit None.
"""
from __future__ import annotations

import pytest

from app.services import runtime_config_apply as apply
from app.services import runtime_revision_store as store
from app.services.store_safety import StoreCorrupt


@pytest.fixture
def revdir(tmp_path, monkeypatch):
    d = tmp_path / "revisions"
    d.mkdir()
    monkeypatch.setattr(store, "REVISION_DIR", d)        # apply reads via store.REVISION_DIR too
    return d


def _write_rev(revdir, rid, content):
    (revdir / f"{rid}.json").write_text(content)


# ── runtime_config_apply._read_raw_revision (the apply path) ────────────

def test_apply_read_missing_returns_none(revdir):
    assert apply._read_raw_revision("rev-deadbeef") is None


def test_apply_read_bad_id_returns_none(revdir):
    assert apply._read_raw_revision("../etc/passwd") is None
    assert apply._read_raw_revision("not-a-rev") is None


def test_apply_read_corrupt_revision_fails_closed_not_notfound(revdir):
    _write_rev(revdir, "rev-corrupt1", "{not valid json")
    with pytest.raises(StoreCorrupt):
        apply._read_raw_revision("rev-corrupt1")
    assert list(revdir.glob("rev-corrupt1.json.corrupt.*"))   # quarantined, not hidden as not-found


def test_apply_read_non_object_fails_closed(revdir):
    _write_rev(revdir, "rev-arr1", "[1, 2, 3]")
    with pytest.raises(StoreCorrupt):
        apply._read_raw_revision("rev-arr1")


# ── runtime_revision_store.get_revision / set_status ────────────────────

def test_get_revision_corrupt_fails_closed_not_none(revdir):
    _write_rev(revdir, "rev-bad2", "garbage{")
    with pytest.raises(StoreCorrupt):
        store.get_revision("rev-bad2")
    assert list(revdir.glob("rev-bad2.json.corrupt.*"))


def test_get_revision_missing_returns_none(revdir):
    assert store.get_revision("rev-nope") is None


def test_set_status_corrupt_fails_closed(revdir):
    _write_rev(revdir, "rev-bad3", "not json at all {")
    with pytest.raises(StoreCorrupt):
        store.set_status("rev-bad3", store.STATUS_VALIDATED)

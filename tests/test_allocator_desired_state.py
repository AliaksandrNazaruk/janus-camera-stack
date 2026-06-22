"""Tests for mountpoint_allocator desired_active extension (Sprint X4).

Covers:
  • Backward-compat: load existing records without desired_active field
  • Round-trip: write/read preserves the flag
  • set_desired idempotent
  • ensure() preregisters static (color) allocation
  • allocate() preserves desired_active across re-calls
  • list_desired_active() filters correctly
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.services.mountpoint_allocator import (
    Allocation,
    LOCAL_SERIAL,
    COLOR_MP_ID,
    COLOR_RTP_PORT,
    allocate,
    ensure,
    get_allocation,
    list_allocations,
    list_desired_active,
    release,
    set_desired,
)


@pytest.fixture
def state_path(tmp_path):
    return tmp_path / "allocations.json"


# ── Backward compat ───────────────────────────────────────────────────

def test_loads_legacy_record_without_desired_active_field(state_path):
    """Records written before Sprint X4 had no desired_active field —
    must load with desired_active=False (safe default)."""
    legacy = {
        "version": 1,
        "allocations": {
            "141722072135:depth": {"mp_id": 1306, "rtp_port": 5006},
        },
    }
    state_path.write_text(json.dumps(legacy))

    alloc = get_allocation("141722072135", "depth", state_path=state_path)
    assert alloc is not None
    assert alloc.mp_id == 1306
    assert alloc.rtp_port == 5006
    assert alloc.desired_active is False


def test_list_allocations_handles_mixed_legacy_and_new(state_path):
    mixed = {
        "version": 1,
        "allocations": {
            "S1:depth": {"mp_id": 1306, "rtp_port": 5006},  # legacy
            "S2:ir1":   {"mp_id": 1307, "rtp_port": 5008, "desired_active": True},
        },
    }
    state_path.write_text(json.dumps(mixed))

    all_ = list_allocations(state_path=state_path)
    assert all_["S1:depth"].desired_active is False
    assert all_["S2:ir1"].desired_active is True


# ── Round-trip ────────────────────────────────────────────────────────

def test_allocate_writes_desired_active_false_by_default(state_path):
    alloc = allocate("S1", "depth", state_path=state_path)
    assert alloc.desired_active is False

    on_disk = json.loads(state_path.read_text())
    record = on_disk["allocations"]["S1:depth"]
    assert "desired_active" in record
    assert record["desired_active"] is False


def test_set_desired_persists_flag(state_path):
    allocate("S1", "depth", state_path=state_path)
    updated = set_desired("S1", "depth", True, state_path=state_path)
    assert updated.desired_active is True

    reloaded = get_allocation("S1", "depth", state_path=state_path)
    assert reloaded.desired_active is True


def test_set_desired_is_idempotent(state_path):
    allocate("S1", "depth", state_path=state_path)
    set_desired("S1", "depth", True, state_path=state_path)
    set_desired("S1", "depth", True, state_path=state_path)
    alloc = get_allocation("S1", "depth", state_path=state_path)
    assert alloc.desired_active is True


def test_set_desired_can_toggle_back_to_false(state_path):
    allocate("S1", "depth", state_path=state_path)
    set_desired("S1", "depth", True, state_path=state_path)
    set_desired("S1", "depth", False, state_path=state_path)
    alloc = get_allocation("S1", "depth", state_path=state_path)
    assert alloc.desired_active is False


def test_set_desired_raises_keyerror_if_not_allocated(state_path):
    with pytest.raises(KeyError, match="no allocation"):
        set_desired("S1", "depth", True, state_path=state_path)


# ── allocate() preserves desired_active across re-calls ───────────────

def test_reallocate_preserves_desired_active(state_path):
    """initialize() calls allocate() then set_desired(). Subsequent
    initialize() must not reset operator's flag."""
    allocate("S1", "depth", state_path=state_path)
    set_desired("S1", "depth", True, state_path=state_path)
    # Re-allocate (simulates re-initialize)
    alloc2 = allocate("S1", "depth", state_path=state_path)
    assert alloc2.desired_active is True


# ── ensure() — static allocation registration ─────────────────────────

def test_ensure_registers_static_allocation(state_path):
    alloc = ensure(LOCAL_SERIAL, "color", COLOR_MP_ID, COLOR_RTP_PORT, state_path=state_path)
    assert alloc.mp_id == COLOR_MP_ID
    assert alloc.rtp_port == COLOR_RTP_PORT
    assert alloc.desired_active is False


def test_ensure_is_idempotent_preserves_existing(state_path):
    ensure(LOCAL_SERIAL, "color", COLOR_MP_ID, COLOR_RTP_PORT, state_path=state_path)
    set_desired(LOCAL_SERIAL, "color", True, state_path=state_path)
    # Second ensure() must NOT reset desired_active
    alloc = ensure(LOCAL_SERIAL, "color", COLOR_MP_ID, COLOR_RTP_PORT, state_path=state_path)
    assert alloc.desired_active is True


# ── list_desired_active() filter ──────────────────────────────────────

def test_list_desired_active_filters_to_marked_only(state_path):
    allocate("S1", "depth", state_path=state_path)
    allocate("S1", "ir1", state_path=state_path)
    allocate("S1", "ir2", state_path=state_path)
    set_desired("S1", "depth", True, state_path=state_path)
    set_desired("S1", "ir2", True, state_path=state_path)

    desired = list_desired_active(state_path=state_path)
    assert set(desired.keys()) == {"S1:depth", "S1:ir2"}


def test_list_desired_active_empty_when_none_marked(state_path):
    allocate("S1", "depth", state_path=state_path)
    desired = list_desired_active(state_path=state_path)
    assert desired == {}


def test_list_desired_active_handles_missing_state_file(state_path):
    # state_path does not exist yet
    desired = list_desired_active(state_path=state_path)
    assert desired == {}


# ── Allocation dataclass ──────────────────────────────────────────────

def test_allocation_to_dict_includes_desired_active():
    a = Allocation(mp_id=1306, rtp_port=5006, desired_active=True)
    assert a.to_dict() == {
        "mp_id": 1306,
        "rtp_port": 5006,
        "desired_active": True,
    }


def test_allocation_from_raw_handles_missing_field():
    """Backward-compat: Allocation.from_raw must default missing field to False."""
    a = Allocation.from_raw({"mp_id": 1306, "rtp_port": 5006})
    assert a.desired_active is False


def test_release_drops_entry_with_desired_active(state_path):
    allocate("S1", "depth", state_path=state_path)
    set_desired("S1", "depth", True, state_path=state_path)
    assert release("S1", "depth", state_path=state_path) is True
    assert get_allocation("S1", "depth", state_path=state_path) is None


# ── ensure() clobber guard (G1 prerequisite — review R2-M2) ───────────

def test_ensure_rejects_clobber_same_mp_different_key(state_path):
    """The serial='unknown' hiccup must not create a 2nd '*:color' row on
    1305/5004 (two rows owning the live stream's mountpoint)."""
    from app.services.mountpoint_allocator import AllocationError
    ensure("A", "color", COLOR_MP_ID, COLOR_RTP_PORT, state_path=state_path)
    with pytest.raises(AllocationError, match="refusing to clobber"):
        ensure("unknown", "color", COLOR_MP_ID, COLOR_RTP_PORT, state_path=state_path)


def test_ensure_rejects_clobber_overlapping_rtcp_port(state_path):
    from app.services.mountpoint_allocator import AllocationError
    ensure("A", "depth", 1306, 5006, state_path=state_path)         # reserves 5006,5007
    with pytest.raises(AllocationError, match="refusing to clobber"):
        ensure("B", "depth", 1399, 5007, state_path=state_path)     # 5007 overlaps the RTCP slot


def test_ensure_same_key_still_idempotent(state_path):
    ensure("A", "color", COLOR_MP_ID, COLOR_RTP_PORT, state_path=state_path)
    again = ensure("A", "color", COLOR_MP_ID, COLOR_RTP_PORT, state_path=state_path)
    assert again.mp_id == COLOR_MP_ID  # same key short-circuits, no clobber error

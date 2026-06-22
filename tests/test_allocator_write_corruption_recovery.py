"""Cycle 15A — allocator write-path corruption recovery.

The READ helpers fail-SAFE to empty on corruption (guard #26). Before Cycle 15A the WRITE path
was inconsistent: invalid JSON CRASHED (`_flock_state` did an unwrapped `json.loads`) while invalid
shape silently reset. A truncated allocator therefore crashed the boot reconciler's seed write.

GO (D1=C, D2=B, D4=B): any CONTENT corruption (invalid JSON or invalid shape) is quarantined to a
`.corrupt.<ts>` forensic copy, the state is reset to empty, and the requested mutation PROCEEDS. An
IO read error instead RAISES `AllocationError` (the file may be fine, just transiently unreadable —
resetting would destroy good data). The read fail-safe is unchanged."""
from __future__ import annotations

import glob
import json

import pytest

from app.services import mountpoint_allocator as m


def _q(path):
    return glob.glob(str(path) + ".corrupt.*")


# ── invalid JSON: write no longer crashes, quarantines, resets, proceeds ──

def test_invalid_json_allocate_does_not_crash_and_persists(tmp_path):
    p = tmp_path / "a.json"
    p.write_text('{"allocations": {"x"')                 # truncated → was JSONDecodeError crash
    alloc = m.allocate("141722072135", "color", state_path=p)   # must NOT raise
    assert alloc.mp_id and alloc.rtp_port
    # file is valid JSON again, with the new allocation persisted
    persisted = json.loads(p.read_text())["allocations"]
    assert "141722072135:color" in persisted
    # forensic copy of the corrupt bytes was preserved
    assert _q(p), "expected a .corrupt.* quarantine artifact"


def test_invalid_json_ensure_does_not_crash(tmp_path):
    p = tmp_path / "a.json"
    p.write_text("not json at all")
    alloc = m.ensure("local", "color", m.COLOR_MP_ID, m.COLOR_RTP_PORT, state_path=p)
    assert alloc.mp_id == m.COLOR_MP_ID
    assert _q(p)


def test_invalid_json_release_and_migrate_do_not_crash(tmp_path):
    p = tmp_path / "a.json"
    p.write_text("{broken")
    assert m.release("s", "color", state_path=p) is False     # reset → key absent, no crash
    p.write_text("{broken")                                    # corrupt again for migrate
    assert m.migrate_color_key("141722072135", state_path=p) is False


# ── invalid shape: now also quarantined (was silent data loss) ──

@pytest.mark.parametrize("allocations", [None, "garbage", 123])
def test_invalid_shape_allocations_quarantined_and_reset(tmp_path, allocations):
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"version": 1, "allocations": allocations}))
    m.allocate("141722072135", "color", state_path=p)
    assert "141722072135:color" in json.loads(p.read_text())["allocations"]
    assert _q(p), "invalid shape must leave a forensic copy too (D4=B uniform)"


def test_non_dict_root_quarantined_and_reset(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(json.dumps([1, 2, 3]))                       # root is a list, not an object
    m.allocate("141722072135", "color", state_path=p)
    assert "141722072135:color" in json.loads(p.read_text())["allocations"]
    assert _q(p)


# ── IO read error: RAISE, never reset (must not destroy good data) ──

def test_io_error_raises_allocation_error_not_reset(tmp_path):
    d = tmp_path / "as_dir"
    # a directory as the state path → read_text raises IsADirectoryError (an OSError subclass)
    d.mkdir()
    with pytest.raises(m.AllocationError):
        m.allocate("141722072135", "color", state_path=d)
    assert not _q(d), "IO error must NOT quarantine/reset — the file may be perfectly good"


# ── probe (D2=B): surface the lingering quarantine even once the file is valid again ──

def test_corruption_status_reports_quarantine_after_recovery(tmp_path):
    p = tmp_path / "a.json"
    p.write_text('{"allocations": {"x"')                      # invalid JSON
    m.allocate("141722072135", "color", state_path=p)         # recovers: quarantine + reset + write
    st = m.allocator_corruption_status(p)
    assert st["allocator_state"] == "ok"                      # the live file IS valid now
    # ...but the forensic copy stays discoverable
    assert st.get("quarantine") and ".corrupt." in st["quarantine"]


# ── read fail-safe unchanged (guard #26 territory; local sanity) ──

def test_read_helpers_still_failsafe_after_change(tmp_path):
    p = tmp_path / "a.json"
    p.write_text("{still broken")
    assert m.list_allocations(p) == {}
    assert m.list_desired_active(p) == {}
    assert m.get_allocation("s", "color", p) is None

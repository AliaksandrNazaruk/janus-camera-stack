"""FDIR-KEY-001 — canonical sensor keying + corrupt-allocations fail-safe.

Color historically used the ``local`` sentinel serial while depth/IR used the
device serial. That heterogeneous identity model, combined with a corrupt
``{"allocations": null}`` state file, let a running color encoder be dropped and
not restored. These tests pin the fixes:
  - canonical ``<serial>:<sensor>`` keys,
  - legacy ``local:color`` migration (idempotent),
  - ``allocations: null`` never crashes readers nor erases running intent.
"""
import json
from pathlib import Path

import pytest

from app.services import mountpoint_allocator as m


def _state(tmp_path, allocations):
    p = tmp_path / "sensor_allocations.json"
    p.write_text(json.dumps({"version": 1, "allocations": allocations}))
    return p


# ── canonical key format ────────────────────────────────────────────

def test_key_is_serial_colon_sensor():
    assert m._key("141722072135", "color") == "141722072135:color"
    assert m._key("141722072135", "depth") == "141722072135:depth"


def test_allocate_writes_canonical_serial_key(tmp_path):
    p = tmp_path / "a.json"
    m.allocate("141722072135", "color", state_path=p)
    keys = list(json.loads(p.read_text())["allocations"])
    assert keys == ["141722072135:color"]  # never "local:color"


# ── legacy migration ────────────────────────────────────────────────

def test_legacy_local_color_migrated(tmp_path):
    p = _state(tmp_path, {"local:color": {"mp_id": 1305, "rtp_port": 5004,
                                          "desired_active": True}})
    assert m.migrate_color_key("141722072135", p) is True
    allocs = json.loads(p.read_text())["allocations"]
    assert "141722072135:color" in allocs
    assert "local:color" not in allocs
    # mp_id / rtp_port / desired_active preserved
    assert allocs["141722072135:color"] == {"mp_id": 1305, "rtp_port": 5004,
                                             "desired_active": True}


def test_migration_idempotent(tmp_path):
    p = _state(tmp_path, {"local:color": {"mp_id": 1305, "rtp_port": 5004,
                                          "desired_active": True}})
    assert m.migrate_color_key("141722072135", p) is True
    assert m.migrate_color_key("141722072135", p) is False  # nothing left to move


def test_migration_noop_for_sentinel_serial(tmp_path):
    p = _state(tmp_path, {"local:color": {"mp_id": 1305, "rtp_port": 5004,
                                          "desired_active": True}})
    assert m.migrate_color_key(m.LOCAL_SERIAL, p) is False
    assert "local:color" in json.loads(p.read_text())["allocations"]


def test_migration_does_not_clobber_existing_canonical(tmp_path):
    p = _state(tmp_path, {
        "local:color": {"mp_id": 1305, "rtp_port": 5004, "desired_active": False},
        "141722072135:color": {"mp_id": 1305, "rtp_port": 5004, "desired_active": True},
    })
    assert m.migrate_color_key("141722072135", p) is False
    assert json.loads(p.read_text())["allocations"]["141722072135:color"]["desired_active"] is True


def test_depth_ir_keys_unchanged(tmp_path):
    p = _state(tmp_path, {"141722072135:depth": {"mp_id": 1306, "rtp_port": 5006,
                                                 "desired_active": True}})
    m.migrate_color_key("141722072135", p)
    assert set(json.loads(p.read_text())["allocations"]) == {"141722072135:depth"}


# ── corrupt allocations: None fail-safe ─────────────────────────────

def test_corrupt_allocations_none_readers_safe(tmp_path):
    p = _state(tmp_path, None)  # {"allocations": null}
    assert m.list_allocations(p) == {}
    assert m.list_desired_active(p) == {}
    assert m.get_allocation("141722072135", "color", p) is None


def test_corrupt_allocations_none_does_not_persist(tmp_path):
    p = _state(tmp_path, None)
    # any write path coerces null → {} and writes a real allocation
    m.allocate("141722072135", "color", state_path=p)
    allocs = json.loads(p.read_text())["allocations"]
    assert isinstance(allocs, dict)
    assert "141722072135:color" in allocs


def test_corrupt_allocations_non_dict_resets(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"version": 1, "allocations": "garbage"}))
    assert m.list_allocations(p) == {}  # no crash, treated as empty


def test_empty_allocations_does_not_erase_via_read(tmp_path):
    # A corrupt/empty desired set must read as "unknown", never silently used to
    # stop streams — list_desired_active just returns {} without mutating state.
    p = _state(tmp_path, None)
    before = p.read_text()
    m.list_desired_active(p)
    assert p.read_text() == before  # read is non-destructive

"""Cycle 1: node_secrets.json (agent-token store) fails CLOSED on corruption — no silent token regen.

Pins the fix for the audit's High finding: a corrupt node_secrets.json must NOT read as {} (which made
nodes.py mint a fresh token → control-plane ↔ node-agent bearer mismatch). It must quarantine + raise.
"""
from __future__ import annotations

import pytest

from app.services.stream_binding_store import secrets as sec
from app.services.stream_binding_store.state_file import StoreCorruptionError


def _secrets_file(state_path):
    return state_path.with_name("node_secrets.json")


# ── absent / empty are legitimate (NOT corruption) ──────────────────────

def test_read_missing_returns_empty(tmp_path):
    assert sec._read_secrets(tmp_path / "state.json") == {}


def test_read_empty_file_returns_empty(tmp_path):
    _secrets_file(tmp_path / "state.json").write_text("")
    assert sec._read_secrets(tmp_path / "state.json") == {}


# ── corruption fails CLOSED (quarantine + raise, never {}) ───────────────

def test_read_corrupt_json_quarantines_and_raises(tmp_path):
    sp = tmp_path / "state.json"
    f = _secrets_file(sp)
    f.write_text("{not valid json")
    with pytest.raises(StoreCorruptionError):
        sec._read_secrets(sp)
    assert list(tmp_path.glob("node_secrets.json.corrupt.*"))   # forensic copy made
    assert f.read_text() == "{not valid json"                   # original LEFT (stays detectable)


def test_read_non_object_raises(tmp_path):
    _secrets_file(tmp_path / "state.json").write_text('["a", "b"]')   # valid JSON, wrong shape
    with pytest.raises(StoreCorruptionError):
        sec._read_secrets(tmp_path / "state.json")


def test_set_on_corrupt_store_fails_closed_does_not_overwrite(tmp_path):
    sp = tmp_path / "state.json"
    _secrets_file(sp).write_text("garbage")
    with pytest.raises(StoreCorruptionError):
        sec._set_node_secret("node-a", "tok-a", sp)
    assert _secrets_file(sp).read_text() == "garbage"           # not silently clobbered


# ── normal RMW: 0600, preserves existing, removable ─────────────────────

def test_set_then_set_preserves_and_is_0600(tmp_path):
    sp = tmp_path / "state.json"
    sec._set_node_secret("node-a", "tok-a", sp)
    sec._set_node_secret("node-b", "tok-b", sp)
    assert sec._read_secrets(sp) == {"node-a": "tok-a", "node-b": "tok-b"}
    assert (_secrets_file(sp).stat().st_mode & 0o777) == 0o600


def test_remove_node_secret(tmp_path):
    sp = tmp_path / "state.json"
    sec._set_node_secret("node-a", "tok-a", sp)
    sec._set_node_secret("node-b", "tok-b", sp)
    sec._remove_node_secret("node-a", sp)
    assert sec._read_secrets(sp) == {"node-b": "tok-b"}


def test_concurrent_set_node_secret_no_lost_update(tmp_path):
    """flock serialises the RMW: concurrent _set_node_secret for distinct nodes all persist (without
    the lock, the load→modify→save would lost-update some across the file I/O)."""
    from concurrent.futures import ThreadPoolExecutor
    sp = tmp_path / "state.json"
    nodes = [f"node-{i}" for i in range(8)]
    with ThreadPoolExecutor(max_workers=len(nodes)) as ex:
        list(ex.map(lambda n: sec._set_node_secret(n, f"tok-{n}", sp), nodes))
    data = sec._read_secrets(sp)
    assert set(data) == set(nodes)
    assert all(data[n] == f"tok-{n}" for n in nodes)

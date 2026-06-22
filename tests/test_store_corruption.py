"""Topology store corruption → fail-closed / quarantine (#2 hardening, review H-02).

Before: `_load_state` swallowed a corrupt stream_bindings.json and returned an EMPTY
topology — the reconciler would then treat the wiped fleet as desired. After: a
corrupt store is quarantined (timestamped forensic copy, original preserved) and
reads + mutations raise StoreCorruptionError; readyz surfaces topology_store_corrupt.
"""
from __future__ import annotations

import glob
import json
import os
import sys

import pytest

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.services import stream_binding_store as sbs


def test_corrupt_json_quarantines_and_raises(tmp_path):
    p = tmp_path / "stream_bindings.json"
    p.write_text("{ this is : not valid json ]")
    with pytest.raises(sbs.StoreCorruptionError):
        sbs._load_state(p)
    q = glob.glob(str(p) + ".corrupt.*")
    assert q, "expected a .corrupt.<ts> forensic quarantine copy"
    assert p.exists(), "original corrupt file must be PRESERVED (durable detection)"


def test_corrupt_does_not_become_empty_topology(tmp_path):
    p = tmp_path / "stream_bindings.json"
    p.write_text("garbage{")
    al = tmp_path / "al.json"
    with pytest.raises(sbs.StoreCorruptionError):
        sbs.list_bindings(state_path=p, alloc_state_path=al)
    with pytest.raises(sbs.StoreCorruptionError):
        sbs.list_nodes(state_path=p)


def test_mutation_fails_closed_and_preserves_file(tmp_path):
    p = tmp_path / "stream_bindings.json"
    p.write_text("not json at all")
    before = p.read_text()
    with pytest.raises(sbs.StoreCorruptionError):
        sbs.add_node_by_host("192.168.1.55", state_path=p)
    assert p.read_text() == before, "mutation must NOT overwrite the corrupt file"


def test_non_object_json_is_corruption(tmp_path):
    p = tmp_path / "stream_bindings.json"
    p.write_text("[1, 2, 3]")           # valid JSON, but not an object
    with pytest.raises(sbs.StoreCorruptionError):
        sbs._load_state(p)


def test_absent_and_empty_are_not_corruption(tmp_path):
    assert sbs._load_state(tmp_path / "absent.json")["nodes"] == {}
    empty = tmp_path / "empty.json"
    empty.write_text("")
    assert sbs._load_state(empty)["bindings"] == {}
    blank = tmp_path / "blank.json"
    blank.write_text("   \n\t")
    assert sbs._load_state(blank)["nodes"] == {}


def test_valid_state_still_loads(tmp_path):
    p = tmp_path / "stream_bindings.json"
    sbs.add_node_by_host("192.168.1.55", display_name="cam55", state_path=p)
    nodes = sbs.list_nodes(state_path=p)   # includes the synthetic local cam10 node too
    assert any(n.host == "192.168.1.55" for n in nodes.values()), nodes
    assert sbs.store_corruption_status(p) == {"topology_store_corrupt": False}


def test_quarantine_is_idempotent(tmp_path):
    p = tmp_path / "stream_bindings.json"
    p.write_text("{bad")
    for _ in range(3):
        with pytest.raises(sbs.StoreCorruptionError):
            sbs._load_state(p)
    assert len(glob.glob(str(p) + ".corrupt.*")) == 1, "only ONE forensic copy per episode"


def test_store_corruption_status_probe(tmp_path):
    p = tmp_path / "stream_bindings.json"
    assert sbs.store_corruption_status(p)["topology_store_corrupt"] is False  # absent
    p.write_text("{bad")
    st = sbs.store_corruption_status(p)
    assert st["topology_store_corrupt"] is True
    assert st["quarantine"] is not None and ".corrupt." in st["quarantine"]


def test_readyz_503_on_corrupt_store(monkeypatch):
    from app.routes import system
    monkeypatch.setattr("app.core.settings.is_production", lambda: False)
    monkeypatch.setattr(sbs, "store_corruption_status",
                        lambda *a, **k: {"topology_store_corrupt": True,
                                         "detail": "bad json", "quarantine": "/x.corrupt.1"})
    resp = system.readyz()
    assert resp.status_code == 503
    body = json.loads(resp.body)
    assert body["topology_store_corrupt"] is True and body["ok"] is False


async def test_app_maps_store_corruption_to_clean_503():
    from app import create_app
    app = create_app()
    assert sbs.StoreCorruptionError in app.exception_handlers, "handler not registered"
    resp = await app.exception_handlers[sbs.StoreCorruptionError](
        None, sbs.StoreCorruptionError("bad json; quarantined /x.corrupt.1"))
    assert resp.status_code == 503
    body = json.loads(resp.body)
    assert body["topology_store_corrupt"] is True and body["ok"] is False

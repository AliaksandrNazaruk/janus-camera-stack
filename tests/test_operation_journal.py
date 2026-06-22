"""Phase 11 — durable OperationJournal + NodeOperationRunner + restart reaper (D5).

Covers: journal begin/finish/conflict/durability; the runner's success/failure/per-node-409;
and the startup reaper (R1) — restart-orphaned `running` ops → `interrupted`, un-sticking a node
left mid-provision (→ failed, retriable) while NEVER clobbering a terminal provision_state.
"""
from __future__ import annotations

import os
import sys
import threading
import time

import pytest

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.services import node_operation_runner as R
from app.services import operation_journal as J
from app.services import stream_binding_store as sbs
from app.services.operation_journal import OperationConflict


@pytest.fixture(autouse=True)
def _tmp_ops(tmp_path, monkeypatch):
    """Point the journal at a tmp file (read at call time via DEFAULT_OPS_PATH)."""
    monkeypatch.setattr(J, "DEFAULT_OPS_PATH", tmp_path / "operations.json")


def _wait_done(node_id, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if J.running_for_node(node_id) is None:
            return
        time.sleep(0.01)
    raise AssertionError(f"op for {node_id} did not finish in {timeout}s")


# ── journal ─────────────────────────────────────────────────────────────────
def test_begin_finish_lifecycle():
    rec = J.begin("cam55", "provision", "op1")
    assert rec["status"] == "running"
    assert J.running_for_node("cam55")["operation_id"] == "op1"
    J.finish("op1", "succeeded")
    assert J.running_for_node("cam55") is None and J.all_running() == []


def test_get_returns_record_or_none():
    """H2: the by-id read backing GET /operations/{id}."""
    J.begin("cam55", "provision", "op1")
    got = J.get("op1")
    assert got["op_type"] == "provision" and got["node_id"] == "cam55" and got["status"] == "running"
    J.finish("op1", "succeeded")
    assert J.get("op1")["status"] == "succeeded"   # terminal state is read back
    assert J.get("does-not-exist") is None


# ── H3: corruption → quarantine + fail-closed (never silently empty) ──────────
def _corrupt_files(parent):
    return [f for f in parent.iterdir() if f.name.startswith("operations.json.corrupt-")]


def test_corrupt_journal_quarantines_and_begin_fails_closed():
    p = J.DEFAULT_OPS_PATH
    p.write_text("{ not valid json")
    with pytest.raises(J.JournalCorrupt):
        J.begin("cam55", "provision", "op1")        # begin must NOT silently start on an empty journal
    assert not p.exists()                            # quarantined: original moved aside
    assert _corrupt_files(p.parent), "evidence file should be preserved"
    # self-heal: the bad file is gone → a fresh begin works (empty journal), guard intact
    assert J.begin("cam55", "provision", "op2")["status"] == "running"


def test_reads_fail_closed_on_corrupt():
    J.DEFAULT_OPS_PATH.write_text("not json")
    with pytest.raises(J.JournalCorrupt):
        J.list_recent()
    J.DEFAULT_OPS_PATH.write_text("still not json")  # first read quarantined it; re-corrupt for get()
    with pytest.raises(J.JournalCorrupt):
        J.get("whatever")


def test_oserror_propagates_without_quarantine():
    """A transient OSError (here: a directory at the path → IsADirectoryError) must propagate, NOT be
    mistaken for corruption — we never quarantine/destroy on a transient read error."""
    p = J.DEFAULT_OPS_PATH
    p.mkdir()                                        # path is now a directory → read_text raises OSError
    with pytest.raises(OSError):
        J.list_recent()
    assert p.is_dir() and not _corrupt_files(p.parent)


def test_reap_survives_corrupt_journal():
    """Startup policy: a corrupt journal is quarantined + logged CRITICAL, boot continues empty."""
    J.DEFAULT_OPS_PATH.write_text("{corrupt")
    assert R.reap_orphans() == []                    # no crash, nothing reaped
    assert _corrupt_files(J.DEFAULT_OPS_PATH.parent)


def test_begin_conflict_same_node_only():
    J.begin("cam55", "provision", "op1")
    with pytest.raises(OperationConflict) as e:
        J.begin("cam55", "activate", "op2")
    assert "busy: provision already in progress" in str(e.value)
    J.begin("cam99", "provision", "op3")            # a different node is fine
    assert len(J.all_running()) == 2


def test_finish_records_status_and_last_error():
    J.begin("cam55", "provision", "op1")
    J.finish("op1", "failed", last_error="boom")
    rec = J.list_recent()[0]
    assert rec["status"] == "failed" and rec["last_error"] == "boom"


def test_running_is_durable_across_reload():
    J.begin("cam55", "provision", "op1")
    assert [o["operation_id"] for o in J.all_running()] == ["op1"]   # a fresh file read still sees it


# ── runner ──────────────────────────────────────────────────────────────────
def test_run_success_marks_succeeded():
    ran = []
    R.run("cam55", "provision", lambda: ran.append(1))
    _wait_done("cam55")
    assert ran == [1] and J.list_recent()[0]["status"] == "succeeded"


def test_run_failure_marks_failed_with_last_error():
    def boom():
        raise RuntimeError("kaboom")
    R.run("cam55", "provision", boom)
    _wait_done("cam55")
    rec = J.list_recent()[0]
    assert rec["status"] == "failed" and "kaboom" in rec["last_error"]


def test_run_conflict_is_per_node():
    ev = threading.Event()
    R.run("cam55", "provision", lambda: ev.wait(2.0))   # holds the running slot
    try:
        with pytest.raises(OperationConflict):
            R.run("cam55", "activate", lambda: None)
    finally:
        ev.set()
        _wait_done("cam55")


# ── reaper (R1) ──────────────────────────────────────────────────────────────
def _seed_node(state_path, node_id="cam55", pstate="probing"):
    sbs.upsert_node(node_id, host="192.168.1.55", role="depth_camera", state_path=state_path)
    sbs.set_provision_state(node_id, pstate, state_path=state_path)


def test_reap_interrupts_and_unsticks_in_progress(tmp_path):
    bind = tmp_path / "stream_bindings.json"
    _seed_node(bind, pstate="probing")
    J.begin("cam55", "provision", "orphan1")            # left "running" by a dead process
    reaped = R.reap_orphans(state_path=bind)
    assert [o["operation_id"] for o in reaped] == ["orphan1"]
    assert J.all_running() == []                        # now interrupted
    node = sbs.get_node("cam55", state_path=bind)
    assert node.provision_state == "failed"
    assert "interrupted by restart" in (node.last_error or "")


def test_reap_does_not_clobber_terminal_state(tmp_path):
    bind = tmp_path / "stream_bindings.json"
    _seed_node(bind, pstate="ready")                    # terminal — must survive
    J.begin("cam55", "rotate-token", "orphan2")
    R.reap_orphans(state_path=bind)
    node = sbs.get_node("cam55", state_path=bind)
    assert node.provision_state == "ready"              # untouched
    assert J.all_running() == []                        # op still marked interrupted


# ── route boundary: durable conflict → 409 ───────────────────────────────────
def test_route_wrapper_maps_conflict_to_409(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from app.routes import stream_bindings as sb
    # the wrapper derives ops_path from BIND_STATE_PATH.parent — point it at the same tmp dir
    # the autouse fixture uses for the journal, so both see the same operations.json.
    monkeypatch.setattr(sb, "BIND_STATE_PATH", tmp_path / "stream_bindings.json")
    J.begin("cam55", "provision", "busy1")              # node already busy (durable)
    with pytest.raises(HTTPException) as e:
        sb._spawn_node_op("cam55", "provision", lambda: None)   # must not start a thread
    assert e.value.status_code == 409 and "busy: provision already in progress" in e.value.detail
    assert len(J.all_running()) == 1                    # the 2nd op was NOT recorded

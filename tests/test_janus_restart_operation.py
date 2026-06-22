"""Cycle 13 — the additive tracked Janus restart operation (POST /janus/restart-tracked).

Reuses operation_journal via node_operation_runner with a synthetic `local_janus` scope. The sync
/janus/restart is unchanged (pinned by test_janus_routes.TestJanusRestart). See
docs/design/JANUS_RESTART_OPERATION.md.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from app.application import janus_restart
from app.services import nat_config, operation_journal
from app.services.operation_journal import OperationConflict


# ── route adapter ───────────────────────────────────────────────────────────

class TestTrackedRestartRoute:
    @pytest.mark.asyncio
    @patch("app.routes.janus.janus_restart_uc.start_tracked_restart", return_value="op123")
    async def test_returns_202_with_operation_id(self, _mock, admin_client):
        resp = await admin_client.post("/janus/restart-tracked")
        assert resp.status_code == 202
        body = resp.json()
        assert body["operation_id"] == "op123"
        assert body["operation_status"] == "running"
        assert body["status_url"] == "/api/v1/admin/operations/op123"

    @pytest.mark.asyncio
    @patch("app.routes.janus.janus_restart_uc.start_tracked_restart",
           side_effect=OperationConflict("local_janus", "janus_restart"))
    async def test_conflict_returns_409(self, _mock, admin_client):
        resp = await admin_client.post("/janus/restart-tracked")
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_requires_admin(self, client):
        resp = await client.post("/janus/restart-tracked")   # no admin token
        assert resp.status_code == 403


# ── use-case wiring + real journal integration ──────────────────────────────

def test_use_case_runs_restart_via_node_operation_runner(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        janus_restart.node_operation_runner, "run",
        lambda node_id, op_type, fn, **kw: seen.update(
            node_id=node_id, op_type=op_type, fn=fn, ops_path=kw.get("ops_path")) or "opX")
    op_id = janus_restart.start_tracked_restart()
    assert op_id == "opX"
    assert seen["node_id"] == "local_janus" and seen["op_type"] == "janus_restart"
    assert seen["fn"] is nat_config.restart_janus and seen["ops_path"] is None


def _poll_terminal(op_id: str, path, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rec = operation_journal.get(op_id, path=path)
        if rec and rec["status"] != "running":
            return rec
        time.sleep(0.02)
    return operation_journal.get(op_id, path=path)


def test_success_records_succeeded_in_journal(tmp_path, monkeypatch):
    ops = tmp_path / "operations.json"
    monkeypatch.setattr(nat_config, "restart_janus", lambda: None)   # no real subprocess
    op_id = janus_restart.start_tracked_restart(ops_path=ops)
    rec = _poll_terminal(op_id, ops)
    assert rec is not None
    assert rec["op_type"] == "janus_restart" and rec["node_id"] == "local_janus"
    assert rec["status"] == "succeeded"


def test_failure_records_failed_with_last_error(tmp_path, monkeypatch):
    def _boom():
        raise nat_config.JanusAdminError("janus-admin restart exit=4", exit_code=4)
    ops = tmp_path / "operations.json"
    monkeypatch.setattr(nat_config, "restart_janus", _boom)
    op_id = janus_restart.start_tracked_restart(ops_path=ops)
    rec = _poll_terminal(op_id, ops)
    assert rec["status"] == "failed" and "exit=4" in rec["last_error"]


def test_one_running_restart_at_a_time(tmp_path, monkeypatch):
    """OperationConflict (→ route 409): a second start while one is running is refused."""
    ops = tmp_path / "operations.json"
    monkeypatch.setattr(nat_config, "restart_janus",
                        lambda: (time.sleep(0.3), None)[1])   # hold the op 'running' briefly
    op_id = janus_restart.start_tracked_restart(ops_path=ops)
    try:
        with pytest.raises(OperationConflict):
            janus_restart.start_tracked_restart(ops_path=ops)   # one already running
    finally:
        _poll_terminal(op_id, ops)   # let the first finish so the journal is clean

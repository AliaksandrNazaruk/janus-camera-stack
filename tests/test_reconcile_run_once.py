"""Explicit run-once Janus reconcile (step 2 of the reconcile-model roadmap).

Acceptance: creates MISSING mountpoints for ACTIVE remote bindings only; skips
operator-stopped (fdir/configured_offline) and maintenance; never destroys an orphan,
never restarts/firewall/systemctl; corrupt store -> 503 (StoreCorruptionError); Janus
down -> 503; idempotent (2nd call creates 0); response carries before/after drift +
created/existing/skipped/failed.
"""
from __future__ import annotations

import inspect
import json
import os
import sys

import pytest

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.services import binding_provision as bp
from app.services import janus_admin
from app.services import stream_binding_store as sbs


def _bj(sensor, mp, port, status, enabled, node="node-x"):
    return {"binding_id": f"{node}:{sensor}", "node_id": node, "sensor": sensor,
            "mode": "remote_producer", "status": status,
            "transport": {"rtp_port": port, "payload_type": 96, "codec": "h264", "srtp": None},
            "janus": {"mountpoint_id": mp, "rtp_iface": "192.168.1.10"},
            "fdir": {"enabled": enabled, "policy": "stream_default"}}


def _seed(tmp_path, bindings):
    sp = tmp_path / "sb.json"
    ap = tmp_path / "al.json"
    sp.write_text(json.dumps({
        "version": 1,
        "nodes": {"node-x": {"host": "192.168.1.55", "role": "remote_producer", "ordinal": 0}},
        "bindings": {b["binding_id"]: b for b in bindings},
    }))
    return sp, ap


@pytest.fixture
def mock_janus(monkeypatch):
    state = {"live": [1305], "created": []}
    monkeypatch.setattr(janus_admin, "list_mountpoints", lambda: [{"id": i} for i in state["live"]])

    def create_mp(**kw):
        state["created"].append(kw["mp_id"])
        state["live"].append(kw["mp_id"])
        return {"streaming": "created"}
    monkeypatch.setattr(janus_admin, "create_mountpoint", create_mp)

    if hasattr(janus_admin, "destroy_mountpoint"):       # run-once must NEVER destroy
        def no_destroy(**kw):
            raise AssertionError("run-once must not destroy mountpoints")
        monkeypatch.setattr(janus_admin, "destroy_mountpoint", no_destroy)
    return state


def _run(sp, ap):
    return bp.run_janus_reconcile_once(mp_secret="s", state_path=sp, alloc_state_path=ap,
                                       rtp_age_fn=lambda mp: 5)


def test_creates_missing_active_skips_stopped(tmp_path, mock_janus):
    sp, ap = _seed(tmp_path, [
        _bj("color", 2000, 5100, "online", True),
        _bj("depth", 2001, 5102, "online", True),
        _bj("ir1", 2002, 5104, "configured_offline", False),   # operator-stopped
    ])
    r = _run(sp, ap)
    assert r["result"] == {"created": 2, "existing": 0, "skipped": 1, "failed": 0}
    assert sorted(mock_janus["created"]) == [2000, 2001]      # ir1's 2002 NOT created
    assert 2002 not in mock_janus["created"]
    assert r["before"]["drift"] is True and r["after"]["drift"] is False
    assert r["ok"] is True and r["action"] == "janus_reconcile_run_once" and r["dry_run"] is False


def test_idempotent_second_call_creates_zero(tmp_path, mock_janus):
    sp, ap = _seed(tmp_path, [
        _bj("color", 2000, 5100, "online", True),
        _bj("depth", 2001, 5102, "online", True),
        _bj("ir1", 2002, 5104, "configured_offline", False),
    ])
    _run(sp, ap)
    mock_janus["created"].clear()
    r2 = _run(sp, ap)
    assert r2["result"]["created"] == 0
    assert r2["result"]["existing"] == 2 and r2["result"]["skipped"] == 1
    assert mock_janus["created"] == [] and r2["after"]["drift"] is False


def test_orphan_unexpected_is_reported_not_destroyed(tmp_path, mock_janus):
    mock_janus["live"].append(2099)                      # an orphan remote-range mountpoint
    sp, ap = _seed(tmp_path, [_bj("color", 2000, 5100, "online", True)])
    r = _run(sp, ap)
    assert r["result"]["created"] == 1                   # color created
    assert r["before"]["counts"].get("unexpected_janus_mountpoint") == 1   # reported
    assert 2099 in mock_janus["live"]                    # NOT destroyed (still live)
    # after still reports the orphan as drift (we never remove it)
    assert r["after"]["counts"].get("unexpected_janus_mountpoint") == 1


def test_local_range_remote_binding_refused_R6(tmp_path, mock_janus):
    # a hand-edited remote binding squatting a cam10-owned id must NOT be ensured
    sp, ap = _seed(tmp_path, [_bj("color", 1306, 5100, "online", True)])
    r = _run(sp, ap)
    assert r["result"]["failed"] == 1 and r["result"]["created"] == 0
    assert 1306 not in mock_janus["created"]
    assert r["outcomes"]["node-x:color"] == "refused_local_range"


def test_per_binding_failure_isolation(tmp_path, mock_janus, monkeypatch):
    sp, ap = _seed(tmp_path, [
        _bj("color", 2000, 5100, "online", True),
        _bj("depth", 2001, 5102, "online", True),
    ])

    def flaky(**kw):
        if kw["mp_id"] == 2000:
            raise janus_admin.JanusAdminError("boom on color")
        mock_janus["live"].append(kw["mp_id"])
        return {"streaming": "created"}
    monkeypatch.setattr(janus_admin, "create_mountpoint", flaky)
    r = _run(sp, ap)
    assert r["result"]["failed"] == 1 and r["result"]["created"] == 1    # depth still made it
    assert r["ok"] is False


# ── route-level: R5 (corrupt) and Janus-down -> 503, and read-only (R3/R4) ──

def test_route_corrupt_store_raises_for_503(tmp_path, monkeypatch):
    from app.routes import stream_bindings as sb
    p = tmp_path / "sb.json"
    p.write_text("{ corrupt")
    monkeypatch.setattr(sb, "BIND_STATE_PATH", p)
    monkeypatch.setattr(sb, "ALLOC_STATE_PATH", tmp_path / "al.json")
    with pytest.raises(sbs.StoreCorruptionError):       # app handler -> 503 topology_store_corrupt
        sb.reconcile_janus_run_once()


def test_route_janus_unavailable_returns_503(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from app.routes import stream_bindings as sb
    sp, ap = _seed(tmp_path, [_bj("color", 2000, 5100, "online", True)])
    monkeypatch.setattr(sb, "BIND_STATE_PATH", sp)
    monkeypatch.setattr(sb, "ALLOC_STATE_PATH", ap)

    def down():
        raise janus_admin.JanusAdminError("janus down")
    monkeypatch.setattr(janus_admin, "list_mountpoints", down)
    with pytest.raises(HTTPException) as ei:
        sb.reconcile_janus_run_once()
    assert ei.value.status_code == 503


def test_route_and_service_are_media_safe_R3_R4():
    from app.routes import stream_bindings as sb
    for fn in (bp.run_janus_reconcile_once, sb.reconcile_janus_run_once):
        src = inspect.getsource(fn)
        for forbidden in ("destroy_mountpoint(", "systemctl", "subprocess",
                          "firewall_sync", "reconcile_gateway("):
            assert forbidden not in src, f"{fn.__name__} must be media-safe — found {forbidden!r}"

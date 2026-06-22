"""Dashboard misc (Phase 4 of admin_dashboard split, C-04): audit-tail, primary_ip, soak,
dashboard_snapshot — the last inline helpers, now re-pointed to their extracted homes:

    _read_audit_tail  -> app/application/audit_view.read_audit_tail
    _primary_ip       -> app/services/netinfo.primary_ip
    soak list/read    -> app/services/soak_files.{list_files,read_file_bytes}
    dashboard_snapshot-> app/application/dashboard.snapshot

Behavior was first locked against the in-route helpers, then these were re-pointed with the
SAME assertions (preservation proof). One route-delegation test keeps the HTTP wiring honest.
"""
from __future__ import annotations

import os
import sys

import pytest

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import HTTPException

from app.application import audit_view, dashboard
from app.services import netinfo, soak_files
from app.routes import admin_dashboard as ad   # route delegation


class _R:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


# ── audit tail filtering (-> application/audit_view) ────────────────────────
def test_read_audit_tail_filters(tmp_path, monkeypatch):
    log = tmp_path / "audit.log"
    log.write_text("\n".join([
        '{"ts":"2026-06-01T00:00:00","action":"a.create","outcome":"success","target":"x"}',
        '{"ts":"2026-06-02T00:00:00","action":"b.delete","outcome":"failure","target":"y","extra1":1}',
        '{"ts":"2026-06-03T00:00:00","action":"a.update","outcome":"success","target":"x"}',
    ]) + "\n")
    monkeypatch.setattr(audit_view, "AUDIT_LOG_FILE", log)

    entries, trunc = audit_view.read_audit_tail(limit=50)
    assert [e.action for e in entries] == ["a.update", "b.delete", "a.create"] and trunc is False
    assert [e.action for e in audit_view.read_audit_tail(action_substr="a.")[0]] == ["a.update", "a.create"]
    fail, _ = audit_view.read_audit_tail(outcome="failure")
    assert [e.action for e in fail] == ["b.delete"] and fail[0].extra == {"extra1": 1}
    one, trunc1 = audit_view.read_audit_tail(limit=1)
    assert len(one) == 1 and trunc1 is True
    since, _ = audit_view.read_audit_tail(since_ts="2026-06-02T00:00:00")
    assert [e.action for e in since] == ["a.update", "b.delete"]


# ── primary_ip (-> services/netinfo) ────────────────────────────────────────
def test_primary_ip(monkeypatch):
    monkeypatch.setattr(netinfo.subprocess, "run", lambda *a, **k: _R(stdout="192.168.1.10 10.0.0.5 \n"))
    assert netinfo.primary_ip() == "192.168.1.10"

    def boom(*_a, **_k):
        raise FileNotFoundError()
    monkeypatch.setattr(netinfo.subprocess, "run", boom)
    assert netinfo.primary_ip() is None


# ── soak files (-> services/soak_files) ─────────────────────────────────────
def test_soak_list_and_read(tmp_path, monkeypatch):
    monkeypatch.setattr(soak_files, "SOAK_DIR", tmp_path)
    (tmp_path / "soak_a.csv").write_text("h1,h2\n1,2\n3,4\n")
    res = soak_files.list_files()
    assert res["files"][0]["name"] == "soak_a.csv" and res["files"][0]["samples"] == 2
    assert b"h1,h2" in soak_files.read_file_bytes("soak_a.csv")


def test_soak_path_traversal_and_404(tmp_path, monkeypatch):
    monkeypatch.setattr(soak_files, "SOAK_DIR", tmp_path)
    with pytest.raises(soak_files.InvalidSoakFilename):   # route maps to 400
        soak_files.read_file_bytes("../etc/passwd")
    with pytest.raises(soak_files.SoakFileNotFound):      # route maps to 404
        soak_files.read_file_bytes("soak_missing.csv")


def test_soak_route_delegates(tmp_path, monkeypatch):
    """Thin route still returns the adapter's bytes with text/csv media type."""
    monkeypatch.setattr(soak_files, "SOAK_DIR", tmp_path)
    (tmp_path / "soak_b.csv").write_text("h\n1\n")
    resp = ad.get_soak_file("soak_b.csv")
    assert resp.media_type == "text/csv" and b"h" in resp.body


# ── dashboard snapshot aggregation (-> application/dashboard) ────────────────
def test_dashboard_snapshot_aggregates(monkeypatch):
    from app.application.services_admin import ServiceState
    from app.application.mountpoint_admin import MountpointInfo
    monkeypatch.setattr(dashboard.services_admin, "service_states",
                        lambda: [ServiceState(name="janus", active=True, state="active", enabled=True)])
    monkeypatch.setattr(dashboard.mountpoint_admin, "list_mountpoint_infos",
                        lambda: ([MountpointInfo(id=1305, type="rtp", enabled=True, is_private=False)], None))
    monkeypatch.setattr(dashboard.audit_view, "read_audit_tail",
                        lambda limit: ([audit_view.AuditEntry(ts="t", action="a.x")], False))
    monkeypatch.setattr(dashboard.netinfo, "primary_ip", lambda: "192.168.1.10")

    class _Paths:
        cfg_dir = "/opt/janus/etc/janus"
    monkeypatch.setattr(dashboard.jcfg_renderer, "detect_janus_paths", lambda: _Paths())

    snap = dashboard.snapshot(audit_limit=20)
    assert snap.services[0].name == "janus" and snap.mountpoints[0].id == 1305
    assert snap.audit[0].action == "a.x" and snap.primary_ip == "192.168.1.10"
    assert snap.janus_cfg_dir == "/opt/janus/etc/janus" and snap.mountpoints_error is None


def test_soak_route_maps_domain_errors_to_http(tmp_path, monkeypatch):
    """D3.3A: the route maps soak_files domain errors to 400 (bad name) / 404 (missing),
    byte-identical to the old service-layer HTTPExceptions."""
    from app.routes import admin_dashboard as ad
    monkeypatch.setattr(soak_files, "SOAK_DIR", tmp_path)
    with pytest.raises(HTTPException) as e1:
        ad.get_soak_file("bad-name.txt")
    assert e1.value.status_code == 400 and e1.value.detail == "invalid filename"
    with pytest.raises(HTTPException) as e2:
        ad.get_soak_file("soak_missing.csv")
    assert e2.value.status_code == 404 and e2.value.detail == "not found"

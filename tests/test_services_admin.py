"""Phase 1 of the admin_dashboard split (C-04): systemd/encoder_admin adapters +
services_admin use-cases. Behavior is preserved from the old inlined route helpers;
these tests pin the adapter primitives and the restart orchestration.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


from app.application import services_admin as sa
from app.services import encoder_admin, service_control, systemd


class _Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _boom(*_a, **_k):
    raise subprocess.TimeoutExpired("cmd", 1)


# ── systemd adapter ───────────────────────────────────────────────────────
def test_systemd_show_parses_keyvalues(monkeypatch):
    monkeypatch.setattr(systemd.subprocess, "run",
                        lambda *a, **k: _Done(stdout="ActiveState=active\nMainPID=123\nUnitFileState=enabled\n"))
    info = systemd.show("janus")
    assert info["ActiveState"] == "active" and info["MainPID"] == "123"


def test_systemd_show_nonzero_and_missing_binary_return_none(monkeypatch):
    monkeypatch.setattr(systemd.subprocess, "run", lambda *a, **k: _Done(returncode=1))
    assert systemd.show("x") is None
    monkeypatch.setattr(systemd.subprocess, "run", _boom)
    assert systemd.show("x") is None


def test_service_control_restart_unit_cmd_and_failure(monkeypatch):
    # P1: privileged restart now goes through the scoped service-admin CLI (was sudo -n /bin/systemctl).
    seen = {}
    monkeypatch.setattr(service_control.subprocess, "run",
                        lambda cmd, **k: seen.update(cmd=cmd) or _Done(returncode=0, stderr=""))
    rc, err = service_control.restart_unit("janus")
    assert rc == 0 and seen["cmd"] == ["sudo", "-n", "/usr/local/bin/service-admin", "restart", "janus"]
    monkeypatch.setattr(service_control.subprocess, "run", _boom)
    with pytest.raises(RuntimeError):
        service_control.restart_unit("janus")


# ── encoder-admin adapter ─────────────────────────────────────────────────
def test_encoder_admin_invoke_builds_cmd(monkeypatch):
    seen = {}
    monkeypatch.setattr(encoder_admin.subprocess, "run",
                        lambda cmd, **k: seen.update(cmd=cmd) or _Done())
    encoder_admin.invoke("restart", "rs-stream", "color")
    assert seen["cmd"] == ["sudo", "-n", "/usr/local/bin/encoder-admin",
                           "restart", "--family", "rs-stream", "--instance", "color"]
    encoder_admin.invoke("restart", "realsense-mux", None)
    assert "--instance" not in seen["cmd"]


# ── services_admin use-cases ──────────────────────────────────────────────
def test_service_state_mapping(monkeypatch):
    monkeypatch.setattr(sa.systemd, "show", lambda u: {
        "ActiveState": "active", "UnitFileState": "enabled", "MainPID": "42",
        "MemoryCurrent": "2048", "SubState": "running"})
    s = sa.service_state("janus")
    assert s.active and s.state == "active" and s.enabled
    assert s.main_pid == 42 and s.memory_bytes == 2048 and s.sub_state == "running"


def test_service_state_absent_and_masked(monkeypatch):
    monkeypatch.setattr(sa.systemd, "show", lambda u: None)
    assert sa.service_state("ghost").state == "absent"
    monkeypatch.setattr(sa.systemd, "show", lambda u: {"LoadState": "masked"})
    assert sa.service_state("m").state == "absent"


def test_restart_refuses_self_and_unknown():
    with pytest.raises(sa.RestartSelfRefused):           # route maps to 400
        sa.restart_service(sa.SELF_SERVICE)
    with pytest.raises(sa.ServiceNotRestartable):        # route maps to 400
        sa.restart_service("not-a-real-service")


def test_restart_systemctl_path(monkeypatch):
    monkeypatch.setattr(sa.time, "sleep", lambda *_: None)
    monkeypatch.setattr(sa.service_control, "restart_unit", lambda u, **k: (0, ""))
    monkeypatch.setattr(sa.systemd, "show", lambda u: {"ActiveState": "active", "UnitFileState": "enabled"})
    r = sa.restart_service("janus")
    assert r.ok and r.method == "systemctl" and r.new_state == "active"


def test_restart_encoder_path_dispatches(monkeypatch):
    monkeypatch.setattr(sa.time, "sleep", lambda *_: None)
    seen = {}
    monkeypatch.setattr(sa.encoder_admin, "invoke",
                        lambda action, family, instance, **k: seen.update(
                            action=action, family=family, instance=instance) or (0, ""))
    monkeypatch.setattr(sa.systemd, "show", lambda u: {"ActiveState": "active", "UnitFileState": "enabled"})
    r = sa.restart_service("rs-stream@color")
    assert r.method == "encoder-admin"
    assert seen == {"action": "restart", "family": "rs-stream", "instance": "color"}


def test_restart_exec_failure_raises(monkeypatch):
    def fail(*_a, **_k):
        raise RuntimeError("boom")
    monkeypatch.setattr(sa.service_control, "restart_unit", fail)
    with pytest.raises(sa.RestartExecFailed):            # route maps to 500
        sa.restart_service("janus")


def test_restart_nonzero_rc_returns_structured_failure(monkeypatch):
    monkeypatch.setattr(sa.time, "sleep", lambda *_: None)
    monkeypatch.setattr(sa.service_control, "restart_unit", lambda u, **k: (1, "unit failed"))
    monkeypatch.setattr(sa.systemd, "show", lambda u: {"ActiveState": "failed"})
    r = sa.restart_service("janus")
    assert r.ok is False and r.stderr == "unit failed" and r.new_state == "failed"


@pytest.mark.asyncio
async def test_restart_route_maps_domain_errors_to_http(admin_client, monkeypatch):
    """D3.2A: the route maps services_admin's domain errors to the SAME HTTP status+detail the
    use-case used to raise directly — 400 (refuse-self) / 500 (exec failed)."""
    # refuse self -> 400 (RestartSelfRefused mapped at the route boundary)
    r = await admin_client.post(f"/api/v1/admin/services/{sa.SELF_SERVICE}/restart")
    assert r.status_code == 400 and "Refusing to restart self" in r.json()["detail"]
    # exec failure -> 500 (RestartExecFailed mapped at the route boundary)
    def fail(*_a, **_k):
        raise RuntimeError("boom")
    monkeypatch.setattr(sa.service_control, "restart_unit", fail)
    r2 = await admin_client.post("/api/v1/admin/services/janus/restart")
    assert r2.status_code == 500 and "restart exec failed" in r2.json()["detail"]

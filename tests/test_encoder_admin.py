"""Encoder vertical (Phase 2 of admin_dashboard split, C-04).

The env-IO / validate / invoke / discover / instance-status assertions were first run
against the old admin_dashboard helpers to lock behavior; here they are re-pointed to the
new modules with the SAME assertions (behavior-preservation proof). Plus use-case +
route→route-fix tests. See docs/design/ADMIN_DASHBOARD_SPLIT.md.
"""
from __future__ import annotations

import inspect
import os
import subprocess
import sys

import pytest

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import HTTPException

from app.services import encoder_admin as enc_adapter
from app.services import encoder_env
from app.application import encoder_admin as enc_uc
from app.application import provision_stream as prov


class _Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


# ── env-file IO (services/encoder_env.py) ──────────────────────────────────
def test_read_env_file_parses_comments_quotes_blank(tmp_path):
    p = tmp_path / "x.env"
    p.write_text('# comment\n\nWIDTH="640"\nFPS=30\nDEV=\'q\'\nbad line\n')
    assert encoder_env.read_env_file(p) == {"WIDTH": "640", "FPS": "30", "DEV": "q"}


def test_read_env_file_absent_is_empty(tmp_path):
    assert encoder_env.read_env_file(tmp_path / "nope.env") == {}


def test_write_env_files_content_and_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(encoder_env, "ENV_DIR", tmp_path)
    spec = encoder_env.EncoderEnvSpec(DEVICE="/dev/video0", WIDTH=1280, HEIGHT=720, FPS=25, BITRATE_KBPS=3000)
    written = encoder_env.write_env_files("rtp-v4l2", "cam0", spec, 5100)
    assert any(w.endswith("rtp-v4l2-cam0.tuning.env") for w in written)
    tuning = (tmp_path / "rtp-v4l2-cam0.tuning.env").read_text()
    assert 'DEVICE="/dev/video0"' in tuning and 'WIDTH="1280"' in tuning and 'FPS="25"' in tuning
    assert 'PORT="5100"' in (tmp_path / "rtp-v4l2-cam0.contract.env").read_text()


def test_write_env_files_rejects_bad_instance(tmp_path, monkeypatch):
    monkeypatch.setattr(encoder_env, "ENV_DIR", tmp_path)
    with pytest.raises(encoder_env.InvalidEncoderInstanceName):   # route maps to 400
        encoder_env.write_env_files("rtp-v4l2", "bad/instance!", encoder_env.EncoderEnvSpec(), 5100)


# ── encoder-admin adapter (services/encoder_admin.py) ──────────────────────
def test_discover_encoder_units_parses(monkeypatch):
    out = ("rs-stream@color.service  loaded active running x\n"
           "realsense-mux.service    loaded active running y\n"
           "unrelated.service        loaded active running z\n")
    monkeypatch.setattr(enc_adapter.subprocess, "run", lambda *a, **k: _Done(stdout=out))
    units = enc_adapter.discover_units()
    assert ("rs-stream", "color") in units and ("realsense-mux", None) in units
    assert all(f != "unrelated" for f, _ in units)


# ── encoder use-cases (application/encoder_admin.py) ───────────────────────
def test_validate_encoder_target():
    enc_uc.validate_encoder_target("realsense-mux", None)
    enc_uc.validate_encoder_target("rs-stream", "color")
    with pytest.raises(enc_uc.UnknownEncoderFamily):       # route maps to 400
        enc_uc.validate_encoder_target("nope", None)
    with pytest.raises(enc_uc.BadEncoderInstance):         # route maps to 400
        enc_uc.validate_encoder_target("rs-stream", None)


def test_encoder_action_response_and_cmd(monkeypatch):
    seen = {}
    monkeypatch.setattr(enc_adapter.subprocess, "run",
                        lambda cmd, **k: seen.update(cmd=cmd) or _Done(returncode=0, stderr=""))
    r = enc_uc.encoder_action("start", "rs-stream", "color")
    assert r.ok and r.rc == 0 and r.family == "rs-stream" and r.instance == "color" and r.action == "start"
    assert seen["cmd"] == ["sudo", "-n", "/usr/local/bin/encoder-admin",
                           "start", "--family", "rs-stream", "--instance", "color"]


def test_encoder_action_exec_failure_raises(monkeypatch):
    def boom(*_a, **_k):
        raise subprocess.TimeoutExpired("cmd", 1)
    monkeypatch.setattr(enc_adapter.subprocess, "run", boom)
    with pytest.raises(enc_uc.EncoderExecFailed):          # route maps to 500
        enc_uc.encoder_action("start", "rs-stream", "color")


def test_start_stop_validate_then_invoke(monkeypatch):
    calls = []
    monkeypatch.setattr(enc_uc, "encoder_action",
                        lambda action, f, i: calls.append((action, f, i)) or "OK")
    assert enc_uc.start_encoder("rs-stream", "color") == "OK"
    assert enc_uc.stop_encoder("rs-stream", "color") == "OK"
    assert calls == [("start", "rs-stream", "color"), ("stop", "rs-stream", "color")]
    with pytest.raises(enc_uc.BadEncoderInstance):   # validation still fires before invoke
        enc_uc.start_encoder("rs-stream", None)


def test_instance_status_combines(tmp_path, monkeypatch):
    monkeypatch.setattr(encoder_env, "ENV_DIR", tmp_path)
    (tmp_path / "rs-stream-color.contract.env").write_text('PORT="5100"\n')
    (tmp_path / "rs-stream-color.tuning.env").write_text('WIDTH="640"\nHEIGHT="480"\nFPS="30"\nBITRATE_KBPS="1500"\n')
    monkeypatch.setattr(enc_uc.systemd, "show",
                        lambda u: {"ActiveState": "active", "MainPID": "99", "ActiveEnterTimestamp": "T"})
    s = enc_uc.instance_status("rs-stream", "color")
    assert s.active and s.ffmpeg_pid == 99 and s.rtp_port == 5100
    assert s.width == 640 and s.height == 480 and s.fps == 30 and s.bitrate_kbps == 1500


def test_list_instances(monkeypatch):
    monkeypatch.setattr(enc_adapter, "discover_units", lambda: [("rs-stream", "color"), ("realsense-mux", None)])
    monkeypatch.setattr(enc_uc, "instance_status", lambda f, i: f"{f}@{i}")
    assert enc_uc.list_instances() == ["rs-stream@color", "realsense-mux@None"]


# ── restart_unit: the shared post-tuning-write restart (Cycle 6 de-dup of color_config + sensor_tuning_env) ──
def test_restart_unit_builds_sudo_argv_via_system_run(monkeypatch):
    seen = {}
    monkeypatch.setattr("app.services.system.run",
                        lambda cmd, timeout=None: seen.update(cmd=cmd, timeout=timeout) or "")
    enc_adapter.restart_unit("rs-stream", "depth", timeout=20)
    assert seen["cmd"] == ["sudo", "/usr/local/bin/encoder-admin", "restart",
                           "--family", "rs-stream", "--instance", "depth"]
    assert seen["timeout"] == 20   # caller's timeout forwarded (color=60, tuning=20)


def test_restart_unit_propagates_runtime_error(monkeypatch):
    def boom(cmd, timeout=None):
        raise RuntimeError("encoder-admin restart exit=1")
    monkeypatch.setattr("app.services.system.run", boom)
    with pytest.raises(RuntimeError):   # callers map this to their domain WriteError → HTTP 500
        enc_adapter.restart_unit("rs-stream", "color", timeout=60)


# ── provision use-case + route→route-fix (application/provision_stream.py) ──
def test_provision_orchestrates(monkeypatch):
    monkeypatch.setattr(prov.encoder_env, "write_env_files", lambda *a, **k: ["/etc/robot/x.tuning.env", "/etc/robot/x.contract.env"])
    monkeypatch.setattr(prov.enc_uc, "encoder_action",
                        lambda action, f, i: enc_uc.EncoderActionResponse(family=f, instance=i, action=action, ok=True, rc=0))

    class _MP:
        created = True
    mp = _MP()
    res = prov.provision_stream(mountpoint_req="REQ", encoder_family="rtp-v4l2", encoder_instance="cam0",
                                encoder_env_spec=None, rtp_port=5100, mp_id=1305,
                                create_mountpoint=lambda req: mp)
    assert res["mountpoint"] is mp and res["encoder"].ok and res["error"] is None and len(res["env_files"]) == 2


def test_provision_skips_encoder_when_mountpoint_fails():
    class _MP:
        created = False
    res = prov.provision_stream(mountpoint_req="R", encoder_family="rtp-v4l2", encoder_instance="cam0",
                                encoder_env_spec=None, rtp_port=5100, mp_id=1,
                                create_mountpoint=lambda req: _MP())
    assert res["encoder"] is None and res["error"] == "mountpoint create failed — encoder skipped"


def test_provision_route_no_longer_calls_route_handler():
    from app.routes import admin_dashboard as ad
    src = inspect.getsource(ad.provision_stream)
    assert "create_mountpoint=mountpoint_admin.create_mountpoint" in src  # injects the use-case (post-3B)
    assert "create_mountpoint(req.mountpoint)" not in src                 # NOT the route handler (smell fixed)


@pytest.mark.asyncio
async def test_encoder_routes_map_domain_errors_to_http(admin_client, monkeypatch):
    """D3.1: the route maps encoder_admin's domain errors to the SAME HTTP status+detail the
    use-case used to raise directly — 400 (unknown family) / 500 (exec failed)."""
    # unknown family -> 400 (UnknownEncoderFamily mapped at the route boundary)
    r = await admin_client.post("/api/v1/admin/encoders/nope/start")
    assert r.status_code == 400 and "Unknown family" in r.json()["detail"]
    # exec failure -> 500 (EncoderExecFailed mapped at the route boundary)
    def boom(*_a, **_k):
        raise subprocess.TimeoutExpired("cmd", 1)
    monkeypatch.setattr(enc_adapter.subprocess, "run", boom)
    r2 = await admin_client.post("/api/v1/admin/encoders/rs-stream/color/start")
    assert r2.status_code == 500 and "exec failed" in r2.json()["detail"]


def test_provision_route_maps_bad_instance_to_400(monkeypatch, tmp_path):
    """D3.3C: write_env_files' InvalidEncoderInstanceName surfaces as 400 at the provision route
    (the mountpoint create step is mocked OK so the bad instance is what fails)."""
    from types import SimpleNamespace
    from app.routes import admin_dashboard as ad
    monkeypatch.setattr(encoder_env, "ENV_DIR", tmp_path)
    monkeypatch.setattr(ad.mountpoint_admin, "create_mountpoint", lambda req: SimpleNamespace(created=True))
    req = ad.ProvisionStreamRequest(
        mountpoint=ad.CreateMountpointRequest(id=2000, rtp_port=5100, codec="h264"),
        encoder_family="rtp-v4l2", encoder_instance="bad/instance!",
        encoder_env=encoder_env.EncoderEnvSpec())
    with pytest.raises(HTTPException) as e:
        ad.provision_stream(req)
    assert e.value.status_code == 400 and "Invalid instance name" in e.value.detail

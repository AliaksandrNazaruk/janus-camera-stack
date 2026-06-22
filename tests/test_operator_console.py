"""Operator console (P0) — node/stream lifecycle + diagnostics + maintenance.

Covers the store ops (remove_node, set_maintenance, set_fdir_enabled, touch_checked,
last_error), the routes (DELETE node, maintenance, fdir toggle, restart/stop,
rtp_age), and the maintenance-aware remote monitor. Companion to
test_stream_bindings_local_activate.py (same admin_client + isolated-store pattern).
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

from app.routes import stream_bindings as sb_routes
from app.services import node_client, remote_stream_monitor, sensor_lifecycle
from app.services import stream_binding_store as sbs

pytestmark = pytest.mark.asyncio

NODES = "/api/v1/admin/nodes"
BINDS = "/api/v1/admin/stream-bindings"
GW = "192.168.1.10"


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    monkeypatch.setattr(sb_routes, "BIND_STATE_PATH", tmp_path / "stream_bindings.json")
    monkeypatch.setattr(sb_routes, "ALLOC_STATE_PATH", tmp_path / "allocations.json")
    return tmp_path


def _bind_path():
    return sb_routes.BIND_STATE_PATH


def _alloc_path():
    return sb_routes.ALLOC_STATE_PATH


def _seed_remote_binding(host="192.168.1.55", sensor="color", mp=2000, port=5100, *, serial=None):
    """Register a remote node (+ pinned key so it looks provisioned) and store one
    remote binding. Returns (node, binding)."""
    bp, ap = _bind_path(), _alloc_path()
    n = sbs.add_node_by_host(host, state_path=bp)
    sbs.set_host_key(n.node_id, f"{host} ssh-ed25519 AAAAFAKE", state_path=bp)
    if serial:
        sbs.set_serial(n.node_id, serial, state_path=bp)
        n = sbs.get_node(n.node_id, state_path=bp)
    bid = sbs.remote_binding_id(n, sensor)
    b = sbs.StreamBinding(
        binding_id=bid, node_id=n.node_id, sensor=sensor,
        mode=sbs.StreamMode.REMOTE_PRODUCER,
        transport=sbs.StreamTransport(rtp_port=port),
        janus=sbs.StreamJanusConfig(mountpoint_id=mp, rtp_iface=GW))
    sbs.upsert_binding(b, state_path=bp, alloc_state_path=ap)
    return n, b


class _FakeClient:
    def __init__(self):
        self.calls = []

    def restart_stream(self, node_id, sensor):
        self.calls.append(("restart", node_id, sensor))
        return node_client.RestartResult(True, f"restarted {sensor}")

    def stop_stream(self, node_id, sensor):
        self.calls.append(("stop", node_id, sensor))
        return node_client.RestartResult(True, f"stopped {sensor}")


# ── store: remove_node ─────────────────────────────────────────────────

def test_remove_node_drops_node_bindings_and_secret(tmp_path):
    n, b = _seed_remote_binding()
    bp = _bind_path()
    assert sbs.get_node(n.node_id, state_path=bp).agent_token        # secret present
    out = sbs.remove_node(n.node_id, state_path=bp)
    assert out["removed"] is True
    assert b.binding_id in out["binding_ids"]
    assert sbs.get_node(n.node_id, state_path=bp) is None
    assert b.binding_id not in sbs.list_bindings(state_path=bp, alloc_state_path=_alloc_path())
    # the 0600 token secret is gone too (no orphan bearer secret)
    assert n.node_id not in sbs._read_secrets(bp)


def test_remove_node_rejects_local():
    with pytest.raises(sbs.BindingValidationError):
        sbs.remove_node(sbs.LOCAL_NODE_ID, state_path=_bind_path())


def test_remove_node_unknown_is_noop():
    out = sbs.remove_node("node-doesnotexist", state_path=_bind_path())
    assert out == {"removed": False, "binding_ids": []}


# ── store: maintenance / fdir / diagnostics ────────────────────────────

def test_set_maintenance_round_trips():
    n, _ = _seed_remote_binding()
    bp = _bind_path()
    assert sbs.get_node(n.node_id, state_path=bp).maintenance is False
    sbs.set_maintenance(n.node_id, True, state_path=bp)
    assert sbs.get_node(n.node_id, state_path=bp).maintenance is True
    sbs.set_maintenance(n.node_id, False, state_path=bp)
    assert sbs.get_node(n.node_id, state_path=bp).maintenance is False


def test_set_fdir_enabled_toggles_remote_binding():
    n, b = _seed_remote_binding()
    bp = _bind_path()
    nb = sbs.set_fdir_enabled(b.binding_id, False, state_path=bp)
    assert nb.fdir.enabled is False
    got = sbs.get_binding(b.binding_id, state_path=bp, alloc_state_path=_alloc_path())
    assert got.fdir.enabled is False


def test_set_fdir_enabled_unknown_binding_raises():
    with pytest.raises(KeyError):
        sbs.set_fdir_enabled("nope:color", True, state_path=_bind_path())


def test_set_provision_state_records_and_clears_last_error():
    n, _ = _seed_remote_binding()
    bp = _bind_path()
    sbs.set_provision_state(n.node_id, "failed", state_path=bp, detail="pyrealsense2 not installed")
    assert sbs.get_node(n.node_id, state_path=bp).last_error == "pyrealsense2 not installed"
    sbs.set_provision_state(n.node_id, "ready", state_path=bp)         # success clears it
    assert sbs.get_node(n.node_id, state_path=bp).last_error is None


def test_touch_checked_sets_timestamp():
    n, _ = _seed_remote_binding()
    bp = _bind_path()
    assert sbs.get_node(n.node_id, state_path=bp).last_checked_at is None
    sbs.touch_checked(n.node_id, "reachable", state_path=bp)
    got = sbs.get_node(n.node_id, state_path=bp)
    assert got.reachability == "reachable" and isinstance(got.last_checked_at, float)


# ── routes: DELETE node ────────────────────────────────────────────────

async def test_delete_node_removes_and_reconciles(admin_client, monkeypatch):
    n, b = _seed_remote_binding()
    destroyed = []
    monkeypatch.setattr(sb_routes.janus_admin, "destroy_mountpoint",
                        lambda **kw: destroyed.append(kw["mp_id"]))
    fw = {"called": False}
    from app.services import firewall_sync
    monkeypatch.setattr(firewall_sync, "reconcile",
                        lambda **kw: fw.__setitem__("called", kw.get("apply")) or firewall_sync.Plan([], []))
    r = await admin_client.delete(f"{NODES}/{n.node_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["removed"] is True and b.binding_id in body["removed_bindings"]
    assert destroyed == [b.janus.mountpoint_id]            # mountpoint torn down
    assert body["firewall_reconciled"] is True and fw["called"] is True
    assert sb_routes.sbs.get_node(n.node_id, state_path=sb_routes.BIND_STATE_PATH) is None


async def test_delete_node_rejects_local(admin_client):
    r = await admin_client.delete(f"{NODES}/cam10")
    assert r.status_code == 400


async def test_delete_node_unknown_404(admin_client):
    r = await admin_client.delete(f"{NODES}/node-missing")
    assert r.status_code == 404


# ── routes: maintenance ────────────────────────────────────────────────

async def test_maintenance_endpoint_toggles(admin_client):
    n, _ = _seed_remote_binding()
    r = await admin_client.post(f"{NODES}/{n.node_id}/maintenance", json={"enabled": True})
    assert r.status_code == 200 and r.json()["maintenance"] is True
    r = await admin_client.post(f"{NODES}/{n.node_id}/maintenance", json={"enabled": False})
    assert r.json()["maintenance"] is False


async def test_maintenance_rejects_local(admin_client):
    r = await admin_client.post(f"{NODES}/cam10/maintenance", json={"enabled": True})
    assert r.status_code == 400


# ── routes: per-binding fdir toggle ────────────────────────────────────

async def test_fdir_toggle_remote(admin_client):
    _, b = _seed_remote_binding()
    r = await admin_client.post(f"{BINDS}/{b.binding_id}/fdir", json={"enabled": False})
    assert r.status_code == 200 and r.json()["fdir_enabled"] is False


async def test_fdir_toggle_local_rejected(admin_client, tmp_path):
    # a local projection binding id ('{serial}:{sensor}') isn't a stored remote binding
    from app.services import mountpoint_allocator
    ap = sb_routes.ALLOC_STATE_PATH
    import json
    ap.write_text(json.dumps({"version": 1, "allocations": {
        "SER:color": {"mp_id": 1305, "rtp_port": 5004, "desired_active": True}}}))
    r = await admin_client.post(f"{BINDS}/SER:color/fdir", json={"enabled": False})
    assert r.status_code == 400


# ── routes: restart / stop ─────────────────────────────────────────────

async def test_restart_remote_uses_node_client(admin_client, monkeypatch):
    _, b = _seed_remote_binding()
    fake = _FakeClient()
    monkeypatch.setattr(sb_routes.node_client, "get_node_client", lambda nid, **kw: fake)
    r = await admin_client.post(f"{BINDS}/{b.binding_id}/restart")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert fake.calls == [("restart", b.node_id, "color")]


async def test_stop_remote_marks_offline(admin_client, monkeypatch):
    _, b = _seed_remote_binding()
    fake = _FakeClient()
    monkeypatch.setattr(sb_routes.node_client, "get_node_client", lambda nid, **kw: fake)
    r = await admin_client.post(f"{BINDS}/{b.binding_id}/stop")
    assert r.status_code == 200 and fake.calls == [("stop", b.node_id, "color")]
    got = sbs.get_binding(b.binding_id, state_path=sb_routes.BIND_STATE_PATH,
                          alloc_state_path=sb_routes.ALLOC_STATE_PATH)
    assert got.status == sbs.StreamStatus.CONFIGURED_OFFLINE.value
    # stop must STICK via desired_up=False (monitor recovery gates on desired_up AND fdir); FDIR
    # itself is left untouched — now an independent concern, not coupled to Stop.
    assert got.desired_up is False
    assert got.fdir.enabled is True


async def test_remote_tuning_get_set(admin_client, monkeypatch):
    _, b = _seed_remote_binding()

    class _Fake:
        def get_tuning(self, sensor):
            return {"width": 640, "height": 480, "fps": 15, "rotation": 0, "bitrate_kbps": 900}

        def set_tuning(self, sensor, body):
            return {"ok": True, "tuning": {"rotation": body.get("rotation")}}

    monkeypatch.setattr(sb_routes.node_client, "get_node_client", lambda nid, **kw: _Fake())
    r = await admin_client.get(f"{BINDS}/{b.binding_id}/tuning")
    assert r.status_code == 200 and r.json()["rotation"] == 0
    r2 = await admin_client.post(f"{BINDS}/{b.binding_id}/tuning", json={"rotation": 90})
    assert r2.status_code == 200 and r2.json()["ok"] is True


async def test_remote_tuning_bad_rotation_400(admin_client, monkeypatch):
    _, b = _seed_remote_binding()
    monkeypatch.setattr(sb_routes.node_client, "get_node_client", lambda nid, **kw: object())
    r = await admin_client.post(f"{BINDS}/{b.binding_id}/tuning", json={"rotation": 45})
    assert r.status_code == 400


async def test_local_tuning_via_remote_endpoint_rejected(admin_client):
    import json
    sb_routes.ALLOC_STATE_PATH.write_text(json.dumps({"version": 1, "allocations": {
        "SER:color": {"mp_id": 1305, "rtp_port": 5004, "desired_active": True}}}))
    r = await admin_client.post(f"{BINDS}/SER:color/tuning", json={"rotation": 90})
    assert r.status_code == 400 and "cameras" in r.text   # points to the local config endpoint


async def test_restart_remote_failure_502(admin_client, monkeypatch):
    _, b = _seed_remote_binding()

    class _Bad:
        def restart_stream(self, *a):
            return node_client.RestartResult(False, "agent unreachable")

    monkeypatch.setattr(sb_routes.node_client, "get_node_client", lambda nid, **kw: _Bad())
    r = await admin_client.post(f"{BINDS}/{b.binding_id}/restart")
    assert r.status_code == 502


async def test_stop_local_uses_sensor_lifecycle(admin_client, monkeypatch, tmp_path):
    import json
    ap = sb_routes.ALLOC_STATE_PATH
    ap.write_text(json.dumps({"version": 1, "allocations": {
        "SER9:color": {"mp_id": 1305, "rtp_port": 5004, "desired_active": True}}}))
    calls = []
    # stop() returns the RUNNING state: (False, "stopped") == success (not running).
    monkeypatch.setattr(sensor_lifecycle, "stop",
                        lambda serial, sensor: calls.append((serial, sensor)) or (False, "stopped"))
    r = await admin_client.post(f"{BINDS}/SER9:color/stop")
    assert r.status_code == 200, r.text                   # False (=stopped) must NOT be read as failure
    assert calls == [("SER9", "color")]                   # serial parsed from the binding id


async def test_stop_local_lifecycle_error_502(admin_client, monkeypatch):
    import json
    ap = sb_routes.ALLOC_STATE_PATH
    ap.write_text(json.dumps({"version": 1, "allocations": {
        "SER9:depth": {"mp_id": 1306, "rtp_port": 5006, "desired_active": True}}}))

    def _boom(serial, sensor):
        raise sensor_lifecycle.LifecycleError("encoder-admin stop failed")

    monkeypatch.setattr(sensor_lifecycle, "stop", _boom)
    r = await admin_client.post(f"{BINDS}/SER9:depth/stop")
    assert r.status_code == 502 and "encoder-admin stop failed" in r.text


# ── routes: rtp_age in the list ────────────────────────────────────────

async def test_list_bindings_includes_rtp_age_when_requested(admin_client, monkeypatch):
    _, b = _seed_remote_binding()
    from app.services import janus
    monkeypatch.setattr(janus, "janus_summary", lambda mp=None: {"video_age_ms": 137})
    r = await admin_client.get(f"{BINDS}?include_rtp_age=true")
    assert r.status_code == 200
    row = {x["binding_id"]: x for x in r.json()["bindings"]}[b.binding_id]
    assert row["rtp_age_ms"] == 137
    # default (no flag) stays cheap → no age
    r2 = await admin_client.get(BINDS)
    row2 = {x["binding_id"]: x for x in r2.json()["bindings"]}[b.binding_id]
    assert row2["rtp_age_ms"] is None


# ── monitor: maintenance pauses FDIR ───────────────────────────────────

def test_monitor_skips_node_in_maintenance(monkeypatch, tmp_path):
    bp = tmp_path / "sb.json"
    ap = tmp_path / "alloc.json"
    monkeypatch.setattr(sbs, "DEFAULT_STATE_PATH", bp)
    n = sbs.add_node_by_host("192.168.1.55", state_path=bp)
    b = sbs.StreamBinding(
        binding_id=sbs.remote_binding_id(n, "color"), node_id=n.node_id, sensor="color",
        mode=sbs.StreamMode.REMOTE_PRODUCER, transport=sbs.StreamTransport(rtp_port=5100),
        janus=sbs.StreamJanusConfig(mountpoint_id=2000, rtp_iface=GW))
    sbs.upsert_binding(b, state_path=bp, alloc_state_path=ap)

    def _boom_summary(mp=None):
        raise AssertionError("janus_summary must not be called for a maintenance node")

    from app.services import janus
    monkeypatch.setattr(janus, "janus_summary", _boom_summary)
    remote_stream_monitor._reset_state_for_tests()

    # maintenance OFF → examined (and janus_summary WOULD be called → guard via a counter)
    seen = {"n": 0}
    monkeypatch.setattr(janus, "janus_summary",
                        lambda mp=None: seen.__setitem__("n", seen["n"] + 1) or {"video_age_ms": 10})
    assert remote_stream_monitor.tick(state_path=bp, alloc_state_path=ap) == 1
    assert seen["n"] == 1

    # maintenance ON → skipped before any janus call
    sbs.set_maintenance(n.node_id, True, state_path=bp)
    monkeypatch.setattr(janus, "janus_summary", _boom_summary)
    assert remote_stream_monitor.tick(state_path=bp, alloc_state_path=ap) == 0

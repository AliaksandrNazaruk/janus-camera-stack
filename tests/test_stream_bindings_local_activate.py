"""Add Camera Host — local (cam10) activation through the unified
/nodes/{id}/streams verb, the allocations-first serial resolver, the gateway-IP
add guard, and the host_key_pinned projection. Companion to
test_stream_bindings_api.py; see docs/design/ADD_CAMERA_HOST_UI.md."""
from __future__ import annotations

import json
import os
import sys

import pytest

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import importlib
from app.routes import stream_bindings as sb_routes
# the MODULE (import-as binds the package's re-exported FUNCTION; importlib gets the submodule)
_activate_local_mod = importlib.import_module("app.application.stream_bindings.activate_local")
from app.services import sensor_lifecycle, device_registry, mountpoint_allocator

pytestmark = pytest.mark.asyncio

NODES = "/api/v1/admin/nodes"


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    monkeypatch.setattr(sb_routes, "BIND_STATE_PATH", tmp_path / "stream_bindings.json")
    monkeypatch.setattr(sb_routes, "ALLOC_STATE_PATH", tmp_path / "allocations.json")


def _seed_allocations(path, mapping):
    """mapping: {'141722072135:color': (mp_id, rtp_port), …}"""
    allocs = {k: {"mp_id": mp, "rtp_port": port, "desired_active": True}
              for k, (mp, port) in mapping.items()}
    path.write_text(json.dumps({"version": 1, "allocations": allocs}))


# ── local activation through the unified verb (review C1: synchronous) ──

async def test_activate_local_calls_initialize_per_sensor(admin_client, monkeypatch):
    monkeypatch.setattr(_activate_local_mod, "_local_serial", lambda *a: "SER123")
    calls = []

    def _fake_init(serial, sensor):
        calls.append((serial, sensor))
        return (True, f"{sensor} up", None)

    monkeypatch.setattr(sensor_lifecycle, "initialize", _fake_init)
    r = await admin_client.post(f"{NODES}/cam10/streams", json={"sensors": ["color", "depth"]})
    assert r.status_code == 200
    body = r.json()
    assert body["node_id"] == "cam10"
    assert body["poll"] is None                              # synchronous, not pollable
    assert [x["sensor"] for x in body["results"]] == ["color", "depth"]
    assert all(x["ok"] for x in body["results"])
    assert calls == [("SER123", "color"), ("SER123", "depth")]   # exactly one per sensor


async def test_activate_local_does_not_touch_remote_ssh_path(admin_client, monkeypatch):
    # cam10 must branch BEFORE _node_for_provision / _transport_for (review M3) —
    # no host key, no bundle, no sudo password required.
    monkeypatch.setattr(_activate_local_mod, "_local_serial", lambda *a: "SER123")
    monkeypatch.setattr(sensor_lifecycle, "initialize", lambda s, sn: (True, "up", None))

    def _boom(*a, **k):
        raise AssertionError("remote SSH path must not run for cam10")

    monkeypatch.setattr(sb_routes, "_transport_for", _boom)
    monkeypatch.setattr(sb_routes, "_node_for_provision", _boom)
    r = await admin_client.post(f"{NODES}/cam10/streams", json={"sensors": ["color"]})
    assert r.status_code == 200


async def test_activate_local_depth_without_serial_refused_color_ok(admin_client, monkeypatch):
    monkeypatch.setattr(_activate_local_mod, "_local_serial", lambda *a: None)  # no allocations + probe failed
    seen = []

    def _fake_init(serial, sensor):
        seen.append((serial, sensor))
        return (True, "up", None)

    monkeypatch.setattr(sensor_lifecycle, "initialize", _fake_init)
    r = await admin_client.post(f"{NODES}/cam10/streams", json={"sensors": ["color", "depth"]})
    assert r.status_code == 200
    results = {x["sensor"]: x for x in r.json()["results"]}
    assert results["color"]["ok"] is True
    assert results["depth"]["ok"] is False and "serial" in results["depth"]["detail"]
    # color used the sentinel; depth never reached initialize (no orphan local:depth — review H1)
    assert seen == [(mountpoint_allocator.LOCAL_SERIAL, "color")]


async def test_activate_local_surfaces_lifecycle_error_per_sensor(admin_client, monkeypatch):
    monkeypatch.setattr(_activate_local_mod, "_local_serial", lambda *a: "SER123")

    def _init(serial, sensor):
        if sensor == "depth":
            raise sensor_lifecycle.LifecycleError("encoder start failed")
        return (True, "up", None)

    monkeypatch.setattr(sensor_lifecycle, "initialize", _init)
    r = await admin_client.post(f"{NODES}/cam10/streams", json={"sensors": ["color", "depth"]})
    assert r.status_code == 200                               # per-sensor outcome, not a 500
    results = {x["sensor"]: x for x in r.json()["results"]}
    assert results["color"]["ok"] is True
    assert results["depth"]["ok"] is False
    assert "encoder start failed" in results["depth"]["detail"]


async def test_activate_local_rejects_bad_sensor(admin_client):
    r = await admin_client.post(f"{NODES}/cam10/streams", json={"sensors": ["bogus"]})
    assert r.status_code == 400


# ── allocations-first serial resolver (review H1) ──────────────────────

async def test_local_serial_prefers_allocations(monkeypatch, tmp_path):
    path = tmp_path / "allocations.json"
    monkeypatch.setattr(sb_routes, "ALLOC_STATE_PATH", path)
    _seed_allocations(path, {"141722072135:color": (1305, 5004),
                             "141722072135:depth": (1306, 5006)})

    def _no_probe():
        raise AssertionError("probe must not run when allocations carry a real serial")

    monkeypatch.setattr(device_registry, "local_serial", _no_probe)
    assert _activate_local_mod._local_serial(path) == "141722072135"


async def test_local_serial_ignores_sentinel_and_falls_back_to_probe(monkeypatch, tmp_path):
    path = tmp_path / "allocations.json"
    monkeypatch.setattr(sb_routes, "ALLOC_STATE_PATH", path)
    _seed_allocations(path, {"local:color": (1305, 5004)})       # only the legacy sentinel
    monkeypatch.setattr(device_registry, "local_serial", lambda: "PROBE999")
    assert _activate_local_mod._local_serial(path) == "PROBE999"


async def test_local_serial_none_when_nothing_resolves(monkeypatch, tmp_path):
    monkeypatch.setattr(device_registry, "local_serial", lambda: None)
    assert _activate_local_mod._local_serial(tmp_path / "absent.json") is None


# ── add-node gateway-IP guard (review L1) + host_key_pinned projection ──

async def test_add_node_rejects_gateway_lan_ip(admin_client, monkeypatch):
    monkeypatch.setattr(sb_routes, "GATEWAY_LAN_IP", "192.168.1.10")
    r = await admin_client.post(NODES, json={"host": "192.168.1.10"})
    assert r.status_code == 400
    assert "local gateway" in r.json()["detail"]


async def test_node_out_exposes_host_key_pinned(admin_client):
    nid = (await admin_client.post(NODES, json={"host": "192.168.1.66"})).json()["node_id"]
    nodes = {n["node_id"]: n for n in (await admin_client.get(NODES)).json()["nodes"]}
    assert nodes[nid]["host_key_pinned"] is False
    sb_routes.sbs.set_host_key(nid, "192.168.1.66 ssh-ed25519 AAAA", state_path=sb_routes.BIND_STATE_PATH)
    nodes = {n["node_id"]: n for n in (await admin_client.get(NODES)).json()["nodes"]}
    assert nodes[nid]["host_key_pinned"] is True


# ── page renders (catches Jinja/template errors + nonce injection) ─────

async def test_camera_hosts_page_renders(admin_client):
    r = await admin_client.get("/camera_hosts.html")
    assert r.status_code == 200
    assert "Camera Hosts" in r.text
    assert "/static/js/camera_hosts.js" in r.text
    assert 'data-gateway-lan-ip="' in r.text          # gateway_lan_ip injected for L1 detection


# ── H1: gateway_host injection defence (validate IPv4 before it hits the SSH cmd) ──

async def test_activate_rejects_non_ipv4_gateway_host(admin_client):
    """H1: gateway_host flows into the node SSH `activate` command. Reject non-IPv4
    / loopback (defence-in-depth on top of shlex.quote) so admin input can't smuggle
    shell metacharacters. Validation runs before _node_for_provision, so no bundle
    or pinned host key is needed to exercise it."""
    nid = (await admin_client.post(NODES, json={"host": "192.168.1.72"})).json()["node_id"]
    for bad in ("1.2.3.4; rm -rf /tmp/x", "$(touch /tmp/pwn)", "not-an-ip", "127.0.0.1"):
        r = await admin_client.post(f"{NODES}/{nid}/streams",
                                    json={"sensors": ["color"], "gateway_host": bad})
        assert r.status_code == 400, (bad, r.status_code, r.text)

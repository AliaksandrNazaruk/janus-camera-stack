"""G6 — gateway topology API: nodes + stream-bindings CRUD + ensure-janus."""
from __future__ import annotations

import os
import sys

import httpx
import pytest

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.routes import stream_bindings as sb_routes
from app.services import janus_admin

pytestmark = pytest.mark.asyncio

NODES = "/api/v1/admin/nodes"
BINDINGS = "/api/v1/admin/stream-bindings"


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    monkeypatch.setattr(sb_routes, "BIND_STATE_PATH", tmp_path / "stream_bindings.json")
    monkeypatch.setattr(sb_routes, "ALLOC_STATE_PATH", tmp_path / "allocations.json")


async def _register(admin_client, node_id="cam55", host="192.168.1.55"):
    return await admin_client.post(f"{NODES}/register", json={"node_id": node_id, "host": host})


async def _create(admin_client, node_id="cam55", sensor="color", iface="192.168.1.10", **extra):
    body = {"node_id": node_id, "sensor": sensor, "rtp_iface": iface, **extra}
    return await admin_client.post(BINDINGS, json=body)


# ── nodes ─────────────────────────────────────────────────────────────

async def test_get_nodes_includes_implicit_local(admin_client):
    r = await admin_client.get(NODES)
    assert r.status_code == 200
    assert "cam10" in {n["node_id"] for n in r.json()["nodes"]}


async def test_register_node_then_list(admin_client):
    r = await _register(admin_client)
    assert r.status_code == 200 and r.json()["ordinal"] == 0
    ids = {n["node_id"] for n in (await admin_client.get(NODES)).json()["nodes"]}
    assert {"cam10", "cam55"} <= ids


async def test_add_node_by_ip_mints_node_id(admin_client):
    r = await admin_client.post(NODES, json={"host": "192.168.1.60", "display_name": "front"})
    assert r.status_code == 200
    body = r.json()
    assert body["node_id"].startswith("node-")          # opaque, gateway-minted
    assert body["host"] == "192.168.1.60"
    assert body["display_name"] == "front"


async def test_add_node_by_ip_is_idempotent(admin_client):
    a = await admin_client.post(NODES, json={"host": "192.168.1.61"})
    b = await admin_client.post(NODES, json={"host": "192.168.1.61"})
    assert a.json()["node_id"] == b.json()["node_id"]   # lookup-or-create, no duplicate


async def test_add_node_by_ip_rejects_loopback(admin_client):
    r = await admin_client.post(NODES, json={"host": "127.0.0.1"})
    assert r.status_code == 400


async def test_provision_node_kicks_off(admin_client, tmp_path, monkeypatch):
    nid = (await admin_client.post(NODES, json={"host": "192.168.1.62"})).json()["node_id"]
    bundle = tmp_path / "bundle.tgz"
    bundle.write_text("x")
    monkeypatch.setattr(sb_routes, "NODE_BUNDLE_TAR", str(bundle))
    monkeypatch.setattr(sb_routes, "capture_host_key", lambda host: "")          # no real ssh-keyscan
    monkeypatch.setattr(sb_routes.node_provisioner, "provision", lambda *a, **k: None)  # no real SSH
    r = await admin_client.post(f"{NODES}/{nid}/provision",
                                json={"sudo_password": "x", "gateway_host": "192.168.1.10",
                                      "allow_tofu": True})
    body = r.json()
    assert r.status_code == 200 and body["started"] is True
    # H1: the durable operation_id is surfaced + a concrete poll hint references it
    assert isinstance(body["operation_id"], str) and len(body["operation_id"]) == 32
    assert "/operations/" in body["operation"] and body["operation"].endswith(body["operation_id"])


async def test_operations_read_api_lists_fetches_and_is_gated(admin_client, client, tmp_path, monkeypatch):
    """H2: GET /operations lists recent ops; GET /operations/{id} fetches one; unknown -> 404;
    both inherit the router's admin gate (no token -> 403). Ties back to H1's operation_id."""
    nid = (await admin_client.post(NODES, json={"host": "192.168.1.67"})).json()["node_id"]
    bundle = tmp_path / "b.tgz"
    bundle.write_text("x")
    monkeypatch.setattr(sb_routes, "NODE_BUNDLE_TAR", str(bundle))
    monkeypatch.setattr(sb_routes, "capture_host_key", lambda host: "")
    monkeypatch.setattr(sb_routes.node_provisioner, "provision", lambda *a, **k: None)
    op_id = (await admin_client.post(f"{NODES}/{nid}/provision",
                                     json={"sudo_password": "x", "allow_tofu": True})).json()["operation_id"]

    lst = await admin_client.get("/api/v1/admin/operations")
    assert lst.status_code == 200
    assert any(o["operation_id"] == op_id and o["node_id"] == nid and o["op_type"] == "provision"
               for o in lst.json()["operations"])

    one = await admin_client.get(f"/api/v1/admin/operations/{op_id}")
    assert one.status_code == 200 and one.json()["operation_id"] == op_id

    assert (await admin_client.get("/api/v1/admin/operations/deadbeef")).status_code == 404
    assert (await client.get("/api/v1/admin/operations")).status_code == 403          # admin-gated


async def test_operations_corrupt_journal_fails_closed(admin_client, tmp_path):
    """H3: a corrupt operations.json makes the read 503 (never a lie of '[]'), quarantines the bad
    file, then self-heals to an empty 200 on the next read."""
    (tmp_path / "operations.json").write_text("{ not valid json ")   # = BIND_STATE_PATH.parent/operations.json
    r = await admin_client.get("/api/v1/admin/operations")
    assert r.status_code == 503
    assert any(p.name.startswith("operations.json.corrupt-") for p in tmp_path.iterdir())
    r2 = await admin_client.get("/api/v1/admin/operations")            # quarantined → now empty
    assert r2.status_code == 200 and r2.json()["operations"] == []


async def test_provision_refuses_to_start_on_corrupt_journal(admin_client, tmp_path, monkeypatch):
    """H3: begin() fails closed → provision returns 503; the long op is NOT started untracked."""
    nid = (await admin_client.post(NODES, json={"host": "192.168.1.68"})).json()["node_id"]
    bundle = tmp_path / "b.tgz"
    bundle.write_text("x")
    monkeypatch.setattr(sb_routes, "NODE_BUNDLE_TAR", str(bundle))
    monkeypatch.setattr(sb_routes, "capture_host_key", lambda host: "")
    monkeypatch.setattr(sb_routes.node_provisioner, "provision", lambda *a, **k: None)
    (tmp_path / "operations.json").write_text("CORRUPT")
    r = await admin_client.post(f"{NODES}/{nid}/provision",
                                json={"sudo_password": "x", "allow_tofu": True})
    assert r.status_code == 503


async def test_provision_refuses_unconfirmed_host_key(admin_client, tmp_path, monkeypatch):
    nid = (await admin_client.post(NODES, json={"host": "192.168.1.63"})).json()["node_id"]
    bundle = tmp_path / "b.tgz"
    bundle.write_text("x")
    monkeypatch.setattr(sb_routes, "NODE_BUNDLE_TAR", str(bundle))
    # no host key pinned and allow_tofu omitted → refuse (P4-SEC Gap 2)
    r = await admin_client.post(f"{NODES}/{nid}/provision", json={"sudo_password": "x"})
    assert r.status_code == 412 and "not confirmed" in r.json()["detail"]


async def test_host_key_confirm_pins_on_match_rejects_on_mismatch(admin_client, monkeypatch):
    nid = (await admin_client.post(NODES, json={"host": "192.168.1.64"})).json()["node_id"]
    monkeypatch.setattr(sb_routes, "capture_host_key", lambda host: "192.168.1.64 ssh-ed25519 AAAAFAKE")
    monkeypatch.setattr(sb_routes, "host_key_fingerprint", lambda line, **k: "SHA256:GOODFP")
    # GET is informational — returns the fingerprint, pins nothing
    g = await admin_client.get(f"{NODES}/{nid}/host-key")
    assert g.status_code == 200 and g.json()["fingerprint"] == "SHA256:GOODFP" and g.json()["pinned"] is False
    # mismatch → 409, nothing pinned
    bad = await admin_client.post(f"{NODES}/{nid}/host-key/confirm",
                                  json={"expected_fingerprint": "SHA256:WRONG"})
    assert bad.status_code == 409
    assert sb_routes.sbs.get_node(nid, state_path=sb_routes.BIND_STATE_PATH).host_key is None
    # match → pins the captured key
    ok = await admin_client.post(f"{NODES}/{nid}/host-key/confirm",
                                 json={"expected_fingerprint": "SHA256:GOODFP"})
    assert ok.status_code == 200 and ok.json()["pinned"] is True
    assert sb_routes.sbs.get_node(nid, state_path=sb_routes.BIND_STATE_PATH).host_key == \
        "192.168.1.64 ssh-ed25519 AAAAFAKE"


async def test_host_key_confirm_refuses_silent_repin(admin_client, monkeypatch):
    nid = (await admin_client.post(NODES, json={"host": "192.168.1.66"})).json()["node_id"]
    # pin key A (confirmed)
    monkeypatch.setattr(sb_routes, "capture_host_key", lambda host: "h ssh-ed25519 KEYA")
    monkeypatch.setattr(sb_routes, "host_key_fingerprint", lambda line, **k: "SHA256:A")
    await admin_client.post(f"{NODES}/{nid}/host-key/confirm", json={"expected_fingerprint": "SHA256:A"})
    # node now presents key B; confirm matches the LIVE key but a pin already exists → refuse, keep A
    monkeypatch.setattr(sb_routes, "capture_host_key", lambda host: "h ssh-ed25519 KEYB")
    monkeypatch.setattr(sb_routes, "host_key_fingerprint", lambda line, **k: "SHA256:B")
    r = await admin_client.post(f"{NODES}/{nid}/host-key/confirm", json={"expected_fingerprint": "SHA256:B"})
    assert r.status_code == 409
    assert sb_routes.sbs.get_node(nid, state_path=sb_routes.BIND_STATE_PATH).host_key == "h ssh-ed25519 KEYA"
    # force=true → deliberate key rotation re-pins to B
    r2 = await admin_client.post(f"{NODES}/{nid}/host-key/confirm",
                                 json={"expected_fingerprint": "SHA256:B", "force": True})
    assert r2.status_code == 200
    assert sb_routes.sbs.get_node(nid, state_path=sb_routes.BIND_STATE_PATH).host_key == "h ssh-ed25519 KEYB"


async def test_provision_proceeds_after_host_key_confirmed(admin_client, tmp_path, monkeypatch):
    nid = (await admin_client.post(NODES, json={"host": "192.168.1.65"})).json()["node_id"]
    monkeypatch.setattr(sb_routes, "capture_host_key", lambda host: "192.168.1.65 ssh-ed25519 AAAAFAKE")
    monkeypatch.setattr(sb_routes, "host_key_fingerprint", lambda line, **k: "SHA256:FP")
    await admin_client.post(f"{NODES}/{nid}/host-key/confirm", json={"expected_fingerprint": "SHA256:FP"})
    bundle = tmp_path / "b.tgz"
    bundle.write_text("x")
    monkeypatch.setattr(sb_routes, "NODE_BUNDLE_TAR", str(bundle))
    monkeypatch.setattr(sb_routes.node_provisioner, "provision", lambda *a, **k: None)
    # host key now pinned → provision proceeds with NO allow_tofu
    r = await admin_client.post(f"{NODES}/{nid}/provision", json={"sudo_password": "x"})
    assert r.status_code == 200 and r.json()["started"] is True


async def test_provision_unknown_node_404(admin_client, tmp_path, monkeypatch):
    bundle = tmp_path / "b.tgz"
    bundle.write_text("x")
    monkeypatch.setattr(sb_routes, "NODE_BUNDLE_TAR", str(bundle))
    r = await admin_client.post(f"{NODES}/node-nope/provision", json={})
    assert r.status_code == 404


async def test_provision_missing_bundle_503(admin_client, monkeypatch):
    nid = (await admin_client.post(NODES, json={"host": "192.168.1.63"})).json()["node_id"]
    monkeypatch.setattr(sb_routes, "NODE_BUNDLE_TAR", "/nonexistent/bundle.tgz")
    r = await admin_client.post(f"{NODES}/{nid}/provision", json={})
    assert r.status_code == 503


async def test_rotate_token_kicks_off(admin_client, monkeypatch):
    """Phase 3 char: rotate-token resolves the node, builds the transport over a PINNED host key,
    spawns the durable op, returns operation_id. (rotate has no allow_tofu, so the key must be pinned
    first or _transport_for 412s.)"""
    nid = (await admin_client.post(NODES, json={"host": "192.168.1.71"})).json()["node_id"]
    monkeypatch.setattr(sb_routes, "capture_host_key", lambda host: "h ssh-ed25519 KEYR")
    monkeypatch.setattr(sb_routes, "host_key_fingerprint", lambda line, **k: "SHA256:R")
    await admin_client.post(f"{NODES}/{nid}/host-key/confirm", json={"expected_fingerprint": "SHA256:R"})
    monkeypatch.setattr(sb_routes.node_provisioner, "rotate_token", lambda *a, **k: None)
    r = await admin_client.post(f"{NODES}/{nid}/rotate-token", json={"sudo_password": "x"})
    body = r.json()
    assert r.status_code == 200 and body["started"] is True
    assert isinstance(body["operation_id"], str) and len(body["operation_id"]) == 32
    assert body["operation"].endswith(body["operation_id"])


async def test_rotate_token_unknown_node_404(admin_client):
    r = await admin_client.post(f"{NODES}/node-nope/rotate-token", json={"sudo_password": "x"})
    assert r.status_code == 404


async def test_provision_tofu_pins_host_key(admin_client, tmp_path, monkeypatch):
    """Phase 3 char: allow_tofu on an unconfirmed node makes _transport_for capture + PIN the host key
    (set_host_key) before provisioning — the TOFU side effect that moves into the adapter in 3-2."""
    nid = (await admin_client.post(NODES, json={"host": "192.168.1.72"})).json()["node_id"]
    bundle = tmp_path / "b.tgz"
    bundle.write_text("x")
    monkeypatch.setattr(sb_routes, "NODE_BUNDLE_TAR", str(bundle))
    monkeypatch.setattr(sb_routes, "capture_host_key", lambda host: "h ssh-ed25519 TOFUKEY")
    monkeypatch.setattr(sb_routes, "host_key_fingerprint", lambda line, **k: "SHA256:TOFU")
    monkeypatch.setattr(sb_routes.node_provisioner, "provision", lambda *a, **k: None)
    r = await admin_client.post(f"{NODES}/{nid}/provision", json={"sudo_password": "x", "allow_tofu": True})
    assert r.status_code == 200
    assert sb_routes.sbs.get_node(nid, state_path=sb_routes.BIND_STATE_PATH).host_key == "h ssh-ed25519 TOFUKEY"


async def test_activate_streams_kicks_off(admin_client, tmp_path, monkeypatch):
    nid = (await admin_client.post(NODES, json={"host": "192.168.1.64"})).json()["node_id"]
    # activate runs on an already-provisioned node, so its host key is pinned
    sb_routes.sbs.set_host_key(nid, "192.168.1.64 ssh-ed25519 AAAA", state_path=sb_routes.BIND_STATE_PATH)
    bundle = tmp_path / "b.tgz"
    bundle.write_text("x")
    monkeypatch.setattr(sb_routes, "NODE_BUNDLE_TAR", str(bundle))
    monkeypatch.setattr(sb_routes.node_provisioner, "activate_streams", lambda *a, **k: None)
    r = await admin_client.post(f"{NODES}/{nid}/streams",
                                json={"sensors": ["depth", "ir1"], "sudo_password": "x"})
    assert r.status_code == 200 and r.json()["sensors"] == ["depth", "ir1"]


async def test_activate_streams_rejects_bad_sensor(admin_client, tmp_path, monkeypatch):
    nid = (await admin_client.post(NODES, json={"host": "192.168.1.65"})).json()["node_id"]
    bundle = tmp_path / "b.tgz"
    bundle.write_text("x")
    monkeypatch.setattr(sb_routes, "NODE_BUNDLE_TAR", str(bundle))
    r = await admin_client.post(f"{NODES}/{nid}/streams", json={"sensors": ["bogus"]})
    assert r.status_code == 400


async def test_firewall_reconcile_endpoint_dryrun(admin_client, monkeypatch):
    from app.services import firewall_sync
    monkeypatch.setattr(firewall_sync, "reconcile", lambda **k: firewall_sync.Plan(
        add=[firewall_sync.Rule(("-p", "udp", "--dport", "5100:5199", "-j", "DROP"), "backstop")],
        remove_comments=["camnode:node-stale:color:9999"]))
    r = await admin_client.post("/api/v1/admin/firewall/reconcile")
    assert r.status_code == 200
    body = r.json()
    assert body["apply"] is False
    assert "backstop" in body["added"]
    assert "camnode:node-stale:color:9999" in body["removed"]


async def test_node_check_unreachable_bootstrap(admin_client, monkeypatch):
    await _register(admin_client)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("no route")))
    r = await admin_client.post(f"{NODES}/check", json={"node_id": "cam55"})
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is False
    assert body["reason"] == "node_agent_unreachable"
    assert body["next_step"] == "bootstrap_required"


async def test_node_check_local_reachable(admin_client):
    r = await admin_client.post(f"{NODES}/check", json={"node_id": "cam10"})
    assert r.status_code == 200 and r.json()["reachable"] is True


# ── bindings ──────────────────────────────────────────────────────────

async def test_create_binding_autoallocates(admin_client):
    await _register(admin_client)
    r = await _create(admin_client)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["binding_id"] == "cam55:color"
    assert b["mode"] == "remote_producer"
    assert b["mountpoint_id"] >= 2000 and b["rtp_port"] >= 5100
    assert b["rtp_iface"] == "192.168.1.10"


async def test_create_binding_unknown_node_404(admin_client):
    assert (await _create(admin_client, node_id="ghost")).status_code == 404


async def test_create_binding_local_node_400(admin_client):
    assert (await _create(admin_client, node_id="cam10")).status_code == 400


async def test_create_binding_loopback_iface_rejected(admin_client):
    await _register(admin_client)
    assert (await _create(admin_client, iface="127.0.0.1")).status_code == 400


async def test_list_bindings_shows_created(admin_client):
    await _register(admin_client)
    await _create(admin_client)
    ids = {b["binding_id"] for b in (await admin_client.get(BINDINGS)).json()["bindings"]}
    assert "cam55:color" in ids


# ── ensure-janus / remove ─────────────────────────────────────────────

async def test_ensure_janus_threads_iface(admin_client, monkeypatch):
    await _register(admin_client)
    await _create(admin_client)
    captured = {}
    monkeypatch.setattr(janus_admin, "create_mountpoint",
                        lambda **kw: captured.update(kw) or {"streaming": "created"})
    r = await admin_client.post(f"{BINDINGS}/cam55:color/ensure-janus")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "created"
    assert captured["iface"] == "192.168.1.10"


async def test_ensure_janus_unknown_404(admin_client):
    assert (await admin_client.post(f"{BINDINGS}/ghost:color/ensure-janus")).status_code == 404


async def test_remove_binding(admin_client, monkeypatch):
    await _register(admin_client)
    await _create(admin_client)
    monkeypatch.setattr(janus_admin, "destroy_mountpoint", lambda **kw: {"streaming": "destroyed"})
    r = await admin_client.post(f"{BINDINGS}/cam55:color/remove")
    assert r.status_code == 200 and r.json()["removed"] is True
    ids = {b["binding_id"] for b in (await admin_client.get(BINDINGS)).json()["bindings"]}
    assert "cam55:color" not in ids


# ── declarative fleet ──────────────────────────────────────────────────

async def test_fleet_plan_then_reconcile(admin_client, monkeypatch):
    from app.services import fleet
    manifest = [fleet.DesiredNode("192.168.1.70", "front", ["depth"])]
    monkeypatch.setattr(fleet, "load_manifest", lambda *a, **k: manifest)
    # plan: node absent → full onboarding needed (read-only)
    r = await admin_client.get("/api/v1/admin/fleet/plan")
    assert r.status_code == 200 and r.json()["in_sync"] is False
    assert r.json()["nodes"][0]["actions"] == ["register", "provision", "activate:depth"]
    # reconcile: registers the node (creds-free); now only provision/activate remain
    r2 = await admin_client.post("/api/v1/admin/fleet/reconcile")
    assert r2.status_code == 200 and len(r2.json()["registered"]) == 1
    assert r2.json()["nodes"][0]["registered"] is True
    assert r2.json()["nodes"][0]["actions"] == ["provision", "activate:depth"]


async def test_fleet_plan_bad_manifest_422(admin_client, monkeypatch):
    from app.services import fleet

    def boom(*a, **k):
        raise fleet.ManifestError("bad manifest")
    monkeypatch.setattr(fleet, "load_manifest", boom)
    r = await admin_client.get("/api/v1/admin/fleet/plan")
    assert r.status_code == 422


# ── auth ──────────────────────────────────────────────────────────────

async def test_requires_admin_token(client):
    r = await client.get(NODES)
    assert r.status_code in (401, 403, 503)

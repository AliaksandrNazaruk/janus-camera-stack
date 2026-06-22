"""P4 — declarative fleet: manifest parse + read-only drift plan + gateway reconcile."""
import os
import sys

import pytest

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.services import fleet
from app.services import stream_binding_store as sbs


def _write(tmp_path, body):
    p = tmp_path / "camera-fleet.toml"
    p.write_text(body)
    return p


def _store(tmp_path):
    return tmp_path / "sb.json", tmp_path / "al.json"


def _remote(node_id, sensor, mp, port):
    return sbs.StreamBinding(
        binding_id=f"{node_id}:{sensor}", node_id=node_id, sensor=sensor,
        mode=sbs.StreamMode.REMOTE_PRODUCER,
        transport=sbs.StreamTransport(rtp_port=port, payload_type=96, codec="h264"),
        janus=sbs.StreamJanusConfig(mountpoint_id=mp, rtp_iface="192.168.1.10"))


MANIFEST = """
[[node]]
host = "192.168.1.55"
display_name = "front"
streams = ["color", "depth", "ir1"]

[[node]]
host = "192.168.1.56"
streams = ["depth"]
"""


# ── manifest parsing ───────────────────────────────────────────────────

def test_load_manifest_valid(tmp_path):
    nodes = fleet.load_manifest(_write(tmp_path, MANIFEST))
    assert [n.host for n in nodes] == ["192.168.1.55", "192.168.1.56"]
    assert nodes[0].streams == ["color", "depth", "ir1"] and nodes[0].display_name == "front"
    assert nodes[1].display_name is None


@pytest.mark.parametrize("body,msg", [
    ('[[node]]\nstreams=["color"]\n', "missing 'host'"),
    ('[[node]]\nhost="192.168.1.55"\nstreams=["bogus"]\n', "invalid sensors"),
    ('[[node]]\nhost="192.168.1.55"\n', "non-empty array"),
    ('[[node]]\nhost="1.1.1.1"\nstreams=["color"]\n[[node]]\nhost="1.1.1.1"\nstreams=["depth"]\n',
     "duplicate host"),
])
def test_load_manifest_rejects_bad(tmp_path, body, msg):
    with pytest.raises(fleet.ManifestError) as e:
        fleet.load_manifest(_write(tmp_path, body))
    assert msg in str(e.value)


def test_load_manifest_not_found_or_garbage(tmp_path):
    with pytest.raises(fleet.ManifestError):
        fleet.load_manifest(tmp_path / "nope.toml")
    with pytest.raises(fleet.ManifestError):
        fleet.load_manifest(_write(tmp_path, "this is = not valid toml ]["))


# ── drift plan (read-only) ─────────────────────────────────────────────

def test_plan_unregistered_node_needs_full_onboarding(tmp_path):
    sp, ap = _store(tmp_path)
    m = [fleet.DesiredNode("192.168.1.55", "front", ["color", "depth"])]
    p = fleet.plan(m, state_path=sp, alloc_state_path=ap)
    n = p.nodes[0]
    assert not n.registered and n.node_id is None
    assert n.actions == ["register", "provision", "activate:color,depth"]
    assert not p.in_sync


def test_plan_registered_unprovisioned(tmp_path):
    sp, ap = _store(tmp_path)
    sbs.add_node_by_host("192.168.1.55", state_path=sp)
    m = [fleet.DesiredNode("192.168.1.55", None, ["depth"])]
    n = fleet.plan(m, state_path=sp, alloc_state_path=ap).nodes[0]
    assert n.registered and not n.provisioned
    assert n.actions == ["provision", "activate:depth"]


def test_plan_in_sync(tmp_path):
    sp, ap = _store(tmp_path)
    node = sbs.add_node_by_host("192.168.1.55", state_path=sp)
    sbs.set_provision_state(node.node_id, "ready", state_path=sp)
    sbs.upsert_binding(_remote(node.node_id, "depth", 2000, 5100), state_path=sp, alloc_state_path=ap)
    m = [fleet.DesiredNode("192.168.1.55", None, ["depth"])]
    p = fleet.plan(m, state_path=sp, alloc_state_path=ap)
    assert p.in_sync and p.nodes[0].actions == [] and p.nodes[0].active_streams == ["depth"]


def test_plan_flags_prune_candidates_without_removing(tmp_path):
    sp, ap = _store(tmp_path)
    node = sbs.add_node_by_host("192.168.1.55", state_path=sp)        # ordinal 0
    sbs.set_provision_state(node.node_id, "ready", state_path=sp)
    sbs.upsert_binding(_remote(node.node_id, "depth", 2000, 5100), state_path=sp, alloc_state_path=ap)
    sbs.upsert_binding(_remote(node.node_id, "color", 2001, 5102), state_path=sp, alloc_state_path=ap)
    sbs.add_node_by_host("192.168.1.99", state_path=sp)               # extra host, not in manifest
    m = [fleet.DesiredNode("192.168.1.55", None, ["depth"])]          # color is extra
    p = fleet.plan(m, state_path=sp, alloc_state_path=ap)
    assert "192.168.1.99" in p.extra_nodes
    assert p.nodes[0].extra_streams == ["color"]
    assert not p.in_sync
    # prune is REPORTED, never applied
    assert sbs.get_binding(f"{node.node_id}:color", state_path=sp, alloc_state_path=ap) is not None
    assert sbs.get_node(node.node_id, state_path=sp) is not None


# ── gateway-side reconcile (creds-free, additive) ──────────────────────

def test_reconcile_gateway_registers_missing_and_is_idempotent(tmp_path):
    sp, _ = _store(tmp_path)
    m = [fleet.DesiredNode("192.168.1.55", "front", ["depth"]),
         fleet.DesiredNode("192.168.1.56", None, ["color"])]
    added = fleet.reconcile_gateway(m, state_path=sp)
    assert len(added) == 2
    hosts = {n.host for nid, n in sbs.list_nodes(state_path=sp).items() if nid != sbs.LOCAL_NODE_ID}
    assert hosts == {"192.168.1.55", "192.168.1.56"}
    assert fleet.reconcile_gateway(m, state_path=sp) == []            # idempotent — no re-register

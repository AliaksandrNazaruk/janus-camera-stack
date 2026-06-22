"""Slice A — minted-identity add-by-IP.

The operator supplies only an IP; the gateway mints an opaque node_id (never the
IP, never a typed label — review I2/I8). Camera serial is attached after probe.
"""
import pytest

from app.services import stream_binding_store as sbs


def _sp(tmp_path):
    return tmp_path / "stream_bindings.json"


def test_add_by_host_mints_opaque_node_id(tmp_path):
    n = sbs.add_node_by_host("192.168.1.55", state_path=_sp(tmp_path))
    assert n.node_id.startswith("node-")
    assert n.node_id != "192.168.1.55"          # identity is never the IP
    assert n.host == "192.168.1.55"
    assert n.role == "remote_producer"
    assert n.ordinal == 0


def test_add_by_host_is_lookup_or_create(tmp_path):
    sp = _sp(tmp_path)
    a = sbs.add_node_by_host("192.168.1.55", state_path=sp)
    b = sbs.add_node_by_host("192.168.1.55", state_path=sp)   # same host
    assert a.node_id == b.node_id                # no duplicate minted (review I8)
    c = sbs.add_node_by_host("192.168.1.56", state_path=sp)
    assert c.node_id != a.node_id
    assert c.ordinal == 1


def test_add_by_host_mints_unique_per_node_token(tmp_path):
    sp = _sp(tmp_path)
    a = sbs.add_node_by_host("192.168.1.55", state_path=sp)
    b = sbs.add_node_by_host("192.168.1.56", state_path=sp)
    assert a.agent_token and b.agent_token
    assert a.agent_token != b.agent_token                     # P4-SEC: per-node, not a shared fleet token
    again = sbs.add_node_by_host("192.168.1.55", state_path=sp)
    assert again.agent_token == a.agent_token                 # idempotent lookup does NOT re-mint


def test_set_agent_token_persists_and_survives_upsert(tmp_path):
    sp = _sp(tmp_path)
    n = sbs.add_node_by_host("192.168.1.55", state_path=sp)
    sbs.set_agent_token(n.node_id, "rotated-value", state_path=sp)
    assert sbs.get_node(n.node_id, state_path=sp).agent_token == "rotated-value"
    sbs.upsert_node(n.node_id, host=n.host, role=n.role, state_path=sp)   # e.g. reachability refresh
    assert sbs.get_node(n.node_id, state_path=sp).agent_token == "rotated-value"  # preserved


def test_upsert_node_register_path_also_mints_token(tmp_path):
    sp = _sp(tmp_path)
    n = sbs.upsert_node("cam55", host="192.168.1.55", role="remote_producer", state_path=sp)
    assert n.agent_token                          # /nodes/register path mints too (review MEDIUM)
    again = sbs.upsert_node("cam55", host="192.168.1.55", role="remote_producer", state_path=sp)
    assert again.agent_token == n.agent_token     # preserved across re-upsert, not re-minted


@pytest.mark.parametrize("bad", ["not-an-ip", "999.1.1.1", "127.0.0.1", "0.0.0.0"])
def test_add_by_host_rejects_bad_addresses(tmp_path, bad):
    with pytest.raises(sbs.BindingValidationError):
        sbs.add_node_by_host(bad, state_path=_sp(tmp_path))


def test_set_serial_and_upsert_preserves_identity_fields(tmp_path):
    sp = _sp(tmp_path)
    n = sbs.add_node_by_host("192.168.1.55", display_name="front", state_path=sp)
    sbs.set_serial(n.node_id, "048522073892", state_path=sp)
    got = sbs.get_node(n.node_id, state_path=sp)
    assert got.serial == "048522073892"
    assert got.display_name == "front"
    # An upsert that doesn't pass serial/display_name must NOT wipe them...
    sbs.upsert_node(n.node_id, host="192.168.1.99", role="remote_producer", state_path=sp)
    again = sbs.get_node(n.node_id, state_path=sp)
    assert again.serial == "048522073892"
    assert again.display_name == "front"
    assert again.host == "192.168.1.99"          # ...but host IS updatable (DHCP re-IP)


def test_remote_binding_id_prefers_serial_with_node_fallback(tmp_path):
    sp = _sp(tmp_path)
    n = sbs.add_node_by_host("192.168.1.55", state_path=sp)
    assert sbs.remote_binding_id(n, "color") == f"{n.node_id}:color"   # pre-probe → node fallback
    sbs.set_serial(n.node_id, "048522073892", state_path=sp)
    n2 = sbs.get_node(n.node_id, state_path=sp)
    assert sbs.remote_binding_id(n2, "color") == "048522073892:color"  # serial-keyed once probed


def test_migrate_remote_binding_ids_rekeys_preserving_allocation(tmp_path):
    sp = tmp_path / "sb.json"
    ap = tmp_path / "al.json"
    n = sbs.add_node_by_host("192.168.1.55", state_path=sp)
    b = sbs.StreamBinding(
        binding_id=f"{n.node_id}:color", node_id=n.node_id, sensor="color",
        mode=sbs.StreamMode.REMOTE_PRODUCER,
        transport=sbs.StreamTransport(rtp_port=5102, payload_type=96, codec="h264"),
        janus=sbs.StreamJanusConfig(mountpoint_id=2002, rtp_iface="192.168.1.10"))
    sbs.upsert_binding(b, state_path=sp, alloc_state_path=ap)
    sbs.set_serial(n.node_id, "048522073892", state_path=sp)

    assert sbs.migrate_remote_binding_ids(state_path=sp) == 1
    got = sbs.get_binding("048522073892:color", state_path=sp, alloc_state_path=ap)
    assert got is not None
    assert got.transport.rtp_port == 5102 and got.janus.mountpoint_id == 2002   # allocation preserved
    assert sbs.get_binding(f"{n.node_id}:color", state_path=sp, alloc_state_path=ap) is None
    assert sbs.migrate_remote_binding_ids(state_path=sp) == 0                    # idempotent


def test_node_entry_roundtrips_new_fields():
    e = sbs.NodeEntry(node_id="node-abc", host="192.168.1.55", role="remote_producer",
                      serial="S1", display_name="d")
    assert sbs.NodeEntry.from_raw("node-abc", e.to_dict()) == e


def test_list_nodes_has_added_plus_local_sentinel(tmp_path):
    sp = _sp(tmp_path)
    n = sbs.add_node_by_host("192.168.1.55", state_path=sp)
    nodes = sbs.list_nodes(state_path=sp)
    assert sbs.LOCAL_NODE_ID in nodes            # cam10 stays the implicit local sentinel
    assert n.node_id in nodes

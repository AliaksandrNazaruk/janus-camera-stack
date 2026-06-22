"""G1 acceptance — StreamBindingStore (docs/design/STREAM_BINDING_MODEL.md v2).

Covers:
  • remote binding round-trip
  • local projection == live allocation (color + depth, multi-sensor)
  • upsert validation (union uniqueness, even port, ranges, loopback, node, mode)
  • cam10 unaffected — store ops never touch sensor_allocations.json
  • allocation above the legacy pool, union-checked
  • node ordinals; status; remove; projection tie-break (clobber shape)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.services import mountpoint_allocator as _alloc
from app.services import stream_binding_store as sb
from app.services.stream_binding_store import (
    StreamBinding,
    StreamJanusConfig,
    StreamMode,
    StreamTransport,
    BindingValidationError,
)

JMID = 1305


@pytest.fixture
def bind_path(tmp_path):
    return tmp_path / "stream_bindings.json"


@pytest.fixture
def alloc_path(tmp_path):
    return tmp_path / "allocations.json"


def _reg(bind_path, node_id="cam55", host="192.168.1.55"):
    return sb.upsert_node(node_id, host=host, role="remote_producer", state_path=bind_path)


def _remote(node_id="cam55", sensor="color", mp=2000, port=5100, iface="192.168.1.10"):
    return StreamBinding(
        binding_id=f"{node_id}:{sensor}", node_id=node_id, sensor=sensor,
        mode=StreamMode.REMOTE_PRODUCER,
        transport=StreamTransport(rtp_port=port),
        janus=StreamJanusConfig(mountpoint_id=mp, rtp_iface=iface),
    )


def _upsert(b, bind_path, alloc_path, jmid=JMID):
    return sb.upsert_binding(b, state_path=bind_path, alloc_state_path=alloc_path, janus_mount_id=jmid)


def _get(bid, bind_path, alloc_path):
    return sb.get_binding(bid, state_path=bind_path, alloc_state_path=alloc_path, janus_mount_id=JMID)


# ── remote round-trip ─────────────────────────────────────────────────

def test_remote_binding_roundtrip(bind_path, alloc_path):
    _reg(bind_path)
    b = _remote(mp=2000, port=5100)
    _upsert(b, bind_path, alloc_path)
    got = _get("cam55:color", bind_path, alloc_path)
    assert got == b
    assert got.mode is StreamMode.REMOTE_PRODUCER


def test_upsert_accepts_serial_keyed_binding_id(bind_path, alloc_path):
    """Bug B regression (SERIAL_KEYED_BINDING_ID): once a node's serial is known,
    make_gateway_binder builds a '{serial}:{sensor}' binding_id via
    remote_binding_id(). _validate_remote used to ONLY accept '{node_id}:{sensor}'
    and rejected the serial-keyed form, breaking re-activation of a provisioned
    node ("binding_id 'SER:color' != 'node:color'"). Both forms must be accepted."""
    sb.upsert_node("cam55", host="192.168.1.55", role="remote_producer", state_path=bind_path)
    sb.set_serial("cam55", "SER771", state_path=bind_path)
    node = sb.get_node("cam55", state_path=bind_path)
    bid = sb.remote_binding_id(node, "color")
    assert bid == "SER771:color"                       # serial-keyed once serial known
    b = StreamBinding(
        binding_id=bid, node_id="cam55", sensor="color",
        mode=StreamMode.REMOTE_PRODUCER,
        transport=StreamTransport(rtp_port=5100),
        janus=StreamJanusConfig(mountpoint_id=2000, rtp_iface="192.168.1.10"),
    )
    assert _upsert(b, bind_path, alloc_path).binding_id == "SER771:color"   # must NOT raise
    # node-id-keyed form still accepted too (pre-probe legacy bindings)
    assert _upsert(_remote(sensor="depth", mp=2001, port=5102),
                   bind_path, alloc_path).binding_id == "cam55:depth"


def test_agent_token_stored_in_separate_0600_secrets_file(bind_path):
    """H3: agent tokens must NOT sit in the world-readable topology file. They go
    in a sibling node_secrets.json at 0600; stream_bindings.json stays readable
    (non-root topology reads keep working) and carries no token."""
    import os as _os, stat as _stat, json as _json
    n = sb.add_node_by_host("192.168.1.73", state_path=bind_path)
    assert n.agent_token, "a token is minted + returned to the caller"
    # NOT in the topology file
    assert "agent_token" not in _json.loads(bind_path.read_text())["nodes"][n.node_id]
    # IS in the 0600 secrets file
    secrets_path = bind_path.with_name("node_secrets.json")
    assert secrets_path.exists()
    assert _stat.S_IMODE(_os.stat(secrets_path).st_mode) == 0o600, oct(_os.stat(secrets_path).st_mode)
    assert _json.loads(secrets_path.read_text())[n.node_id] == n.agent_token
    # reads round-trip the token from the secret store
    assert sb.get_node(n.node_id, state_path=bind_path).agent_token == n.agent_token


def test_remote_binding_survives_serialization(bind_path, alloc_path):
    _reg(bind_path)
    _upsert(_remote(), bind_path, alloc_path)
    on_disk = json.loads(bind_path.read_text())
    assert on_disk["bindings"]["cam55:color"]["mode"] == "remote_producer"
    assert on_disk["nodes"]["cam55"]["host"] == "192.168.1.55"


# ── local projection == live allocation (multi-sensor) ────────────────

def test_local_projection_color_and_depth(bind_path, alloc_path):
    _alloc.ensure(_alloc.LOCAL_SERIAL, "color", 1305, 5004, state_path=alloc_path)
    depth = _alloc.allocate("SER1", "depth", state_path=alloc_path)

    bindings = sb.list_bindings(state_path=bind_path, alloc_state_path=alloc_path, janus_mount_id=JMID)
    color = bindings[f"{_alloc.LOCAL_SERIAL}:color"]   # serial-keyed (sentinel pre-migration)
    assert color.mode is StreamMode.LOCAL_PRODUCER
    assert color.node_id == "cam10"                    # node_id stays the local sentinel
    assert color.janus.mountpoint_id == 1305
    assert color.janus.rtp_iface == "127.0.0.1"   # local defaults to loopback
    assert color.transport.rtp_port == 5004

    d = bindings["SER1:depth"]                      # serial-keyed local binding
    assert d.janus.mountpoint_id == depth.mp_id
    assert d.transport.rtp_port == depth.rtp_port


def test_local_projection_two_cameras_same_sensor_are_distinct(bind_path, alloc_path):
    """Two local serials with the same sensor (e.g. two D435i on .10) project to TWO
    distinct serial-keyed bindings — the serial-keyed fold replaces the old
    fold-by-sensor + janus_mount_id tie-break, enabling local multi-camera."""
    alloc_path.write_text(json.dumps({"version": 1, "allocations": {
        "A:color": {"mp_id": 1305, "rtp_port": 5004},
        "B:color": {"mp_id": 1400, "rtp_port": 5050},
    }}))
    bindings = sb.list_bindings(state_path=bind_path, alloc_state_path=alloc_path, janus_mount_id=JMID)
    assert bindings["A:color"].janus.mountpoint_id == 1305
    assert bindings["B:color"].janus.mountpoint_id == 1400
    assert bindings["A:color"].node_id == bindings["B:color"].node_id == "cam10"


def test_merged_view_has_local_and_remote(bind_path, alloc_path):
    _alloc.ensure(_alloc.LOCAL_SERIAL, "color", 1305, 5004, state_path=alloc_path)
    _reg(bind_path)
    _upsert(_remote(), bind_path, alloc_path)
    bindings = sb.list_bindings(state_path=bind_path, alloc_state_path=alloc_path, janus_mount_id=JMID)
    assert bindings[f"{_alloc.LOCAL_SERIAL}:color"].mode is StreamMode.LOCAL_PRODUCER
    assert bindings["cam55:color"].mode is StreamMode.REMOTE_PRODUCER


# ── upsert validation ─────────────────────────────────────────────────

def test_upsert_rejects_local_mode(bind_path, alloc_path):
    _reg(bind_path)
    local = StreamBinding(
        binding_id="cam55:color", node_id="cam55", sensor="color",
        mode=StreamMode.LOCAL_PRODUCER,
        transport=StreamTransport(rtp_port=5100),
        janus=StreamJanusConfig(mountpoint_id=2000, rtp_iface="192.168.1.10"))
    with pytest.raises(BindingValidationError, match="projections"):
        _upsert(local, bind_path, alloc_path)


def test_upsert_rejects_unknown_node(bind_path, alloc_path):
    with pytest.raises(BindingValidationError, match="unknown node"):
        _upsert(_remote(), bind_path, alloc_path)


def test_upsert_rejects_loopback_host(bind_path, alloc_path):
    _reg(bind_path, host="127.0.0.1")
    with pytest.raises(BindingValidationError, match="non-loopback"):
        _upsert(_remote(), bind_path, alloc_path)


def test_upsert_rejects_loopback_iface(bind_path, alloc_path):
    _reg(bind_path)
    with pytest.raises(BindingValidationError, match="rtp_iface"):
        _upsert(_remote(iface="127.0.0.1"), bind_path, alloc_path)


def test_upsert_rejects_odd_port(bind_path, alloc_path):
    _reg(bind_path)
    with pytest.raises(BindingValidationError, match="even"):
        _upsert(_remote(port=5101), bind_path, alloc_path)


def test_upsert_rejects_sub_pool_mountpoint(bind_path, alloc_path):
    _reg(bind_path)
    with pytest.raises(BindingValidationError, match="above the legacy pool"):
        _upsert(_remote(mp=1500), bind_path, alloc_path)


def test_upsert_rejects_sub_pool_port(bind_path, alloc_path):
    _reg(bind_path)
    with pytest.raises(BindingValidationError, match="above the legacy pool"):
        _upsert(_remote(port=5006), bind_path, alloc_path)


def test_upsert_rejects_mountpoint_eq_janus_mount_id(bind_path, alloc_path):
    _reg(bind_path)
    # override janus_mount_id into the remote range to exercise the invariant
    with pytest.raises(BindingValidationError, match="local watchdog target"):
        sb.upsert_binding(_remote(mp=2000, port=5100), state_path=bind_path,
                          alloc_state_path=alloc_path, janus_mount_id=2000)


def test_upsert_rejects_duplicate_mountpoint(bind_path, alloc_path):
    _reg(bind_path)
    _upsert(_remote(sensor="color", mp=2000, port=5100), bind_path, alloc_path)
    with pytest.raises(BindingValidationError, match="mountpoint_id 2000 already"):
        _upsert(_remote(sensor="depth", mp=2000, port=5102), bind_path, alloc_path)


def test_upsert_rejects_duplicate_port_pair(bind_path, alloc_path):
    _reg(bind_path)
    _upsert(_remote(sensor="color", mp=2000, port=5100), bind_path, alloc_path)
    with pytest.raises(BindingValidationError, match="port pair"):
        _upsert(_remote(sensor="depth", mp=2002, port=5100), bind_path, alloc_path)


def test_upsert_replace_same_binding_is_idempotent(bind_path, alloc_path):
    _reg(bind_path)
    _upsert(_remote(mp=2000, port=5100), bind_path, alloc_path)
    # re-upsert same binding_id with same numbers must not self-collide
    _upsert(_remote(mp=2000, port=5100), bind_path, alloc_path)
    assert _get("cam55:color", bind_path, alloc_path).janus.mountpoint_id == 2000


def test_from_raw_rejects_invalid_mode():
    with pytest.raises(ValueError):
        StreamBinding.from_raw({
            "binding_id": "x:color", "node_id": "x", "sensor": "color", "mode": "bogus",
            "transport": {"rtp_port": 5100}, "janus": {"mountpoint_id": 2000, "rtp_iface": "192.168.1.10"},
        })


# ── cam10 unaffected ──────────────────────────────────────────────────

def test_store_ops_never_touch_allocator_file(bind_path, alloc_path):
    _reg(bind_path)
    _upsert(_remote(), bind_path, alloc_path)
    sb.set_status("cam55:color", "waiting_for_rtp", state_path=bind_path)
    sb.remove_binding("cam55:color", state_path=bind_path)
    assert bind_path.exists()
    assert not alloc_path.exists()      # the legacy allocator file was never created/written


# ── allocation (above legacy pool, union-checked) ─────────────────────

def test_allocate_above_legacy_pool_even(bind_path, alloc_path):
    _reg(bind_path)
    mp = sb.allocate_mountpoint("cam55", state_path=bind_path, alloc_state_path=alloc_path)
    port = sb.allocate_port("cam55", state_path=bind_path, alloc_state_path=alloc_path)
    assert mp >= sb.REMOTE_MP_MIN
    assert port >= sb.REMOTE_PORT_MIN and port % 2 == 0


def test_allocate_skips_used_remote_slots(bind_path, alloc_path):
    _reg(bind_path)
    _upsert(_remote(mp=2000, port=5100), bind_path, alloc_path)
    assert sb.allocate_mountpoint("cam55", state_path=bind_path, alloc_state_path=alloc_path) == 2001
    assert sb.allocate_port("cam55", state_path=bind_path, alloc_state_path=alloc_path) == 5102


def test_allocate_unknown_node_raises(bind_path, alloc_path):
    with pytest.raises(_alloc.AllocationError, match="unknown"):
        sb.allocate_mountpoint("ghost", state_path=bind_path, alloc_state_path=alloc_path)


# ── node table ────────────────────────────────────────────────────────

def test_node_ordinals_distinct_and_stable(bind_path):
    n1 = sb.upsert_node("cam55", host="192.168.1.55", role="remote_producer", state_path=bind_path)
    n2 = sb.upsert_node("cam56", host="192.168.1.56", role="remote_producer", state_path=bind_path)
    assert n1.ordinal == 0 and n2.ordinal == 1
    # re-upsert keeps ordinal
    assert sb.upsert_node("cam55", host="192.168.1.99", role="remote_producer", state_path=bind_path).ordinal == 0


def test_local_node_is_implicit(bind_path):
    assert sb.get_node("cam10", state_path=bind_path).host == "127.0.0.1"
    with pytest.raises(BindingValidationError):
        sb.upsert_node("cam10", host="x", role="y", state_path=bind_path)


# ── status / remove ───────────────────────────────────────────────────

def test_set_status_on_remote(bind_path, alloc_path):
    _reg(bind_path)
    _upsert(_remote(), bind_path, alloc_path)
    sb.set_status("cam55:color", "online", state_path=bind_path)
    assert _get("cam55:color", bind_path, alloc_path).status == "online"


def test_set_status_on_local_projection_raises(bind_path, alloc_path):
    _alloc.ensure(_alloc.LOCAL_SERIAL, "color", 1305, 5004, state_path=alloc_path)
    with pytest.raises(KeyError):
        sb.set_status(f"{_alloc.LOCAL_SERIAL}:color", "online", state_path=bind_path)


def test_set_status_invalid_value_raises(bind_path):
    with pytest.raises(ValueError):
        sb.set_status("cam55:color", "bogus", state_path=bind_path)


def test_remove_binding(bind_path, alloc_path):
    _reg(bind_path)
    _upsert(_remote(), bind_path, alloc_path)
    assert sb.remove_binding("cam55:color", state_path=bind_path) is True
    assert _get("cam55:color", bind_path, alloc_path) is None
    assert sb.remove_binding("cam55:color", state_path=bind_path) is False


# ── service-layer LAN invariants (review P0-4) ─────────────────────────

def test_add_node_rejects_gateway_ip_and_out_of_subnet(tmp_path, monkeypatch):
    sp = tmp_path / "sb.json"
    monkeypatch.setattr(sb.nodes, "GATEWAY_LAN_IP", "192.168.1.10")   # add_node_by_host reads it here (13E1)
    monkeypatch.setattr(sb.nodes, "CAMERA_LAN_CIDR", "192.168.1.0/24")
    # the gateway's own IP is not a remote producer
    with pytest.raises(sb.BindingValidationError):
        sb.add_node_by_host("192.168.1.10", state_path=sp)
    # outside the camera LAN
    with pytest.raises(sb.BindingValidationError):
        sb.add_node_by_host("10.0.0.5", state_path=sp)
    # a valid in-subnet, non-gateway host is accepted
    n = sb.add_node_by_host("192.168.1.55", state_path=sp)
    assert n.host == "192.168.1.55"


def test_add_node_cidr_unconstrained_when_env_empty(tmp_path, monkeypatch):
    sp = tmp_path / "sb.json"
    monkeypatch.setattr(sb.nodes, "GATEWAY_LAN_IP", "")              # add_node_by_host reads it here (13E1)
    monkeypatch.setattr(sb.nodes, "CAMERA_LAN_CIDR", "")             # dev/bench: no subnet constraint
    n = sb.add_node_by_host("10.20.30.40", state_path=sp)
    assert n.host == "10.20.30.40"


# ── desired_up: Start/Stop intent, SEPARATE from fdir.enabled (unified node lifecycle) ──

def test_desired_up_defaults_true_and_round_trips():
    b = _remote()
    assert b.desired_up is True                                  # new binding is desired-up
    again = StreamBinding.from_raw(b.to_dict())
    assert again.desired_up is True and "desired_up" in b.to_dict()


def test_desired_up_back_compat_derives_from_fdir_enabled():
    """A legacy row (no desired_up) must read desired_up = its old Stop flag (fdir.enabled), so
    behaviour is unchanged until the desired_up gates land."""
    base = _remote().to_dict()
    base.pop("desired_up")
    base["fdir"] = {"enabled": False, "policy": "stream_default"}
    assert StreamBinding.from_raw(base).desired_up is False      # legacy stopped → desired_up False
    base["fdir"] = {"enabled": True, "policy": "stream_default"}
    assert StreamBinding.from_raw(base).desired_up is True       # legacy enabled → desired_up True


def test_set_desired_up_is_independent_of_fdir(bind_path, alloc_path):
    _reg(bind_path)
    _upsert(_remote(), bind_path, alloc_path)
    sb.set_fdir_enabled("cam55:color", False, state_path=bind_path)   # FDIR off (recovery)
    b = sb.set_desired_up("cam55:color", True, state_path=bind_path)  # but desired UP
    assert b.desired_up is True and b.fdir.enabled is False           # decoupled
    b2 = sb.set_desired_up("cam55:color", False, state_path=bind_path)
    assert b2.desired_up is False and b2.fdir.enabled is False


def test_set_desired_up_local_projection_raises(bind_path):
    import pytest
    with pytest.raises(KeyError):
        sb.set_desired_up("141722072135:color", True, state_path=bind_path)

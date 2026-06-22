"""Slice B — provisioner state machine (FakeTransport, no SSH/hardware).

Two uniform phases: provision() deploys the pipe; activate_streams() activates a
chosen subset of streams (no sensor is special).
"""
import json

from app.services import node_provisioner as prov
from app.services import stream_binding_store as sbs
from app.services.node_provisioner import BindOutcome
from app.services.ssh_transport import FakeTransport, RunResult

DEVICE_JSON = json.dumps({
    "available": True, "error": None,
    "devices": [{"serial": "048522073892", "name": "D435",
                 "sensors": ["color", "depth", "infrared"]}],
})
EMPTY_JSON = json.dumps({"available": False, "error": None, "devices": []})


def _node(tmp_path):
    sp = tmp_path / "sb.json"
    return sbs.add_node_by_host("192.168.1.55", state_path=sp), sp


# ── provision: deploy the pipe (sensor-agnostic) ───────────────────────
def test_provision_deploys_pipe_to_ready(tmp_path):
    n, sp = _node(tmp_path)
    t = FakeTransport(responses={"realsense_probe_cli": RunResult(0, DEVICE_JSON)})
    r = prov.provision(n.node_id, t, bundle_tar="/tmp/b.tgz", state_path=sp)
    assert r.ok and r.state == prov.PState.READY
    assert r.serial == "048522073892"
    got = sbs.get_node(n.node_id, state_path=sp)
    assert got.provision_state == prov.PState.READY and got.serial == "048522073892"
    # the pipe deploy ran with sudo; NO stream was activated during provision
    assert any(k == "run" and "bootstrap.sh deploy" in c and sudo for (k, c, sudo) in t.calls)
    assert not any(k == "run" and "activate --sensor" in c for (k, c, _s) in t.calls)


def test_provision_no_camera(tmp_path):
    n, sp = _node(tmp_path)
    t = FakeTransport(responses={"realsense_probe_cli": RunResult(0, EMPTY_JSON)})
    r = prov.provision(n.node_id, t, bundle_tar="/tmp/b.tgz", state_path=sp)
    assert r.state == prov.PState.NO_CAMERA
    assert not any(k == "run" and "bootstrap.sh deploy" in c for (k, c, _s) in t.calls)
    assert sbs.get_node(n.node_id, state_path=sp).provision_state == prov.PState.NO_CAMERA


def test_provision_unreachable(tmp_path):
    n, sp = _node(tmp_path)
    t = FakeTransport(responses={"echo provision-ok": RunResult(255, "", "timeout")})
    assert prov.provision(n.node_id, t, bundle_tar="/tmp/b.tgz", state_path=sp).state == prov.PState.FAILED


def test_provision_deploy_failure(tmp_path):
    n, sp = _node(tmp_path)
    t = FakeTransport(responses={"realsense_probe_cli": RunResult(0, DEVICE_JSON),
                                 "bootstrap.sh deploy": RunResult(1, "", "boom")})
    r = prov.provision(n.node_id, t, bundle_tar="/tmp/b.tgz", state_path=sp)
    assert r.state == prov.PState.FAILED and "deploy failed" in r.detail


def test_provision_pushes_per_node_token(tmp_path):
    n, sp = _node(tmp_path)
    assert n.agent_token                            # minted at enrollment (add_node_by_host)
    t = FakeTransport(responses={"realsense_probe_cli": RunResult(0, DEVICE_JSON)})
    prov.provision(n.node_id, t, bundle_tar="/tmp/b.tgz", state_path=sp)
    # the node's OWN token is pushed — not a shared fleet token
    assert any(k == "run" and f"bootstrap.sh deploy --agent-token {n.agent_token}" in c
               for (k, c, _s) in t.calls)


def test_provision_mints_token_for_legacy_node(tmp_path):
    import json
    sp = tmp_path / "sb.json"
    sbs.upsert_node("legacy", host="192.168.1.55", role="remote_producer", state_path=sp)
    # simulate a node persisted BEFORE per-node tokens: no token in topology AND none
    # in the separate secret store (post-H3 the token lives in node_secrets.json).
    state = json.loads(sp.read_text())
    state["nodes"]["legacy"].pop("agent_token", None)
    sp.write_text(json.dumps(state))
    sp.with_name("node_secrets.json").unlink(missing_ok=True)
    assert sbs.get_node("legacy", state_path=sp).agent_token is None
    t = FakeTransport(responses={"realsense_probe_cli": RunResult(0, DEVICE_JSON)})
    prov.provision("legacy", t, bundle_tar="/tmp/b.tgz", state_path=sp)
    tok = sbs.get_node("legacy", state_path=sp).agent_token
    assert tok and any(k == "run" and f"--agent-token {tok}" in c for (k, c, _s) in t.calls)


def test_rotate_token_pushes_set_token_and_persists(tmp_path):
    n, sp = _node(tmp_path)
    old = n.agent_token
    t = FakeTransport()                              # set-token succeeds
    assert prov.rotate_token(n.node_id, t, state_path=sp) is True
    new = sbs.get_node(n.node_id, state_path=sp).agent_token
    assert new and new != old                       # rotated to a fresh value
    # lightweight set-token (with sudo), NOT a full redeploy / mux restart
    assert any(k == "run" and "set-token --agent-token" in c and new in c and sudo
               for (k, c, sudo) in t.calls)
    assert not any(k == "run" and "bootstrap.sh deploy" in c for (k, c, _s) in t.calls)


def test_rotate_token_failure_keeps_old(tmp_path):
    n, sp = _node(tmp_path)
    old = n.agent_token
    t = FakeTransport(responses={"set-token": RunResult(1, "", "boom")})
    assert prov.rotate_token(n.node_id, t, state_path=sp) is False
    assert sbs.get_node(n.node_id, state_path=sp).agent_token == old   # unchanged on failure


# ── activate_streams: uniform per chosen sensor ────────────────────────
def _binder(records):
    def bind(node, sensor):
        records.append(sensor)
        return BindOutcome(binding_id=f"{node.node_id}:{sensor}", rtp_port=5100 + 2 * len(records))
    return bind


def test_activate_streams_uniform_multi_sensor(tmp_path):
    n, sp = _node(tmp_path)
    t = FakeTransport()  # all ok
    recs = []
    res = prov.activate_streams(n.node_id, t, sensors=["depth", "ir1"],
                                gateway_host="192.168.1.10", on_bind=_binder(recs), state_path=sp)
    assert [x.sensor for x in res] == ["depth", "ir1"]
    assert all(x.ok for x in res)
    assert recs == ["depth", "ir1"]                      # each bound uniformly, no color special-case
    acts = [c for (k, c, sudo) in t.calls if k == "run" and "activate --sensor" in c and sudo]
    assert any("--sensor depth" in c for c in acts)
    assert any("--sensor ir1" in c for c in acts)


def test_activate_streams_bind_failure_isolated(tmp_path):
    n, sp = _node(tmp_path)
    t = FakeTransport()

    def bind(node, sensor):
        if sensor == "color":
            raise RuntimeError("janus down")
        return BindOutcome(f"{node.node_id}:{sensor}", 5102)

    res = {x.sensor: x for x in prov.activate_streams(
        n.node_id, t, sensors=["color", "depth"], gateway_host="192.168.1.10",
        on_bind=bind, state_path=sp)}
    assert res["color"].ok is False and "bind failed" in res["color"].detail
    assert res["depth"].ok is True                       # one sensor's failure isolates


def test_activate_streams_node_activate_failure(tmp_path):
    n, sp = _node(tmp_path)
    t = FakeTransport(responses={"activate --sensor depth": RunResult(1, "", "fifo timeout")})
    res = prov.activate_streams(n.node_id, t, sensors=["depth"], gateway_host="192.168.1.10",
                                on_bind=_binder([]), state_path=sp)
    assert res[0].ok is False and "activate failed" in res[0].detail

"""Phase 10 use-case unit tests — restart_binding / stop_binding return BindingOpResult and
raise domain errors (no FastAPI). Complements the route-level oracle in test_operator_console.py
(which proves the HTTP mapping is unchanged); here we pin the use-case contract directly.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.services import janus_admin, node_client, sensor_lifecycle
from app.services import fleet, firewall_sync, mountpoint_allocator
from app.services import reconcile_drift as drift_svc
from app.services import stream_binding_store as sbs
from app.application.stream_bindings import (
    AddNodeCommand,
    AddNodeIsLocalGateway,
    AllocationConflict,
    BindingInvalid,
    BindingNodeNotFound,
    BindingNotFound,
    CheckNodeCommand,
    ConfirmHostKeyCommand,
    CreateBindingCommand,
    EnsureJanusCommand,
    EnsureJanusLocalRejected,
    FirewallReconcileCommand,
    FleetPlanCommand,
    FleetReconcileCommand,
    GetHostKeyCommand,
    GetTuningCommand,
    HostKeyFingerprintMismatch,
    HostKeyPinReplaceRejected,
    HostKeyUnreachable,
    InvalidRotation,
    JanusUnreachable,
    ListBindingsCommand,
    ListNodesCommand,
    LocalBindingNotCreatable,
    LocalBindingNotRemovable,
    LocalFdirNotToggleable,
    LocalNodeNoHostKey,
    LocalTuningRejected,
    MaintenanceLocalRejected,
    ManifestInvalid,
    NoTuningFields,
    NodeAgentError,
    NodeNotFound,
    NodeRegistrationInvalid,
    ReconcileDriftCommand,
    RegisterNodeCommand,
    RemoveBindingCommand,
    RestartBindingCommand,
    SetFdirCommand,
    SetMaintenanceCommand,
    SetTuningCommand,
    StopBindingCommand,
    UnsupportedSensorError,
    add_node,
    check_node,
    confirm_host_key,
    create_binding,
    ensure_janus,
    firewall_reconcile,
    fleet_plan,
    fleet_reconcile,
    get_host_key,
    get_tuning,
    get_modes,
    list_bindings,
    list_nodes,
    reconcile_drift,
    register_node,
    remove_binding,
    restart_binding,
    set_fdir,
    set_maintenance,
    set_tuning,
    stop_binding,
)

_P = Path("/tmp/_phase10")


def _remote(binding_id="cam55:color", node_id="cam55", sensor="color"):
    return SimpleNamespace(mode=sbs.StreamMode.REMOTE_PRODUCER, node_id=node_id, sensor=sensor,
                           janus=SimpleNamespace(mountpoint_id=2000))


def _local(sensor="color"):
    return SimpleNamespace(mode=sbs.StreamMode.LOCAL_PRODUCER, node_id="cam10", sensor=sensor)


def _cmd_restart(bid="cam55:color"):
    return RestartBindingCommand(binding_id=bid, bind_state_path=_P, alloc_state_path=_P)


def _cmd_stop(bid="cam55:color"):
    return StopBindingCommand(binding_id=bid, bind_state_path=_P, alloc_state_path=_P)


# audit() is test-safe (the route oracle exercises it too) — let it run.


# ── restart_binding ─────────────────────────────────────────────────────────
def test_restart_not_found_raises(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: None)
    with pytest.raises(BindingNotFound):
        restart_binding(_cmd_restart("nope:color"))


def _isolate_remote_resume(monkeypatch, calls):
    """Patch the resume side-effects (set_desired_up + ensure_janus) so remote-restart unit tests
    don't touch the real store/Janus. Appends ('desired'/'ensure', ...) to calls."""
    monkeypatch.setattr(sbs, "set_desired_up",
                        lambda bid, up, **k: calls.append(("desired", bid, up)))
    monkeypatch.setattr("app.services.binding_provision.ensure_janus",
                        lambda b, **k: calls.append(("ensure", b.janus.mountpoint_id)))


def test_restart_remote_resumes_desired_up_and_ensures(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _remote())
    calls = []
    _isolate_remote_resume(monkeypatch, calls)
    fake = SimpleNamespace(
        restart_stream=lambda nid, s: calls.append(("restart", nid, s)) or SimpleNamespace(
            ok=True, detail="ok"))
    monkeypatch.setattr(node_client, "get_node_client", lambda nid, **k: fake)
    res = restart_binding(_cmd_restart())
    assert res.ok is True and res.binding_id == "cam55:color"
    assert ("desired", "cam55:color", True) in calls   # resume = desired_up True (durable)
    assert ("ensure", 2000) in calls                   # mountpoint re-ensured (mp 2000)
    assert ("restart", "cam55", "color") in calls      # node bounced


def test_restart_remote_failure_ok_false(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _remote())
    _isolate_remote_resume(monkeypatch, [])
    fake = SimpleNamespace(restart_stream=lambda *a: SimpleNamespace(ok=False, detail="agent unreachable"))
    monkeypatch.setattr(node_client, "get_node_client", lambda nid, **k: fake)
    res = restart_binding(_cmd_restart())
    assert res.ok is False and res.detail == "agent unreachable"


def test_restart_local_reasserts_intent(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _local())
    calls = []
    monkeypatch.setattr(sensor_lifecycle, "set_desired", lambda *a: calls.append(("set_desired", *a)))
    monkeypatch.setattr(sensor_lifecycle, "_encoder_action", lambda *a: calls.append(("encoder", *a)))
    res = restart_binding(_cmd_restart("SER:color"))
    assert res.ok is True and ("set_desired", "SER", "color", True) in calls
    assert ("encoder", "restart", "rs-stream", "color") in calls


# ── stop_binding ────────────────────────────────────────────────────────────
def test_stop_remote_marks_offline_and_desired_down(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _remote())
    fake = SimpleNamespace(stop_stream=lambda *a: SimpleNamespace(ok=True, detail="stopped"))
    monkeypatch.setattr(node_client, "get_node_client", lambda nid, **k: fake)
    writes = []
    monkeypatch.setattr(sbs, "set_status", lambda bid, st, **k: writes.append(("status", bid, st)))
    monkeypatch.setattr(sbs, "set_desired_up", lambda bid, up, **k: writes.append(("desired", bid, up)))
    # Stop must NOT touch FDIR any more — recovery is gated on desired_up instead.
    monkeypatch.setattr(sbs, "set_fdir_enabled",
                        lambda *a, **k: writes.append(("fdir", "UNEXPECTED")))
    res = stop_binding(_cmd_stop())
    assert res.ok is True
    assert ("status", "cam55:color", sbs.StreamStatus.CONFIGURED_OFFLINE.value) in writes
    assert ("desired", "cam55:color", False) in writes      # stop = desired_up False (sticks)
    assert ("fdir", "UNEXPECTED") not in writes             # FDIR untouched


def test_stop_local_running_state_is_success(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _local())
    # stop() returns RUNNING state: (False, "stopped") == success
    monkeypatch.setattr(sensor_lifecycle, "stop", lambda serial, sensor: (False, "stopped"))
    res = stop_binding(_cmd_stop("SER9:color"))
    assert res.ok is True and res.detail == "stopped"


def test_stop_local_unsupported_sensor_raises(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _local())

    def _boom(serial, sensor):
        raise sensor_lifecycle.UnsupportedSensor("ir2")
    monkeypatch.setattr(sensor_lifecycle, "stop", _boom)
    with pytest.raises(UnsupportedSensorError):
        stop_binding(_cmd_stop("SER9:ir2"))


def test_stop_local_lifecycle_error_ok_false(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _local())

    def _boom(serial, sensor):
        raise sensor_lifecycle.LifecycleError("encoder-admin stop failed")
    monkeypatch.setattr(sensor_lifecycle, "stop", _boom)
    res = stop_binding(_cmd_stop("SER9:color"))
    assert res.ok is False and "encoder-admin stop failed" in res.detail


def test_stop_not_found_raises(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: None)
    with pytest.raises(BindingNotFound):
        stop_binding(_cmd_stop("nope:color"))


# ── set_fdir ────────────────────────────────────────────────────────────────
def _cmd_fdir(bid="cam55:color", enabled=False):
    return SetFdirCommand(binding_id=bid, enabled=enabled, bind_state_path=_P, alloc_state_path=_P)


def test_set_fdir_remote_returns_updated_binding(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _remote())
    nb = SimpleNamespace(fdir=SimpleNamespace(enabled=False))
    seen = []
    monkeypatch.setattr(sbs, "set_fdir_enabled", lambda bid, en, **k: seen.append((bid, en)) or nb)
    res = set_fdir(_cmd_fdir(enabled=False))
    assert res is nb and seen == [("cam55:color", False)]


def test_set_fdir_local_rejected(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _local())
    with pytest.raises(LocalFdirNotToggleable):
        set_fdir(_cmd_fdir("SER:color"))


def test_set_fdir_not_found(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: None)
    with pytest.raises(BindingNotFound):
        set_fdir(_cmd_fdir("nope:color"))


def test_set_fdir_keyerror_race_is_not_found(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _remote())

    def _boom(*a, **k):
        raise KeyError("gone")
    monkeypatch.setattr(sbs, "set_fdir_enabled", _boom)
    with pytest.raises(BindingNotFound):
        set_fdir(_cmd_fdir())


# ── remove_binding ──────────────────────────────────────────────────────────
def _cmd_remove(bid="cam55:color"):
    return RemoveBindingCommand(binding_id=bid, bind_state_path=_P, alloc_state_path=_P)


def test_remove_remote_destroys_then_removes(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _remote())
    monkeypatch.setattr("app.services.janus_admin.destroy_mountpoint", lambda **k: {"ok": True})
    monkeypatch.setattr(sbs, "remove_binding", lambda bid, **k: True)
    res = remove_binding(_cmd_remove())
    assert res.removed is True and res.binding_id == "cam55:color"


def test_remove_janus_teardown_failure_still_removes(monkeypatch):
    """best-effort: a Janus teardown failure is swallowed; the store removal still proceeds."""
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _remote())

    def _boom(**k):
        raise RuntimeError("janus down")
    monkeypatch.setattr("app.services.janus_admin.destroy_mountpoint", _boom)
    monkeypatch.setattr(sbs, "remove_binding", lambda bid, **k: True)
    res = remove_binding(_cmd_remove())
    assert res.removed is True


def test_remove_local_rejected(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _local())
    with pytest.raises(LocalBindingNotRemovable):
        remove_binding(_cmd_remove("SER:color"))


def test_remove_not_found(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: None)
    with pytest.raises(BindingNotFound):
        remove_binding(_cmd_remove("nope:color"))


# ── ensure_janus ────────────────────────────────────────────────────────────
def _cmd_ensure(bid="cam55:color"):
    return EnsureJanusCommand(binding_id=bid, bind_state_path=_P, alloc_state_path=_P)


def _outcome(ok=True, status="created", mp=2000, iface="192.168.1.10", detail=""):
    return SimpleNamespace(ok=ok, status=SimpleNamespace(value=status),
                           mountpoint_id=mp, iface=iface, detail=detail)


def test_ensure_remote_ok_sets_waiting_and_returns_result(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _remote())
    monkeypatch.setattr("app.services.binding_provision.ensure_janus", lambda b, **k: _outcome())
    statuses = []
    monkeypatch.setattr(sbs, "set_status", lambda bid, st, **k: statuses.append((bid, st)))
    res = ensure_janus(_cmd_ensure())
    assert res.status == "created" and res.mountpoint_id == 2000 and res.iface == "192.168.1.10"
    assert statuses == [("cam55:color", sbs.StreamStatus.WAITING_FOR_RTP.value)]


def test_ensure_remote_failed_no_status_write(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _remote())
    monkeypatch.setattr("app.services.binding_provision.ensure_janus",
                        lambda b, **k: _outcome(ok=False, status="failed"))
    statuses = []
    monkeypatch.setattr(sbs, "set_status", lambda *a, **k: statuses.append(a))
    res = ensure_janus(_cmd_ensure())
    assert res.status == "failed" and statuses == []  # no WAITING_FOR_RTP on a failed provision


def test_ensure_local_rejected(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _local())
    with pytest.raises(EnsureJanusLocalRejected):
        ensure_janus(_cmd_ensure("SER:color"))


def test_ensure_not_found(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: None)
    with pytest.raises(BindingNotFound):
        ensure_janus(_cmd_ensure("nope:color"))


# ── get_tuning / set_tuning ─────────────────────────────────────────────────
def _cmd_get_tuning(bid="cam55:color"):
    return GetTuningCommand(binding_id=bid, bind_state_path=_P, alloc_state_path=_P)


def _cmd_set_tuning(bid="cam55:color", tuning=None):
    return SetTuningCommand(binding_id=bid,
                            tuning=tuning if tuning is not None else {"rotation": 90},
                            bind_state_path=_P, alloc_state_path=_P)


def test_get_tuning_remote_returns_agent_dict(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _remote())
    fake = SimpleNamespace(get_tuning=lambda s: {"width": 640, "rotation": 0})
    monkeypatch.setattr(node_client, "get_node_client", lambda nid, **k: fake)
    assert get_tuning(_cmd_get_tuning())["width"] == 640


def test_get_tuning_node_failure_raises_node_agent_error(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _remote())

    def _boom(s):
        raise RuntimeError("agent down")
    monkeypatch.setattr(node_client, "get_node_client", lambda nid, **k: SimpleNamespace(get_tuning=_boom))
    with pytest.raises(NodeAgentError):
        get_tuning(_cmd_get_tuning())


def test_get_tuning_local_rejected(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _local())
    with pytest.raises(LocalTuningRejected):
        get_tuning(_cmd_get_tuning("SER:color"))


# ── get_modes (remote supported resolution/fps for the console dropdown) ──────
def test_get_modes_remote_returns_agent_dict(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _remote())
    fake = SimpleNamespace(get_modes=lambda s: {
        "sensor": "color", "modes": [{"width": 1280, "height": 720, "fps": [30, 15, 6]}]})
    monkeypatch.setattr(node_client, "get_node_client", lambda nid, **k: fake)
    out = get_modes(_cmd_get_tuning())
    assert out["modes"][0]["width"] == 1280 and 30 in out["modes"][0]["fps"]


def test_get_modes_node_failure_raises_node_agent_error(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _remote())

    def _boom(s):
        raise RuntimeError("agent down")
    monkeypatch.setattr(node_client, "get_node_client",
                        lambda nid, **k: SimpleNamespace(get_modes=_boom))
    with pytest.raises(NodeAgentError):
        get_modes(_cmd_get_tuning())


def test_get_modes_local_rejected(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _local())
    with pytest.raises(LocalTuningRejected):
        get_modes(_cmd_get_tuning("SER:color"))


def test_set_tuning_remote_ok(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _remote())
    seen = []
    fake = SimpleNamespace(set_tuning=lambda s, body: seen.append((s, body)) or {"ok": True})
    monkeypatch.setattr(node_client, "get_node_client", lambda nid, **k: fake)
    res = set_tuning(_cmd_set_tuning(tuning={"rotation": 90, "fps": 30}))
    assert res["ok"] is True and seen[0][1] == {"rotation": 90, "fps": 30}


def test_set_tuning_bad_rotation(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _remote())
    with pytest.raises(InvalidRotation):
        set_tuning(_cmd_set_tuning(tuning={"rotation": 45}))


def test_set_tuning_empty_fields(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _remote())
    with pytest.raises(NoTuningFields):
        set_tuning(_cmd_set_tuning(tuning={}))


def test_set_tuning_node_failure_raises_node_agent_error(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _remote())

    def _boom(s, body):
        raise RuntimeError("write failed")
    monkeypatch.setattr(node_client, "get_node_client", lambda nid, **k: SimpleNamespace(set_tuning=_boom))
    with pytest.raises(NodeAgentError):
        set_tuning(_cmd_set_tuning(tuning={"fps": 30}))


def test_set_tuning_local_rejected(monkeypatch):
    monkeypatch.setattr(sbs, "get_binding", lambda *a, **k: _local())
    with pytest.raises(LocalTuningRejected):
        set_tuning(_cmd_set_tuning("SER:color"))


# ── activate_local / activate_remote (Phase 12.1) ────────────────────────────
import importlib
# the MODULE (import-as binds the package's re-exported FUNCTION; importlib gets the submodule)
_al_mod = importlib.import_module("app.application.stream_bindings.activate_local")
from app.application.stream_bindings import activate_remote
from app.application.stream_bindings.commands import ActivateLocalCommand


def test_activate_local_per_sensor_results(monkeypatch):
    from app.services import sensor_lifecycle
    monkeypatch.setattr(_al_mod, "_local_serial", lambda *a: "SER123")
    monkeypatch.setattr(sensor_lifecycle, "initialize", lambda s, sn: (True, f"{sn} up", None))
    res = _al_mod.activate_local(ActivateLocalCommand(sensors=["color", "depth"], alloc_state_path=_P))
    assert res["poll"] is None and [r["sensor"] for r in res["results"]] == ["color", "depth"]
    assert all(r["ok"] for r in res["results"])


def test_activate_local_depth_without_serial_refused(monkeypatch):
    from app.services import sensor_lifecycle
    monkeypatch.setattr(_al_mod, "_local_serial", lambda *a: None)
    monkeypatch.setattr(sensor_lifecycle, "initialize", lambda s, sn: (True, "up", None))
    res = _al_mod.activate_local(ActivateLocalCommand(sensors=["depth"], alloc_state_path=_P))
    assert res["results"][0]["ok"] is False and "serial" in res["results"][0]["detail"]


def test_activate_remote_runs_activate_then_firewall(monkeypatch):
    from app.services import node_provisioner
    calls = []
    monkeypatch.setattr(node_provisioner, "activate_streams",
                        lambda nid, t, **k: calls.append(("activate", nid, k.get("sensors"))))
    monkeypatch.setattr("app.services.firewall_sync.reconcile",
                        lambda **k: calls.append(("firewall", k.get("apply"))))
    activate_remote("cam55", transport=object(), sensors=["color"], gateway_host="192.168.1.10",
                            binder=lambda *a: None, bind_state_path=_P, alloc_state_path=_P)
    assert ("activate", "cam55", ["color"]) in calls and ("firewall", True) in calls


def test_activate_remote_firewall_failure_swallowed(monkeypatch):
    from app.services import node_provisioner
    monkeypatch.setattr(node_provisioner, "activate_streams", lambda *a, **k: None)

    def _boom(**k):
        raise RuntimeError("nft down")
    monkeypatch.setattr("app.services.firewall_sync.reconcile", _boom)
    activate_remote("cam55", transport=object(), sensors=["color"], gateway_host="x",
                            binder=lambda *a: None, bind_state_path=_P, alloc_state_path=_P)  # no raise


# ── delete_node (Phase 12.2) ─────────────────────────────────────────────────
from app.application.stream_bindings import (
    delete_node, DeleteNodeCommand, NodeNotFound, LocalNodeNotRemovable,
)


def _node(node_id="cam55", host="192.168.1.55"):
    return SimpleNamespace(node_id=node_id, host=host)


def _node_binding(bid="cam55:color", node_id="cam55", sensor="color", mp=2000):
    return SimpleNamespace(binding_id=bid, node_id=node_id, sensor=sensor,
                           mode=sbs.StreamMode.REMOTE_PRODUCER, janus=SimpleNamespace(mountpoint_id=mp))


def _cmd_delete(node_id="cam55", deprovision=False):
    return DeleteNodeCommand(node_id=node_id, deprovision=deprovision,
                             bind_state_path=_P, alloc_state_path=_P)


def test_delete_node_unknown_raises(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: None)
    with pytest.raises(NodeNotFound):
        delete_node(_cmd_delete("ghost"))


def test_delete_node_local_rejected(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: _node(node_id=sbs.LOCAL_NODE_ID))
    with pytest.raises(LocalNodeNotRemovable):
        delete_node(_cmd_delete(sbs.LOCAL_NODE_ID))


def test_delete_node_cascade(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: _node())
    monkeypatch.setattr(sbs, "list_bindings", lambda **k: {"cam55:color": _node_binding()})
    monkeypatch.setattr("app.services.janus_admin.destroy_mountpoint", lambda **k: {"ok": True})
    monkeypatch.setattr(sbs, "remove_node",
                        lambda nid, **k: {"removed": True, "binding_ids": ["cam55:color"]})
    monkeypatch.setattr("app.services.firewall_sync.reconcile", lambda **k: None)
    res = delete_node(_cmd_delete())
    assert res.removed is True and res.removed_bindings == ["cam55:color"]
    assert res.destroyed_mountpoints == [2000] and res.firewall_reconciled is True
    assert res.deprovisioned is False


def test_delete_node_deprovision_stops_streams(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: _node())
    monkeypatch.setattr(sbs, "list_bindings", lambda **k: {"cam55:color": _node_binding()})
    stopped = []
    monkeypatch.setattr(node_client, "get_node_client",
                        lambda nid, **k: SimpleNamespace(stop_stream=lambda n, s: stopped.append((n, s))))
    monkeypatch.setattr("app.services.janus_admin.destroy_mountpoint", lambda **k: None)
    monkeypatch.setattr(sbs, "remove_node", lambda nid, **k: {"removed": True, "binding_ids": []})
    monkeypatch.setattr("app.services.firewall_sync.reconcile", lambda **k: None)
    res = delete_node(_cmd_delete(deprovision=True))
    assert stopped == [("cam55", "color")] and res.deprovisioned is True


def test_delete_node_best_effort_failures_swallowed(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: _node())
    monkeypatch.setattr(sbs, "list_bindings", lambda **k: {"cam55:color": _node_binding()})

    def _boom_destroy(**k):
        raise RuntimeError("janus down")
    monkeypatch.setattr("app.services.janus_admin.destroy_mountpoint", _boom_destroy)
    monkeypatch.setattr(sbs, "remove_node",
                        lambda nid, **k: {"removed": True, "binding_ids": ["cam55:color"]})

    def _boom_fw(**k):
        raise RuntimeError("nft down")
    monkeypatch.setattr("app.services.firewall_sync.reconcile", _boom_fw)
    res = delete_node(_cmd_delete())
    # destroy + firewall both failed (swallowed); the store removal still succeeded
    assert res.removed is True and res.destroyed_mountpoints == [] and res.firewall_reconciled is False


# ── 12.3A read/list/view use-cases ───────────────────────────────────────────

_P123 = Path("/tmp/_phase123a")


def _mp_binding(mp=2000):
    return SimpleNamespace(janus=SimpleNamespace(mountpoint_id=mp))


# list_nodes ──
def test_list_nodes_threads_path_and_returns_store_map(monkeypatch):
    seen = {}
    def fake(*, state_path):
        seen["path"] = state_path
        return {"cam10": object(), "cam55": object()}
    monkeypatch.setattr(sbs, "list_nodes", fake)
    res = list_nodes(ListNodesCommand(bind_state_path=_P123))
    assert seen["path"] == _P123 and set(res) == {"cam10", "cam55"}


# list_bindings ──
def test_list_bindings_without_rtp_age_skips_probe(monkeypatch):
    monkeypatch.setattr(sbs, "list_bindings", lambda **k: {"b1": _mp_binding(2000)})
    calls = []
    pairs = list_bindings(
        ListBindingsCommand(include_rtp_age=False, bind_state_path=_P123, alloc_state_path=_P123),
        rtp_age_fn=lambda mp: calls.append(mp) or 42)
    assert calls == []                       # probe NOT called when not requested
    assert [age for _, age in pairs] == [None]


def test_list_bindings_with_rtp_age_probes_each_mountpoint(monkeypatch):
    monkeypatch.setattr(sbs, "list_bindings",
                        lambda **k: {"b1": _mp_binding(2000), "b2": _mp_binding(2001)})
    seen = []
    pairs = list_bindings(
        ListBindingsCommand(include_rtp_age=True, bind_state_path=_P123, alloc_state_path=_P123),
        rtp_age_fn=lambda mp: seen.append(mp) or (mp - 2000))
    assert seen == [2000, 2001]              # injected probe hit per mountpoint, in order
    assert [(b.janus.mountpoint_id, age) for b, age in pairs] == [(2000, 0), (2001, 1)]


# fleet_plan ──
def test_fleet_plan_shapes_plan_dict(monkeypatch):
    monkeypatch.setattr(fleet, "load_manifest", lambda: "MANIFEST")
    seen = {}
    def fake_plan(manifest, *, state_path, alloc_state_path):
        seen.update(manifest=manifest, sp=state_path, ap=alloc_state_path)
        return SimpleNamespace(in_sync=True, extra_nodes=["x"], nodes=[])
    monkeypatch.setattr(fleet, "plan", fake_plan)
    res = fleet_plan(FleetPlanCommand(bind_state_path=_P123, alloc_state_path=_P123))
    assert res == {"in_sync": True, "extra_nodes": ["x"], "nodes": []}
    assert seen == {"manifest": "MANIFEST", "sp": _P123, "ap": _P123}


def test_fleet_plan_bad_manifest_raises_manifest_invalid(monkeypatch):
    def boom():
        raise fleet.ManifestError("bad yaml at line 3")
    monkeypatch.setattr(fleet, "load_manifest", boom)
    with pytest.raises(ManifestInvalid) as ei:
        fleet_plan(FleetPlanCommand(bind_state_path=_P123, alloc_state_path=_P123))
    assert str(ei.value) == "bad yaml at line 3"      # message carried verbatim for the route's 422


# reconcile_drift ──
def test_reconcile_drift_gathers_desired_actual_and_audits(monkeypatch):
    monkeypatch.setattr(sbs, "list_bindings", lambda **k: {"b1": _mp_binding(2000)})
    # non-dict + dict-without-id are filtered; only int-able ids survive
    monkeypatch.setattr(janus_admin, "list_mountpoints",
                        lambda: ["junk", {"id": 2000}, {"nope": 1}, {"id": 2099}])
    monkeypatch.setattr(sbs, "list_nodes", lambda **k: {
        "n1": SimpleNamespace(maintenance=True), "n2": SimpleNamespace(maintenance=False)})
    captured = {}
    def fake_compute(bindings, live, *, rtp_age_fn, maintenance_node_ids):
        captured.update(bindings=bindings, live=live, rtp=rtp_age_fn, maint=maintenance_node_ids)
        return {"drift": False, "counts": {"in_sync": 1}, "bindings": []}
    monkeypatch.setattr(drift_svc, "compute_drift", fake_compute)
    age_fn = lambda mp: 5
    res = reconcile_drift(
        ReconcileDriftCommand(bind_state_path=_P123, alloc_state_path=_P123), rtp_age_fn=age_fn)
    assert res["drift"] is False
    assert captured["live"] == [2000, 2099]            # filtered + int-coerced
    assert captured["maint"] == frozenset({"n1"})      # only maintenance=True nodes
    assert captured["rtp"] is age_fn                   # injected probe threaded through


def test_reconcile_drift_janus_outage_raises_truncated(monkeypatch):
    monkeypatch.setattr(sbs, "list_bindings", lambda **k: {})
    msg = "connection refused to janus admin api " + "x" * 200
    def boom():
        raise RuntimeError(msg)
    monkeypatch.setattr(janus_admin, "list_mountpoints", boom)
    with pytest.raises(JanusUnreachable) as ei:
        reconcile_drift(
            ReconcileDriftCommand(bind_state_path=_P123, alloc_state_path=_P123), rtp_age_fn=lambda mp: 1)
    assert ei.value.reason == msg[:120] and len(ei.value.reason) == 120


def test_reconcile_drift_store_corruption_propagates(monkeypatch):
    # [R5] a corrupt desired store must NOT be swallowed by the janus try/except — it propagates
    # so the app maps it to 503 topology_store_corrupt (never a fabricated empty report).
    def corrupt(**k):
        raise sbs.StoreCorruptionError("bad store")
    monkeypatch.setattr(sbs, "list_bindings", corrupt)
    with pytest.raises(sbs.StoreCorruptionError):
        reconcile_drift(
            ReconcileDriftCommand(bind_state_path=_P123, alloc_state_path=_P123), rtp_age_fn=lambda mp: 1)


# ── 12.3B node check / maintenance / host-key use-cases ──────────────────────

def _hknode(node_id="cam55", host="192.168.1.55", host_key=None):
    return SimpleNamespace(node_id=node_id, host=host, host_key=host_key)


_CAP = lambda host: "h ssh-ed25519 KEYX"          # injected capture_host_key
_FP = lambda line, **k: "SHA256:FP"               # injected fingerprint_fn


# check_node ──
def test_check_node_unknown_raises(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: None)
    with pytest.raises(NodeNotFound):
        check_node(CheckNodeCommand(node_id="nope", bind_state_path=_P123))


def test_check_node_local_is_trivially_reachable(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: _hknode(node_id=sbs.LOCAL_NODE_ID))
    res = check_node(CheckNodeCommand(node_id=sbs.LOCAL_NODE_ID, bind_state_path=_P123))
    assert res == {"node_id": sbs.LOCAL_NODE_ID, "reachable": True, "reason": "local", "next_step": None}


def test_check_node_remote_probes_and_records(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: _hknode())
    monkeypatch.setattr(node_client, "probe_agent", lambda host: {
        "reachable": True, "reachability": "reachable", "reason": "ok", "next_step": None})
    rec = []
    monkeypatch.setattr(sbs, "touch_checked", lambda nid, reach, **k: rec.append((nid, reach)))
    res = check_node(CheckNodeCommand(node_id="cam55", bind_state_path=_P123))
    assert res["reachable"] is True and res["reason"] == "ok"
    assert rec == [("cam55", "reachable")]


def test_check_node_touch_checked_keyerror_swallowed(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: _hknode())
    monkeypatch.setattr(node_client, "probe_agent", lambda host: {
        "reachable": False, "reachability": "unreachable", "reason": "x", "next_step": "y"})
    def boom(*a, **k):
        raise KeyError("race: node deleted")
    monkeypatch.setattr(sbs, "touch_checked", boom)
    res = check_node(CheckNodeCommand(node_id="cam55", bind_state_path=_P123))   # must not raise
    assert res["reachable"] is False and res["next_step"] == "y"


# set_maintenance ──
def test_set_maintenance_unknown_raises(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: None)
    with pytest.raises(NodeNotFound):
        set_maintenance(SetMaintenanceCommand(node_id="nope", enabled=True, bind_state_path=_P123))


def test_set_maintenance_local_rejected(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: _hknode(node_id=sbs.LOCAL_NODE_ID))
    with pytest.raises(MaintenanceLocalRejected):
        set_maintenance(SetMaintenanceCommand(node_id=sbs.LOCAL_NODE_ID, enabled=True, bind_state_path=_P123))


def test_set_maintenance_happy_returns_node(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: _hknode())
    updated = _hknode()
    seen = []
    monkeypatch.setattr(sbs, "set_maintenance", lambda nid, en, **k: seen.append((nid, en)) or updated)
    res = set_maintenance(SetMaintenanceCommand(node_id="cam55", enabled=True, bind_state_path=_P123))
    assert res is updated and seen == [("cam55", True)]


def test_set_maintenance_race_keyerror_is_not_found(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: _hknode())
    def boom(*a, **k):
        raise KeyError("gone")
    monkeypatch.setattr(sbs, "set_maintenance", boom)
    with pytest.raises(NodeNotFound):
        set_maintenance(SetMaintenanceCommand(node_id="cam55", enabled=False, bind_state_path=_P123))


# get_host_key ──
def test_get_host_key_unknown_raises(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: None)
    with pytest.raises(NodeNotFound):
        get_host_key(GetHostKeyCommand(node_id="nope", bind_state_path=_P123),
                     capture_host_key=_CAP, fingerprint_fn=_FP)


def test_get_host_key_local_rejected(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: _hknode(node_id=sbs.LOCAL_NODE_ID))
    with pytest.raises(LocalNodeNoHostKey):
        get_host_key(GetHostKeyCommand(node_id=sbs.LOCAL_NODE_ID, bind_state_path=_P123),
                     capture_host_key=_CAP, fingerprint_fn=_FP)


def test_get_host_key_unreachable_raises(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: _hknode())
    with pytest.raises(HostKeyUnreachable):
        get_host_key(GetHostKeyCommand(node_id="cam55", bind_state_path=_P123),
                     capture_host_key=lambda host: "", fingerprint_fn=_FP)


def test_get_host_key_happy_uses_injected_fns(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: _hknode(host_key="h ssh-ed25519 PINNED"))
    res = get_host_key(GetHostKeyCommand(node_id="cam55", bind_state_path=_P123),
                       capture_host_key=_CAP, fingerprint_fn=_FP)
    assert res["fingerprint"] == "SHA256:FP" and res["pinned"] is True and res["host"] == "192.168.1.55"


# confirm_host_key ──
def test_confirm_host_key_mismatch_pins_nothing(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: _hknode())
    pins = []
    monkeypatch.setattr(sbs, "set_host_key", lambda *a, **k: pins.append(a))
    with pytest.raises(HostKeyFingerprintMismatch):
        confirm_host_key(
            ConfirmHostKeyCommand(node_id="cam55", expected_fingerprint="SHA256:WRONG",
                                  force=False, bind_state_path=_P123),
            capture_host_key=_CAP, fingerprint_fn=_FP)
    assert pins == []


def test_confirm_host_key_match_pins(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: _hknode(host_key=None))
    pins = []
    monkeypatch.setattr(sbs, "set_host_key", lambda nid, hk, **k: pins.append((nid, hk)))
    res = confirm_host_key(
        ConfirmHostKeyCommand(node_id="cam55", expected_fingerprint="SHA256:FP",
                              force=False, bind_state_path=_P123),
        capture_host_key=_CAP, fingerprint_fn=_FP)
    assert res["pinned"] is True and pins == [("cam55", "h ssh-ed25519 KEYX")]


def test_confirm_host_key_refuses_silent_repin(monkeypatch):
    # existing DIFFERENT pin + matching live fingerprint + no force -> reject, nothing re-pinned
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: _hknode(host_key="h ssh-ed25519 OLDKEY"))
    pins = []
    monkeypatch.setattr(sbs, "set_host_key", lambda *a, **k: pins.append(a))
    with pytest.raises(HostKeyPinReplaceRejected):
        confirm_host_key(
            ConfirmHostKeyCommand(node_id="cam55", expected_fingerprint="SHA256:FP",
                                  force=False, bind_state_path=_P123),
            capture_host_key=_CAP, fingerprint_fn=_FP)
    assert pins == []


def test_confirm_host_key_force_repins(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: _hknode(host_key="h ssh-ed25519 OLDKEY"))
    pins = []
    monkeypatch.setattr(sbs, "set_host_key", lambda nid, hk, **k: pins.append((nid, hk)))
    res = confirm_host_key(
        ConfirmHostKeyCommand(node_id="cam55", expected_fingerprint="SHA256:FP",
                              force=True, bind_state_path=_P123),
        capture_host_key=_CAP, fingerprint_fn=_FP)
    assert res["pinned"] is True and pins == [("cam55", "h ssh-ed25519 KEYX")]


# ── 12.3C create binding / fleet reconcile / firewall reconcile use-cases ─────

def _create_cmd(node_id="cam55", sensor="color", mp=None, port=None, iface="192.168.1.10"):
    return CreateBindingCommand(
        node_id=node_id, sensor=sensor, mountpoint_id=mp, rtp_port=port,
        payload_type=96, codec="H264", rtp_iface=iface,
        bind_state_path=_P123, alloc_state_path=_P123)


# firewall_reconcile ──
def test_firewall_reconcile_summarizes_plan(monkeypatch):
    plan = SimpleNamespace(
        add=[SimpleNamespace(comment="camnode:n1:color:5100"), SimpleNamespace(comment="backstop")],
        remove_comments=["camnode:stale:depth:9999"])
    seen = {}
    def fake(*, state_path, alloc_state_path, apply):
        seen.update(sp=state_path, ap=alloc_state_path, apply=apply)
        return plan
    monkeypatch.setattr(firewall_sync, "reconcile", fake)
    res = firewall_reconcile(FirewallReconcileCommand(apply=True, bind_state_path=_P123, alloc_state_path=_P123))
    assert res == {"apply": True, "added": ["camnode:n1:color:5100", "backstop"],
                   "removed": ["camnode:stale:depth:9999"]}
    assert seen == {"sp": _P123, "ap": _P123, "apply": True}


# fleet_reconcile ──
def test_fleet_reconcile_registers_and_returns_plan(monkeypatch):
    monkeypatch.setattr(fleet, "load_manifest", lambda: ["M"])
    monkeypatch.setattr(fleet, "reconcile_gateway", lambda m, **k: ["node-new"])
    monkeypatch.setattr(fleet, "plan", lambda m, **k: SimpleNamespace(in_sync=False, extra_nodes=[], nodes=[]))
    res = fleet_reconcile(FleetReconcileCommand(bind_state_path=_P123, alloc_state_path=_P123))
    assert res["registered"] == ["node-new"] and res["in_sync"] is False


def test_fleet_reconcile_bad_manifest_raises(monkeypatch):
    def boom():
        raise fleet.ManifestError("bad")
    monkeypatch.setattr(fleet, "load_manifest", boom)
    with pytest.raises(ManifestInvalid):
        fleet_reconcile(FleetReconcileCommand(bind_state_path=_P123, alloc_state_path=_P123))


# create_binding ──
def test_create_binding_happy_allocates_and_persists(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: _hknode())
    monkeypatch.setattr(sbs, "allocate_mountpoint", lambda *a, **k: 2000)
    monkeypatch.setattr(sbs, "allocate_port", lambda *a, **k: 5100)
    monkeypatch.setattr(sbs, "remote_binding_id", lambda node, sensor: f"{node.node_id}:{sensor}")
    saved = []
    monkeypatch.setattr(sbs, "upsert_binding", lambda b, **k: saved.append(b))
    res = create_binding(_create_cmd())
    assert res.binding_id == "cam55:color" and res.node_id == "cam55"
    assert res.janus.mountpoint_id == 2000 and res.transport.rtp_port == 5100
    assert res.mode == sbs.StreamMode.REMOTE_PRODUCER
    assert saved == [res]


def test_create_binding_respects_caller_supplied_mp_and_port(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: _hknode())
    def _should_not_alloc(*a, **k):
        raise AssertionError("must not auto-allocate when caller supplies values")
    monkeypatch.setattr(sbs, "allocate_mountpoint", _should_not_alloc)
    monkeypatch.setattr(sbs, "allocate_port", _should_not_alloc)
    monkeypatch.setattr(sbs, "remote_binding_id", lambda node, sensor: "cam55:color")
    monkeypatch.setattr(sbs, "upsert_binding", lambda b, **k: None)
    res = create_binding(_create_cmd(mp=2222, port=5150))
    assert res.janus.mountpoint_id == 2222 and res.transport.rtp_port == 5150


def test_create_binding_local_rejected():
    with pytest.raises(LocalBindingNotCreatable):
        create_binding(_create_cmd(node_id=sbs.LOCAL_NODE_ID))


def test_create_binding_unknown_node_raises(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: None)
    with pytest.raises(BindingNodeNotFound):
        create_binding(_create_cmd(node_id="ghost"))


def test_create_binding_allocation_error_is_conflict(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: _hknode())
    def boom(*a, **k):
        raise mountpoint_allocator.AllocationError("pool exhausted")
    monkeypatch.setattr(sbs, "allocate_mountpoint", boom)
    with pytest.raises(AllocationConflict) as ei:
        create_binding(_create_cmd())
    assert str(ei.value) == "pool exhausted"


def test_create_binding_validation_error_is_binding_invalid(monkeypatch):
    monkeypatch.setattr(sbs, "get_node", lambda *a, **k: _hknode())
    monkeypatch.setattr(sbs, "allocate_mountpoint", lambda *a, **k: 2000)
    monkeypatch.setattr(sbs, "allocate_port", lambda *a, **k: 5100)
    monkeypatch.setattr(sbs, "remote_binding_id", lambda node, sensor: "cam55:color")
    def boom(*a, **k):
        raise sbs.BindingValidationError("rtp_iface must be a LAN address")
    monkeypatch.setattr(sbs, "upsert_binding", boom)
    with pytest.raises(BindingInvalid) as ei:
        create_binding(_create_cmd())
    assert str(ei.value) == "rtp_iface must be a LAN address"


# ── 12.3D node register / add-by-host use-cases ──────────────────────────────

def _add_cmd(host="192.168.1.60", display_name="front", gw="192.168.1.10"):
    return AddNodeCommand(host=host, display_name=display_name, gateway_lan_ip=gw, bind_state_path=_P123)


# register_node ──
def test_register_node_happy_returns_node(monkeypatch):
    made = _hknode()
    seen = {}
    monkeypatch.setattr(sbs, "upsert_node",
                        lambda nid, *, host, role, state_path: seen.update(nid=nid, host=host, role=role) or made)
    res = register_node(RegisterNodeCommand(node_id="cam55", host="192.168.1.55",
                                            role="remote_producer", bind_state_path=_P123))
    assert res is made and seen == {"nid": "cam55", "host": "192.168.1.55", "role": "remote_producer"}


def test_register_node_validation_error_is_400(monkeypatch):
    def boom(*a, **k):
        raise sbs.BindingValidationError("bad host")
    monkeypatch.setattr(sbs, "upsert_node", boom)
    with pytest.raises(NodeRegistrationInvalid) as ei:
        register_node(RegisterNodeCommand(node_id="cam55", host="x", role="r", bind_state_path=_P123))
    assert str(ei.value) == "bad host"


# add_node ──
def test_add_node_happy_mints_and_returns(monkeypatch):
    made = SimpleNamespace(node_id="node-abc")
    seen = {}
    monkeypatch.setattr(sbs, "add_node_by_host",
                        lambda host, *, display_name, state_path: seen.update(host=host, dn=display_name) or made)
    res = add_node(_add_cmd())
    assert res is made and seen == {"host": "192.168.1.60", "dn": "front"}


@pytest.mark.parametrize("host", ["192.168.1.10", " 192.168.1.10 ", "127.0.0.1", "localhost", "::1", "0.0.0.0"])
def test_add_node_rejects_local_gateway(host):
    with pytest.raises(AddNodeIsLocalGateway):
        add_node(_add_cmd(host=host))            # gateway_lan_ip default 192.168.1.10


def test_add_node_local_reject_uses_injected_gateway_ip():
    # a host equal to the INJECTED gateway ip is rejected (proves the ip is threaded, not hardcoded)
    with pytest.raises(AddNodeIsLocalGateway):
        add_node(_add_cmd(host="10.0.0.5", gw="10.0.0.5"))


def test_add_node_validation_error_is_400(monkeypatch):
    def boom(*a, **k):
        raise sbs.BindingValidationError("not an ip")
    monkeypatch.setattr(sbs, "add_node_by_host", boom)
    with pytest.raises(NodeRegistrationInvalid) as ei:
        add_node(_add_cmd(host="999.1.1.1"))
    assert str(ei.value) == "not an ip"

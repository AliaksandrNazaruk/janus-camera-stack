"""G2 acceptance — binding-driven Janus provisioning + RTP target contract.

Covers (GATEWAY_REMOTE_RTP_MODE.md §2/§3):
  • ensure_janus threads binding.janus.rtp_iface into create_mountpoint
  • local binding → iface 127.0.0.1 (backward-compat); remote → LAN iface
  • idempotency state contract: CREATED / EXISTS / CONFLICT / FAILED
  • admin secret never required to be echoed (create kwargs only)
  • _write_contract_env emits RTP_TARGET_HOST (default loopback)
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

from app.services import binding_provision as bp
from app.services import janus_admin
from app.services import stream_binding_store as sbs
from app.services.binding_provision import ProvisionStatus
from app.services.stream_binding_store import (
    StreamBinding, StreamJanusConfig, StreamMode, StreamTransport,
)


def _binding(*, mp=2000, port=5100, iface="192.168.1.10", codec="h264", pt=96,
             mode=StreamMode.REMOTE_PRODUCER, node="cam55"):
    return StreamBinding(
        binding_id=f"{node}:color", node_id=node, sensor="color", mode=mode,
        transport=StreamTransport(rtp_port=port, payload_type=pt, codec=codec),
        janus=StreamJanusConfig(mountpoint_id=mp, rtp_iface=iface))


@pytest.fixture
def capture_create(monkeypatch):
    """Replace janus_admin.create_mountpoint with a capturing stub."""
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return {"streaming": "created"}

    monkeypatch.setattr(janus_admin, "create_mountpoint", fake_create)
    return captured


# ── iface threading ───────────────────────────────────────────────────

def test_ensure_janus_threads_remote_iface(capture_create):
    out = bp.ensure_janus(_binding(iface="192.168.1.10"), mp_secret="s3cr3t")
    assert out.status is ProvisionStatus.CREATED
    assert capture_create["iface"] == "192.168.1.10"
    assert capture_create["mp_id"] == 2000
    assert capture_create["rtp_port"] == 5100
    assert capture_create["codec"] == "h264"
    assert capture_create["payload_type"] == 96


def test_ensure_janus_local_binding_uses_loopback(capture_create):
    local = _binding(mp=1305, port=5004, iface="127.0.0.1",
                     mode=StreamMode.LOCAL_PRODUCER, node="cam10")
    out = bp.ensure_janus(local, mp_secret="s")
    assert out.status is ProvisionStatus.CREATED
    assert capture_create["iface"] == "127.0.0.1"      # backward-compat


def test_ensure_janus_passes_secret_only_as_kwarg(capture_create):
    bp.ensure_janus(_binding(), mp_secret="TOP-SECRET")
    # secret reaches create_mountpoint as mp_secret, never embedded in description
    assert capture_create["mp_secret"] == "TOP-SECRET"
    assert "TOP-SECRET" not in capture_create["description"]


# ── idempotency state contract ────────────────────────────────────────

def test_ensure_janus_created(capture_create):
    assert bp.ensure_janus(_binding(), mp_secret="s").status is ProvisionStatus.CREATED


def test_ensure_janus_exists_same_port(monkeypatch):
    def boom(**kw):
        raise janus_admin.JanusAdminError("streaming plugin error 456: already exists")
    monkeypatch.setattr(janus_admin, "create_mountpoint", boom)
    monkeypatch.setattr(janus_admin, "list_mountpoints",
                        lambda: [{"id": 2000, "video_port": 5100}])
    out = bp.ensure_janus(_binding(mp=2000, port=5100), mp_secret="s")
    assert out.status is ProvisionStatus.EXISTS
    assert out.ok


def test_ensure_janus_conflict_diff_port(monkeypatch):
    def boom(**kw):
        raise janus_admin.JanusAdminError("error 456: already exists")
    monkeypatch.setattr(janus_admin, "create_mountpoint", boom)
    monkeypatch.setattr(janus_admin, "list_mountpoints",
                        lambda: [{"id": 2000, "video_port": 5998}])  # different port
    out = bp.ensure_janus(_binding(mp=2000, port=5100), mp_secret="s")
    assert out.status is ProvisionStatus.CONFLICT
    assert not out.ok
    assert "5998" in out.detail


def test_ensure_janus_exists_when_introspection_unavailable(monkeypatch):
    def boom(**kw):
        raise janus_admin.JanusAdminError("already exists")
    def list_boom():
        raise janus_admin.JanusAdminError("list failed")
    monkeypatch.setattr(janus_admin, "create_mountpoint", boom)
    monkeypatch.setattr(janus_admin, "list_mountpoints", list_boom)
    # can't introspect → benign EXISTS, not CONFLICT
    assert bp.ensure_janus(_binding(), mp_secret="s").status is ProvisionStatus.EXISTS


def test_ensure_janus_failed_on_other_error(monkeypatch):
    def boom(**kw):
        raise janus_admin.JanusAdminError("admin_key invalid")
    monkeypatch.setattr(janus_admin, "create_mountpoint", boom)
    out = bp.ensure_janus(_binding(), mp_secret="s")
    assert out.status is ProvisionStatus.FAILED
    assert not out.ok


def test_is_already_exists_detection():
    assert bp.is_already_exists(janus_admin.JanusAdminError("Mountpoint already exists"))
    assert bp.is_already_exists(janus_admin.JanusAdminError("streaming plugin error 456: exists"))
    assert not bp.is_already_exists(janus_admin.JanusAdminError("admin_key invalid"))


# ── contract.env RTP_TARGET_HOST ──────────────────────────────────────

def test_contract_env_default_loopback(monkeypatch, tmp_path):
    # _contract_path + _write_contract_env live in the contract_env submodule after the Phase 4
    # split — patch at the source (the facade re-exports the same objects).
    from app.services.sensor_lifecycle import contract_env as sl
    monkeypatch.setattr(sl, "_contract_path", lambda s: tmp_path / f"rs-{s}.contract.env")
    sl._write_contract_env("color", 5004)
    body = (tmp_path / "rs-color.contract.env").read_text()
    assert 'PORT="5004"' in body
    assert 'RTP_TARGET_HOST="127.0.0.1"' in body     # backward-compat default


def test_contract_env_explicit_remote_host(monkeypatch, tmp_path):
    # _contract_path + _write_contract_env live in the contract_env submodule after the Phase 4
    # split — patch at the source (the facade re-exports the same objects).
    from app.services.sensor_lifecycle import contract_env as sl
    monkeypatch.setattr(sl, "_contract_path", lambda s: tmp_path / f"rs-{s}.contract.env")
    sl._write_contract_env("color", 5104, "192.168.1.10")
    body = (tmp_path / "rs-color.contract.env").read_text()
    assert 'PORT="5104"' in body
    assert 'RTP_TARGET_HOST="192.168.1.10"' in body


# ── reconcile_janus: gateway-side mountpoint recovery (UNIFIED_FDIR §4.7) ──

def _store_with_remote(tmp_path, *, sensors=("color",), mp_base=2000, host="192.168.1.55"):
    sp = tmp_path / "sb.json"
    ap = tmp_path / "al.json"
    n = sbs.add_node_by_host(host, state_path=sp)
    for i, s in enumerate(sensors):
        b = StreamBinding(
            binding_id=f"{n.node_id}:{s}", node_id=n.node_id, sensor=s,
            mode=StreamMode.REMOTE_PRODUCER,
            transport=StreamTransport(rtp_port=5100 + 2 * i, payload_type=96, codec="h264"),
            janus=StreamJanusConfig(mountpoint_id=mp_base + i, rtp_iface="192.168.1.10"))
        sbs.upsert_binding(b, state_path=sp, alloc_state_path=ap)
    return sp, ap, n


def test_reconcile_janus_ensures_only_remote(tmp_path, monkeypatch):
    sp, ap, n = _store_with_remote(tmp_path, sensors=("color", "depth", "ir1"))
    created_ids = []
    monkeypatch.setattr(janus_admin, "create_mountpoint",
                        lambda **kw: created_ids.append(kw["mp_id"]) or {"streaming": "created"})
    summary = bp.reconcile_janus(mp_secret="s", state_path=sp, alloc_state_path=ap)
    assert summary.created == 3 and summary.failed == 0 and summary.ok
    # exactly the three remote mountpoints — no local projection is ever ensured
    assert sorted(created_ids) == [2000, 2001, 2002]


def test_reconcile_janus_idempotent_exists(tmp_path, monkeypatch):
    sp, ap, n = _store_with_remote(tmp_path, sensors=("color",))

    def boom(**kw):
        raise janus_admin.JanusAdminError("streaming plugin error 456: already exists")
    monkeypatch.setattr(janus_admin, "create_mountpoint", boom)
    monkeypatch.setattr(janus_admin, "list_mountpoints",
                        lambda: [{"id": 2000, "video_port": 5100}])
    summary = bp.reconcile_janus(mp_secret="s", state_path=sp, alloc_state_path=ap)
    assert summary.existing == 1 and summary.created == 0 and summary.ok


def test_reconcile_janus_isolates_per_binding_failure(tmp_path, monkeypatch):
    sp, ap, n = _store_with_remote(tmp_path, sensors=("color", "depth"))
    seen = []

    def flaky(**kw):
        seen.append(kw["mp_id"])
        if kw["mp_id"] == 2000:
            raise janus_admin.JanusAdminError("admin_key invalid")   # hard, non-exists error
        return {"streaming": "created"}
    monkeypatch.setattr(janus_admin, "create_mountpoint", flaky)
    summary = bp.reconcile_janus(mp_secret="s", state_path=sp, alloc_state_path=ap)
    assert summary.failed == 1 and summary.created == 1 and not summary.ok
    assert sorted(seen) == [2000, 2001]     # sweep continued past the failed binding


def test_reconcile_janus_refuses_local_range_mountpoint(tmp_path, monkeypatch):
    import json
    sp = tmp_path / "sb.json"
    ap = tmp_path / "al.json"
    sbs.upsert_node("cam55", host="192.168.1.55", role="remote_producer", state_path=sp)
    state = json.loads(sp.read_text())
    # hand-edited file (bypasses _validate_remote) squatting a cam10-owned id that is
    # NOT janus_mount_id — the OLD ==janus_mount_id guard would have missed 1306.
    state["bindings"]["cam55:color"] = {
        "binding_id": "cam55:color", "node_id": "cam55", "sensor": "color",
        "mode": "remote_producer",
        "transport": {"rtp_port": 5100, "payload_type": 96, "codec": "h264", "srtp": None},
        "janus": {"mountpoint_id": 1306, "rtp_iface": "192.168.1.10"},
        "fdir": {"enabled": True, "policy": "stream_default"}, "status": "configured_offline"}
    sp.write_text(json.dumps(state))

    called = []
    monkeypatch.setattr(janus_admin, "create_mountpoint",
                        lambda **kw: called.append(kw["mp_id"]) or {})
    summary = bp.reconcile_janus(mp_secret="s", state_path=sp, alloc_state_path=ap)
    assert summary.skipped == 1 and summary.created == 0
    assert called == []     # fail-closed: a local-range id is never ensured (§4.6 widened)


def test_reconcile_janus_skips_operator_stopped(tmp_path, monkeypatch):
    """Back-compat: a LEGACY row (no desired_up) with fdir.enabled=False derives desired_up=False
    and is skipped — Stop is honored unchanged for pre-desired_up files. (New Stop = desired_up=False,
    covered separately.)"""
    import json
    sp, ap, n = _store_with_remote(tmp_path, sensors=("color",))
    state = json.loads(sp.read_text())
    state["bindings"][f"{n.node_id}:color"]["fdir"]["enabled"] = False
    state["bindings"][f"{n.node_id}:color"].pop("desired_up", None)   # legacy row → derive from fdir
    sp.write_text(json.dumps(state))

    called = []
    monkeypatch.setattr(janus_admin, "create_mountpoint",
                        lambda **kw: called.append(kw["mp_id"]) or {"streaming": "created"})
    summary = bp.reconcile_janus(mp_secret="s", state_path=sp, alloc_state_path=ap)
    assert summary.skipped == 1 and summary.created == 0 and summary.existing == 0
    assert called == []     # operator Stop is honored — no listener resurrected


def test_reconcile_janus_ensures_desired_up_even_with_fdir_off(tmp_path, monkeypatch):
    """The durability fix: a desired-UP binding's mountpoint is maintained even with FDIR off —
    Start/Stop (desired_up) is now SEPARATE from FDIR (recovery). So a desired-up stream survives a
    gateway restart instead of dropping because FDIR happened to be disabled."""
    import json
    sp, ap, n = _store_with_remote(tmp_path, sensors=("color",))
    state = json.loads(sp.read_text())
    state["bindings"][f"{n.node_id}:color"]["fdir"]["enabled"] = False   # FDIR off
    state["bindings"][f"{n.node_id}:color"]["desired_up"] = True         # but desired UP
    sp.write_text(json.dumps(state))

    called = []
    monkeypatch.setattr(janus_admin, "create_mountpoint",
                        lambda **kw: called.append(kw["mp_id"]) or {"streaming": "created"})
    summary = bp.reconcile_janus(mp_secret="s", state_path=sp, alloc_state_path=ap)
    assert summary.created == 1 and summary.skipped == 0    # ensured despite FDIR off
    assert len(called) == 1


def test_reconcile_janus_skips_desired_down_even_with_fdir_on(tmp_path, monkeypatch):
    """Symmetric: desired_down (Stopped) is honored even if FDIR is on — desired_up is the gate."""
    import json
    sp, ap, n = _store_with_remote(tmp_path, sensors=("color",))
    state = json.loads(sp.read_text())
    state["bindings"][f"{n.node_id}:color"]["fdir"]["enabled"] = True    # FDIR on
    state["bindings"][f"{n.node_id}:color"]["desired_up"] = False        # but Stopped
    sp.write_text(json.dumps(state))

    called = []
    monkeypatch.setattr(janus_admin, "create_mountpoint",
                        lambda **kw: called.append(kw["mp_id"]) or {"streaming": "created"})
    summary = bp.reconcile_janus(mp_secret="s", state_path=sp, alloc_state_path=ap)
    assert summary.skipped == 1 and summary.created == 0
    assert called == []

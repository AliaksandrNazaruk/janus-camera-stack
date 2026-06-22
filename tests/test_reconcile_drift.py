"""Read-only desired/actual drift diagnostic (ADR DESIRED_ACTUAL_RECONCILE_MODEL).

Covers the invariants the diagnostic must honor:
  R2  operator-stopped (fdir=false / configured_offline) is NOT drift
  R4  the route is read-only (no ensure-janus / create / firewall / systemctl)
  R5  a corrupt desired store raises (-> app 503 topology_store_corrupt), not an empty report
  R7  clear per-class counts
  R9  pure / deterministic
plus the concrete cases: active mp 2000/2001 missing == drift; ir1 mp 2002 absent != drift.
"""
from __future__ import annotations

import inspect
import os
import sys

import pytest

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.services import reconcile_drift as drift
from app.services import stream_binding_store as sbs

ONLINE = sbs.StreamStatus.ONLINE.value
WAITING = sbs.StreamStatus.WAITING_FOR_RTP.value
OFFLINE = sbs.StreamStatus.CONFIGURED_OFFLINE.value


def _b(sensor, mp, *, status=ONLINE, enabled=True,
       mode=sbs.StreamMode.REMOTE_PRODUCER, node="node-x"):
    return sbs.StreamBinding(
        binding_id=f"{node}:{sensor}", node_id=node, sensor=sensor, mode=mode,
        transport=sbs.StreamTransport(rtp_port=5100),
        janus=sbs.StreamJanusConfig(mountpoint_id=mp, rtp_iface="192.168.1.10"),
        fdir=sbs.StreamFdirConfig(enabled=enabled),
        status=status)


def _bindings(*bs):
    return {b.binding_id: b for b in bs}


def test_in_sync_active_present_fresh():
    r = drift.compute_drift(_bindings(_b("color", 2000)), {2000, 1305}, rtp_age_fn=lambda mp: 30)
    assert r["bindings"][0]["classification"] == drift.IN_SYNC
    assert r["drift"] is False and r["counts"].get("in_sync") == 1


def test_missing_active_mountpoint_is_drift():
    # mp 2000/2001 active but absent from Janus -> drift (the .55 outage shape)
    r = drift.compute_drift(_bindings(_b("color", 2000, status=ONLINE),
                                      _b("depth", 2001, status=WAITING)),
                            {1305}, rtp_age_fn=lambda mp: 30)
    cls = {it["binding_id"]: it["classification"] for it in r["bindings"]}
    assert cls["node-x:color"] == drift.MISSING_JANUS_MOUNTPOINT
    assert cls["node-x:depth"] == drift.MISSING_JANUS_MOUNTPOINT
    assert r["drift"] is True and r["counts"]["missing_janus_mountpoint"] == 2


def test_operator_stopped_is_not_drift_R2():
    # ir1: fdir disabled + configured_offline, mp 2002 absent -> stopped, NOT drift.
    # (live = just cam10's static 1305; no orphan remote mountpoints to muddy the result)
    r = drift.compute_drift(_bindings(_b("ir1", 2002, status=OFFLINE, enabled=False)),
                            {1305}, rtp_age_fn=lambda mp: 30)
    assert r["bindings"][0]["classification"] == drift.STOPPED_BY_OPERATOR
    assert r["drift"] is False
    assert 2002 not in r["unexpected_mountpoints"]


def test_stopped_binding_lingering_listener_not_unexpected():
    # even if mp 2002 still lives, it maps to a known (stopped) binding -> not 'unexpected'
    r = drift.compute_drift(_bindings(_b("ir1", 2002, status=OFFLINE, enabled=False)),
                            {2002}, rtp_age_fn=lambda mp: 30)
    assert r["bindings"][0]["classification"] == drift.STOPPED_BY_OPERATOR
    assert r["unexpected_mountpoints"] == [] and r["drift"] is False


def test_unexpected_orphan_remote_mountpoint_is_drift():
    r = drift.compute_drift(_bindings(_b("color", 2000)), {2000, 2099}, rtp_age_fn=lambda mp: 30)
    assert r["unexpected_mountpoints"] == [2099]
    assert r["counts"]["unexpected_janus_mountpoint"] == 1 and r["drift"] is True


def test_local_static_ids_never_unexpected():
    # cam10's static 1305-1308 are < REMOTE_MP_MIN -> out of the remote drift scope
    r = drift.compute_drift(_bindings(_b("color", 2000)),
                            {1305, 1306, 1307, 1308, 2000}, rtp_age_fn=lambda mp: 5)
    assert r["unexpected_mountpoints"] == [] and r["drift"] is False


def test_stale_rtp_present_but_no_fresh_media():
    b = _bindings(_b("color", 2000))
    assert drift.compute_drift(b, {2000}, rtp_age_fn=lambda mp: 99999)["bindings"][0]["classification"] == drift.STALE_RTP
    assert drift.compute_drift(b, {2000}, rtp_age_fn=lambda mp: None)["bindings"][0]["classification"] == drift.STALE_RTP
    assert drift.compute_drift(b, {2000}, rtp_age_fn=lambda mp: 99999)["drift"] is True


def test_maintenance_node_binding_is_stopped_R2():
    b = _b("color", 2000, status=ONLINE)
    # without maintenance: active + mp absent -> missing (drift)
    r0 = drift.compute_drift(_bindings(b), {1305}, rtp_age_fn=lambda mp: 5)
    assert r0["bindings"][0]["classification"] == drift.MISSING_JANUS_MOUNTPOINT and r0["drift"] is True
    # node under maintenance: same binding -> stopped, NOT drift (must not be resurrected)
    r1 = drift.compute_drift(_bindings(b), {1305}, rtp_age_fn=lambda mp: 5,
                             maintenance_node_ids={"node-x"})
    assert r1["bindings"][0]["classification"] == drift.STOPPED_BY_OPERATOR and r1["drift"] is False


def test_local_producer_binding_ignored():
    loc = _b("color", 1305, status=ONLINE, mode=sbs.StreamMode.LOCAL_PRODUCER)
    r = drift.compute_drift(_bindings(loc), {1305}, rtp_age_fn=lambda mp: 5)
    assert r["bindings"] == [] and r["drift"] is False


def test_idempotent_pure_R9():
    args = (_bindings(_b("color", 2000), _b("ir1", 2002, status=OFFLINE, enabled=False)), {2000, 2099})
    a = drift.compute_drift(*args, rtp_age_fn=lambda mp: 10)
    b = drift.compute_drift(*args, rtp_age_fn=lambda mp: 10)
    assert a == b


def test_counts_shape_R7():
    r = drift.compute_drift(
        _bindings(_b("color", 2000, status=ONLINE),          # in_sync
                  _b("depth", 2001, status=ONLINE),          # missing
                  _b("ir1", 2002, status=OFFLINE, enabled=False)),  # stopped
        {2000, 2099}, rtp_age_fn=lambda mp: 20)
    assert r["counts"]["in_sync"] == 1
    assert r["counts"]["missing_janus_mountpoint"] == 1
    assert r["counts"]["stopped_by_operator"] == 1
    assert r["counts"]["unexpected_janus_mountpoint"] == 1
    assert r["drift"] is True


# ── route-level: R5 (corrupt -> raise -> app 503) and R4 (read-only) ──

def test_route_corrupt_store_raises_for_503_R5(tmp_path, monkeypatch):
    from app.routes import stream_bindings as sb
    p = tmp_path / "sb.json"
    p.write_text("{ corrupt not json")
    monkeypatch.setattr(sb, "BIND_STATE_PATH", p)
    monkeypatch.setattr(sb, "ALLOC_STATE_PATH", tmp_path / "al.json")
    with pytest.raises(sbs.StoreCorruptionError):   # app handler maps this to 503 topology_store_corrupt
        sb.reconcile_drift()


def test_route_is_read_only_R4():
    from app.routes import stream_bindings as sb
    src = inspect.getsource(sb.reconcile_drift)
    for forbidden in ("ensure_janus(", "create_mountpoint(", "destroy_mountpoint(",
                      "reconcile_gateway(", "subprocess"):
        assert forbidden not in src, f"drift route must be read-only — found {forbidden!r}"

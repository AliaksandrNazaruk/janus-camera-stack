"""`/api/v1/ui/fleet` view-model — aggregation shape + state pass-through.

The console (design_system ui kit) consumes this; raw machine states pass through
verbatim (the client StatusBadge owns colour). Pure builder is exercised with
injected I/O (no Janus / FDIR ring / clock); the route is admin-gated.
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

from app.routes import ui_viewmodel as ui_routes
from app.services import stream_binding_store as sbs
from app.services import ui_viewmodel

pytestmark = pytest.mark.asyncio

GW = "192.168.1.10"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    bp = tmp_path / "stream_bindings.json"
    ap = tmp_path / "allocations.json"
    monkeypatch.setattr(ui_routes, "BIND_STATE_PATH", bp)
    monkeypatch.setattr(ui_routes, "ALLOC_STATE_PATH", ap)
    return bp, ap


def _seed(bp, ap, *, sensor="color", mp=2000, port=5100, status="online", fdir=True):
    n = sbs.add_node_by_host("192.168.1.55", display_name="cam55", state_path=bp)
    sbs.set_host_key(n.node_id, "192.168.1.55 ssh-ed25519 AAAA", state_path=bp)
    sbs.set_serial(n.node_id, "141722072135", state_path=bp)
    sbs.set_provision_state(n.node_id, "ready", state_path=bp)
    n = sbs.get_node(n.node_id, state_path=bp)
    b = sbs.StreamBinding(
        binding_id=sbs.remote_binding_id(n, sensor), node_id=n.node_id, sensor=sensor,
        mode=sbs.StreamMode.REMOTE_PRODUCER, transport=sbs.StreamTransport(rtp_port=port),
        janus=sbs.StreamJanusConfig(mountpoint_id=mp, rtp_iface=GW))
    sbs.upsert_binding(b, state_path=bp, alloc_state_path=ap)
    if not fdir:
        sbs.set_fdir_enabled(b.binding_id, False, state_path=bp)
    if status != b.status:
        sbs.set_status(b.binding_id, status, state_path=bp)
    return n, b


def _build(bp, ap, **kw):
    return ui_viewmodel.build_fleet(
        state_path=bp, alloc_state_path=ap,
        rtp_age_fn=kw.get("rtp_age_fn", lambda mp: 90),
        events_fn=kw.get("events_fn", lambda n=30: []),
        mode_fn=kw.get("mode_fn", lambda: "nominal"),
        janus_ok_fn=kw.get("janus_ok_fn", lambda: True),
        webrtc_fn=kw.get("webrtc_fn", lambda: [
            {"key": "turn_server", "value": "turn.example", "status": "ok"},
            {"key": "turn_credentials", "value": "present", "status": "ok"}]),
        firewall_fn=kw.get("firewall_fn", lambda: "synced"),
        now=kw.get("now", 1_000_000.0))


def test_fleet_has_local_node_and_seeded_remote(_isolate):
    bp, ap = _isolate
    _seed(bp, ap)
    fm = _build(bp, ap)
    ids = {n["nodeId"] for n in fm["nodes"]}
    assert sbs.LOCAL_NODE_ID in ids and any(n["host"] == "192.168.1.55" for n in fm["nodes"])
    cam55 = next(n for n in fm["nodes"] if n["host"] == "192.168.1.55")
    assert cam55["local"] is False and cam55["role"] == "remote_producer"
    assert cam55["serial"] == "141722072135"
    assert cam55["health"]["hostKey"] == "pinned" and cam55["health"]["token"] == "present"
    assert cam55["health"]["provision"] == "ready" and cam55["health"]["maintenance"] == "off"


def test_states_pass_through_verbatim(_isolate):
    bp, ap = _isolate
    _seed(bp, ap, status="waiting_for_rtp", fdir=False)
    fm = _build(bp, ap)
    row = next(s for s in fm["streams"] if s["node"] != sbs.LOCAL_NODE_ID)
    assert row["status"] == "waiting_for_rtp"        # not mapped to a colour family
    assert row["fdir"] == "disabled"
    assert row["rtpAgeMs"] == 90 and row["mountpoint"] == 2000 and row["rtpPort"] == 5100


def test_metrics_alert_attention_from_worst_stream(_isolate):
    bp, ap = _isolate
    _seed(bp, ap, status="stale")
    fm = _build(bp, ap)
    assert fm["metrics"]["streamsLive"][0] == 0          # the only stream is stale
    assert fm["alert"]["severity"] == "critical"         # stale ∈ bad family
    assert fm["attention"]["status"] == "stale"
    svc = {s["name"]: s for s in fm["services"]}
    assert svc["Streams"]["label"].endswith("live") and svc["Janus"]["status"] == "healthy"


def test_maintenance_reflected(_isolate):
    bp, ap = _isolate
    n, _ = _seed(bp, ap)
    sbs.set_maintenance(n.node_id, True, state_path=bp)
    fm = _build(bp, ap)
    cam55 = next(x for x in fm["nodes"] if x["host"] == "192.168.1.55")
    assert cam55["health"]["maintenance"] == "on"


def test_events_mapped(_isolate):
    bp, ap = _isolate
    _seed(bp, ap)
    evts = [{"timestamp": 1_000_000.0, "domain": "producer", "detection_signal": "rtp_age_ms=9000",
             "recovery_action": "none", "outcome": "degraded", "binding_id": "141722072135:color",
             "node_id": "n", "sensor": "color"}]
    fm = _build(bp, ap, events_fn=lambda n=30: evts)
    assert fm["events"] and fm["events"][0]["target"] == "141722072135:color"
    assert fm["events"][0]["result"] == "degraded"
    assert fm["metrics"]["fdirEvents"] == 1
    # the Diagnostics > FDIR table needs the binding/domain/signal/suppressed columns
    fe = fm["fdirEvents"][0]
    assert fe["binding"] == "141722072135:color" and fe["domain"] == "producer"
    assert fe["signal"] == "rtp_age_ms=9000" and fe["suppressed"] == "no"


def test_firewall_status_is_real_not_hardcoded(_isolate):
    bp, ap = _isolate
    _seed(bp, ap)
    fm = _build(bp, ap, firewall_fn=lambda: "drift")    # reflect the actual dry-run
    svc = {s["name"]: s for s in fm["services"]}
    assert svc["Firewall"]["status"] == "drift"
    fm2 = _build(bp, ap, firewall_fn=lambda: "synced")
    assert {s["name"]: s for s in fm2["services"]}["Firewall"]["status"] == "synced"


def test_webrtc_panel_present(_isolate):
    bp, ap = _isolate
    _seed(bp, ap)
    fm = _build(bp, ap)
    assert isinstance(fm["webrtc"], list) and fm["webrtc"], "webrtc rows present for the Settings panel"
    keys = {r["key"] for r in fm["webrtc"]}
    assert "turn_server" in keys and "turn_credentials" in keys


def test_webrtc_default_builder_no_secret(_isolate, monkeypatch):
    # the real builder must never leak the TURN password/shared secret, and must
    # report creds as present/UNSET status only.
    bp, ap = _isolate
    _seed(bp, ap)
    fm = ui_viewmodel.build_fleet(state_path=bp, alloc_state_path=ap,
                                  rtp_age_fn=lambda mp: None, events_fn=lambda n=30: [],
                                  mode_fn=lambda: "nominal", janus_ok_fn=lambda: True, now=1.0)
    blob = repr(fm["webrtc"]).lower()
    assert "secret" not in blob and "password" not in blob       # mechanism label only, no value
    cred = next((r for r in fm["webrtc"] if r["key"] == "turn_credentials"), None)
    assert cred and cred["value"] in ("present", "UNSET")


def test_security_rows_reflect_fleet(_isolate):
    bp, ap = _isolate
    _seed(bp, ap)                                       # one remote, pinned key + token
    fm = _build(bp, ap)
    rows = {r["key"]: r for r in fm["security"]}
    assert rows["host_keys_pinned"]["value"] == "1/1" and rows["host_keys_pinned"]["status"] == "ok"
    assert rows["node_tokens"]["value"] == "1/1"
    assert rows["admin_api"]["value"] == "token-gated"


def test_local_node_serial_derived_from_projections(_isolate):
    bp, ap = _isolate
    import json
    # a local projection is a serial-keyed allocation; cam10's serial isn't on the
    # node row, so it must be derived from the projection's binding_id prefix.
    ap.write_text(json.dumps({"version": 1, "allocations": {
        "938422071421:color": {"mp_id": 1305, "rtp_port": 5004, "desired_active": True}}}))
    fm = _build(bp, ap)
    cam10 = next(n for n in fm["nodes"] if n["nodeId"] == sbs.LOCAL_NODE_ID)
    assert cam10["serial"] == "938422071421" and cam10["health"]["camera"] == "present"


def test_no_streams_no_alert(_isolate):
    bp, ap = _isolate
    fm = _build(bp, ap)                                  # only the implicit local node, no bindings
    assert fm["alert"] is None and fm["attention"] is None
    assert fm["metrics"]["streamsLive"] == [0, 0]
    assert fm["gateway"]["cidr"] == "192.168.1.0/24"


# ── route: admin-gated + returns the shape ─────────────────────────────

async def test_route_requires_admin(client):
    r = await client.get("/api/v1/ui/fleet")
    assert r.status_code in (401, 403)


async def test_operator_console_page_serves_live_assets(admin_client):
    # canonical /console.html (console.your-domain.example) + the alias both serve
    # the new design-system console.
    for path in ("/console.html", "/operator_console.html"):
        r = await admin_client.get(path)
        assert r.status_code == 200, path
        assert "Gateway Operator Console" in r.text          # new console, not the legacy SPA
        assert "/static/console/app.js" in r.text            # the wired orchestrator
        assert "/static/js/console_lib.js" in r.text          # admin-token auth for live calls
        assert "fleet-data.mock" not in r.text                # Phase 3: live data, mock removed
        assert "unpkg.com" not in r.text and "cdn." not in r.text   # self-hosted, CSP-safe


async def test_legacy_console_preserved(admin_client):
    r = await admin_client.get("/console_legacy.html")
    assert r.status_code == 200                                # old SPA still reachable


# ── opaque-session auth (review P0-1): cookie holds a session id, not the token ──

_TT = "test-token-conftest-default"


def _sid(sc: str) -> str:
    import re
    m = re.search(r"cam_admin=([^;]+)", sc)
    return m.group(1) if m else ""


async def test_session_sets_opaque_cookie_not_the_token(admin_client):
    from app.core import session_store
    session_store._reset_for_tests()
    r = await admin_client.post("/api/v1/ui/session")
    assert r.status_code == 200 and r.json()["ok"] is True
    sc = r.headers.get("set-cookie", "")
    low = sc.lower()
    assert "cam_admin=" in low and "httponly" in low and "samesite=lax" in low
    val = _sid(sc)
    assert val and val != _TT                                  # NOT the master token
    assert session_store.is_valid(val)                         # a real, live session id


async def test_session_cookie_authenticates_but_token_cookie_does_not(client):
    from app.core import session_store
    sid = session_store.create_session()
    r = await client.get("/api/v1/ui/fleet", cookies={"cam_admin": sid})
    assert r.status_code == 200                                # valid session id → in
    assert (await client.get("/api/v1/ui/fleet", cookies={"cam_admin": _TT})).status_code == 403   # raw token cookie rejected
    assert (await client.get("/api/v1/ui/fleet", cookies={"cam_admin": "nope"})).status_code == 403
    assert (await client.get("/api/v1/ui/fleet")).status_code == 403                                # anon


async def test_session_delete_revokes(client):
    from app.core import session_store
    sid = session_store.create_session()
    assert (await client.get("/api/v1/ui/fleet", cookies={"cam_admin": sid})).status_code == 200
    await client.delete("/api/v1/ui/session", cookies={"cam_admin": sid})
    assert session_store.is_valid(sid) is False
    assert (await client.get("/api/v1/ui/fleet", cookies={"cam_admin": sid})).status_code == 403


async def test_preview_session_cookie_bypasses_viewer_gate(client, monkeypatch):
    from app.core import session_store, viewer_auth
    monkeypatch.setattr(viewer_auth, "VIEWER_TOKENS", ["viewer-token-1234567890abc"])
    sid = session_store.create_session()
    ok = await client.get("/preview/1305", cookies={"cam_admin": sid})
    assert ok.status_code == 200                               # admin session ⊇ viewer
    assert (await client.get("/preview/1305")).status_code == 401   # neither → gated


def test_session_store_expiry_and_revoke():
    from app.core import session_store
    sid = session_store.create_session(ttl=0)                  # already expired
    assert session_store.is_valid(sid) is False
    sid2 = session_store.create_session(ttl=3600)
    assert session_store.is_valid(sid2) is True
    session_store.revoke(sid2)
    assert session_store.is_valid(sid2) is False
    assert session_store.is_valid("") is False


async def test_route_returns_fleet(admin_client, _isolate):
    bp, ap = _isolate
    _seed(bp, ap)
    r = await admin_client.get("/api/v1/ui/fleet")
    assert r.status_code == 200
    body = r.json()
    for key in ("gateway", "services", "metrics", "alert", "attention", "nodes", "streams", "events"):
        assert key in body
    assert any(n["nodeId"] == sbs.LOCAL_NODE_ID for n in body["nodes"])

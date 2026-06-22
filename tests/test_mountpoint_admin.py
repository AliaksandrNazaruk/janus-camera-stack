"""Mountpoint admin (Phase 3B of admin_dashboard split, C-04): dashboard Janus HTTP de-dup.

Behavior was locked first against the old admin_dashboard helpers; here re-pointed to
services/janus_dashboard_admin.py + application/mountpoint_admin.py with the SAME assertions
(preservation proof). De-dup only. The production services/janus_admin.py (reconcile path)
is NOT touched — asserted below.
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

from fastapi import HTTPException

from app.application import mountpoint_admin as ma
from app.services import janus_dashboard_admin as jda


def _attach_ok(monkeypatch, payload):
    monkeypatch.setattr(jda, "streaming_admin_key", lambda: "KEY")
    monkeypatch.setattr(jda, "attach", lambda: ("sid", "handle", None))
    monkeypatch.setattr(jda, "destroy_session", lambda sid: None)
    monkeypatch.setattr(jda, "streaming_message", lambda *a, **k: payload)


def _req(**kw):
    base = dict(id=2000, rtp_port=5100, codec="h264")
    base.update(kw)
    return ma.CreateMountpointRequest(**base)


# ── create ──────────────────────────────────────────────────────────────────
def test_create_admin_key_missing_raises(monkeypatch):
    monkeypatch.setattr(jda, "streaming_admin_key", lambda: None)
    with pytest.raises(ma.StreamingAdminKeyMissing):     # route maps to 500
        ma.create_mountpoint(_req())


def test_create_attach_failure_502(monkeypatch):
    monkeypatch.setattr(jda, "streaming_admin_key", lambda: "KEY")
    monkeypatch.setattr(jda, "attach", lambda: (None, None, "Janus unreachable: boom"))
    with pytest.raises(ma.JanusAttachFailed):            # route maps to 502
        ma.create_mountpoint(_req())


def test_create_success(monkeypatch):
    _attach_ok(monkeypatch, {"plugindata": {"data": {"streaming": "created"}}})
    r = ma.create_mountpoint(_req(id=2000, rtp_port=5100))
    assert r.created is True and r.id == 2000 and r.rtp_port == 5100 and r.error is None


def test_create_janus_error(monkeypatch):
    _attach_ok(monkeypatch, {"plugindata": {"data": {"error": "ID exists", "error_code": 456}}})
    r = ma.create_mountpoint(_req())
    assert r.created is False and r.error == "ID exists (code=456)"


def test_create_body_iface_and_fmtp(monkeypatch):
    seen = {}
    monkeypatch.setattr(jda, "streaming_admin_key", lambda: "KEY")
    monkeypatch.setattr(jda, "attach", lambda: ("sid", "handle", None))
    monkeypatch.setattr(jda, "destroy_session", lambda sid: None)
    monkeypatch.setattr(jda, "streaming_message",
                        lambda sid, handle, body, **k: seen.update(body=body) or {"plugindata": {"data": {"streaming": "created"}}})
    ma.create_mountpoint(_req(codec="h264", iface="192.168.1.10", secret="s"))
    media = seen["body"]["media"][0]
    assert media["iface"] == "192.168.1.10" and "fmtp" in media and seen["body"]["secret"] == "s"


# ── destroy ─────────────────────────────────────────────────────────────────
def test_destroy_success(monkeypatch):
    _attach_ok(monkeypatch, {"plugindata": {"data": {"streaming": "destroyed"}}})
    assert ma.destroy_mountpoint(2000) == {"id": 2000, "destroyed": True}


def test_destroy_error(monkeypatch):
    _attach_ok(monkeypatch, {"plugindata": {"data": {"error": "No such mountpoint"}}})
    out = ma.destroy_mountpoint(2000)
    assert out["destroyed"] is False and out["error"] == "No such mountpoint"


def test_destroy_admin_key_missing_raises(monkeypatch):
    monkeypatch.setattr(jda, "streaming_admin_key", lambda: None)
    with pytest.raises(ma.StreamingAdminKeyMissing):     # route maps to 500
        ma.destroy_mountpoint(2000)


# ── info (app.services.janus, not the streaming client) ─────────────────────
def test_mountpoint_info_success(monkeypatch):
    from app.services import janus as _janus
    monkeypatch.setattr(_janus, "streaming_info", lambda mp: {"data": {"info": {"id": mp, "viewers": 2}}})
    monkeypatch.setattr(_janus, "janus_summary", lambda mp: {"video_age_ms": 30})
    out = ma.mountpoint_info(2000)
    assert out["mp_id"] == 2000 and out["raw"] == {"id": 2000, "viewers": 2} and out["summary"] == {"video_age_ms": 30}


def test_mountpoint_info_janus_unavailable_502(monkeypatch):
    from app.services import janus as _janus

    def boom(mp):
        raise RuntimeError("conn refused")
    monkeypatch.setattr(_janus, "streaming_info", boom)
    with pytest.raises(ma.JanusUnreachable):             # route maps to 502
        ma.mountpoint_info(2000)


def test_mountpoint_info_bad_id_raises():
    with pytest.raises(ma.InvalidMountpointId):          # route maps to 400
        ma.mountpoint_info(0)


def test_mountpoint_routes_map_domain_errors_to_http(monkeypatch):
    """D3.3C: the routes map mountpoint_admin's domain errors to the SAME HTTP status+detail the
    use-case used to raise directly — 500 (admin-key) / 502 (attach) / 400 (bad id)."""
    from app.routes import admin_dashboard as ad
    # create: admin-key missing -> 500
    monkeypatch.setattr(jda, "streaming_admin_key", lambda: None)
    with pytest.raises(HTTPException) as e:
        ad.create_mountpoint(_req())
    assert e.value.status_code == 500 and "STREAMING_ADMIN_KEY not set" in e.value.detail
    # create: attach failure -> 502
    monkeypatch.setattr(jda, "streaming_admin_key", lambda: "KEY")
    monkeypatch.setattr(jda, "attach", lambda: (None, None, "boom"))
    with pytest.raises(HTTPException) as e2:
        ad.create_mountpoint(_req())
    assert e2.value.status_code == 502 and e2.value.detail == "boom"
    # info: bad id -> 400
    with pytest.raises(HTTPException) as e3:
        ad.mountpoint_info(0)
    assert e3.value.status_code == 400


# ── list ─────────────────────────────────────────────────────────────────────
def test_list_mountpoints_maps(monkeypatch):
    monkeypatch.setattr(jda, "list_mountpoints_raw",
                        lambda: ([{"id": 1305, "type": "rtp", "enabled": True, "video": True}], None))
    out = ma.list_mountpoints()
    assert out["error"] is None and out["mountpoints"][0]["id"] == 1305 and out["mountpoints"][0]["video"] is True
    infos, err = ma.list_mountpoint_infos()
    assert err is None and infos[0].id == 1305


# ── boundaries: routes thin (no httpx); reconcile path untouched ────────────
def test_routes_delegate_no_httpx():
    from app.routes import admin_dashboard as ad
    for fn in (ad.create_mountpoint, ad.destroy_mountpoint, ad.mountpoint_info, ad.list_mountpoints):
        src = inspect.getsource(fn)
        assert "mountpoint_admin." in src and "httpx" not in src


def test_reconcile_janus_admin_not_imported_by_dashboard_adapter():
    # the dashboard adapter may MENTION janus_admin.py in prose, but must not IMPORT the
    # reconcile-path module (keeps the production reconcile contract untouchable).
    src = inspect.getsource(jda)
    assert "from app.services import janus_admin" not in src
    assert "import app.services.janus_admin" not in src

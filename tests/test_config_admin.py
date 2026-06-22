"""admin_config split (route-purity Phase 5): characterization of the systemctl infra +
apply/snapshot orchestration, re-pointed to their extracted homes:

    _systemctl / _service_active -> services/systemd.{systemctl_action,is_active}  (bare, verbatim)
    apply_config body            -> application/config_apply.apply
    get_snapshot body            -> application/config_view.snapshot

The decisive lock is the COMMAND: bare `["systemctl", action, unit]` — no sudo, no /bin/ path
(distinct from the dashboard's sudo'd restart_unit). Behavior was locked against the in-route
helpers first, then re-pointed with identical assertions. Route-delegation tests keep the
thin handlers honest.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import HTTPException

from app.application import config_apply, config_view
from app.services import service_control, systemd
from app.routes import admin_config as ac   # route delegation


class _Res:
    def __init__(self, rc, stderr="boom", stdout=""):
        self.returncode, self.stderr, self.stdout = rc, stderr, stdout


class _Rendered:
    def __init__(self, rendered=None, skipped=None):
        self.rendered = rendered if rendered is not None else []
        self.skipped_templates = skipped if skipped is not None else []


# ── systemctl infra (-> services/systemd): BARE command, bool, swallow ──────
def test_systemctl_bare_command_bool_swallow(monkeypatch):
    calls = []
    monkeypatch.setattr(systemd.subprocess, "run", lambda cmd, **kw: (calls.append((cmd, kw)), _Res(0))[1])
    assert systemd.systemctl_action("restart", "janus.service", timeout=30) is True
    # The whole point of Phase 5: no sudo, no /bin/, exactly these three argv tokens.
    assert calls[0][0] == ["systemctl", "restart", "janus.service"]
    assert "sudo" not in calls[0][0] and calls[0][1]["timeout"] == 30

    monkeypatch.setattr(systemd.subprocess, "run", lambda *a, **k: _Res(1))
    assert systemd.systemctl_action("restart", "x") is False  # non-zero rc -> False

    def fnf(*_a, **_k):
        raise FileNotFoundError()
    monkeypatch.setattr(systemd.subprocess, "run", fnf)
    assert systemd.systemctl_action("restart", "x") is False  # exec failure swallowed

    def to(*_a, **_k):
        raise systemd.subprocess.TimeoutExpired(cmd="systemctl", timeout=1)
    monkeypatch.setattr(systemd.subprocess, "run", to)
    assert systemd.systemctl_action("restart", "x") is False


def test_is_active_uses_is_active_timeout3(monkeypatch):
    calls = []
    monkeypatch.setattr(systemd.subprocess, "run", lambda cmd, **kw: (calls.append((cmd, kw)), _Res(0))[1])
    assert systemd.is_active("janus") is True
    assert calls[0][0] == ["systemctl", "is-active", "janus"] and calls[0][1]["timeout"] == 3


def test_sudo_restart_unit_unchanged(monkeypatch):
    """Guard the OTHER contract stays scoped + sudo'd — the bare-systemctl (admin_config) and the
    privileged restart paths must not converge by accident. (P1: the privileged restart now goes
    through the scoped service-admin CLI, services/service_control.py — still distinct from bare.)"""
    calls = []
    monkeypatch.setattr(service_control.subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _Res(0))[1])
    service_control.restart_unit("janus.service")
    assert calls[0] == ["sudo", "-n", "/usr/local/bin/service-admin", "restart", "janus.service"]


# ── apply orchestration (-> application/config_apply) ───────────────────────
def test_apply_restart_order_and_fallback(monkeypatch):
    monkeypatch.setattr(config_apply.jcfg_renderer, "render", lambda: _Rendered([Path("/a.jcfg")]))
    monkeypatch.setattr(config_apply, "audit", lambda *a, **k: None)
    seen = []

    def fake(unit, *, timeout=30):
        seen.append((unit, timeout))
        # janus restarts; relay's first variant (janus-textroom-relay) succeeds -> no hook fallback
        return (0, "") if unit in ("janus", "janus-textroom-relay") else (1, "no such unit")
    monkeypatch.setattr(config_apply.service_control, "restart_unit", fake)

    resp = config_apply.apply(restart_janus=True, restart_relay=True)
    assert resp.janus_restarted is True and resp.relay_restarted is True
    assert resp.rendered == ["/a.jcfg"] and resp.errors == []
    # janus once (service-admin normalises .service — no .service/bare double-try); relay first variant
    assert [s[0] for s in seen] == ["janus", "janus-textroom-relay"]
    assert seen[0][1] == 30 and seen[1][1] == 15


def test_apply_partial_failure_strings(monkeypatch):
    monkeypatch.setattr(config_apply.jcfg_renderer, "render", lambda: _Rendered())
    monkeypatch.setattr(config_apply, "audit", lambda *a, **k: None)
    monkeypatch.setattr(config_apply.service_control, "restart_unit",
                        lambda unit, *, timeout=30: (1, "fail"))
    resp = config_apply.apply(restart_janus=True, restart_relay=True)
    assert resp.janus_restarted is False and resp.relay_restarted is False
    assert "janus restart failed — see journalctl -u janus" in resp.errors
    assert "relay restart failed (or not installed)" in resp.errors


def test_apply_restart_exec_failure_swallowed(monkeypatch):
    """A service-admin exec failure (RuntimeError) is swallowed to janus_restarted=False — apply
    continues + reports it, matching the prior bare-systemctl behavior."""
    monkeypatch.setattr(config_apply.jcfg_renderer, "render", lambda: _Rendered())
    monkeypatch.setattr(config_apply, "audit", lambda *a, **k: None)

    def boom(unit, *, timeout=30):
        raise RuntimeError("service-admin not found")
    monkeypatch.setattr(config_apply.service_control, "restart_unit", boom)
    resp = config_apply.apply(restart_janus=True, restart_relay=False)
    assert resp.janus_restarted is False


def test_apply_render_failure_raises(monkeypatch):
    def boom():
        raise RuntimeError("bad template")
    monkeypatch.setattr(config_apply.jcfg_renderer, "render", boom)
    monkeypatch.setattr(config_apply, "audit", lambda *a, **k: None)
    with pytest.raises(config_apply.ConfigRenderFailed) as e:    # route maps to 500
        config_apply.apply(restart_janus=True, restart_relay=True)
    # detail MUST stay a list[str] (structured render errors), not a joined string
    assert isinstance(e.value.errors, list) and "render failed: bad template" in e.value.errors[0]


def test_apply_flags_false_skips_restart(monkeypatch):
    monkeypatch.setattr(config_apply.jcfg_renderer, "render", lambda: _Rendered())
    monkeypatch.setattr(config_apply, "audit", lambda *a, **k: None)
    called = []
    monkeypatch.setattr(config_apply.service_control, "restart_unit",
                        lambda *a, **k: (called.append(a), (0, ""))[1])
    resp = config_apply.apply(restart_janus=False, restart_relay=False)
    assert resp.janus_restarted is False and resp.relay_restarted is False and called == []


# ── snapshot aggregation (-> application/config_view) ───────────────────────
def test_snapshot_aggregation(monkeypatch):
    class V:
        def __init__(self, key):
            self.key, self.masked, self.is_set, self.is_sensitive, self.last_rotated_ts = key, "***", True, True, None
    monkeypatch.setattr(config_view.secret_store, "snapshot", lambda: {"B": V("B"), "A": V("A")})

    class _Paths:
        cfg_dir = Path("/opt/janus/etc/janus")
    monkeypatch.setattr(config_view.jcfg_renderer, "detect_janus_paths", lambda: _Paths())
    monkeypatch.setattr(config_view.jcfg_renderer, "detect_template_dir", lambda: Path("/tpl"))
    monkeypatch.setattr(config_view.jcfg_renderer, "_read_current_nat_mapping", lambda p: "1.2.3.4")
    monkeypatch.setattr(config_view.jcfg_renderer, "detect_primary_iface", lambda: "eth0")
    monkeypatch.setattr(config_view.systemd, "is_active", lambda u: u in ("janus", "janus-textroom-relay"))

    snap = config_view.snapshot()
    assert [s.key for s in snap.secrets] == ["A", "B"]  # sorted by key
    assert snap.janus_cfg_dir == "/opt/janus/etc/janus" and snap.nat_1_1_mapping == "1.2.3.4"
    assert snap.ice_enforce_list == "eth0" and snap.template_dir == "/tpl"
    assert snap.janus_active is True and snap.relay_active is True


# ── route delegation (thin handlers wire to the use-cases) ──────────────────
def test_routes_delegate(monkeypatch):
    sentinel_snap = config_view.ConfigSnapshot(secrets=[])
    monkeypatch.setattr(ac.config_view, "snapshot", lambda: sentinel_snap)
    assert ac.get_snapshot() is sentinel_snap

    captured = {}
    sentinel_apply = config_apply.ApplyResponse(rendered=[], janus_restarted=True, relay_restarted=False)

    def fake_apply(rj, rr):
        captured["args"] = (rj, rr)
        return sentinel_apply
    monkeypatch.setattr(ac.config_apply, "apply", fake_apply)
    assert ac.apply_config(restart_janus=False, restart_relay=True) is sentinel_apply
    assert captured["args"] == (False, True)


def test_apply_route_maps_render_failure_to_500_list_detail(monkeypatch):
    """D3.2B: the route maps ConfigRenderFailed -> 500 with detail kept as a LIST[str]
    (structured render errors), byte-identical to the old HTTPException(detail=errors)."""
    from app.routes import admin_config as ac
    def boom(*a, **k):
        raise config_apply.ConfigRenderFailed(["render failed: bad template"])
    monkeypatch.setattr(ac.config_apply, "apply", boom)
    with pytest.raises(HTTPException) as e:
        ac.apply_config(restart_janus=True, restart_relay=True)
    assert e.value.status_code == 500
    assert isinstance(e.value.detail, list) and e.value.detail == ["render failed: bad template"]

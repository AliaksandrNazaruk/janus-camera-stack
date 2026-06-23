"""Track 1 — depth-query over the node model (DEPTH_OVER_NODES.md).

Covers all four layers:
  • node_client.get_depth/get_depth_frame — local → local mux directly; remote → agent (token);
    unprovisioned stub → raises.
  • use-case get_depth/get_depth_frame — routes by node_id, wraps failures in NodeAgentError.
  • gateway route GET /api/v1/admin/nodes/{id}/depth[/frame] — admin-gated; routes via the use-case.
  • node-agent _mux_fetch — token-gated passthrough of the node's LOCAL mux (no re-encode).
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.services import node_client as nc
from app.services import stream_binding_store as sbs
from app.application.stream_bindings import get_depth, get_depth_frame
from app.application.stream_bindings.results import NodeAgentError


@pytest.fixture
def bind_path(tmp_path):
    return tmp_path / "stream_bindings.json"


class _Resp:
    def __init__(self, code=200, payload=None):
        self.status_code = code
        self._p = payload if payload is not None else {"depth": 0.42, "age_ms": 12, "stale": False}

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise nc.httpx.HTTPStatusError("err", request=None, response=None)


# ── node_client layer ──────────────────────────────────────────────────

def test_local_adapter_get_depth_hits_local_mux(monkeypatch):
    seen = {}
    monkeypatch.setattr(nc.httpx, "get",
                        lambda url, timeout=None, headers=None: seen.update(url=url, headers=headers) or _Resp())
    out = nc.LocalNodeClientAdapter().get_depth(10, 20)
    assert out["depth"] == 0.42
    assert "/depth?x=10&y=20" in seen["url"]          # local mux, direct
    assert "8901" not in seen["url"]                  # NOT via an agent


def test_real_client_get_depth_hits_agent_with_token(bind_path, monkeypatch):
    sbs.upsert_node("cam55", host="192.168.1.55", role="remote_producer", state_path=bind_path)
    sbs.set_agent_token("cam55", "node55-secret", state_path=bind_path)
    seen = {}
    monkeypatch.setattr(nc.httpx, "get",
                        lambda url, headers=None, timeout=None: seen.update(url=url, headers=headers) or _Resp())
    client = nc.get_node_client("cam55", state_path=bind_path)
    assert isinstance(client, nc.RealNodeClient)
    client.get_depth(5, 6, aligned=True)
    assert "192.168.1.55:8901/depth?x=5&y=6" in seen["url"] and "aligned=true" in seen["url"]
    assert seen["headers"].get("X-Node-Token") == "node55-secret"   # per-node token


def test_real_client_get_depth_frame_hits_agent(bind_path, monkeypatch):
    sbs.upsert_node("cam55", host="192.168.1.55", role="remote_producer", state_path=bind_path)
    seen = {}
    monkeypatch.setattr(nc.httpx, "get",
                        lambda url, headers=None, timeout=None: seen.update(url=url) or _Resp(payload={"data": "b64"}))
    nc.get_node_client("cam55", state_path=bind_path).get_depth_frame()
    assert "192.168.1.55:8901/depth/frame" in seen["url"]


def test_unprovisioned_stub_get_depth_raises(bind_path):
    client = nc.get_node_client("ghost", state_path=bind_path)   # no host → inert stub
    assert isinstance(client, nc.RemoteNodeClientStub)
    with pytest.raises(RuntimeError, match="bootstrap_required"):
        client.get_depth(1, 2)


# ── use-case layer ─────────────────────────────────────────────────────

class _FakeClient:
    def __init__(self, payload=None, boom=False):
        self._p = payload or {"depth": 1.5}
        self._boom = boom

    def get_depth(self, x, y, *, aligned=False):
        if self._boom:
            raise RuntimeError("agent unreachable")
        return {**self._p, "x": x, "y": y}

    def get_depth_frame(self):
        if self._boom:
            raise RuntimeError("agent unreachable")
        return {"data": "frame"}


def test_use_case_routes_via_node_client(bind_path, monkeypatch):
    monkeypatch.setattr(nc, "get_node_client", lambda nid, **k: _FakeClient())
    out = get_depth("cam10", 3, 4, bind_state_path=bind_path)
    assert out["depth"] == 1.5 and out["x"] == 3


def test_use_case_wraps_failure_in_node_agent_error(bind_path, monkeypatch):
    monkeypatch.setattr(nc, "get_node_client", lambda nid, **k: _FakeClient(boom=True))
    with pytest.raises(NodeAgentError):
        get_depth("cam55", 1, 2, bind_state_path=bind_path)
    with pytest.raises(NodeAgentError):
        get_depth_frame("cam55", bind_state_path=bind_path)


# ── gateway route layer ────────────────────────────────────────────────

pytestmark_async = pytest.mark.asyncio


@pytest.mark.asyncio
async def test_route_node_depth_ok(admin_client, monkeypatch):
    monkeypatch.setattr(nc, "get_node_client", lambda nid, **k: _FakeClient({"depth": 0.9}))
    r = await admin_client.get("/api/v1/admin/nodes/cam10/depth?x=12&y=34")
    assert r.status_code == 200, r.text
    assert r.json()["depth"] == 0.9 and r.json()["x"] == 12.0


@pytest.mark.asyncio
async def test_route_node_depth_frame_ok(admin_client, monkeypatch):
    monkeypatch.setattr(nc, "get_node_client", lambda nid, **k: _FakeClient())
    r = await admin_client.get("/api/v1/admin/nodes/cam55/depth/frame")
    assert r.status_code == 200 and r.json()["data"] == "frame"


@pytest.mark.asyncio
async def test_route_node_depth_unreachable_502(admin_client, monkeypatch):
    monkeypatch.setattr(nc, "get_node_client", lambda nid, **k: _FakeClient(boom=True))
    r = await admin_client.get("/api/v1/admin/nodes/cam55/depth?x=1&y=2")
    assert r.status_code == 502


@pytest.mark.asyncio
async def test_route_node_depth_requires_admin(client):
    r = await client.get("/api/v1/admin/nodes/cam10/depth?x=1&y=2")
    assert r.status_code in (401, 403, 503)


# ── node-agent layer (_mux_fetch passthrough) ──────────────────────────

_AGENT = (Path(__file__).resolve().parent.parent
          / "host_infra" / "node-bundle" / "node-agent" / "camera-node-agent.py")
_spec = importlib.util.spec_from_file_location("camera_node_agent", _AGENT)
agent_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent_mod)


class _UrlResp:
    status = 200

    def __init__(self, body: bytes, ct="application/json"):
        self._b = body
        self.headers = {"Content-Type": ct}

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    # headers.get is used by the agent
    class _H(dict):
        pass


def test_agent_mux_fetch_passthrough(monkeypatch):
    captured = {}

    def fake_urlopen(url, timeout=None):
        captured["url"] = url
        r = _UrlResp(b'{"depth":0.7}')
        r.headers = type("H", (), {"get": lambda self, k, d=None: "application/json"})()
        return r
    monkeypatch.setattr(agent_mod.urllib.request, "urlopen", fake_urlopen)
    code, body, ct = agent_mod._mux_fetch("/depth", {"x": "10", "y": "20", "aligned": None})
    assert code == 200 and body == b'{"depth":0.7}'
    assert "/depth?x=10&y=20" in captured["url"] and "aligned" not in captured["url"]  # None dropped


def test_agent_mux_fetch_unreachable_502(monkeypatch):
    def boom(url, timeout=None):
        raise agent_mod.urllib.error.URLError("refused")
    monkeypatch.setattr(agent_mod.urllib.request, "urlopen", boom)
    code, body, ct = agent_mod._mux_fetch("/depth/frame", {"format": "json"})
    assert code == 502 and b"mux unreachable" in body

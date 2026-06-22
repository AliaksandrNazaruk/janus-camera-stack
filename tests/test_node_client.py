"""G5.1b / P2 — NodeClient routing: local vs real-remote vs inert stub."""
from __future__ import annotations

import os
import sys

import pytest

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.services import node_client as nc
from app.services import stream_binding_store as sbs


@pytest.fixture
def bind_path(tmp_path):
    return tmp_path / "stream_bindings.json"


class _Resp:
    def __init__(self, code, payload):
        self.status_code = code
        self._p = payload
        self.text = str(payload)

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise nc.httpx.HTTPStatusError("err", request=None, response=None)


def test_real_get_modes_calls_agent(bind_path, monkeypatch):
    sbs.upsert_node("cam55", host="192.168.1.55", role="remote_producer", state_path=bind_path)
    sbs.set_agent_token("cam55", "node55-secret", state_path=bind_path)
    seen = {}

    def _get(url, headers=None, timeout=None):
        seen.update(url=url, headers=headers)
        return _Resp(200, {"sensor": "color",
                           "modes": [{"width": 1280, "height": 720, "fps": [30, 15]}]})
    monkeypatch.setattr(nc.httpx, "get", _get)
    out = nc.get_node_client("cam55", state_path=bind_path).get_modes("color")
    assert out["modes"][0]["width"] == 1280
    assert "192.168.1.55:8901/modes?sensor=color" in seen["url"]
    assert seen["headers"].get("X-Node-Token") == "node55-secret"   # per-node token


def test_local_node_routes_to_local_adapter(bind_path):
    c = nc.get_node_client("cam10", state_path=bind_path)
    assert isinstance(c, nc.LocalNodeClientAdapter)
    assert c.status("cam10") is nc.NodeReachability.LOCAL


def test_provisioned_remote_routes_to_real_client(bind_path):
    sbs.upsert_node("cam55", host="192.168.1.55", role="remote_producer", state_path=bind_path)
    assert isinstance(nc.get_node_client("cam55", state_path=bind_path), nc.RealNodeClient)


def test_unknown_node_routes_to_inert_stub(bind_path):
    c = nc.get_node_client("ghost", state_path=bind_path)   # no host -> inert stub
    assert isinstance(c, nc.RemoteNodeClientStub)
    assert c.status("ghost") is nc.NodeReachability.UNREACHABLE
    res = c.restart_stream("ghost", "color")
    assert res.ok is False and "bootstrap_required" in res.detail


def test_real_restart_calls_agent(bind_path, monkeypatch):
    sbs.upsert_node("cam55", host="192.168.1.55", role="remote_producer", state_path=bind_path)
    seen = {}
    monkeypatch.setattr(nc.httpx, "post",
                        lambda url, headers=None, timeout=None: seen.update(url=url) or _Resp(200, {"ok": True}))
    res = nc.get_node_client("cam55", state_path=bind_path).restart_stream("cam55", "depth")
    assert res.ok and "rs-stream@depth" in res.detail
    assert "192.168.1.55:8901/restart_stream?sensor=depth" in seen["url"]


def test_real_client_sends_per_node_token(bind_path, monkeypatch):
    sbs.upsert_node("cam55", host="192.168.1.55", role="remote_producer", state_path=bind_path)
    sbs.set_agent_token("cam55", "node55-secret", state_path=bind_path)
    seen = {}
    monkeypatch.setattr(nc.httpx, "post",
                        lambda url, headers=None, timeout=None: seen.update(headers=headers) or _Resp(200, {"ok": True}))
    nc.get_node_client("cam55", state_path=bind_path).restart_stream("cam55", "color")
    assert seen["headers"].get("X-Node-Token") == "node55-secret"   # the node's OWN token, not a global


def test_real_restart_handles_unreachable(bind_path, monkeypatch):
    sbs.upsert_node("cam55", host="192.168.1.55", role="remote_producer", state_path=bind_path)

    def boom(*a, **k):
        raise nc.httpx.ConnectError("down")

    monkeypatch.setattr(nc.httpx, "post", boom)
    res = nc.get_node_client("cam55", state_path=bind_path).restart_stream("cam55", "color")
    assert res.ok is False and "unreachable" in res.detail


def test_remote_recovery_clients_run_no_local_command():
    """Neither remote client may run a local/gateway command — HTTP to the node only."""
    import inspect
    for cls in (nc.RealNodeClient, nc.RemoteNodeClientStub):
        src = inspect.getsource(cls)
        for forbidden in ("subprocess", "os.system", "Popen", "_encoder_action", "systemctl"):
            assert forbidden not in src, f"{cls.__name__} must not reference {forbidden}"

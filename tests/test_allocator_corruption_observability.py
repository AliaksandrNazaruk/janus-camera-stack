"""Cycle 14A — allocator corruption observability.

The allocator read API is deliberately fail-SAFE (corrupt -> empty) so live encoder streams are
never torn down (Cycle 1). That makes a corrupt allocator INDISTINGUISHABLE from an empty one on
every read surface. `allocator_corruption_status` restores the distinction for health/diagnostics
WITHOUT changing any read path, and `/readyz` surfaces it as a NON-FATAL field (a corrupt
allocator must NOT fail readiness — the streams are still up)."""
from __future__ import annotations

import json

from app.services import mountpoint_allocator as m


# ── probe: classify state without raising ────────────────────────────

def test_probe_missing_is_not_corruption(tmp_path):
    p = tmp_path / "sensor_allocations.json"           # never created -> cold start
    assert m.allocator_corruption_status(p) == {"allocator_state": "missing"}


def test_probe_empty_file_is_ok(tmp_path):
    p = tmp_path / "a.json"
    p.write_text("")
    assert m.allocator_corruption_status(p) == {"allocator_state": "ok"}


def test_probe_valid_state_is_ok(tmp_path):
    p = tmp_path / "a.json"
    m.allocate("141722072135", "color", state_path=p)  # writes a real allocation
    assert m.allocator_corruption_status(p) == {"allocator_state": "ok"}


def test_probe_invalid_json_is_corrupt(tmp_path):
    p = tmp_path / "a.json"
    p.write_text('{"allocations": {"x"')               # truncated -> JSONDecodeError
    st = m.allocator_corruption_status(p)
    assert st["allocator_state"] == "corrupt"
    assert st["allocator_detail"]


def test_probe_null_allocations_is_corrupt(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"version": 1, "allocations": None}))
    assert m.allocator_corruption_status(p)["allocator_state"] == "corrupt"


def test_probe_non_dict_allocations_is_corrupt(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"version": 1, "allocations": "garbage"}))
    assert m.allocator_corruption_status(p)["allocator_state"] == "corrupt"


def test_probe_missing_allocations_key_is_ok(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"version": 1}))            # no allocations key -> {} default
    assert m.allocator_corruption_status(p) == {"allocator_state": "ok"}


# ── invariant: a corrupt allocator stays fail-SAFE on every reader (never raises) ──

def test_corrupt_allocator_readers_never_raise(tmp_path):
    p = tmp_path / "a.json"
    p.write_text('{"allocations": {"x"')               # invalid JSON
    assert m.list_allocations(p) == {}                  # no raise
    assert m.list_desired_active(p) == {}
    assert m.get_allocation("141722072135", "color", p) is None


# ── /readyz: surface allocator_state but stay NON-FATAL ───────────────

def test_readyz_corrupt_allocator_is_non_fatal(monkeypatch):
    """A corrupt allocator must NOT fail readiness (fail-safe: streams still run) — but the
    degraded state must be VISIBLE in the body."""
    from app.routes import system
    monkeypatch.setattr("app.core.settings.is_production", lambda: False)
    monkeypatch.setattr(system.janus, "janus_summary", lambda *a, **k: {"streaming": "ok"})
    monkeypatch.setattr(m, "allocator_corruption_status",
                        lambda *a, **k: {"allocator_state": "corrupt", "allocator_detail": "bad"})
    resp = system.readyz()
    assert resp.status_code == 200                       # NON-FATAL
    body = json.loads(resp.body)
    assert body["ok"] is True
    assert body["allocator_state"] == "corrupt"          # but visible


def test_readyz_healthy_allocator_reports_ok(monkeypatch):
    from app.routes import system
    monkeypatch.setattr("app.core.settings.is_production", lambda: False)
    monkeypatch.setattr(system.janus, "janus_summary", lambda *a, **k: {"streaming": "ok"})
    monkeypatch.setattr(m, "allocator_corruption_status",
                        lambda *a, **k: {"allocator_state": "ok"})
    resp = system.readyz()
    assert resp.status_code == 200
    assert json.loads(resp.body)["allocator_state"] == "ok"

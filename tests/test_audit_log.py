"""Regression guard: the admin audit trail must actually write.

Four route modules historically imported a *missing* ``audit`` symbol behind a
no-op ``except`` fallback (``audit_log`` exports ``emit``, not ``audit``),
silently dropping every audit entry — including secret rotate/reveal, service
restarts, mountpoint CRUD, and node/binding CRUD. These tests pin the wiring so
the trail cannot silently die again.
"""
import json

from app.services import audit_log


def _redirect(tmp_path, monkeypatch):
    f = tmp_path / "audit" / "audit.jsonl"
    monkeypatch.setattr(audit_log, "AUDIT_LOG_DIR", f.parent)
    monkeypatch.setattr(audit_log, "AUDIT_LOG_FILE", f)
    return f


def test_emit_writes_entry(tmp_path, monkeypatch):
    f = _redirect(tmp_path, monkeypatch)
    audit_log.emit(action="x.y", target="t", outcome="success", details={"a": 1})
    (line,) = f.read_text().splitlines()
    e = json.loads(line)
    assert e["action"] == "x.y"
    assert e["target"] == "t"
    assert e["outcome"] == "success"
    assert e["details"] == {"a": 1}


def test_audit_wrapper_writes_and_infers_outcome(tmp_path, monkeypatch):
    f = _redirect(tmp_path, monkeypatch)
    audit_log.audit("stream_bindings.node.register", {"node_id": "cam55"})
    audit_log.audit("admin_dashboard.restart.refused_self", {"service": "s"})
    e0, e1 = (json.loads(x) for x in f.read_text().splitlines())
    assert e0["outcome"] == "success"
    assert e0["details"] == {"node_id": "cam55"}
    assert e0["target"] == "stream_bindings.node.register"
    assert e1["outcome"] == "failure"  # inferred from the 'refused' marker


def test_admin_route_modules_use_real_audit():
    """The four modules must bind the real wrapper, not a no-op fallback."""
    from app.routes import admin_config, admin_dashboard, runtime_config, stream_bindings

    for mod in (stream_bindings, runtime_config, admin_config, admin_dashboard):
        assert mod.audit is audit_log.audit, f"{mod.__name__} not using real audit"

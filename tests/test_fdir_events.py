"""Unit tests for fdir_events.py — FDIR event emission + ring buffer + persistence.

fdir_events.py (157 LOC) — central audit trail for ALL FDIR actions
(recovery ladder, mode transitions, watchdog escalations). If broken,
we lose visibility into what recovery did.

Tests cover:
- Enum integrity (Severity, Domain, RecoveryAction)
- FdirEvent dataclass (immutable, JSON serializable)
- emit() — adds to ring + logs + persists + accepts enum or string args
- recent() — newest-first, capped to N
- Ring buffer overflow (deque maxlen=RING_MAX)
- File persistence + rotation (5MB → fdir.jsonl.1 backup)
"""
from __future__ import annotations

import importlib
import json
import logging
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services import fdir_events
from app.services.fdir_events import (
    Domain,
    FdirEvent,
    RecoveryAction,
    Severity,
)


# ── Fixtures: clean ring + redirect persistence dir ──────────────────

@pytest.fixture(autouse=True)
def clean_ring(monkeypatch):
    """Each test starts with empty ring buffer."""
    fdir_events._ring.clear()


@pytest.fixture
def tmp_log_dir(tmp_path, monkeypatch):
    """Redirect FDIR_LOG_DIR to tmp_path."""
    monkeypatch.setattr(fdir_events, "LOG_DIR", tmp_path)
    return tmp_path


# ── Enum integrity ────────────────────────────────────────────────────

def test_severity_values():
    assert Severity.INFO.value == "info"
    assert Severity.WARN.value == "warn"
    assert Severity.ERROR.value == "error"
    assert Severity.CRITICAL.value == "critical"


def test_domain_covers_required_subsystems():
    # "producer" (G5) routes remote-stream staleness away from the local
    # JANUS/PIPELINE ladder — see UNIFIED_FDIR_OVER_STREAM_BINDINGS.md.
    required = {"sensor", "pipeline", "janus", "network", "turn", "client", "system", "producer"}
    actual = {d.value for d in Domain}
    assert required == actual


def test_recovery_actions_match_ladder():
    """Recovery actions consumed by recovery_ladder ladder levels."""
    required = {"retry_handle", "restart_pipeline", "restart_janus",
                "usb_reset", "reboot_node", "switch_mode", "none", "degrade_profile"}
    actual = {a.value for a in RecoveryAction}
    assert required.issubset(actual)


def test_producer_domain_exists():
    assert Domain.PRODUCER.value == "producer"


def test_emit_carries_binding_identity(tmp_log_dir):
    e = fdir_events.emit(Domain.PRODUCER, Severity.WARN, "rtp_age_ms=25000",
                         RecoveryAction.NONE, "degraded",
                         binding_id="cam55:color", node_id="cam55", sensor="color")
    assert (e.binding_id, e.node_id, e.sensor) == ("cam55:color", "cam55", "color")
    assert fdir_events.recent(1)[0]["binding_id"] == "cam55:color"


def test_emit_without_binding_identity_is_none(tmp_log_dir):
    """Every pre-G5 call site omits the binding kwargs — must stay None."""
    e = fdir_events.emit(Domain.SYSTEM, Severity.INFO, "x", RecoveryAction.NONE, "y")
    assert (e.binding_id, e.node_id, e.sensor) == (None, None, None)


def test_severity_str_compatibility():
    """Severity is str enum — comparable with raw strings."""
    assert Severity.INFO == "info"


# ── FdirEvent dataclass ───────────────────────────────────────────────

def test_fdir_event_is_immutable():
    """Frozen dataclass → mutation raises FrozenInstanceError."""
    e = FdirEvent(
        timestamp=1.0, domain="system", severity="info",
        detection_signal="test", recovery_action="none", outcome="ok",
    )
    with pytest.raises(Exception):  # FrozenInstanceError
        e.timestamp = 2.0


def test_fdir_event_to_json_round_trip():
    e = FdirEvent(
        timestamp=1234567890.5, domain="janus", severity="error",
        detection_signal="watchdog_stale", recovery_action="restart_janus",
        outcome="restarted ok", details={"attempts": 3},
    )
    parsed = json.loads(e.to_json())
    assert parsed["timestamp"] == 1234567890.5
    assert parsed["domain"] == "janus"
    assert parsed["details"] == {"attempts": 3}


def test_fdir_event_default_details_empty():
    e = FdirEvent(
        timestamp=1.0, domain="sys", severity="info",
        detection_signal="x", recovery_action="none", outcome="y",
    )
    assert e.details == {}


def test_fdir_event_node_from_env(monkeypatch):
    """node field is taken from HOSTNAME env."""
    monkeypatch.setenv("HOSTNAME", "test-host-42")
    e = FdirEvent(
        timestamp=1.0, domain="sys", severity="info",
        detection_signal="x", recovery_action="none", outcome="y",
    )
    assert e.node == "test-host-42"


# ── emit() — basic recording ──────────────────────────────────────────

def test_emit_adds_to_ring(tmp_log_dir):
    e = fdir_events.emit(Domain.PIPELINE, Severity.WARN, "stale", RecoveryAction.RESTART_PIPELINE, "restarted")
    assert e in fdir_events._ring
    assert len(fdir_events._ring) == 1


def test_emit_with_enum_args(tmp_log_dir):
    """Accepts Enum types directly."""
    e = fdir_events.emit(Domain.JANUS, Severity.ERROR, "sig", RecoveryAction.RESTART_JANUS, "out")
    assert e.domain == "janus"
    assert e.severity == "error"
    assert e.recovery_action == "restart_janus"


def test_emit_with_string_args(tmp_log_dir):
    """Accepts raw strings — backward-compat for callers without Enum import."""
    e = fdir_events.emit("janus", "warn", "sig", "restart_janus", "out")
    assert e.domain == "janus"
    assert e.severity == "warn"


def test_emit_includes_details(tmp_log_dir):
    e = fdir_events.emit(Domain.SYSTEM, Severity.INFO, "boot", RecoveryAction.NONE,
                         "started", details={"version": "1.0", "uptime": 100})
    assert e.details == {"version": "1.0", "uptime": 100}


def test_emit_default_details_empty(tmp_log_dir):
    e = fdir_events.emit(Domain.SYSTEM, Severity.INFO, "x", RecoveryAction.NONE, "y")
    assert e.details == {}


def test_emit_returns_event_instance(tmp_log_dir):
    """emit() returns the created event for chaining."""
    e = fdir_events.emit(Domain.SYSTEM, Severity.INFO, "x", RecoveryAction.NONE, "y")
    assert isinstance(e, FdirEvent)


def test_emit_sets_timestamp_to_now(tmp_log_dir):
    import time
    before = time.time()
    e = fdir_events.emit(Domain.SYSTEM, Severity.INFO, "x", RecoveryAction.NONE, "y")
    after = time.time()
    assert before <= e.timestamp <= after


# ── emit() — log levels ───────────────────────────────────────────────

@pytest.mark.parametrize("severity,expected_level", [
    (Severity.INFO, logging.INFO),
    (Severity.WARN, logging.WARNING),
    (Severity.ERROR, logging.ERROR),
    (Severity.CRITICAL, logging.CRITICAL),
])
def test_emit_logs_at_correct_level(tmp_log_dir, caplog, severity, expected_level):
    with caplog.at_level(logging.INFO, logger="fdir"):
        fdir_events.emit(Domain.SYSTEM, severity, "x", RecoveryAction.NONE, "y")
    rec = [r for r in caplog.records if r.name == "fdir"]
    assert len(rec) >= 1
    assert rec[0].levelno == expected_level


def test_emit_log_message_contains_json(tmp_log_dir, caplog):
    with caplog.at_level(logging.INFO, logger="fdir"):
        fdir_events.emit(Domain.JANUS, Severity.WARN, "test_sig", RecoveryAction.RESTART_JANUS, "ok")
    rec = [r for r in caplog.records if r.name == "fdir"]
    assert any("test_sig" in r.getMessage() for r in rec)


# ── recent() ──────────────────────────────────────────────────────────

def test_recent_returns_newest_first(tmp_log_dir):
    fdir_events.emit(Domain.SYSTEM, Severity.INFO, "first", RecoveryAction.NONE, "x")
    fdir_events.emit(Domain.SYSTEM, Severity.INFO, "second", RecoveryAction.NONE, "x")
    fdir_events.emit(Domain.SYSTEM, Severity.INFO, "third", RecoveryAction.NONE, "x")
    items = fdir_events.recent(10)
    assert [i["detection_signal"] for i in items] == ["third", "second", "first"]


def test_recent_caps_at_n(tmp_log_dir):
    for i in range(20):
        fdir_events.emit(Domain.SYSTEM, Severity.INFO, f"sig{i}", RecoveryAction.NONE, "x")
    items = fdir_events.recent(5)
    assert len(items) == 5
    # Newest 5
    assert items[0]["detection_signal"] == "sig19"
    assert items[4]["detection_signal"] == "sig15"


def test_recent_default_50(tmp_log_dir):
    """Default n=50."""
    for i in range(100):
        fdir_events.emit(Domain.SYSTEM, Severity.INFO, f"sig{i}", RecoveryAction.NONE, "x")
    items = fdir_events.recent()
    assert len(items) == 50


def test_recent_empty_returns_empty():
    assert fdir_events.recent(10) == []


def test_recent_returns_dicts_not_events(tmp_log_dir):
    fdir_events.emit(Domain.SYSTEM, Severity.INFO, "x", RecoveryAction.NONE, "y")
    items = fdir_events.recent(1)
    assert isinstance(items[0], dict)
    assert items[0]["domain"] == "system"


# ── Ring buffer overflow ──────────────────────────────────────────────

def test_ring_caps_at_RING_MAX():
    """Once ring reaches maxlen, oldest events evicted."""
    # ring is deque(maxlen=RING_MAX). RING_MAX default 500.
    # Use direct ring access to avoid persistence overhead.
    for i in range(fdir_events.RING_MAX + 10):
        fdir_events._ring.append(FdirEvent(
            timestamp=float(i), domain="sys", severity="info",
            detection_signal=f"sig{i}", recovery_action="none", outcome="ok",
        ))
    assert len(fdir_events._ring) == fdir_events.RING_MAX
    # Oldest 10 evicted — newest signal is the last one we appended
    assert fdir_events._ring[-1].detection_signal == f"sig{fdir_events.RING_MAX + 9}"


# ── File persistence ──────────────────────────────────────────────────

def test_emit_writes_jsonl_to_disk(tmp_log_dir):
    fdir_events.emit(Domain.SYSTEM, Severity.INFO, "persist_test", RecoveryAction.NONE, "ok")
    log_file = tmp_log_dir / "fdir.jsonl"
    assert log_file.exists()
    line = log_file.read_text().strip()
    parsed = json.loads(line)
    assert parsed["detection_signal"] == "persist_test"


def test_emit_appends_multiple_lines(tmp_log_dir):
    for i in range(5):
        fdir_events.emit(Domain.SYSTEM, Severity.INFO, f"sig{i}", RecoveryAction.NONE, "ok")
    log_file = tmp_log_dir / "fdir.jsonl"
    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 5
    # Each line valid JSON
    for line in lines:
        json.loads(line)


def test_persist_creates_log_dir(tmp_path, monkeypatch):
    """LOG_DIR is created if it does not exist."""
    new_dir = tmp_path / "fdir" / "deep" / "nested"
    assert not new_dir.exists()
    monkeypatch.setattr(fdir_events, "LOG_DIR", new_dir)
    fdir_events.emit(Domain.SYSTEM, Severity.INFO, "x", RecoveryAction.NONE, "y")
    assert new_dir.exists()
    assert (new_dir / "fdir.jsonl").exists()


def test_persist_silently_swallows_io_errors(monkeypatch):
    """File write failures must not crash emit()."""
    # Unwritable directory
    monkeypatch.setattr(fdir_events, "LOG_DIR", Path("/proc/cannot_write_here"))
    # Should NOT raise
    e = fdir_events.emit(Domain.SYSTEM, Severity.INFO, "x", RecoveryAction.NONE, "y")
    assert e in fdir_events._ring  # ring still works


# ── Rotation ──────────────────────────────────────────────────────────

def test_rotation_triggers_at_max_bytes(tmp_log_dir, monkeypatch):
    """When fdir.jsonl > FDIR_LOG_MAX_BYTES → rotate to fdir.jsonl.1."""
    # Lower threshold for fast test
    monkeypatch.setattr(fdir_events, "_PERSIST_MAX_BYTES", 200)

    # Write enough to exceed threshold
    for i in range(20):
        fdir_events.emit(Domain.SYSTEM, Severity.INFO, f"sig{i}",
                         RecoveryAction.NONE, "ok",
                         details={"padding": "x" * 50})

    backup = tmp_log_dir / "fdir.jsonl.1"
    primary = tmp_log_dir / "fdir.jsonl"
    assert backup.exists()
    assert primary.exists()
    # Backup contains earlier events
    assert backup.read_text().strip() != ""


def test_rotation_keeps_only_one_backup(tmp_log_dir, monkeypatch):
    """Repeated rotation: backup overwritten, no .2 etc."""
    monkeypatch.setattr(fdir_events, "_PERSIST_MAX_BYTES", 200)
    for i in range(50):
        fdir_events.emit(Domain.SYSTEM, Severity.INFO, f"sig{i}",
                         RecoveryAction.NONE, "ok",
                         details={"padding": "x" * 50})

    files = sorted(tmp_log_dir.glob("fdir.jsonl*"))
    # Only fdir.jsonl + fdir.jsonl.1 (no .2, .3, etc.)
    assert {f.name for f in files} == {"fdir.jsonl", "fdir.jsonl.1"}


# ── Concurrency ───────────────────────────────────────────────────────

def test_emit_thread_safe_ring(tmp_log_dir):
    """Many threads emit simultaneously — ring receives all events, no exceptions."""
    import threading
    N = 50

    def worker(tid):
        for i in range(N):
            fdir_events.emit(Domain.SYSTEM, Severity.INFO, f"t{tid}_s{i}",
                             RecoveryAction.NONE, "ok")

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    # Total events: 4 threads × 50 = 200 (ring max 500 — no overflow)
    assert len(fdir_events._ring) == 200

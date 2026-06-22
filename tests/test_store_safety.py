"""Unit tests for the shared store-safety primitives (Cycle 1 — secret/config store safety)."""
from __future__ import annotations

import os

import pytest

from app.services import store_safety as ss


def _raise(exc):
    raise exc


# ── atomic_write_text ──────────────────────────────────────────────────

def test_atomic_write_text_writes_content_no_tmp_left(tmp_path):
    p = tmp_path / "f.txt"
    ss.atomic_write_text(p, "hello\n")
    assert p.read_text() == "hello\n"
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_text_applies_mode_before_visible(tmp_path):
    p = tmp_path / "secret.env"
    ss.atomic_write_text(p, "K=V\n", mode=0o600)
    assert (p.stat().st_mode & 0o777) == 0o600


def test_atomic_write_text_fsyncs_file_and_dir(tmp_path, monkeypatch):
    """Durability: both the temp file fd AND the parent-dir fd are fsync'd (the rename is durable)."""
    seen = []
    real = os.fsync
    monkeypatch.setattr(ss.os, "fsync", lambda fd: (seen.append(fd), real(fd))[1])
    ss.atomic_write_text(tmp_path / "f.txt", "x")
    assert len(seen) >= 2


def test_atomic_write_text_cleans_tmp_on_error(tmp_path, monkeypatch):
    monkeypatch.setattr(ss.os, "replace", lambda *a: _raise(OSError("boom")))
    with pytest.raises(OSError):
        ss.atomic_write_text(tmp_path / "f.txt", "x")
    assert not list(tmp_path.glob("*.tmp"))


# ── quarantine_corrupt ──────────────────────────────────────────────────

def test_quarantine_forensic_copy_and_leaves_original(tmp_path):
    p = tmp_path / "store.json"
    p.write_text("{bad json")
    q = ss.quarantine_corrupt(p, "invalid JSON")
    assert q is not None and q.exists()
    assert q.read_text() == "{bad json"                       # forensic copy keeps the bad bytes
    assert p.exists() and p.read_text() == "{bad json"        # original LEFT in place (stays detectable)


def test_quarantine_is_idempotent(tmp_path):
    p = tmp_path / "store.json"
    p.write_text("garbage")
    q1 = ss.quarantine_corrupt(p, "x")
    q2 = ss.quarantine_corrupt(p, "x")
    assert q1 == q2
    assert len(list(tmp_path.glob("store.json.corrupt.*"))) == 1   # not spammed


def test_quarantine_best_effort_returns_none_on_failure(tmp_path, monkeypatch):
    p = tmp_path / "store.json"
    p.write_text("x")
    monkeypatch.setattr(ss.shutil, "copy2", lambda *a: _raise(OSError("ro")))
    assert ss.quarantine_corrupt(p, "x") is None                  # never raises

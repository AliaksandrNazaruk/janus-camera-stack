"""Unit tests for turn_probe module.

Mocks subprocess.run + shutil.which. Verifies probe returns structured
dict with stun_ok / turn_alloc_ok / mapped_address / error_detail.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from app.services.turn_probe import probe, probe_summary


# ── Helpers ───────────────────────────────────────────────────────────

def _mock_run(stunclient_result, uclient_result=None):
    """Return a side_effect for subprocess.run based on cmd path."""
    def side_effect(cmd, **kwargs):
        if "turnutils_stunclient" in cmd[0]:
            return stunclient_result
        if "turnutils_uclient" in cmd[0]:
            return uclient_result or MagicMock(returncode=1, stdout="", stderr="not set")
        raise FileNotFoundError(cmd[0])
    return side_effect


def _completed(stdout="", stderr="", returncode=0):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


# ── Tools missing ─────────────────────────────────────────────────────

def test_returns_tools_unavailable_if_stunclient_missing():
    with patch("app.services.turn_probe.shutil.which", side_effect=lambda p: None):
        r = probe(turn_host="turn.example.com")
    assert r["ok"] is False
    assert r["tools_available"] is False
    assert "coturn" in (r["error"] or "")


# ── STUN-only probe (no TURN credentials) ─────────────────────────────

def test_stun_success_without_credentials_returns_stun_ok_but_not_overall():
    """STUN works → stun_ok=True. But without credentials, turn_alloc_ok=False → ok=False."""
    stun_ok = _completed(
        stdout="UDP reflexive addr: 203.0.113.42:54321\nLocal address: 127.0.0.1:5000\n"
    )
    with patch("app.services.turn_probe.shutil.which", return_value="/usr/bin/stub"), \
         patch("app.services.turn_probe.subprocess.run", side_effect=_mock_run(stun_ok)):
        r = probe(turn_host="turn.example.com")
    assert r["stun_ok"] is True
    assert r["turn_alloc_ok"] is False
    assert r["ok"] is False  # alloc not verified
    assert "no" in (r["error"] or "").lower() or "credentials" in (r["error"] or "").lower()


def test_stun_failure_returns_stun_not_ok():
    stun_fail = _completed(stderr="Cannot connect to STUN server", returncode=1)
    with patch("app.services.turn_probe.shutil.which", return_value="/usr/bin/stub"), \
         patch("app.services.turn_probe.subprocess.run", side_effect=_mock_run(stun_fail)):
        r = probe(turn_host="turn.example.com")
    assert r["stun_ok"] is False
    assert r["ok"] is False
    assert "STUN binding failed" in (r["error"] or "")


def test_stun_timeout_returns_structured_error():
    def timeout_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 5))
    with patch("app.services.turn_probe.shutil.which", return_value="/usr/bin/stub"), \
         patch("app.services.turn_probe.subprocess.run", side_effect=timeout_run):
        r = probe(turn_host="turn.example.com", stun_timeout=3)
    assert r["stun_ok"] is False
    assert r["ok"] is False
    assert "timeout" in (r["error"] or "").lower()


# ── Full probe with credentials ──────────────────────────────────────────

def test_full_success_stun_plus_turn_alloc():
    """Both STUN binding + TURN allocate succeed → ok=True."""
    stun = _completed(stdout="UDP reflexive addr: 1.2.3.4:5000")
    uclient = _completed(
        stdout="tot_send_bytes=1024\nTotal transmit time=0.123s\nAlloc: success"
    )
    with patch("app.services.turn_probe.shutil.which", return_value="/usr/bin/stub"), \
         patch("app.services.turn_probe.subprocess.run", side_effect=_mock_run(stun, uclient)):
        r = probe(
            turn_host="turn.example.com",
            turn_user="webrtc",
            turn_password="secret",
        )
    assert r["stun_ok"] is True
    assert r["turn_alloc_ok"] is True
    assert r["ok"] is True
    assert r["error"] is None


def test_turn_alloc_failure_with_valid_stun():
    """STUN works, but TURN allocate fails (auth error) → ok=False."""
    stun = _completed(stdout="UDP reflexive addr: 1.2.3.4:5000")
    uclient = _completed(
        returncode=1,
        stderr="401 Unauthorized: wrong credentials",
    )
    with patch("app.services.turn_probe.shutil.which", return_value="/usr/bin/stub"), \
         patch("app.services.turn_probe.subprocess.run", side_effect=_mock_run(stun, uclient)):
        r = probe(
            turn_host="turn.example.com",
            turn_user="webrtc",
            turn_password="bad",
        )
    assert r["stun_ok"] is True
    assert r["turn_alloc_ok"] is False
    assert r["ok"] is False
    assert "TURN allocation failed" in (r["error"] or "")
    assert "401" in (r["error_detail"] or "")


def test_turn_alloc_timeout():
    stun = _completed(stdout="UDP reflexive addr: 1.2.3.4:5000")
    def side_effect(cmd, **kw):
        if "stunclient" in cmd[0]:
            return stun
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 8))
    with patch("app.services.turn_probe.shutil.which", return_value="/usr/bin/stub"), \
         patch("app.services.turn_probe.subprocess.run", side_effect=side_effect):
        r = probe(
            turn_host="turn.example.com",
            turn_user="u", turn_password="p",
            turn_timeout=2,
        )
    assert r["stun_ok"] is True
    assert r["turn_alloc_ok"] is False
    assert "timeout" in (r["error"] or "").lower()


# ── mapped_address extraction ─────────────────────────────────────────

def test_mapped_address_extracted_from_stun_output():
    stun = _completed(stdout="UDP reflexive addr: 203.0.113.42:54321\n")
    with patch("app.services.turn_probe.shutil.which", return_value="/usr/bin/stub"), \
         patch("app.services.turn_probe.subprocess.run", side_effect=_mock_run(stun)):
        r = probe(turn_host="turn.example.com")
    assert r["mapped_address"] is not None
    assert "203.0.113.42" in r["mapped_address"]


# ── probe_summary wrapper ─────────────────────────────────────────────

def test_probe_summary_never_raises():
    """Wrapper consumed by health endpoint MUST never raise."""
    with patch("app.services.turn_probe.shutil.which", side_effect=RuntimeError("kaboom")):
        r = probe_summary(turn_host="turn.example.com")
    # Should return a dict, not raise
    assert isinstance(r, dict)
    assert r["ok"] is False

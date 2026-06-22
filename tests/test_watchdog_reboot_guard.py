"""G5.1d — shared-Janus reboot guard (UNIFIED_FDIR §4.5, review B1).

A Janus admin-probe failure must escalate toward JANUS recovery (→ reboot) ONLY
when the local stream is not independently confirmed alive. The guard uses the
color snapshot's monotonic freshness — independent of the (possibly wedged)
Janus admin API — and can only ever SUPPRESS a reboot when cam10 is provably
alive, never weaken real recovery.
"""
from __future__ import annotations

import os
import sys
import time

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.services import watchdogs as wd


class _Settings:
    def __init__(self, enabled=True, stale_ms=10000, mount_id=1305, color_id=1305,
                 snapshot_path="/nonexistent/color-snapshot.jpg"):
        self.snapshot_watchdog_enabled = enabled
        self.watchdog_stale_ms = stale_ms
        self.janus_mount_id = mount_id          # G5.4 cross-sensor guard inputs
        self.janus_color_stream_id = color_id
        self.snapshot_path = snapshot_path      # G5.4: statted directly (default absent → use seed)


def _fresh(monkeypatch, *, enabled=True, stale_ms=10000, age_sec=0.0):
    monkeypatch.setattr(wd, "get_settings", lambda: _Settings(enabled, stale_ms))
    monkeypatch.setattr(wd, "_last_mtime_change_mono", time.monotonic() - age_sec)


def test_not_alive_when_snapshot_watchdog_disabled(monkeypatch):
    _fresh(monkeypatch, enabled=False, age_sec=0.0)   # fresh, but watchdog off
    assert wd._local_stream_recently_alive() is False


def test_not_alive_when_never_seeded(monkeypatch):
    monkeypatch.setattr(wd, "get_settings", lambda: _Settings())
    monkeypatch.setattr(wd, "_last_mtime_change_mono", 0.0)
    assert wd._local_stream_recently_alive() is False


def test_alive_when_snapshot_fresh(monkeypatch):
    _fresh(monkeypatch, stale_ms=10000, age_sec=1.0)   # 1s old ≤ 10s
    assert wd._local_stream_recently_alive() is True


def test_alive_via_direct_snapshot_stat_without_watchdog_seed(tmp_path, monkeypatch):
    # G5.4 robustness: a FRESH snapshot file makes the guard return True even when the
    # snapshot watchdog never seeded _last_mtime_change_mono (the spurious-burst cause).
    snap = tmp_path / "color-snapshot.jpg"
    snap.write_bytes(b"x")                                   # mtime = now
    monkeypatch.setattr(wd, "get_settings", lambda: _Settings(snapshot_path=str(snap)))
    monkeypatch.setattr(wd, "_last_mtime_change_mono", 0.0)  # watchdog NOT seeding
    assert wd._local_stream_recently_alive() is True


def test_not_alive_when_snapshot_file_stale_and_unseeded(tmp_path, monkeypatch):
    snap = tmp_path / "color-snapshot.jpg"
    snap.write_bytes(b"x")
    old = time.time() - 100                                  # 100s old > 10s stale
    os.utime(snap, (old, old))
    monkeypatch.setattr(wd, "get_settings", lambda: _Settings(snapshot_path=str(snap)))
    monkeypatch.setattr(wd, "_last_mtime_change_mono", 0.0)
    assert wd._local_stream_recently_alive() is False


def test_not_alive_when_probing_non_color_mount(monkeypatch):
    # cross-sensor guard (G5.4 / review HIGH-2): the COLOR snapshot is not a liveness
    # proxy for a non-color probed mountpoint, even when the snapshot is fresh.
    monkeypatch.setattr(wd, "get_settings",
                        lambda: _Settings(mount_id=1306, color_id=1305))
    monkeypatch.setattr(wd, "_last_mtime_change_mono", time.monotonic())   # fresh
    assert wd._local_stream_recently_alive() is False


def test_not_alive_when_snapshot_stale(monkeypatch):
    _fresh(monkeypatch, stale_ms=10000, age_sec=100.0)  # 100s old > 10s
    assert wd._local_stream_recently_alive() is False


def test_escalation_allowed_is_inverse_of_alive(monkeypatch):
    _fresh(monkeypatch, age_sec=1.0)                    # alive
    assert wd._janus_exception_escalation_allowed() is False   # suppress reboot path
    _fresh(monkeypatch, age_sec=100.0)                 # dead
    assert wd._janus_exception_escalation_allowed() is True    # real local outage → escalate

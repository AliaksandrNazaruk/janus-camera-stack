"""Cross-process per-(serial,sensor) flock in sensor_lifecycle (review C2).

The lock serialises initialize()/stop() across the admin route, the boot
reconciler and the local FDIR recovery adapter (separate processes)."""
from __future__ import annotations

import fcntl
import os
import sys
from pathlib import Path

import pytest

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

# The lock + its config (_SENSOR_LOCK_DIR/_TIMEOUT) live in the pipeline submodule after the
# Phase 4 package split — patch at the source there (the facade re-exports the same objects).
from app.services.sensor_lifecycle import pipeline as sl


def test_sensor_lock_acquires_and_releases(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "_SENSOR_LOCK_DIR", tmp_path)
    with sl._sensor_lock("SER", "color"):
        pass
    # released → a second acquisition must succeed (no leak)
    with sl._sensor_lock("SER", "color"):
        pass


def test_sensor_lock_blocks_then_times_out(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "_SENSOR_LOCK_DIR", tmp_path)
    monkeypatch.setattr(sl, "_SENSOR_LOCK_TIMEOUT", 0.5)
    # Hold the same lock file via a separate fd (flock treats fds independently —
    # this faithfully simulates a second process holding it).
    lock_path = tmp_path / "sensor-lifecycle-SER-color.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        with pytest.raises(sl.LifecycleError):
            with sl._sensor_lock("SER", "color"):
                pass
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_sensor_lock_degrades_when_dir_unwritable(monkeypatch):
    # Lock dir unwritable (restricted env) → degrade to no-lock, never break.
    monkeypatch.setattr(sl, "_SENSOR_LOCK_DIR", Path("/dev/null/cannot-mkdir"))
    with sl._sensor_lock("SER", "color"):
        pass


def test_distinct_sensors_do_not_block_each_other(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "_SENSOR_LOCK_DIR", tmp_path)
    monkeypatch.setattr(sl, "_SENSOR_LOCK_TIMEOUT", 0.5)
    # Holding color must NOT block depth (per-(serial,sensor) granularity).
    with sl._sensor_lock("SER", "color"):
        with sl._sensor_lock("SER", "depth"):
            pass

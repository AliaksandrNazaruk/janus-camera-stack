"""Tests for app.tools.sensor_reconcile boot-time reconciler.

Mocks sensor_lifecycle.initialize so tests run without production deps
(no encoder-admin, no Janus, no D435). Verifies decision logic:
  • Seed-on-empty: missing state → color marked desired_active
  • plan() ordering: color first, then sensors alphabetical
  • reconcile_stream skips already-running streams (idempotent)
  • Failed initialize logs error but doesn't stop subsequent streams
  • dry-run does not call initialize()
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.services import mountpoint_allocator as alloc_mod
from app.services.mountpoint_allocator import (
    COLOR_MP_ID,
    COLOR_RTP_PORT,
    LOCAL_SERIAL,
)
from app.tools import sensor_reconcile


@pytest.fixture
def state_path(tmp_path):
    return tmp_path / "allocations.json"


# ── parse_key ─────────────────────────────────────────────────────────

def test_parse_key_simple():
    assert sensor_reconcile.parse_key("141722072135:depth") == ("141722072135", "depth")


def test_parse_key_local_color():
    assert sensor_reconcile.parse_key("local:color") == ("local", "color")


def test_parse_key_raises_on_malformed():
    with pytest.raises(ValueError, match="malformed"):
        sensor_reconcile.parse_key("just_a_sensor")


# ── seed_if_empty ─────────────────────────────────────────────────────

def test_seed_if_empty_creates_color_on_missing_state(state_path):
    assert not state_path.exists()
    seeded = sensor_reconcile.seed_if_empty(state_path)
    assert seeded is True

    alloc = alloc_mod.get_allocation(LOCAL_SERIAL, "color", state_path=state_path)
    assert alloc is not None
    assert alloc.mp_id == COLOR_MP_ID
    assert alloc.rtp_port == COLOR_RTP_PORT
    assert alloc.desired_active is True


def test_seed_if_empty_noop_on_existing_state(state_path):
    """Production has 3 existing allocations — seed must NOT auto-enable
    color to avoid changing operator's recorded intent."""
    alloc_mod.allocate("S1", "depth", state_path=state_path)
    seeded = sensor_reconcile.seed_if_empty(state_path)
    assert seeded is False

    # Color allocation NOT injected
    color = alloc_mod.get_allocation(LOCAL_SERIAL, "color", state_path=state_path)
    assert color is None


# ── plan() ordering ───────────────────────────────────────────────────

def test_plan_orders_color_first(state_path):
    """Color must come first so it's not blocked by D435 init time."""
    alloc_mod.allocate("S1", "ir1", state_path=state_path)
    alloc_mod.set_desired("S1", "ir1", True, state_path=state_path)
    alloc_mod.allocate("S1", "depth", state_path=state_path)
    alloc_mod.set_desired("S1", "depth", True, state_path=state_path)
    alloc_mod.ensure(LOCAL_SERIAL, "color", COLOR_MP_ID, COLOR_RTP_PORT, state_path=state_path)
    alloc_mod.set_desired(LOCAL_SERIAL, "color", True, state_path=state_path)

    ordered = list(sensor_reconcile.plan(state_path).keys())
    assert ordered[0].endswith(":color")
    # Remaining are sorted alphabetically
    assert ordered[1:] == sorted(ordered[1:])


def test_plan_excludes_desired_false(state_path):
    alloc_mod.allocate("S1", "depth", state_path=state_path)
    alloc_mod.allocate("S1", "ir1", state_path=state_path)
    alloc_mod.set_desired("S1", "depth", True, state_path=state_path)
    # ir1 left at False

    keys = list(sensor_reconcile.plan(state_path).keys())
    assert "S1:depth" in keys
    assert "S1:ir1" not in keys


def test_plan_empty_when_nothing_desired(state_path):
    alloc_mod.allocate("S1", "depth", state_path=state_path)
    assert sensor_reconcile.plan(state_path) == {}


# ── reconcile_stream ──────────────────────────────────────────────────

def test_reconcile_stream_skips_already_running():
    with patch("app.tools.sensor_reconcile.sensor_lifecycle") as lc:
        lc.is_running.return_value = True
        ok, msg = sensor_reconcile.reconcile_stream("S1", "depth", dry_run=False)
    assert ok is True
    assert "already active" in msg
    lc.initialize.assert_not_called()


def test_reconcile_stream_calls_initialize_when_inactive():
    with patch("app.tools.sensor_reconcile.sensor_lifecycle") as lc:
        lc.is_running.return_value = False
        lc.initialize.return_value = (True, "started ok", MagicMock())
        ok, msg = sensor_reconcile.reconcile_stream("S1", "depth", dry_run=False)
    assert ok is True
    assert "started" in msg
    lc.initialize.assert_called_once_with("S1", "depth")


def test_reconcile_stream_dry_run_skips_initialize():
    with patch("app.tools.sensor_reconcile.sensor_lifecycle") as lc:
        lc.is_running.return_value = False
        ok, msg = sensor_reconcile.reconcile_stream("S1", "depth", dry_run=True)
    assert ok is True
    assert "dry-run" in msg
    lc.initialize.assert_not_called()


def test_reconcile_stream_logs_failure_returns_false():
    # Need actual exception class to match against
    from app.services import sensor_lifecycle as real_lc

    def raise_lifecycle_error(*_a, **_kw):
        raise real_lc.LifecycleError("encoder-admin failed")

    with patch("app.tools.sensor_reconcile.sensor_lifecycle") as lc:
        lc.is_running.return_value = False
        lc.LifecycleError = real_lc.LifecycleError
        lc.UnsupportedSensor = real_lc.UnsupportedSensor
        lc.initialize.side_effect = raise_lifecycle_error
        ok, msg = sensor_reconcile.reconcile_stream("S1", "depth", dry_run=False)
    assert ok is False
    assert "FAILED" in msg


# ── main() entrypoint ─────────────────────────────────────────────────

def test_main_seeds_then_starts_color(state_path):
    """First boot scenario: no state file → seed color=ON → start it."""
    with patch("app.tools.sensor_reconcile.sensor_lifecycle") as lc:
        lc.is_running.return_value = False
        lc.initialize.return_value = (True, "color running", MagicMock())
        rc = sensor_reconcile.main([
            "--state-path", str(state_path),
        ])
    assert rc == 0
    lc.initialize.assert_called_once_with(LOCAL_SERIAL, "color")


def test_main_existing_state_starts_only_desired(state_path):
    """Production scenario: 3 existing allocations, none marked desired.
    Reconciler must NOT auto-enable anything."""
    alloc_mod.allocate("S1", "depth", state_path=state_path)
    alloc_mod.allocate("S1", "ir1", state_path=state_path)
    alloc_mod.allocate("S1", "ir2", state_path=state_path)

    with patch("app.tools.sensor_reconcile.sensor_lifecycle") as lc:
        lc.is_running.return_value = False
        rc = sensor_reconcile.main(["--state-path", str(state_path)])
    assert rc == 0
    lc.initialize.assert_not_called()


def test_main_no_seed_flag_skips_seeding(state_path):
    """--no-seed: missing state → exit without doing anything."""
    with patch("app.tools.sensor_reconcile.sensor_lifecycle") as lc:
        rc = sensor_reconcile.main([
            "--state-path", str(state_path),
            "--no-seed",
        ])
    assert rc == 0
    lc.initialize.assert_not_called()
    # State file must remain not created
    assert not state_path.exists()


def test_main_dry_run_no_side_effects(state_path):
    alloc_mod.ensure(LOCAL_SERIAL, "color", COLOR_MP_ID, COLOR_RTP_PORT, state_path=state_path)
    alloc_mod.set_desired(LOCAL_SERIAL, "color", True, state_path=state_path)

    with patch("app.tools.sensor_reconcile.sensor_lifecycle") as lc:
        lc.is_running.return_value = False
        rc = sensor_reconcile.main([
            "--state-path", str(state_path),
            "--dry-run",
        ])
    assert rc == 0
    lc.initialize.assert_not_called()


def test_main_partial_failure_still_exits_zero(state_path):
    """Failed init for one stream must NOT block siblings, and exit code
    stays 0 (boot continues, journal has errors for operator)."""
    from app.services import sensor_lifecycle as real_lc

    alloc_mod.allocate("S1", "depth", state_path=state_path)
    alloc_mod.set_desired("S1", "depth", True, state_path=state_path)
    alloc_mod.allocate("S1", "ir1", state_path=state_path)
    alloc_mod.set_desired("S1", "ir1", True, state_path=state_path)

    def init_fail_depth(serial, sensor):
        if sensor == "depth":
            raise real_lc.LifecycleError("depth pipeline broken")
        return (True, f"{sensor} running", MagicMock())

    with patch("app.tools.sensor_reconcile.sensor_lifecycle") as lc:
        lc.is_running.return_value = False
        lc.LifecycleError = real_lc.LifecycleError
        lc.UnsupportedSensor = real_lc.UnsupportedSensor
        lc.initialize.side_effect = init_fail_depth
        rc = sensor_reconcile.main(["--state-path", str(state_path), "--no-seed"])

    assert rc == 0  # partial failure → still 0
    # ir1 still got tried despite depth failing
    sensors_tried = [call.args[1] for call in lc.initialize.call_args_list]
    assert "depth" in sensors_tried
    assert "ir1" in sensors_tried

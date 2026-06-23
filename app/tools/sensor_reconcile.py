"""Boot-time stream reconciler (Sprint X4).

Reads sensor_allocations.json and brings up exactly those streams marked
`desired_active=True`. Runs at boot as sensor-reconcile.service oneshot,
replacing per-stream systemd `enable` flags as the autostart mechanism.

Invariants:
  • Idempotent — safe to re-run; skips streams already active.
  • Fail-soft — one failed stream does not block siblings; exit code 0
    until all required streams completed (operator gets clear journal log).
  • Seed-on-empty — first deploy on production without state file → only
    color/RGB defaults to ON (mirrors "currently 1 stream" baseline).
  • Audit trail — every decision logged to stdout (journald captures it).

Run manually for debugging:
    cd /home/boris/robot/janus_camera_page
    python3 -m app.tools.sensor_reconcile --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from app.services import mountpoint_allocator as alloc_mod
from app.services import sensor_lifecycle
from app.services.mountpoint_allocator import (
    Allocation,
    COLOR_MP_ID,
    COLOR_RTP_PORT,
    LOCAL_SERIAL,
    DEFAULT_STATE_PATH,
)

log = logging.getLogger("sensor-reconcile")


def parse_key(key: str) -> Tuple[str, str]:
    """Split `<serial>:<sensor>` keys. Reverse of mountpoint_allocator._key()."""
    if ":" not in key:
        raise ValueError(f"malformed allocation key {key!r} — expected <serial>:<sensor>")
    serial, sensor = key.split(":", 1)
    return serial, sensor


def seed_if_empty(state_path: Path) -> bool:
    """First-boot bootstrap: if state file missing/empty, register color
    with desired_active=True. Matches "1 stream baseline" used pre-Sprint X4.

    Returns True if seeded (state was empty), False if existing state preserved.
    """
    existing = alloc_mod.list_allocations(state_path)
    if existing:
        return False
    log.info("seed: empty state — initializing with color=desired_active")
    alloc_mod.ensure(LOCAL_SERIAL, "color", COLOR_MP_ID, COLOR_RTP_PORT, state_path=state_path)
    alloc_mod.set_desired(LOCAL_SERIAL, "color", True, state_path=state_path)
    return True


def plan(state_path: Path) -> Dict[str, Allocation]:
    """Return ordered map of {key: Allocation} reflecting boot intent.

    Order: color first (so it cannot be blocked by depth/IR D435 init time),
    then depth/IR alphabetically. Reconciler honors this for deterministic
    journal logs.
    """
    desired = alloc_mod.list_desired_active(state_path)
    # color first then sorted
    color_keys = sorted(k for k in desired if k.endswith(":color"))
    other_keys = sorted(k for k in desired if not k.endswith(":color"))
    return {k: desired[k] for k in (*color_keys, *other_keys)}


def reconcile_stream(serial: str, sensor: str, dry_run: bool) -> Tuple[bool, str]:
    """Bring (serial, sensor) up if not already running.

    Returns (succeeded, message). dry_run=True → log intent without side effects.
    """
    label = f"{serial}:{sensor}"

    running = sensor_lifecycle.is_running(sensor)
    if running is True:
        return True, f"  ✓ {label} already active — no action"

    if dry_run:
        return True, f"  ⊘ {label} would call lifecycle.initialize() (dry-run)"

    try:
        _ok, msg, _alloc = sensor_lifecycle.initialize(serial, sensor)
        return True, f"  ✓ {label} started — {msg}"
    except sensor_lifecycle.LifecycleError as e:
        return False, f"  ✗ {label} FAILED: {e}"
    except sensor_lifecycle.UnsupportedSensor as e:
        return False, f"  ✗ {label} UNSUPPORTED: {e}"


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Boot-time stream reconciler — starts desired streams from sensor_allocations.json"
    )
    parser.add_argument(
        "--state-path",
        type=Path,
        default=DEFAULT_STATE_PATH,
        help="Path to allocations JSON (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log intent without calling lifecycle.initialize() — safe for inspection",
    )
    parser.add_argument(
        "--no-seed",
        action="store_true",
        help="Skip seed-if-empty behavior (treat missing/empty state as no-op)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    log.info("reconcile: state_path=%s dry_run=%s", args.state_path, args.dry_run)

    if not args.no_seed:
        if seed_if_empty(args.state_path):
            log.info("reconcile: seeded default state (color=ON)")

    desired = plan(args.state_path)
    if not desired:
        log.info("reconcile: no streams marked desired_active=True — exiting")
        return 0

    log.info("reconcile: plan = %s", list(desired.keys()))

    failures = 0
    for key in desired:
        try:
            serial, sensor = parse_key(key)
        except ValueError as e:
            log.error("  ✗ %s SKIPPED: %s", key, e)
            failures += 1
            continue
        ok, msg = reconcile_stream(serial, sensor, args.dry_run)
        log.log(logging.INFO if ok else logging.ERROR, msg)
        if not ok:
            failures += 1

    if failures:
        log.warning("reconcile: completed with %d failure(s)", failures)
        # Exit 0 even on partial failure — boot must continue. Operator
        # sees failures in journal and addresses individually. Exit non-zero
        # only on catastrophic state-read failure (uncaught above).
    log.info("reconcile: done")
    return 0


if __name__ == "__main__":
    sys.exit(main())

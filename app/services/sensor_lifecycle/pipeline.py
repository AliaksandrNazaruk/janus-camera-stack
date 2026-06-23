"""Sensor pipeline lifecycle orchestration.

initialize / stop, the cross-process per-(serial,sensor) flock, and the color-static vs depth/IR-dynamic
branching. Drives the encoder-admin port + the contract-env store + janus_admin/mountpoint_allocator (the
mountpoint port). Extracted verbatim from the monolithic sensor_lifecycle.py (Phase 4 / A-04); the package
__init__ re-exports the public API + these constants so all callers stay unchanged.
"""
from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional, Tuple

from app.services import janus_admin
from app.services.mountpoint_allocator import (
    allocate,
    ensure,
    get_allocation,
    migrate_color_key,
    set_desired,
    Allocation,
    LOCAL_SERIAL,
)
from app.services.sensor_lifecycle.contract_env import (
    _ensure_default_tuning_env,
    _write_contract_env,
)
from app.services.sensor_lifecycle.encoder_admin import _encoder_action, is_running
from app.services.sensor_lifecycle.errors import LifecycleError, UnsupportedSensor

log = logging.getLogger(__name__)

# Static (baseline) mountpoint for color (rgb-rtp in jcfg). Encoder instance =
# "color" (rs-stream@color, mux consumer) since Phase 2 retired rtp-rgb@cam-rgb.
COLOR_MP_ID    = 1305
COLOR_RTP_PORT = 5004
COLOR_ENCODER_INSTANCE = "color"

# Per-sensor metadata: description + RealSense secret entry name
_SENSOR_META = {
    "color": {"label": "RGB Camera",     "secret_key": "cam-rgb"},
    "depth": {"label": "Depth (Z16→RGB)", "secret_key": "cam-depth"},
    "ir1":   {"label": "IR left (Y8)",   "secret_key": "cam-ir1"},
    "ir2":   {"label": "IR right (Y8)",  "secret_key": "cam-ir2"},
}

# Per-mountpoint secret: same admin_key works for list/create, but destroy needs
# the per-mountpoint secret. For now we use the streaming admin_key as fallback
# (Janus accepts admin_key for destroy as well in current versions).
MP_DEFAULT_SECRET = os.getenv("JANUS_STREAMING_ADMIN_KEY", "")


# ── Cross-process pipeline lock (review C2) ─────────────────────────
# initialize()/stop() shell out to encoder-admin (mux + rs-stream@{sensor}) then
# check is_running(). THREE separate processes drive this entrypoint — the admin
# route, the boot reconciler (sensor-reconcile.service) and the local FDIR
# recovery adapter — so an in-process lock is insufficient: two concurrent starts
# race the is_running() check and one raises a spurious LifecycleError. A
# per-(serial,sensor) flock serialises all three. Lock order is sensor-flock
# (outer) → allocator-flock (inner), always, so no deadlock.
_SENSOR_LOCK_DIR = Path(os.getenv("SENSOR_LOCK_DIR", "/run/lock"))
_SENSOR_LOCK_TIMEOUT = float(os.getenv("SENSOR_LOCK_TIMEOUT", "30"))


@contextlib.contextmanager
def _sensor_lock(serial: str, sensor: str):
    """Exclusive per-(serial,sensor) flock around a pipeline mutation. Bounded
    wait → LifecycleError (surfaced to the caller, never silently swallowed).
    Released on every exit path. If the lock dir is unwritable (restricted env)
    we degrade to no-lock + warn rather than break activation."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{serial}-{sensor}")
    lock_path = _SENSOR_LOCK_DIR / f"sensor-lifecycle-{safe}.lock"
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY, 0o644)
    except OSError as e:
        log.warning("sensor lock unavailable (%s) — proceeding without it", e)
        yield
        return
    deadline = time.monotonic() + _SENSOR_LOCK_TIMEOUT
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LifecycleError(
                        f"{serial}:{sensor} busy — another activation/stop held "
                        f"the lock > {_SENSOR_LOCK_TIMEOUT:.0f}s")
                time.sleep(0.2)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# ── Public API ─────────────────────────────────────────────────────

def initialize(serial: str, sensor: str) -> Tuple[bool, str, Optional[Allocation]]:
    """Bring (serial, sensor) pipeline up. Idempotent.

    Returns (running, message, allocation). allocation=None for color
    (static mountpoint), populated dict for dynamic depth/ir/ir2.

    Serialised per (serial, sensor) across the admin route, the boot reconciler
    and the local FDIR recovery adapter via a cross-process flock (review C2).
    """
    meta = _SENSOR_META.get(sensor)
    if not meta:
        raise UnsupportedSensor(f"unknown sensor: {sensor!r}")
    with _sensor_lock(serial, sensor):
        return _initialize_locked(serial, sensor, meta)


def _initialize_locked(serial: str, sensor: str, meta: dict) -> Tuple[bool, str, Optional[Allocation]]:
    """Body of :func:`initialize`, run under the per-(serial,sensor) flock."""
    if sensor == "color":
        # Sprint X4: color participates in desired-state model too. ensure()
        # creates the allocation entry (idempotent) so reconciler sees it,
        # then set_desired(True) marks it ON for boot recovery.
        #
        # FDIR-KEY-001: color uses the canonical <serial>:color key (same identity
        # model as depth/IR), migrating any legacy ``local:color`` entry. The
        # passed serial is the real device serial from the route; boot reconcile
        # re-supplies it from the persisted key, so no live-enumeration dependency.
        # (LOCAL_SERIAL remains only as a backward-compat sentinel if a legacy
        # caller still passes it.)
        #
        # Phase 2: color produced by mux (pyrealsense2) + rs-stream@color consumer.
        # Mountpoint 1305 + port 5004 static (in jcfg). Seed rs-color.{tuning,
        # contract}.env (self-sufficient — no ansible/manual dependency, fresh-host
        # safe), then bring up mux so color.fifo exists before rs-stream@color opens.
        migrate_color_key(serial)
        ensure(serial, "color", COLOR_MP_ID, COLOR_RTP_PORT)
        _ensure_default_tuning_env("color")
        _write_contract_env("color", COLOR_RTP_PORT)
        # Persist intent BEFORE the start attempt: if mux/encoder start fails
        # transiently, desired_active stays True → boot reconciler retries on
        # the next reboot. (Previously set_desired was after start → fail = intent
        # was lost, color did not come up automatically.)
        set_desired(serial, "color", True)
        _encoder_action("start", "realsense-mux")
        _encoder_action("start", "rs-stream", "color")
        running = is_running("color")
        if running is False:
            raise LifecycleError("encoder-admin start returned 0 but color unit not active")
        return True, f"color encoder ({serial}) running", Allocation(COLOR_MP_ID, COLOR_RTP_PORT, desired_active=True)

    # depth | ir1 | ir2 dynamic path
    alloc = allocate(serial, sensor)

    # 1. Mux must be running first (rs-stream@.service Requires=realsense-mux but
    # explicit start tells systemd to bring up the dependency without waiting).
    _encoder_action("start", "realsense-mux")

    # 2a. Write default tuning.env if missing (idempotent — preserves any
    # operator edits on re-Initialize).
    _ensure_default_tuning_env(sensor)
    # 2b. Write contract.env with allocated port (rs-stream.sh reads it).
    _write_contract_env(sensor, alloc.rtp_port)

    # 3. Register dynamic mountpoint in Janus (idempotent — caches "already exists").
    try:
        janus_admin.create_mountpoint(
            mp_id=alloc.mp_id,
            rtp_port=alloc.rtp_port,
            description=f"{meta['label']} · {serial}",
            mp_secret=MP_DEFAULT_SECRET,
        )
    except janus_admin.JanusAdminError as e:
        msg = str(e).lower()
        if "already exists" in msg or "exists" in msg and "1456" in msg:
            log.info("mountpoint %d already exists, reusing", alloc.mp_id)
        else:
            raise LifecycleError(f"Janus create_mountpoint failed: {e}") from e

    # 4. Start the per-sensor ffmpeg consumer.
    _encoder_action("start", "rs-stream", sensor)

    running = is_running(sensor)
    if running is False:
        raise LifecycleError(
            f"rs-stream@{sensor}.service not active after start "
            "(check journalctl -u rs-stream@{sensor})"
        )

    # 5. Wait for real readiness signal (Phase 1 fix — replaces 2sec blind sleep).
    # Poll Janus mountpoint age_ms until media is actively flowing (<500ms = first
    # RTP packets arrived). Bounded to 3sec — return regardless past that, watchdog
    # will catch real failures. Reduces median initialize time under healthy
    # conditions from 2.0sec → ~0.3-0.8sec while still preventing premature 200.
    import time as _t
    deadline = _t.monotonic() + 3.0
    ready = False
    while _t.monotonic() < deadline:
        try:
            mounts = janus_admin.list_mountpoints()
            for m in mounts:
                if int(m.get("id", 0)) != alloc.mp_id:
                    continue
                media = m.get("media", [])
                if media:
                    age_ms = media[0].get("age_ms")
                    if isinstance(age_ms, (int, float)) and age_ms < 500:
                        ready = True
                        break
            if ready:
                break
        except janus_admin.JanusAdminError:
            pass
        _t.sleep(0.1)
    if not ready:
        log.warning("%s pipeline mp_id=%d did not reach steady-state within 3s — "
                    "returning anyway; watchdog will catch real failures",
                    sensor, alloc.mp_id)

    # Sprint X4: mark desired_active=True so boot reconciler resurrects this
    # pipeline on next reboot. Set after pipeline confirmed up so we don't
    # persist intent for streams that failed to start.
    set_desired(serial, sensor, True)

    return True, f"{sensor} pipeline ({serial}) running on mp_id={alloc.mp_id}", alloc


def stop(serial: str, sensor: str) -> Tuple[bool, str]:
    """Stop (serial, sensor) pipeline. Allocation is preserved (stable URLs)."""
    meta = _SENSOR_META.get(sensor)
    if not meta:
        raise UnsupportedSensor(f"unknown sensor: {sensor!r}")
    with _sensor_lock(serial, sensor):
        return _stop_locked(serial, sensor)


def _stop_locked(serial: str, sensor: str) -> Tuple[bool, str]:
    if sensor == "color":
        # Sprint X4: clear desired_active BEFORE the stop attempt so even
        # if encoder-admin transiently fails, intent persists and next
        # reconciler invocation honors operator's choice.
        # FDIR-KEY-001: canonical <serial>:color (migrate any legacy local:color).
        migrate_color_key(serial)
        try:
            set_desired(serial, "color", False)
        except KeyError:
            pass  # color not yet allocated — nothing to persist
        # Phase 2.2: stop rs-stream@color consumer (mux keeps running for
        # depth/ir — color frames written to FIFO with no reader are just dropped,
        # harmless). NOT stopping mux: that would kill depth/ir too.
        _encoder_action("stop", "rs-stream", "color")
        return False, f"color encoder ({serial}) stopped"

    # depth | ir1 | ir2 — stop ffmpeg consumer, destroy Janus mountpoint.
    # Mux daemon stays alive (might have other consumers); operator can stop
    # it explicitly if no consumers active.
    try:
        set_desired(serial, sensor, False)
    except KeyError:
        pass  # never allocated — stop is no-op
    _encoder_action("stop", "rs-stream", sensor)

    alloc = get_allocation(serial, sensor)
    if alloc:
        try:
            janus_admin.destroy_mountpoint(mp_id=alloc.mp_id, mp_secret=MP_DEFAULT_SECRET)
        except janus_admin.JanusAdminError as e:
            log.warning("Janus destroy_mountpoint(%d): %s — proceeding", alloc.mp_id, e)
    # Keep allocation entry so next Initialize gets same ID and preserves
    # operator's recorded desired_active=False state.

    return False, f"{sensor} pipeline ({serial}) stopped"

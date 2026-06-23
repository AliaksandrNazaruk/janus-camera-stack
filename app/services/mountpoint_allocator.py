"""Mountpoint + RTP port allocator (Sprint X3).

Allocates (mountpoint_id, rtp_port) pairs for (serial, sensor) tuples
on demand, persisting state in flock'ed JSON so re-Initialize gives same
IDs (stable URLs across operator restarts).

Pool:
  - mountpoint IDs: 1306..1999 (1305 is the static color baseline)
  - RTP ports:      5006..5099 (5004/5005 are color RTP/RTCP)

State file (versioned, backward-compat on read):
  /var/lib/camera-fdir/sensor_allocations.json
  {
    "version": 1,
    "allocations": {
      "<serial>:<sensor>": {
        "mp_id": int,
        "rtp_port": int,
        "desired_active": bool   # — added Sprint X4 (boot reconciler)
      }
    }
  }

`desired_active` is the source of truth for boot-time stream lifecycle.
sensor-reconcile.service reads this flag and starts only marked streams,
replacing systemd-level `enable` for stream units (rs-stream@, realsense-mux).
Older records without the field load as desired_active=False (safe default —
operator must explicitly toggle to ON).

Concurrency: flock on the file for cross-process safety (uvicorn workers,
encoder-admin CLI invocations, future janus-admin tooling, boot reconciler).
"""
from __future__ import annotations

import fcntl
import glob
import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, Optional, Tuple

from app.services.store_safety import atomic_write_text, quarantine_corrupt

log = logging.getLogger(__name__)


# Pool bounds — keep generous headroom. Janus accepts 32-bit IDs.
MP_ID_MIN  = 1306
MP_ID_MAX  = 1999
PORT_MIN   = 5006
PORT_MAX   = 5099

# Color/RGB is the static baseline (Sprint X3 pre-allocated). Use a
# sentinel serial key so color participates in the same desired-state
# model as dynamic depth/IR allocations.
LOCAL_SERIAL = "local"
COLOR_MP_ID = 1305
COLOR_RTP_PORT = 5004

DEFAULT_STATE_PATH = Path("/var/lib/camera-fdir/sensor_allocations.json")
LOCK_SUFFIX = ".lock"


class AllocationError(RuntimeError):
    """No free slot or persistence failure."""


@dataclass(frozen=True)
class Allocation:
    mp_id: int
    rtp_port: int
    desired_active: bool = False

    def to_dict(self) -> dict:
        return {
            "mp_id": self.mp_id,
            "rtp_port": self.rtp_port,
            "desired_active": self.desired_active,
        }

    @classmethod
    def from_raw(cls, raw: dict) -> "Allocation":
        """Reconstruct from JSON record. Missing desired_active → False
        (backward-compat for records written before Sprint X4)."""
        return cls(
            mp_id=int(raw["mp_id"]),
            rtp_port=int(raw["rtp_port"]),
            desired_active=bool(raw.get("desired_active", False)),
        )


def _key(serial: str, sensor: str) -> str:
    return f"{serial}:{sensor}"


def _alloc_map(state) -> dict:
    """The allocations dict, or {} for any corrupt shape (None / non-dict /
    non-dict state). Read-side fail-safe — never raises, never returns None."""
    if not isinstance(state, dict):
        return {}
    allocs = state.get("allocations")
    return allocs if isinstance(allocs, dict) else {}


def migrate_color_key(serial: str, state_path: Path = DEFAULT_STATE_PATH) -> bool:
    """One-shot legacy migration: ``local:color`` → ``<serial>:color``.

    Color historically used the ``local`` sentinel serial while depth/IR used the
    device serial, giving a heterogeneous identity model (FDIR-KEY-001). Once the
    real device serial is known (route-initiated initialize), fold the legacy
    entry into the canonical key, preserving mp_id/rtp_port/desired_active.
    Idempotent: no-op if there is nothing to migrate or ``serial`` is the sentinel.
    Returns True if a migration was performed.
    """
    if serial == LOCAL_SERIAL or not serial:
        return False
    legacy = _key(LOCAL_SERIAL, "color")
    canonical = _key(serial, "color")
    with _flock_state(state_path) as state:
        allocs = state["allocations"]
        if legacy not in allocs or canonical in allocs:
            return False
        allocs[canonical] = allocs.pop(legacy)
        log.info("migrated legacy color key %s -> %s", legacy, canonical)
        return True


def _load_state_for_write(path: Path) -> dict:
    """Load the allocations state for a MUTATION, recovering from corruption fail-SAFELY
    (Cycle 15A). The READ helpers degrade to {} on any corruption (guard #26); a write must
    likewise never crash on a corrupt file — a truncated allocator would otherwise crash the
    boot reconciler's seed write. Recovery: quarantine a forensic `.corrupt.<ts>` copy (via the
    shared `store_safety` primitive — no new framework), then reset and PROCEED.

      missing / empty file         -> fresh empty state (legitimate cold start)
      valid JSON + valid shape      -> loaded as-is
      invalid JSON                  -> quarantine + reset (was: crash)
      root / `allocations` non-dict -> quarantine + reset (was: silent reset / data loss)
      IO read error (perms/disk)    -> raise AllocationError; do NOT reset (the file may be
                                       perfectly good, just transiently unreadable — a reset
                                       would destroy good data on a glitch).
    """
    state: object = {}
    if path.exists():
        try:
            raw = path.read_text().strip()
        except OSError as e:
            raise AllocationError(f"cannot read allocator state {path}: {e}") from e
        if raw:
            try:
                state = json.loads(raw)
            except json.JSONDecodeError as e:
                quarantine_corrupt(path, f"invalid JSON: {e}")
                state = {}
    if not isinstance(state, dict):
        quarantine_corrupt(path, f"top-level is {type(state).__name__}, not an object")
        state = {}
    state.setdefault("version", 1)
    # A non-dict `allocations` map (incl. legacy `null`) must coerce to an empty map — never
    # propagate None (readers crash) and never persist the bad shape. Quarantine so the reset is
    # not silent data loss; a corrupt allocations map must not read as "nothing is desired"
    # (which would let recovery skip restarting a live encoder).
    if not isinstance(state.get("allocations"), dict):
        quarantine_corrupt(path, f"allocations is {type(state.get('allocations')).__name__}")
        state["allocations"] = {}
    return state


@contextmanager
def _flock_state(path: Path):
    """Open + flock state file, yield (state_dict, file_handle for write)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = Path(str(path) + LOCK_SUFFIX)
    with open(lock_path, "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            state = _load_state_for_write(path)
            yield state
            # Persist back atomically + DURABLY (fsync file + dir — was tmp+replace with no fsync).
            atomic_write_text(path, json.dumps(state, indent=2, sort_keys=True) + "\n")
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


def _pick_free(used_mp: set, used_port: set) -> Tuple[int, int]:
    """Pick lowest free (mp_id, rtp_port). RTP+RTCP occupy a pair (port, port+1)
    so we step by 2 starting from an even base, and check both port + port+1 free.
    """
    mp_id = next((i for i in range(MP_ID_MIN, MP_ID_MAX + 1) if i not in used_mp), None)
    if mp_id is None:
        raise AllocationError(f"mountpoint ID pool exhausted [{MP_ID_MIN}..{MP_ID_MAX}]")
    # RTP convention: even RTP port + odd RTCP. PORT_MIN must already be even.
    port = None
    for p in range(PORT_MIN, PORT_MAX, 2):
        if p not in used_port and (p + 1) not in used_port:
            port = p
            break
    if port is None:
        raise AllocationError(f"RTP port pair pool exhausted [{PORT_MIN}..{PORT_MAX}]")
    return mp_id, port


def get_allocation(serial: str, sensor: str,
                   state_path: Path = DEFAULT_STATE_PATH) -> Optional[Allocation]:
    """Lookup without allocation. Returns None if not yet allocated."""
    if not state_path.exists():
        return None
    try:
        with open(state_path, "r") as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("get_allocation: state read failed: %s", e)
        return None
    raw = _alloc_map(state).get(_key(serial, sensor))
    if raw:
        return Allocation.from_raw(raw)
    return None


def allocate(serial: str, sensor: str,
             state_path: Path = DEFAULT_STATE_PATH) -> Allocation:
    """Get or create allocation for (serial, sensor). Idempotent.

    Re-allocate preserves desired_active flag — operator's choice survives
    repeated initialize() calls. Set the flag explicitly via set_desired().
    """
    with _flock_state(state_path) as state:
        key = _key(serial, sensor)
        existing = state["allocations"].get(key)
        if existing:
            return Allocation.from_raw(existing)
        used_mp = {int(v["mp_id"]) for v in state["allocations"].values()}
        # Each existing allocation reserves RTP + RTCP (rtp_port and rtp_port+1).
        used_port: set = set()
        for v in state["allocations"].values():
            rtp = int(v["rtp_port"])
            used_port.add(rtp); used_port.add(rtp + 1)  # noqa: E702
        mp_id, port = _pick_free(used_mp, used_port)
        alloc = Allocation(mp_id=mp_id, rtp_port=port)
        state["allocations"][key] = alloc.to_dict()
        log.info("allocated %s -> mp_id=%d port=%d", key, mp_id, port)
        return alloc


def ensure(serial: str, sensor: str, mp_id: int, rtp_port: int,
           state_path: Path = DEFAULT_STATE_PATH) -> Allocation:
    """Register a pre-determined (mp_id, rtp_port) allocation idempotently.

    Unlike allocate() which picks free slots from the dynamic pool, ensure()
    accepts caller-supplied IDs — used for static streams (color/RGB on
    1305:5004) so they participate in the same desired-state model as
    dynamic allocations. Preserves desired_active across calls.
    """
    with _flock_state(state_path) as state:
        key = _key(serial, sensor)
        existing = state["allocations"].get(key)
        if existing:
            return Allocation.from_raw(existing)
        # Clobber guard (G1 / review R2-M2): a pre-determined (mp_id, rtp_port)
        # already held by a DIFFERENT key must NOT be silently re-pinned. The
        # known trigger is the `serial="unknown"` discovery hiccup, which would
        # otherwise create a second "*:color" row on 1305/5004 — two rows owning
        # the live stream's mountpoint. Fail closed instead.
        for other_key, v in state["allocations"].items():
            o_mp, o_rtp = int(v["mp_id"]), int(v["rtp_port"])
            if o_mp == mp_id:
                raise AllocationError(
                    f"mp_id {mp_id} already held by {other_key}; refusing to clobber for {key}")
            if {rtp_port, rtp_port + 1} & {o_rtp, o_rtp + 1}:
                raise AllocationError(
                    f"RTP port pair ({rtp_port},{rtp_port + 1}) overlaps {other_key} "
                    f"({o_rtp},{o_rtp + 1}); refusing to clobber for {key}")
        alloc = Allocation(mp_id=mp_id, rtp_port=rtp_port)
        state["allocations"][key] = alloc.to_dict()
        log.info("ensured %s -> mp_id=%d port=%d (static)", key, mp_id, rtp_port)
        return alloc


def set_desired(serial: str, sensor: str, active: bool,
                state_path: Path = DEFAULT_STATE_PATH) -> Allocation:
    """Toggle desired_active flag on an existing allocation.

    Raises KeyError if no allocation exists for (serial, sensor) — caller
    must allocate() or ensure() first. This separation lets us audit
    desired-state changes without implicit allocation side effects.
    """
    with _flock_state(state_path) as state:
        key = _key(serial, sensor)
        raw = state["allocations"].get(key)
        if not raw:
            raise KeyError(f"no allocation for {key} — call allocate()/ensure() first")
        updated = replace(Allocation.from_raw(raw), desired_active=bool(active))
        state["allocations"][key] = updated.to_dict()
        log.info("set_desired %s -> %s", key, active)
        return updated


def release(serial: str, sensor: str,
            state_path: Path = DEFAULT_STATE_PATH) -> bool:
    """Drop allocation entry. Returns True if removed."""
    with _flock_state(state_path) as state:
        key = _key(serial, sensor)
        if key not in state["allocations"]:
            return False
        del state["allocations"][key]
        log.info("released %s", key)
        return True


def list_allocations(state_path: Path = DEFAULT_STATE_PATH) -> Dict[str, Allocation]:
    """Snapshot of all current allocations (for registry display)."""
    if not state_path.exists():
        return {}
    try:
        with open(state_path, "r") as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return {
        k: Allocation.from_raw(v)
        for k, v in _alloc_map(state).items()
    }


def list_desired_active(state_path: Path = DEFAULT_STATE_PATH) -> Dict[str, Allocation]:
    """Subset of allocations with desired_active=True — boot reconciler entry point."""
    return {k: a for k, a in list_allocations(state_path).items() if a.desired_active}


def allocator_corruption_status(state_path: Path = DEFAULT_STATE_PATH) -> dict:
    """Probe the allocator state file WITHOUT raising — for health/diagnostics surfaces
    (Cycle 14A). Companion to ``stream_binding_store.store_corruption_status``.

    The read API (``list_allocations`` / ``get_allocation`` / ``list_desired_active``) is
    deliberately fail-SAFE: corruption degrades to empty so live encoder streams are never
    torn down (Cycle 1, fitness guard #18 excludes this store). The cost is that a corrupt
    allocator is INDISTINGUISHABLE from a legitimately-empty one on every read surface — so
    a corrupt file silently reads as "no desired active streams." This probe restores that
    distinction for OBSERVABILITY only; it changes no read path and not the fail-safe behavior.

    Returns ``{"allocator_state": "ok"|"missing"|"corrupt"|"io_error"}`` plus an
    ``"allocator_detail"`` string for the non-ok content/IO cases. ``missing`` (no file yet —
    legitimate cold start) is NOT corruption. Unlike the topology store this is deliberately
    NON-FATAL to readiness: a corrupt allocator keeps live streams running, so callers surface
    it as degraded, never as a 503. When a prior write-time recovery left a forensic copy, the
    latest ``<path>.corrupt.<ts>`` is reported as ``"quarantine"`` even once the live file is
    valid again (Cycle 15A, D2) — so a silent recovery stays discoverable."""
    status = _classify_allocator_state(state_path)
    q = sorted(glob.glob(str(state_path) + ".corrupt.*"))
    if q:
        status["quarantine"] = q[-1]
    return status


def _classify_allocator_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {"allocator_state": "missing"}
    try:
        with open(state_path, "r") as f:
            raw = f.read().strip()
    except OSError as e:
        return {"allocator_state": "io_error", "allocator_detail": str(e)[:200]}
    if not raw:
        return {"allocator_state": "ok"}        # empty file == empty allocations (cold start)
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as e:
        return {"allocator_state": "corrupt", "allocator_detail": str(e)[:200]}
    # Valid JSON but a shape the read path silently coerces to {} (and the next write would
    # reset) — null/non-dict allocations. Surface it as corrupt so it is not mistaken for empty.
    if not isinstance(state, dict) or not isinstance(state.get("allocations", {}), dict):
        return {"allocator_state": "corrupt",
                "allocator_detail": "allocations is not an object"}
    return {"allocator_state": "ok"}

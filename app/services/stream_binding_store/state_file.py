"""Topology state-file persistence + corruption handling (R2+R3) for the stream_binding_store
package (Phase 13B, D2). Owns the single JSON state file, its flock, and the atomic write, plus the
fail-closed corruption path: a malformed file is quarantined (forensic copy) and StoreCorruptionError
is raised rather than silently degrading to an empty topology — a silent reset would let the
reconciler treat a wiped fleet as desired and tear down live bindings (review H-02).

Leaf module: depends only on stdlib (imports nothing from the package). The node/binding logic in the
facade (__init__) calls `_flock_state` / `_load_state`; the facade re-exports DEFAULT_STATE_PATH,
StoreCorruptionError, and store_corruption_status for all callers. Moved verbatim from the original
module; no behavior change."""
from __future__ import annotations

import fcntl
import glob
import json
import logging
import os
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_STATE_PATH = Path(
    os.environ.get("CAM_STREAM_BINDINGS_PATH", "/var/lib/camera-fdir/stream_bindings.json")
)
LOCK_SUFFIX = ".lock"


class StoreCorruptionError(RuntimeError):
    """The topology store exists but is unparseable / malformed.

    Fail closed: reads AND mutations raise this rather than silently degrading to an
    empty topology — a silent reset would let the reconciler treat a wiped fleet as
    desired and tear down live bindings (review H-02). The corrupt file is preserved
    in place and a timestamped ``<path>.corrupt.<ts>`` forensic copy is made; recovery
    is operator-driven (fix or restore the file and ops resume automatically)."""


def _quarantine_corrupt_state(path: Path, reason: str) -> Optional[Path]:
    """Make ONE timestamped forensic copy of a corrupt store file (idempotent — a
    repeated corrupt read does not spam copies). The original is deliberately LEFT in
    place so corruption stays detectable across restarts until an operator fixes it."""
    existing = sorted(glob.glob(str(path) + ".corrupt.*"))
    if existing:
        return Path(existing[-1])
    qpath = Path(f"{path}.corrupt.{time.strftime('%Y%m%d_%H%M%S')}")
    try:
        shutil.copy2(path, qpath)
        log.critical("topology store CORRUPT (%s) — quarantined forensic copy at %s", reason, qpath)
        return qpath
    except OSError as e:
        log.critical("topology store CORRUPT (%s) — quarantine copy failed: %s", reason, e)
        return None


def _normalize_state(state: dict) -> dict:
    state.setdefault("version", 1)
    if not isinstance(state.get("nodes"), dict):
        state["nodes"] = {}
    if not isinstance(state.get("bindings"), dict):
        state["bindings"] = {}
    return state


def _load_state(path: Path) -> dict:
    """Read the topology store, FAILING CLOSED on corruption.

    Absent or empty file -> a well-formed empty shape (a legitimate first run).
    A file that exists with NON-empty content that is unparseable or not a JSON
    object is corruption: quarantine it and raise StoreCorruptionError so callers
    fail loud instead of silently operating on (or persisting) an empty fleet."""
    if not path.exists():
        return _normalize_state({})
    try:
        raw = path.read_text().strip()
    except OSError as e:
        _quarantine_corrupt_state(path, f"read error: {e}")
        raise StoreCorruptionError(f"cannot read topology store {path}: {e}") from e
    if not raw:
        return _normalize_state({})                 # empty file == empty topology (normal)
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as e:
        q = _quarantine_corrupt_state(path, f"invalid JSON: {e}")
        raise StoreCorruptionError(
            f"topology store {path} is not valid JSON ({e}); quarantined {q}") from e
    if not isinstance(state, dict):
        q = _quarantine_corrupt_state(path, "top-level is not a JSON object")
        raise StoreCorruptionError(
            f"topology store {path} top-level is not a JSON object; quarantined {q}")
    return _normalize_state(state)


@contextmanager
def _flock_state(path: Path):
    """Open + flock the store file, yield the state dict, persist atomically.

    This file (stream_bindings.json) is NON-secret topology — per-node agent
    tokens live in a separate 0600 ``node_secrets.json`` (review H3), so this
    stays readable (non-root topology/allocation reads must keep working)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = Path(str(path) + LOCK_SUFFIX)
    with open(lock_path, "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            state = _load_state(path)
            yield state
            tmp = path.with_suffix(path.suffix + ".tmp")
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


def store_corruption_status(state_path: Path = DEFAULT_STATE_PATH) -> dict:
    """Probe the topology store WITHOUT raising — for health / diagnostics surfaces.
    Returns ``{"topology_store_corrupt": bool, "detail"?, "quarantine"?}``."""
    try:
        _load_state(state_path)
        return {"topology_store_corrupt": False}
    except StoreCorruptionError as e:
        q = sorted(glob.glob(str(state_path) + ".corrupt.*"))
        return {"topology_store_corrupt": True, "detail": str(e),
                "quarantine": q[-1] if q else None}

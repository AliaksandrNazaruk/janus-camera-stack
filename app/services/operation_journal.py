"""Durable JSON journal of long node operations (provision / rotate-token / activate).

ADDITIVE tracking on top of the store's business status (provision_state / binding status /
last_error): it records each long op's lifecycle so a process restart can REAP orphaned ops
(their daemon thread died with the process). Pure persistence — no node/store un-stick logic
here; the runner orchestrates that. flock'd + atomic write, mirroring stream_binding_store.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional

from app.services import stream_binding_store as sbs

log = logging.getLogger("operation_journal")

DEFAULT_OPS_PATH = Path(
    os.environ.get("CAM_OPERATIONS_PATH",
                   str(Path(sbs.DEFAULT_STATE_PATH).parent / "operations.json"))
)
_LOCK_SUFFIX = ".lock"
_MAX_RECORDS = 500  # cap history; oldest FINISHED records pruned on write (running never dropped)


class OperationConflict(Exception):
    """A long op is already running for this node (the durable 409 guard)."""

    def __init__(self, node_id: str, op_type: str) -> None:
        self.node_id = node_id
        self.op_type = op_type
        super().__init__(f"node {node_id} busy: {op_type} already in progress")


class JournalCorrupt(Exception):
    """operations.json exists but is not valid JSON. It has been QUARANTINED (moved aside) and the
    caller must FAIL CLOSED rather than continue on a silently-empty journal — an empty journal would
    drop the per-node running-guard (H3). `quarantined` is the path the bad file was renamed to."""

    def __init__(self, path, quarantined) -> None:
        self.path = path
        self.quarantined = quarantined
        super().__init__(f"operations journal corrupt: {path} (quarantined -> {quarantined})")


def _ops_path(path: Optional[Path]) -> Path:
    return path or DEFAULT_OPS_PATH


def _quarantine(path: Path) -> Path:
    """Move a corrupt journal aside (atomic rename) so the next write starts clean while the bad
    bytes are preserved as evidence. Best-effort: if it already vanished/was quarantined by another
    caller, return the intended destination anyway."""
    dest = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
    try:
        os.replace(path, dest)
    except OSError:
        pass
    return dest


def _load(path: Path) -> dict:
    """Read the journal. NOT-FOUND → empty (legitimate). Invalid JSON (or non-UTF-8 bytes) →
    QUARANTINE + raise JournalCorrupt so callers fail closed (H3) — never silently empty, which would
    drop the running-guard. A transient OSError (unreadable, not parse) propagates un-quarantined."""
    if not path.exists():
        return {"version": 1, "operations": []}
    try:
        data = json.loads(path.read_text() or "{}") or {}
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        dest = _quarantine(path)
        log.critical("operations journal corrupt (%s); quarantined %s -> %s",
                     type(e).__name__, path, dest)
        raise JournalCorrupt(path, dest) from e
    data.setdefault("operations", [])
    return data


@contextmanager
def _flock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = Path(str(path) + _LOCK_SUFFIX)
    with open(lock_path, "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            state = _load(path)
            yield state
            tmp = path.with_suffix(path.suffix + ".tmp")
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


def begin(node_id: str, op_type: str, operation_id: str, *, path: Optional[Path] = None) -> dict:
    """Record a new `running` op. Raises OperationConflict if one is already running for node_id."""
    with _flock(_ops_path(path)) as state:
        for op in state["operations"]:
            if op["node_id"] == node_id and op["status"] == "running":
                raise OperationConflict(node_id, op["op_type"])
        rec = {
            "operation_id": operation_id,
            "op_type": op_type,
            "node_id": node_id,
            "status": "running",
            "started_at": int(time.time()),
            "finished_at": None,
            "last_error": "",
        }
        state["operations"].append(rec)
        ops = state["operations"]
        if len(ops) > _MAX_RECORDS:                       # prune oldest FINISHED, keep all running
            n_drop = len(ops) - _MAX_RECORDS
            kept = []
            for o in ops:
                if n_drop > 0 and o["status"] != "running":
                    n_drop -= 1
                    continue
                kept.append(o)
            state["operations"] = kept
        return dict(rec)


def finish(operation_id: str, status: str, *, last_error: str = "",
           path: Optional[Path] = None) -> None:
    """Mark an op terminal: succeeded / failed / interrupted."""
    with _flock(_ops_path(path)) as state:
        for op in state["operations"]:
            if op["operation_id"] == operation_id:
                op["status"] = status
                op["finished_at"] = int(time.time())
                op["last_error"] = (last_error or "")[:300]
                return


def running_for_node(node_id: str, *, path: Optional[Path] = None) -> Optional[dict]:
    for op in _load(_ops_path(path))["operations"]:
        if op["node_id"] == node_id and op["status"] == "running":
            return dict(op)
    return None


def all_running(*, path: Optional[Path] = None) -> List[dict]:
    return [dict(o) for o in _load(_ops_path(path))["operations"] if o["status"] == "running"]


def list_recent(limit: int = 50, *, path: Optional[Path] = None) -> List[dict]:
    return [dict(o) for o in _load(_ops_path(path))["operations"][-limit:][::-1]]


def get(operation_id: str, *, path: Optional[Path] = None) -> Optional[dict]:
    """One op record by id (operation_ids are unique uuids), or None — the read for GET /operations/{id}."""
    for op in _load(_ops_path(path))["operations"]:
        if op["operation_id"] == operation_id:
            return dict(op)
    return None

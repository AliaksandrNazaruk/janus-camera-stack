"""Per-node agent-token secret store (R4, review H3) for the stream_binding_store package
(Phase 13C, D2). The agent_token is a bearer secret, kept OUT of the world-readable topology file
and in a sibling 0600 ``node_secrets.json`` {node_id: token}. Writes set 0600 BEFORE the rename so the
token is never momentarily world-readable.

Cycle 1 (store safety): a CORRUPT node_secrets.json now FAILS CLOSED — ``_read_secrets`` quarantines a
forensic copy and raises StoreCorruptionError instead of returning {} (which made the nodes.py token
chain fall through to ``mint_agent_token()`` → a NEW control-plane token while the node-agent kept the
OLD one → silent auth mismatch / re-enrollment). The read-modify-write is flock-serialised, and writes
go through store_safety.atomic_write_text (fsync file + dir). Imports only leaf/stdlib helpers (the
package's StoreCorruptionError + store_safety) — no settings/HTTP pulled in."""
from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
from pathlib import Path
from secrets import token_urlsafe
from typing import Dict, Iterator

from app.services.store_safety import atomic_write_text, quarantine_corrupt
from app.services.stream_binding_store.state_file import StoreCorruptionError

log = logging.getLogger(__name__)


def _secrets_path(state_path: Path) -> Path:
    return state_path.with_name("node_secrets.json")


@contextlib.contextmanager
def _secrets_lock(state_path: Path) -> Iterator[None]:
    """Exclusive flock around a node_secrets.json read-modify-write so two concurrent provisions don't
    lost-update each other's tokens. Lock order is topology-state flock (outer, held by the node op) →
    this secrets flock (inner), always — so no deadlock."""
    lock = _secrets_path(state_path).with_suffix(".json.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock), os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read_secrets(state_path: Path) -> Dict[str, str]:
    """Read the 0600 {node_id: token} store, FAILING CLOSED on corruption.

    Absent/empty → {} (a legitimate first run). A file that EXISTS with non-empty content that is
    unparseable / not a JSON object is corruption: quarantine a forensic copy and raise
    StoreCorruptionError. NEVER return {} on a corrupt file — that would make the caller mint a fresh
    token and silently re-enroll the node (control-plane ↔ node-agent bearer mismatch)."""
    p = _secrets_path(state_path)
    if not p.exists():
        return {}
    try:
        raw = p.read_text()
    except OSError as e:
        # An access/IO error (e.g. permission) is NOT content corruption: the bytes may be perfectly
        # fine and we merely can't read them — and the write path would fail too, so there is no
        # destructive overwrite / silent regen here (self-correcting on the next readable access).
        # Degrade to empty + warn; do NOT quarantine known-good-but-unreadable bytes.
        log.warning("node secret store %s unreadable (%s) — treating as empty for this read", p, e)
        return {}
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        q = quarantine_corrupt(p, f"invalid JSON: {e}")
        raise StoreCorruptionError(
            f"node secret store {p} is not valid JSON ({e}); quarantined {q}") from e
    if not isinstance(data, dict):
        q = quarantine_corrupt(p, "top-level is not a JSON object")
        raise StoreCorruptionError(
            f"node secret store {p} top-level is not a JSON object; quarantined {q}")
    return data


def _write_secrets(p: Path, data: Dict[str, str]) -> None:
    atomic_write_text(p, json.dumps(data, indent=2, sort_keys=True) + "\n", mode=0o600)


def _set_node_secret(node_id: str, token: str, state_path: Path) -> None:
    """Persist {node_id: token} at 0600 (atomic, flock'd RMW). Only place a token is stored.
    A corrupt store fails closed (StoreCorruptionError) rather than overwriting unknown state."""
    p = _secrets_path(state_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _secrets_lock(state_path):
        data = _read_secrets(state_path)
        data[node_id] = token
        _write_secrets(p, data)


def _remove_node_secret(node_id: str, state_path: Path) -> None:
    """Drop a node's token from the 0600 secret store (atomic, flock'd RMW). Called on node removal
    so a forgotten host leaves no orphan bearer secret behind."""
    p = _secrets_path(state_path)
    if not p.exists():
        return
    with _secrets_lock(state_path):
        data = _read_secrets(state_path)
        if node_id not in data:
            return
        del data[node_id]
        _write_secrets(p, data)


def mint_agent_token() -> str:
    """A fresh, opaque per-node node-agent bearer token (URL-safe, ~256 bits)."""
    return token_urlsafe(32)

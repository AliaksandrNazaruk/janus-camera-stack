"""AE-0/AE-1 — the safe rs-runtime.env writer primitive + apply-lock context.

A purpose-built atomic writer for the NEW_SESSIONS_ONLY runtime-config file that avoids
every gotcha the apply-engine review found in ``env_store.write_env_atomic``:

  - AE-C2  a SEPARATE apply lock (``<file>.apply.lock``, NOT ``<file>.lock``), exposed as a
           context manager so the AE-1 orchestrator can hold it across the WHOLE apply and
           call the LOCK-FREE inner writer — no self-deadlock.
  - AE-C17 ``flush()+fsync(tmp)`` before rename + ``fsync(dir)`` → durable.
  - AE-C6  preserves the existing file mode → never silently weakens a hardened 0600.
  - AE-C7  re-emits the allowlist header → the security contract survives a write.
  - AE-C8  rejects foreign / secret keys (existing OR update) BEFORE writing → no write-through.
  - AE-C19 optional ``expected_hash`` precheck under the lock → closes the read→write TOCTOU.

WRITES ONLY rs-runtime.env (allowlisted keys). No ``os.environ``, no settings cache, no
service restart, no FDIR.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Optional

from app.services.runtime_revision_store import RUNTIME_ENV_PATH, RUNTIME_ENV_SENTINEL

# The only keys this file may ever contain (TA-C10 invariant). Any other key — including a
# hand-added secret — is a hard error, never written through.
ALLOWLIST = {"ICE_POLICY", "TURN_CRED_TTL"}

_HEADER = (
    "# Non-secret runtime-tunable knobs for the L4 service (managed by runtime-config apply).\n"
    "# Allowlist: ICE_POLICY, TURN_CRED_TTL only. Do NOT add secrets here.\n"
)


class ForeignKeyError(ValueError):
    """rs-runtime.env contains, or the update introduces, a non-allowlisted/secret key."""


class DriftError(RuntimeError):
    """rs-runtime.env changed since the expected_hash was taken (TOCTOU guard)."""


class LockHeld(RuntimeError):
    """The apply lock is already held (non-blocking acquire) — concurrent apply → 423."""


def apply_lock_path(env_path: Path) -> Path:
    """The apply lock — deliberately DISTINCT from env_store's ``<path>.lock`` so an outer
    holder cannot self-deadlock against this writer (AE-C2)."""
    return Path(str(env_path) + ".apply.lock")


@contextmanager
def runtime_env_lock(env_path: Optional[Path] = None, *, blocking: bool = True):
    """Hold the apply lock for the duration of the block. ``blocking=False`` raises
    ``LockHeld`` immediately if another holder has it (the 423 path). The AE-1 orchestrator
    wraps the WHOLE apply in this and then calls the lock-free ``write_locked``/``restore_locked``."""
    p = Path(env_path or RUNTIME_ENV_PATH)
    lock_path = apply_lock_path(p)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if not blocking else 0)
    with open(lock_path, "w") as lock_file:
        try:
            fcntl.flock(lock_file, flags)
        except BlockingIOError as e:
            raise LockHeld("apply lock held by another apply") from e
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def current_hash(env_path: Optional[Path] = None) -> str:
    """Full-file sha256 of rs-runtime.env, or the absence sentinel."""
    p = Path(env_path or RUNTIME_ENV_PATH)
    return ("sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()) if p.is_file() else RUNTIME_ENV_SENTINEL


def read_keys(env_path: Optional[Path] = None) -> Dict[str, str]:
    p = Path(env_path or RUNTIME_ENV_PATH)
    out: Dict[str, str] = {}
    if not p.is_file():
        return out
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _sensitive_keys() -> set:
    try:
        from app.services.secret_store import SENSITIVE_KEYS
        return set(SENSITIVE_KEYS)
    except Exception:  # pragma: no cover — defensive
        return set()


def _assert_allowlisted(keys: set, where: str) -> None:
    foreign = keys - ALLOWLIST
    if foreign:
        raise ForeignKeyError(f"{where}: non-allowlisted key(s) {sorted(foreign)}")
    secret = keys & _sensitive_keys()
    if secret:
        raise ForeignKeyError(f"{where}: secret key(s) {sorted(secret)} must never live in rs-runtime.env")


def _fsync_dir(d: Path) -> None:
    try:
        dfd = os.open(str(d), os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except (OSError, AttributeError):  # pragma: no cover
        pass


def _atomic_write(env_path: Path, body: str, mode: int) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=env_path.name + ".", suffix=".tmp", dir=str(env_path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, env_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:  # pragma: no cover
            pass
        raise
    _fsync_dir(env_path.parent)


def write_locked(updates: Dict[str, object], *, env_path: Optional[Path] = None,
                 expected_hash: Optional[str] = None) -> str:
    """LOCK-FREE merge-write (caller MUST hold ``runtime_env_lock``). Used by the AE-1
    orchestrator. Raises ForeignKeyError / DriftError as documented on ``write_runtime_env``."""
    env_path = Path(env_path or RUNTIME_ENV_PATH)
    _assert_allowlisted(set(updates), "updates")
    if expected_hash is not None and current_hash(env_path) != expected_hash:
        raise DriftError("rs-runtime.env changed since validate (hash mismatch)")
    cur = read_keys(env_path)
    _assert_allowlisted(set(cur), "rs-runtime.env on disk")   # never write-through a foreign/secret key
    merged = {**cur, **{k: str(v) for k, v in updates.items()}}
    _assert_allowlisted(set(merged), "merged")
    mode = (os.stat(env_path).st_mode & 0o777) if env_path.is_file() else 0o644   # preserve / default
    body = _HEADER + "".join(f"{k}={merged[k]}\n" for k in sorted(merged))
    _atomic_write(env_path, body, mode)
    return current_hash(env_path)


def restore_locked(prior_bytes: Optional[bytes], *, env_path: Optional[Path] = None,
                   mode: int = 0o644) -> None:
    """LOCK-FREE rollback restore (caller MUST hold the lock). Writes ``prior_bytes`` verbatim
    (faithful — drops any key the failed apply introduced, AE-C3 file-level); if ``prior_bytes``
    is None (the file was absent before), removes the file."""
    env_path = Path(env_path or RUNTIME_ENV_PATH)
    if prior_bytes is None:
        env_path.unlink(missing_ok=True)
        _fsync_dir(env_path.parent)
        return
    env_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=env_path.name + ".", suffix=".tmp", dir=str(env_path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(prior_bytes)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, env_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:  # pragma: no cover
            pass
        raise
    _fsync_dir(env_path.parent)


def write_runtime_env(updates: Dict[str, object], *, env_path: Optional[Path] = None,
                      expected_hash: Optional[str] = None) -> str:
    """Self-locking public writer (AE-0 API): acquire the apply lock, then ``write_locked``.
    For standalone use + AE-0 tests. The AE-1 orchestrator uses ``runtime_env_lock`` +
    ``write_locked`` directly so it can hold the lock across write→verify→rollback."""
    with runtime_env_lock(env_path):
        return write_locked(updates, env_path=env_path, expected_hash=expected_hash)

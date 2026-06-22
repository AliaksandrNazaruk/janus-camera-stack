"""Store-safety primitives (stdlib only) — atomic durable writes + corrupt-file quarantine.

Shared by the secret/config stores so a corrupt store FAILS CLOSED (quarantine + raise) instead of
fail-open (corrupt read as empty → silent regenerate / lost update → control-plane ↔ node/runtime
mismatch). Generalises the H-02/H3 pattern already proven in `stream_binding_store/state_file.py` +
`operation_journal.py`. STDLIB ONLY on purpose — the leaf secret stores (`secret_store`,
`stream_binding_store/secrets`) import it without pulling in settings/HTTP.
"""
from __future__ import annotations

import glob
import logging
import os
import shutil
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


class StoreCorrupt(RuntimeError):
    """A store file exists with non-empty content that is unparseable / the wrong shape. Fail closed:
    the read quarantines a forensic copy and raises this, rather than returning an empty store (which
    would silently regenerate secrets or lose state). A genuinely ABSENT file is NOT corruption."""


def atomic_write_text(path: Path, content: str, *, mode: Optional[int] = None) -> None:
    """Crash-safe + durable text write: tmp → fsync(file) → [chmod] → os.replace → fsync(dir).

    Either the old or the new content is visible, never a partial write; the new content survives a
    power loss because BOTH the temp file and the parent directory are fsync'd (the directory fsync
    makes the rename itself durable — the gap every hand-rolled store helper here was missing). If
    *mode* is given it is applied to the temp file BEFORE the rename, so a 0600 secret is never
    momentarily world-readable (mkstemp already starts at 0600; this is for an explicit mode)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        os.write(fd, content.encode())
        os.fsync(fd)
        if mode is not None:
            os.fchmod(fd, mode)
        os.close(fd)
        fd = -1
        os.replace(tmp, str(path))
        with suppress(OSError):                       # dir fsync → the rename itself is durable
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        with suppress(OSError):
            os.unlink(tmp)
        raise


def quarantine_corrupt(path: Path, reason: str) -> Optional[Path]:
    """Make ONE timestamped forensic copy of a corrupt store file (``<path>.corrupt.<ts>``), idempotent
    — a repeated corrupt read does not spam copies. The original is deliberately LEFT IN PLACE so the
    corruption stays detectable across restarts until an operator fixes it (moving it aside would let
    the next read silently re-empty the store). Best-effort; never raises. Returns the quarantine path
    (or the pre-existing one), or None if the copy failed."""
    existing = sorted(glob.glob(str(path) + ".corrupt.*"))
    if existing:
        return Path(existing[-1])
    qpath = Path(f"{path}.corrupt.{time.strftime('%Y%m%d_%H%M%S')}")
    try:
        shutil.copy2(path, qpath)
        log.critical("store CORRUPT (%s) — quarantined forensic copy at %s", reason, qpath)
        return qpath
    except OSError as e:
        log.critical("store CORRUPT (%s) at %s — quarantine copy failed: %s", reason, path, e)
        return None

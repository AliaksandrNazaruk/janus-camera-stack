"""Recovery ladder persistence — extracted from recovery_ladder.py.

Sprint D refactor (partial): single-responsibility persistence class.
Encapsulates file I/O for:
  - reboot_count (file-locked, atomic increment)
  - ladder state JSON (atomic write, corruption-recovery on load)

Public API stable. recovery_ladder.py imports + delegates. Existing
tests continue to work without modification — they monkeypatch
recovery_ladder module-level paths which remain valid (backward-compat
aliases).

Future split (deferred): RecoveryExecutor (action execution),
RecoveryStateMachine (escalation policy). Keep RecoveryLadder facade.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger("fdir.persistence")


class RecoveryPersistence:
    """File-backed persistence for recovery ladder + reboot counter.

    Paths injected at construction time for testability — no module-level
    state. atomic_write_text injected to allow shared atomic-write
    convention across L4.
    """

    def __init__(
        self,
        *,
        ladder_state_path: Path,
        reboot_count_dir: Path,
        atomic_write_text,  # callable(path, content) — injected
        emit_event=None,    # optional callable for corruption event
    ) -> None:
        self._ladder_state_path = ladder_state_path
        self._reboot_count_dir = reboot_count_dir
        self._reboot_count_path = reboot_count_dir / "reboot_count"
        self._reboot_marker_path = reboot_count_dir / "last_reboot_request"
        self._atomic_write_text = atomic_write_text
        self._emit = emit_event

    # ── Reboot counter ────────────────────────────────────────────────

    def read_reboot_count(self) -> int:
        try:
            return int(self._reboot_count_path.read_text().strip())
        except (FileNotFoundError, ValueError):
            return 0

    def write_reboot_count(self, n: int) -> None:
        """Atomically write reboot count with file-level lock to prevent TOCTOU."""
        try:
            self._reboot_count_dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self._reboot_count_path), os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                os.ftruncate(fd, 0)
                os.lseek(fd, 0, os.SEEK_SET)
                os.write(fd, (str(n) + "\n").encode())
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as exc:
            logger.warning("Cannot write reboot count: %s", exc)

    def atomic_increment_reboot_count(self) -> int:
        """Atomically read-increment-write reboot count. Returns NEW count."""
        try:
            self._reboot_count_dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self._reboot_count_path), os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                raw = os.read(fd, 64).decode().strip()
                try:
                    current = int(raw) if raw else 0
                except (ValueError, TypeError):
                    current = 0
                new_val = current + 1
                os.ftruncate(fd, 0)
                os.lseek(fd, 0, os.SEEK_SET)
                os.write(fd, (str(new_val) + "\n").encode())
                os.fsync(fd)
                return new_val
            finally:
                os.close(fd)
        except OSError as exc:
            logger.warning("Cannot increment reboot count: %s", exc)
            return self.read_reboot_count() + 1

    @property
    def reboot_marker_path(self) -> Path:
        return self._reboot_marker_path

    # ── Ladder state ──────────────────────────────────────────────────

    def save_ladder_state(self, level: int, levels: list, total: int) -> None:
        """Atomically persist ladder state. Best-effort — no raise on OSError."""
        try:
            state = {
                "level": level,
                "attempts": [lv.attempts for lv in levels],
                "last_attempt": [lv.last_attempt for lv in levels],
                "total_recoveries": total,
                "ts": time.time(),
            }
            self._atomic_write_text(self._ladder_state_path, json.dumps(state))
        except OSError as exc:
            logger.warning("Cannot save ladder state: %s", exc)

    def load_ladder_state(self, levels: list) -> tuple[int, int]:
        """Load ladder state. Mutates `levels` in-place. Returns (level, total).

        On corruption: emits warning event (if emit_event provided),
        returns (0, 0). On missing file: returns (0, 0) silently.
        """
        try:
            fd = os.open(str(self._ladder_state_path), os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_SH)
                raw_bytes = os.read(fd, 8192)
            finally:
                os.close(fd)
            raw = json.loads(raw_bytes.decode())
            saved_level = int(raw.get("level", 0))
            total = int(raw.get("total_recoveries", 0))
            saved_attempts = raw.get("attempts", [])
            saved_last = raw.get("last_attempt", [])
            for i, lv in enumerate(levels):
                if i < len(saved_attempts):
                    lv.attempts = int(saved_attempts[i])
                if i < len(saved_last):
                    lv.last_attempt = float(saved_last[i])
            saved_level = max(0, min(saved_level, len(levels)))
            logger.info("Loaded ladder state from disk: level=%d total=%d", saved_level, total)
            return saved_level, total
        except FileNotFoundError:
            return 0, 0
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning(
                "Corrupted ladder state file %s: %s — resetting to level 0",
                self._ladder_state_path, exc,
            )
            if self._emit is not None:
                self._emit(exc)
            return 0, 0

"""F-Isolation phase для L0 — SAFE mode (quarantine).

ECSS-Q-ST-30 FDIR phases:
  - **D**etection — checks (existing)
  - **I**dentification — status + summary (existing)
  - **I**solation — это модуль: квaranтинн bad component
  - **R**ecovery — fixers (existing)

Semantics:
  - SAFE mode = explicit operator action ("заморозь L0 до моих указаний")
    либо automated FDIR ("повторные fail'ы → isolate, не trigger recovery loop")
  - Apply ЗАБЛОКИРОВАН в SAFE mode (fail-safe: не делаем mutations)
  - Verify работает (read-only)
  - Higher layers могут query is_safe_mode() и переключаться на degraded ops

Persistence:
  - Marker файл: /var/lib/camera/<instance>.safe (JSON: reason, ts, set_by)
  - Survives reboot — выйти можно только явно через exit_safe_mode()
  - Per-instance: каждая instance имеет свой safe mode

Use cases:
  - "Подменяю камеру, не trigger recovery": enter_safe_mode("camera swap in progress")
  - FDIR detected 5 fail'ов подряд за 10 минут → auto-isolate
  - Maintenance mode перед firmware update
"""
from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from typing import Any

from camera_bringup.spec import ACTIVE_INSTANCE, FINGERPRINT_DIR


def _safe_marker_path() -> Path:
    """Per-instance marker file: /var/lib/camera/<instance>.safe."""
    return Path(FINGERPRINT_DIR) / f"{ACTIVE_INSTANCE.instance_id}.safe"


def is_safe_mode() -> bool:
    return _safe_marker_path().is_file()


def safe_mode_info() -> dict[str, Any] | None:
    """Return marker content or None если не в SAFE mode."""
    path = _safe_marker_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"reason": "unknown (marker unreadable)", "ts": None, "set_by": None}


def enter_safe_mode(reason: str, set_by: str | None = None) -> dict[str, Any]:
    """Quarantine этот instance. Apply будет заблокирован.

    Requires write access to FINGERPRINT_DIR (обычно sudo).
    Idempotent: если уже в SAFE — обновляется timestamp + reason.
    """
    path = _safe_marker_path()
    payload = {
        "reason": reason,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "set_by": set_by or socket.gethostname(),
        "instance_id": ACTIVE_INSTANCE.instance_id,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp, path)
    return payload


def exit_safe_mode() -> bool:
    """Remove SAFE marker. Apply снова разрешено.

    Returns True если был в SAFE и вышли. False если уже не был.
    Requires write access to FINGERPRINT_DIR.
    """
    path = _safe_marker_path()
    if not path.is_file():
        return False
    path.unlink()
    return True

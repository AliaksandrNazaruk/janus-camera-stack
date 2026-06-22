from __future__ import annotations

import os
import subprocess
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any, Dict

from app.core.settings import get_settings


def atomic_write_text(path: Path, content: str) -> None:
    """Write *content* to *path* atomically via tempfile + rename.

    Ensures crash-safe writes: either the old content or the new content
    is visible, never a partial write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        os.write(fd, content.encode())
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.rename(tmp, str(path))
    except BaseException:
        if fd >= 0:
            os.close(fd)
        with suppress(OSError):
            os.unlink(tmp)
        raise


def run(cmd: list[str], timeout: int = 5) -> str:
    result = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cmd failed: {' '.join(cmd)} :: {result.stderr.strip()}")
    return result.stdout


def service_restart() -> None:
    """Restart encoder via L2-owned encoder-admin CLI (no direct systemctl)."""
    run(["sudo", "/usr/local/bin/encoder-admin", "restart"], timeout=60)


def service_status() -> Dict[str, Any]:
    """Read encoder state via encoder-admin status (JSON output)."""
    import json as _json
    settings = get_settings()
    try:
        raw = run(["sudo", "/usr/local/bin/encoder-admin", "status"], timeout=5)
        data = _json.loads(raw)
        return {"service": data.get("unit", settings.service_name),
                "active": bool(data.get("active")), "raw": str(data.get("active"))}
    except RuntimeError:
        return {"service": settings.service_name, "active": False, "raw": "unknown"}


def systemd_brief(unit: str | None = None) -> Dict[str, Any]:
    service = unit or get_settings().service_name
    output = run(
        [
            "systemctl",
            "show",
            service,
            "-p",
            "ActiveState",
            "-p",
            "ActiveEnterTimestamp",
            "-p",
            "NRestarts",
        ]
    )
    kv = dict(line.split("=", 1) for line in output.strip().splitlines() if "=" in line)
    return {
        "active": kv.get("ActiveState") == "active",
        "since": kv.get("ActiveEnterTimestamp"),
        "restarts": int(kv.get("NRestarts", "0")),
    }


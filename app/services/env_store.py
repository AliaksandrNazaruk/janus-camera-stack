from __future__ import annotations

import fcntl
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from app.core.settings import get_settings


def write_env_atomic(new_data: Dict[str, Any],
                     env_path: Optional[Path] = None,
                     lock_path: Optional[Path] = None) -> None:
    """Atomic write. Defaults to settings.env_path (rs-color.tuning.env) but
    accepts overrides for per-sensor envs (rs-depth.tuning.env, etc).
    """
    if env_path is None:
        settings = get_settings()
        env_path = settings.env_path
    if lock_path is None:
        lock_path = Path(str(env_path) + ".lock")
    env_path.parent.mkdir(parents=True, exist_ok=True)

    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix=env_path.name + ".", suffix=".tmp", dir=str(env_path.parent)
        )
        with os.fdopen(tmp_fd, "w") as tmp_file:
            for key, value in new_data.items():
                tmp_file.write(f"{key}={value}\n")
        shutil.move(tmp_name, env_path)
        os.chmod(env_path, 0o644)
        fcntl.flock(lock_file, fcntl.LOCK_UN)


def read_env(env_path: Optional[Path] = None) -> Dict[str, str]:
    """Read env file. Defaults to settings.env_path (rs-color.tuning.env), accepts override."""
    if env_path is None:
        env_path = get_settings().env_path
    data: Dict[str, str] = {}
    if not env_path.exists():
        return data

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


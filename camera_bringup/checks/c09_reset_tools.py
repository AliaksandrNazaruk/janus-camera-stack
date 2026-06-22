"""c09_reset_tools — проверить, что все способы reset камеры доступны.

Reset arsenal (от мягкого к жёсткому):
  1. /usr/bin/usbreset — standard USB device reset (ioctl USBDEVFS_RESET)
  2. echo 0/1 > /sys/bus/usb/devices/<N-N>/authorized — toggle authorize
  3. hw_reset_realsense.py через pyrealsense2 — firmware-level reset
     (ЕДИНСТВЕННОЕ что чинит «stuck VIDIOC_S_FMT errno=5» баг FW)

Что проверяем:
  - usbreset binary установлен и executable
  - /sys/bus/usb/devices/<N-N>/authorized — writeable (sudo)
  - hw_reset_realsense.py существует, executable, и pyrealsense2 import'ится
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from shutil import which
from typing import Any

from camera_bringup.check import CheckResult, Status
from camera_bringup.spec import HW_RESET_SCRIPT, PYREALSENSE_IMPORT_NAME


def _shebang_python(script_path: str) -> str | None:
    """Прочитать первую строку файла; вернуть путь к python если это shebang."""
    try:
        with open(script_path) as f:
            first = f.readline().strip()
    except OSError:
        return None
    # `#!/usr/bin/env python3` или `#!/path/to/python3`
    m = re.match(r"#!\s*(\S+)(?:\s+(\S+))?", first)
    if not m:
        return None
    interp = m.group(1)
    arg = m.group(2)
    # env-стиль: ищем второй токен на PATH
    if interp.endswith("/env") and arg:
        found = which(arg)
        return found
    # прямой путь
    if Path(interp).is_file():
        return interp
    return None


def _can_import_module(python: str, module: str) -> tuple[bool, str]:
    """Попробовать импортировать модуль в заданном Python interpreter."""
    try:
        result = subprocess.run(
            [python, "-c", f"import {module}; print('ok')"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"subprocess error: {exc}"
    if result.returncode == 0 and "ok" in result.stdout:
        return True, "ok"
    err = (result.stderr or "").strip().splitlines()
    return False, err[-1] if err else "unknown error"


def check(ctx: dict[str, Any]) -> CheckResult:
    sysfs_path = ctx.get("sysfs_path")

    findings: list[str] = []
    issues: list[str] = []
    severity = Status.OK

    # 1. /usr/bin/usbreset
    usbreset_path = which("usbreset")
    if usbreset_path:
        findings.append(f"usbreset @ {usbreset_path}")
    else:
        issues.append("usbreset не установлен (apt install usbutils)")
        severity = Status.WARN

    # 2. authorize toggle path
    authorize_writable = False
    authorize_path = None
    if sysfs_path:
        authorize_path = f"{sysfs_path}/authorized"
        # readable?
        authorize_readable = os.access(authorize_path, os.R_OK)
        # writable? — обычно требует root
        authorize_writable = os.access(authorize_path, os.W_OK)
        findings.append(
            f"authorize: {authorize_path} readable={authorize_readable} "
            f"writable={authorize_writable} (требуется root)"
        )
    else:
        issues.append("нет sysfs_path для authorize toggle")
        severity = Status.WARN if severity != Status.FAIL else severity

    # 3. hw_reset_realsense.py — present + executable + shebang valid
    hw_reset = Path(HW_RESET_SCRIPT)
    hw_reset_python: str | None = None
    if not hw_reset.is_file():
        issues.append(f"{HW_RESET_SCRIPT} отсутствует")
        severity = Status.FAIL
    else:
        if not os.access(HW_RESET_SCRIPT, os.X_OK):
            issues.append(f"{HW_RESET_SCRIPT} не executable")
            severity = Status.WARN
        hw_reset_python = _shebang_python(HW_RESET_SCRIPT)
        if hw_reset_python is None:
            issues.append(f"{HW_RESET_SCRIPT}: shebang не указывает на валидный python")
            severity = Status.WARN
        else:
            findings.append(f"hw_reset @ {HW_RESET_SCRIPT} (python {hw_reset_python})")

    # 4. pyrealsense2 importable из ТОГО python который шебанг hw_reset
    # (не sys.executable — у camera_bringup может быть другой python чем у hw_reset)
    pyrs_python = hw_reset_python or sys.executable
    pyrs_ok, pyrs_msg = _can_import_module(pyrs_python, PYREALSENSE_IMPORT_NAME)
    if pyrs_ok:
        findings.append(f"{PYREALSENSE_IMPORT_NAME} importable from {pyrs_python}")
    else:
        issues.append(
            f"{PYREALSENSE_IMPORT_NAME} не импортируется из {pyrs_python}: {pyrs_msg}"
        )
        severity = Status.FAIL

    details = {
        "usbreset_path": usbreset_path,
        "authorize_path": authorize_path,
        "authorize_writable": authorize_writable,
        "hw_reset_script": HW_RESET_SCRIPT,
        "hw_reset_present": hw_reset.is_file(),
        "hw_reset_python": hw_reset_python,
        "pyrealsense2_importable": pyrs_ok,
        "findings": findings,
    }

    if not issues:
        return CheckResult(
            name="reset_tools",
            status=Status.OK,
            summary=f"все 3 пути reset доступны ({len(findings)} tools)",
            details=details,
        )

    return CheckResult(
        name="reset_tools",
        status=severity,
        summary="; ".join(issues),
        details=details,
        fix_hint=(
            "apt install usbutils для usbreset; "
            "pip install pyrealsense2 для firmware reset; "
            "chmod +x hw_reset_realsense.py"
        ),
    )

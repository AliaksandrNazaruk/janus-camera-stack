"""c05_dev_symlinks — проверить /dev/cam-rgb symlink.

Что проверяет:
  - /dev/cam-rgb существует и это symlink
  - указывает на /dev/videoN
  - target node имеет ID_USB_INTERFACE_NUM=03 (RGB sensor)
  - target node имеет ID_V4L_CAPABILITIES=:capture: (только capture node, не control)
  - target node принадлежит правильному устройству (8086:0b3a)

Кладёт в ctx['v4l_dev'] = "/dev/videoN" для последующих v4l2 checks.
"""
from __future__ import annotations

import os
import subprocess
from typing import Any

from camera_bringup.check import CheckResult, Status
from camera_bringup.spec import DEV_SYMLINK, USB_INTERFACE_NUM_RGB, USB_PRODUCT_ID, USB_VENDOR_ID


def _udev_properties(device: str) -> dict[str, str]:
    """Возвращает map свойств от `udevadm info -q property -n <device>`."""
    try:
        result = subprocess.run(
            ["udevadm", "info", "-q", "property", "-n", device],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    out: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def check(ctx: dict[str, Any]) -> CheckResult:
    details: dict[str, Any] = {"symlink": DEV_SYMLINK}

    if not os.path.lexists(DEV_SYMLINK):
        return CheckResult(
            name="dev_symlinks",
            status=Status.FAIL,
            summary=f"{DEV_SYMLINK} не существует",
            details=details,
            fix_hint=(
                "udevadm trigger --subsystem-match=video4linux; "
                "если не помогает — проверить udev rule и USB enumeration"
            ),
        )

    if not os.path.islink(DEV_SYMLINK):
        return CheckResult(
            name="dev_symlinks",
            status=Status.FAIL,
            summary=f"{DEV_SYMLINK} существует но это НЕ symlink",
            details=details,
            fix_hint=f"rm {DEV_SYMLINK} && udevadm trigger",
        )

    target = os.path.realpath(DEV_SYMLINK)
    details["target"] = target

    # Target должен быть V4L2 video node (basename = videoN).
    # Чек на basename вместо full path — позволяет ENV override DEV_SYMLINK
    # под tests без необходимости подменять реальные /dev/.
    target_name = os.path.basename(target)
    if not target_name.startswith("video"):
        return CheckResult(
            name="dev_symlinks",
            status=Status.FAIL,
            summary=f"{DEV_SYMLINK} → {target} (ожидался videoN node)",
            details=details,
            fix_hint="rm symlink, udevadm trigger",
        )

    # Проверим что target существует
    if not os.path.exists(target):
        return CheckResult(
            name="dev_symlinks",
            status=Status.FAIL,
            summary=f"{DEV_SYMLINK} → {target} (target не существует)",
            details=details,
            fix_hint="udevadm trigger или replug камеры",
        )

    # udev свойства target ноды
    props = _udev_properties(target)
    interface = props.get("ID_USB_INTERFACE_NUM", "")
    capabilities = props.get("ID_V4L_CAPABILITIES", "")
    vendor = props.get("ID_VENDOR_ID", "")
    model = props.get("ID_MODEL_ID", "")

    details["target_props"] = {
        "interface": interface,
        "capabilities": capabilities,
        "vendor": vendor,
        "model": model,
    }

    ctx["v4l_dev"] = target

    issues = []
    severity = Status.OK

    if vendor != USB_VENDOR_ID or model != USB_PRODUCT_ID:
        issues.append(
            f"target указывает на {vendor}:{model}, не на наш {USB_VENDOR_ID}:{USB_PRODUCT_ID}"
        )
        severity = Status.FAIL

    if interface != USB_INTERFACE_NUM_RGB:
        issues.append(
            f"target interface={interface} (ожидался RGB={USB_INTERFACE_NUM_RGB})"
        )
        severity = Status.FAIL

    if ":capture:" not in capabilities:
        issues.append(
            f"target capabilities={capabilities!r} (ожидался ':capture:')"
        )
        severity = Status.FAIL

    if not issues:
        return CheckResult(
            name="dev_symlinks",
            status=Status.OK,
            summary=f"{DEV_SYMLINK} → {target} (RGB capture interface)",
            details=details,
        )

    return CheckResult(
        name="dev_symlinks",
        status=severity,
        summary="; ".join(issues),
        details=details,
        fix_hint=(
            "вероятно udev rule сработал на неверной ноде; "
            "проверить ID_USB_INTERFACE_NUM в udev rule"
        ),
    )

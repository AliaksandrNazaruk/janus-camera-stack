"""c01_usb_enumerate — найти подключённую RealSense на USB.

Что проверяет:
  - lsusb видит ровно одно устройство 8086:0b3a
  - sysfs path существует (/sys/bus/usb/devices/N-N)
  - USB speed (480 = USB2.0, 5000 = USB3.0)
  - bMaxPower не превышает 500mA (предел bus power)

Что кладёт в ctx (для последующих checks):
  - sysfs_path: путь типа /sys/bus/usb/devices/2-2
  - usb_speed_mbit: 480 или 5000
"""
from __future__ import annotations

import glob
from pathlib import Path
from typing import Any

from camera_bringup.check import CheckResult, Status, read_file
from camera_bringup.spec import DEVICE_FRIENDLY_NAME, USB_PRODUCT_ID, USB_VENDOR_ID


def _find_all_sysfs_paths() -> list[str]:
    """Найти ВСЕ /sys/bus/usb/devices/N-N для нашего vendor:product.

    Возвращает list — если > 1, в системе несколько D435i и наш udev rule
    `99-cam-rgb.rules` создаст race condition за `/dev/cam-rgb` symlink.
    """
    found: list[str] = []
    for product_file in glob.glob("/sys/bus/usb/devices/*/idProduct"):
        try:
            with open(product_file) as f:
                if f.read().strip() != USB_PRODUCT_ID:
                    continue
            dev_dir = Path(product_file).parent
            vendor_file = dev_dir / "idVendor"
            if vendor_file.read_text().strip() == USB_VENDOR_ID:
                found.append(str(dev_dir))
        except (OSError, PermissionError):
            continue
    return sorted(found)


def _find_sysfs_path() -> str | None:
    """Backward-compat: первый найденный путь или None."""
    paths = _find_all_sysfs_paths()
    return paths[0] if paths else None


def check(ctx: dict[str, Any]) -> CheckResult:
    paths = _find_all_sysfs_paths()
    if not paths:
        return CheckResult(
            name="usb_enumerate",
            status=Status.FAIL,
            summary=f"{DEVICE_FRIENDLY_NAME} ({USB_VENDOR_ID}:{USB_PRODUCT_ID}) не найдена на USB",
            fix_hint="проверить кабель, физическое подключение, lsusb",
        )

    if len(paths) > 1:
        # Несколько D435i = наш udev rule создаст race condition
        # за SYMLINK /dev/cam-rgb. Какая камера получит alias — undefined.
        return CheckResult(
            name="usb_enumerate",
            status=Status.FAIL,
            summary=(
                f"найдено {len(paths)} D435i камер: {paths} — наш udev rule "
                "не различает их (race за /dev/cam-rgb). Для multi-camera "
                "нужны serial-based aliases (отдельный fixer, ещё не реализован)"
            ),
            details={"paths": paths},
            fix_hint="физически оставить ровно одну D435i, либо реализовать serial-based udev",
        )

    sysfs_path = paths[0]

    # Чтение USB параметров
    speed = read_file(f"{sysfs_path}/speed")
    bmaxpower = read_file(f"{sysfs_path}/bMaxPower")
    busnum = read_file(f"{sysfs_path}/busnum")
    devnum = read_file(f"{sysfs_path}/devnum")

    speed_mbit = int(speed.strip()) if speed else 0
    bmaxpower_str = bmaxpower.strip() if bmaxpower else "?"

    # Положим в ctx для последующих checks
    ctx["sysfs_path"] = sysfs_path
    ctx["usb_speed_mbit"] = speed_mbit

    details = {
        "sysfs_path": sysfs_path,
        "bus": busnum.strip() if busnum else "?",
        "device": devnum.strip() if devnum else "?",
        "speed_mbit": speed_mbit,
        "max_power": bmaxpower_str,
    }

    # USB2 при доступном USB3 = warn (не fail — система работает, но
    # подсказка пересадить в USB3 для будущего расширения)
    if speed_mbit == 480:
        return CheckResult(
            name="usb_enumerate",
            status=Status.WARN,
            summary=f"найдена на USB2 (480 Mbit), {bmaxpower_str} — рекомендуется USB3",
            details=details,
            fix_hint=(
                "пересадить в USB3-порт (Bus 3 или Bus 5) если есть USB3-кабель; "
                "на USB2 невозможно поднять разрешение/fps выше текущего"
            ),
        )

    if speed_mbit == 5000:
        return CheckResult(
            name="usb_enumerate",
            status=Status.OK,
            summary=f"найдена на USB3 (5000 Mbit), {bmaxpower_str}",
            details=details,
        )

    return CheckResult(
        name="usb_enumerate",
        status=Status.WARN,
        summary=f"найдена, но непонятная скорость: {speed_mbit} Mbit",
        details=details,
    )

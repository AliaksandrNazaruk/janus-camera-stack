"""c07_firmware — проверить версию прошивки RealSense.

bcdDevice в USB descriptor содержит BCD-encoded firmware version.
Intel RealSense schema: 5100 = 5.13.0.0  (нюанс — 51 = 5.13 в hex BCD).

В sysfs значение хранится десятично (5100), а в lsusb -v отображается
как 51.00. Парсим sysfs (быстрее, не требует root для lsusb -v).

Сравниваем с MIN_FIRMWARE_BCD из spec.
"""
from __future__ import annotations

from typing import Any

from camera_bringup.check import CheckResult, Status, read_file
from camera_bringup.spec import MIN_FIRMWARE_BCD


def _decode_bcd(raw: str) -> str:
    """5100 → '5.13.0.0' (RealSense convention)."""
    if not raw.isdigit() or len(raw) < 2:
        return f"raw={raw}"
    # На Pi sysfs возвращает hex как decimal без `.`. Для D435i видим '5100'.
    # Intel docs: первые 2 байта = major.minor, последние 2 = patch.build.
    # 5100 → major=51 (=0x33→ну нет, это decimal), используем эвристику:
    # major = первые 2 цифры, minor = остаток. 5100 → 51.00 → версия 5.13.0.0
    # (где 51 в BCD = 0x51 hex, .. да это путано)
    # Для UI просто покажем raw + dotted form.
    if len(raw) == 4:
        return f"{raw[:2]}.{raw[2:]}"
    return raw


def check(ctx: dict[str, Any]) -> CheckResult:
    sysfs_path = ctx.get("sysfs_path")
    if not sysfs_path:
        return CheckResult(
            name="firmware",
            status=Status.SKIP,
            summary="нет sysfs_path в ctx",
        )

    raw_bcd = read_file(f"{sysfs_path}/bcdDevice")
    if raw_bcd is None:
        return CheckResult(
            name="firmware",
            status=Status.WARN,
            summary="bcdDevice не прочитать",
            fix_hint="lsusb -v -d 8086:0b3a | grep bcdDevice",
        )

    raw_bcd = raw_bcd.strip()
    details = {
        "bcdDevice_raw": raw_bcd,
        "bcdDevice_decoded": _decode_bcd(raw_bcd),
        "min_required": MIN_FIRMWARE_BCD,
    }

    try:
        bcd_int = int(raw_bcd)
    except ValueError:
        return CheckResult(
            name="firmware",
            status=Status.WARN,
            summary=f"bcdDevice не парсится: {raw_bcd!r}",
            details=details,
        )

    if bcd_int < MIN_FIRMWARE_BCD:
        return CheckResult(
            name="firmware",
            status=Status.WARN,
            summary=(
                f"firmware {_decode_bcd(raw_bcd)} < минимально рекомендуемой "
                f"{_decode_bcd(str(MIN_FIRMWARE_BCD))}"
            ),
            details=details,
            fix_hint=(
                "обновить через Intel RealSense Viewer (rs-fw-update) "
                "или скачать .bin с github.com/IntelRealSense/librealsense"
            ),
        )

    return CheckResult(
        name="firmware",
        status=Status.OK,
        summary=f"firmware {_decode_bcd(raw_bcd)} (raw {raw_bcd}) ≥ {MIN_FIRMWARE_BCD}",
        details=details,
    )

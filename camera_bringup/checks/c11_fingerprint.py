"""c11_fingerprint — детектор «камеру подменили».

Сравнивает текущий identity с baseline в `/var/lib/camera/fingerprint.json`.

Семантика:
  - НЕТ baseline файла → WARN «no baseline». Чинится через
    `camera_bringup apply --only fingerprint` (записывает текущее как baseline).
  - vendor:product mismatch → FAIL «wrong device entirely» (другой production)
  - serial mismatch → FAIL «camera replaced» (физический swap)
  - firmware version different → WARN «firmware changed» (informational)
  - sysfs_path different → WARN «physical USB port changed» (replug)
  - всё совпало → OK

Зависит от ctx['sysfs_path'] от c01.
"""
from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

from camera_bringup.check import CheckResult, Status
from camera_bringup.realsense_query import primary_device
from camera_bringup.signing import load_secret, secret_exists
from camera_bringup.signing import verify as hmac_verify
from camera_bringup.spec import (
    FINGERPRINT_PATH,
    USB_PRODUCT_ID,
    USB_VENDOR_ID,
)


def _current_fingerprint(ctx: dict[str, Any]) -> dict[str, Any]:
    """Собрать текущий identity камеры + host context + calibration."""
    device = primary_device() or {}
    sysfs_path = ctx.get("sysfs_path")
    v4l_dev = ctx.get("v4l_dev")

    return {
        "camera": {
            "vendor_id": USB_VENDOR_ID,
            "product_id": USB_PRODUCT_ID,
            "serial": device.get("serial"),
            "firmware": device.get("firmware"),
            "usb_type": device.get("usb_type"),
            "product_name": device.get("name"),
            "product_line": device.get("product_line"),
        },
        "host": {
            "sysfs_path": sysfs_path,
            "v4l_dev": v4l_dev,
            "hostname": socket.gethostname(),
        },
        "calibration": device.get("calibration", {}),
    }


def _load_baseline() -> dict[str, Any] | None:
    path = Path(FINGERPRINT_PATH)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _compare(current: dict[str, Any], baseline: dict[str, Any]) -> list[dict[str, Any]]:
    """Возвращает list расхождений с severity для каждого."""
    diffs: list[dict[str, Any]] = []

    cur_cam = current.get("camera", {})
    base_cam = baseline.get("camera", {})
    cur_host = current.get("host", {})
    base_host = baseline.get("host", {})

    def _diff(path: str, cur, base, severity: Status):
        if cur != base:
            diffs.append({
                "field": path,
                "baseline": base,
                "current": cur,
                "severity": severity.value,
            })

    # CRITICAL: vendor/product change = wrong device entirely
    _diff("camera.vendor_id", cur_cam.get("vendor_id"), base_cam.get("vendor_id"), Status.FAIL)
    _diff("camera.product_id", cur_cam.get("product_id"), base_cam.get("product_id"), Status.FAIL)

    # CRITICAL: serial change = physical swap
    if base_cam.get("serial") is not None:
        _diff("camera.serial", cur_cam.get("serial"), base_cam.get("serial"), Status.FAIL)

    # INFORMATIONAL: firmware drift (update / regression)
    if base_cam.get("firmware") is not None and cur_cam.get("firmware") != base_cam.get("firmware"):
        _diff("camera.firmware", cur_cam.get("firmware"), base_cam.get("firmware"), Status.WARN)

    # INFORMATIONAL: USB port change (replug to different port)
    if base_host.get("sysfs_path") is not None and cur_host.get("sysfs_path") != base_host.get("sysfs_path"):
        _diff("host.sysfs_path", cur_host.get("sysfs_path"), base_host.get("sysfs_path"), Status.WARN)

    # INFORMATIONAL only (kernel can rotate /dev/videoN)
    # — не diff'им v4l_dev и hostname

    # Calibration drift — критично для CV pipeline (3D reconstruction
    # будет давать неверные результаты если intrinsics ≠ ожидаемым).
    cur_cal = current.get("calibration", {})
    base_cal = baseline.get("calibration", {})
    # Только diff'им если baseline has calibration (backward compat с v1
    # fingerprint без calibration field)
    if base_cal:
        for stream_type in ("color", "depth", "infrared"):
            base_intr = base_cal.get(stream_type)
            cur_intr = cur_cal.get(stream_type)
            if base_intr is None:
                continue
            if cur_intr is None:
                _diff(f"calibration.{stream_type}", "MISSING", "PRESENT", Status.WARN)
                continue
            # Сравниваем критичные intrinsics fields (с tolerance для float)
            for field in ("fx", "fy", "ppx", "ppy", "width", "height"):
                b = base_intr.get(field)
                c = cur_intr.get(field)
                if isinstance(b, float) and isinstance(c, float):
                    if abs(b - c) > 0.5:  # >0.5 pixel diff = WARN
                        _diff(f"calibration.{stream_type}.{field}", c, b, Status.WARN)
                elif b != c:
                    _diff(f"calibration.{stream_type}.{field}", c, b, Status.WARN)
            # Distortion coefficients — точное соответствие
            if base_intr.get("coeffs") != cur_intr.get("coeffs"):
                _diff(f"calibration.{stream_type}.coeffs", cur_intr.get("coeffs"),
                      base_intr.get("coeffs"), Status.WARN)

    return diffs


def check(ctx: dict[str, Any]) -> CheckResult:
    current = _current_fingerprint(ctx)
    serial = current["camera"].get("serial")
    details: dict[str, Any] = {
        "fingerprint_path": FINGERPRINT_PATH,
        "current": current,
    }

    baseline = _load_baseline()
    if baseline is None:
        return CheckResult(
            name="fingerprint",
            status=Status.WARN,
            summary=(
                f"baseline отсутствует ({FINGERPRINT_PATH}); "
                f"current serial={serial!r}"
            ),
            details=details,
            fix_hint="sudo python3 -m camera_bringup apply --only fingerprint",
        )

    details["baseline"] = baseline
    diffs = _compare(current, baseline)
    details["diffs"] = diffs

    # HMAC signature check — детектор подделки baseline
    if secret_exists() and "_hmac" in baseline:
        secret = load_secret()
        if secret is None:
            # secret file существует но не читается (permission denied) — пропускаем
            details["hmac_check"] = "secret unreadable"
        else:
            try:
                secret_bytes = bytes.fromhex(secret.decode())
            except (ValueError, UnicodeDecodeError):
                secret_bytes = secret
            if not hmac_verify(baseline, secret_bytes):
                return CheckResult(
                    name="fingerprint",
                    status=Status.FAIL,
                    summary=(
                        f"baseline HMAC signature INVALID — tamper detected "
                        f"({FINGERPRINT_PATH})"
                    ),
                    details={**details, "hmac_check": "INVALID"},
                    fix_hint=(
                        "если изменения легитимны (physical swap или FW update): "
                        "sudo python3 -m camera_bringup apply --only fingerprint "
                        "(перепишет baseline с новой signature)"
                    ),
                )
            details["hmac_check"] = "valid"
    elif baseline.get("_hmac"):
        # Baseline подписан но secret отсутствует — broken state
        details["hmac_check"] = "secret missing for signed baseline"

    if not diffs:
        history = baseline.get("history", {})
        history.get("last_verified_utc", "?")
        return CheckResult(
            name="fingerprint",
            status=Status.OK,
            summary=(
                f"serial={serial} firmware={current['camera'].get('firmware')} "
                f"(baseline since {history.get('first_seen_utc', '?')})"
            ),
            details=details,
        )

    # Highest severity = result severity
    has_fail = any(d["severity"] == Status.FAIL.value for d in diffs)
    severity = Status.FAIL if has_fail else Status.WARN

    diff_strs = [
        f"{d['field']}: {d['baseline']!r} → {d['current']!r}"
        for d in diffs
    ]

    fix_hint = None
    if not has_fail:
        # Только WARN-diffs (firmware update, port change) — можно обновить baseline
        fix_hint = "если изменение ожидаемое: sudo python3 -m camera_bringup apply --only fingerprint"

    return CheckResult(
        name="fingerprint",
        status=severity,
        summary="; ".join(diff_strs),
        details=details,
        fix_hint=fix_hint,
    )

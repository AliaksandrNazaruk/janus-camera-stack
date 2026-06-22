"""c06_v4l2 — проверить V4L2 capability и реальную работу capture.

Что проверяет:
  - v4l2-ctl установлен
  - /dev/cam-rgb открывается V4L2 layer'ом
  - YUYV формат поддерживается
  - комбинация WIDTH×HEIGHT@FPS из spec есть в списке supported
  - capture 3 кадров без error (то же что cam-wait-capture.sh)
  - текущий пользователь имеет права на /dev/cam-rgb

Не делает sustained capture — это уже e2e smoke (c10).

ВАЖНО: если другой процесс держит /dev/cam-rgb (rtp-rgb сервис),
v4l2-ctl --stream-mmap вернёт error EBUSY. Это НЕ FAIL — нормальное состояние,
просто SKIP теста capture.
"""
from __future__ import annotations

import re
import subprocess
from shutil import which
from typing import Any

from camera_bringup.check import CheckResult, Status
from camera_bringup.spec import DEV_SYMLINK, V4L2_SPEC


def _run(cmd: list[str], timeout: float = 5) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _list_formats(device: str) -> list[dict[str, Any]]:
    """Парсим вывод `v4l2-ctl --list-formats-ext` в structured list."""
    try:
        result = _run(["v4l2-ctl", "--device", device, "--list-formats-ext"])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []

    formats: list[dict[str, Any]] = []
    current_fmt: dict[str, Any] | None = None
    current_size: dict[str, Any] | None = None

    for line in result.stdout.splitlines():
        # `[0]: 'YUYV' (YUYV 4:2:2)`
        m_fmt = re.match(r"\s*\[\d+\]:\s*'(\w+)'", line)
        if m_fmt:
            current_fmt = {"format": m_fmt.group(1), "sizes": []}
            formats.append(current_fmt)
            continue
        # `Size: Discrete 640x480`
        m_size = re.search(r"Size:\s*\w+\s+(\d+)x(\d+)", line)
        if m_size and current_fmt is not None:
            current_size = {
                "width": int(m_size.group(1)),
                "height": int(m_size.group(2)),
                "fps": [],
            }
            current_fmt["sizes"].append(current_size)
            continue
        # `Interval: Discrete 0.033s (30.000 fps)` или похожее
        m_fps = re.search(r"\((\d+(?:\.\d+)?)\s*fps\)", line)
        if m_fps and current_size is not None:
            current_size["fps"].append(float(m_fps.group(1)))

    return formats


def _capture_test(device: str, count: int = 3) -> tuple[bool, str]:
    """Попробовать захватить count кадров. Возвращает (ok, reason)."""
    try:
        result = _run(
            [
                "v4l2-ctl",
                "--device",
                device,
                "--stream-mmap",
                f"--stream-count={count}",
                "--stream-to=/dev/null",
            ],
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return False, "timeout (10s) — устройство залипло?"
    except FileNotFoundError:
        return False, "v4l2-ctl не установлен"

    if result.returncode == 0:
        return True, "ok"

    # EBUSY = другой процесс использует устройство → не считаем ошибкой
    err = (result.stderr or "").strip()
    if "Device or resource busy" in err or "EBUSY" in err:
        return False, "BUSY (другой процесс держит устройство — нормально если rtp-rgb работает)"
    return False, err.splitlines()[0] if err else "unknown error"


def check(ctx: dict[str, Any]) -> CheckResult:
    if which("v4l2-ctl") is None:
        return CheckResult(
            name="v4l2",
            status=Status.FAIL,
            summary="v4l2-ctl не установлен (apt install v4l-utils)",
            fix_hint="sudo apt install v4l-utils",
        )

    device = ctx.get("v4l_dev") or DEV_SYMLINK

    formats = _list_formats(device)
    details: dict[str, Any] = {
        "device": device,
        "formats_supported": [f["format"] for f in formats],
    }

    if not formats:
        return CheckResult(
            name="v4l2",
            status=Status.FAIL,
            summary=f"v4l2-ctl не может перечислить форматы для {device}",
            details=details,
            fix_hint="проверить права (`crw-rw---- root video`), модули, замена USB",
        )

    # Найдём наш спек-комбо
    target_fmt = V4L2_SPEC.pixel_format
    target_w = V4L2_SPEC.width
    target_h = V4L2_SPEC.height
    target_fps = V4L2_SPEC.fps

    has_format = False
    has_resolution = False
    has_fps = False
    for f in formats:
        if f["format"] == target_fmt:
            has_format = True
            for sz in f["sizes"]:
                if sz["width"] == target_w and sz["height"] == target_h:
                    has_resolution = True
                    if any(abs(fps - target_fps) < 0.5 for fps in sz["fps"]):
                        has_fps = True

    details["spec"] = f"{target_fmt} {target_w}x{target_h}@{target_fps}fps"
    details["spec_ok"] = {
        "format": has_format,
        "resolution": has_resolution,
        "fps": has_fps,
    }

    if not has_format:
        return CheckResult(
            name="v4l2",
            status=Status.FAIL,
            summary=f"формат {target_fmt} не поддерживается; есть: {details['formats_supported']}",
            details=details,
        )
    if not has_resolution:
        return CheckResult(
            name="v4l2",
            status=Status.FAIL,
            summary=f"разрешение {target_w}x{target_h} не поддерживается для {target_fmt}",
            details=details,
        )
    if not has_fps:
        return CheckResult(
            name="v4l2",
            status=Status.WARN,
            summary=f"{target_fmt} {target_w}x{target_h} поддерживается, но не на {target_fps}fps",
            details=details,
        )

    # Capture test (может пропасть если устройство занято)
    capture_ok, capture_reason = _capture_test(device)
    details["capture_test"] = {"ok": capture_ok, "reason": capture_reason}

    if capture_ok:
        return CheckResult(
            name="v4l2",
            status=Status.OK,
            summary=f"{target_fmt} {target_w}x{target_h}@{target_fps}fps; capture 3 кадра ok",
            details=details,
        )

    # Если BUSY — не fail
    if "BUSY" in capture_reason or "busy" in capture_reason:
        return CheckResult(
            name="v4l2",
            status=Status.OK,
            summary=(
                f"{target_fmt} {target_w}x{target_h}@{target_fps}fps; "
                f"capture skipped (BUSY — другой процесс использует)"
            ),
            details=details,
        )

    return CheckResult(
        name="v4l2",
        status=Status.FAIL,
        summary=f"capture test failed: {capture_reason}",
        details=details,
        fix_hint="hw_reset_realsense.py — попробовать firmware-level reset",
    )

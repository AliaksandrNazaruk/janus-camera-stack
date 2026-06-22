"""c08_bandwidth — посчитать что текущий streaming profile вмещается в USB шину.

Формула:
  raw_bitrate_mbit = width * height * fps * bytes_per_pixel * 8 / 1_000_000
  useful_bus_mbit = USB2 (~360) или USB3 (~4000)
  utilization = raw_bitrate_mbit / useful_bus_mbit * 100%

Если utilization > BANDWIDTH_WARN_PCT (60%) — WARN.
Если > 100% — FAIL (физически не пройдёт).

Берём актуальный профиль из /etc/robot/cam-rgb.env, а не из spec, потому
что админ может крутить FPS/resolution через UI/API.
"""
from __future__ import annotations

import re
from typing import Any

from camera_bringup.check import CheckResult, Status, read_file
from camera_bringup.spec import (
    BANDWIDTH_WARN_PCT,
    BYTES_PER_PIXEL,
    USB2_USEFUL_MBIT,
    USB3_USEFUL_MBIT,
    V4L2_SPEC,
)


def _parse_env(path: str) -> dict[str, str]:
    raw = read_file(path) or ""
    out: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r'(\w+)=["\']?([^"\']+)["\']?', line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def check(ctx: dict[str, Any]) -> CheckResult:
    env = _parse_env("/etc/robot/cam-rgb.env")
    # default to spec если в env нет
    width = int(env.get("WIDTH", V4L2_SPEC.width))
    height = int(env.get("HEIGHT", V4L2_SPEC.height))
    fps = int(env.get("FPS", V4L2_SPEC.fps))
    pix_fmt = env.get("PIX_FMT", V4L2_SPEC.pixel_format).upper()

    # PIX_FMT в env может быть `yuyv422` (lowercase, FFmpeg name).
    # Mapping на наш ключ.
    pix_norm = {
        "YUYV422": "YUYV",
        "YUYV": "YUYV",
        "MJPG": "MJPG",
        "MJPEG": "MJPG",
        "NV12": "NV12",
        "RGB24": "RGB3",
        "RGB3": "RGB3",
    }.get(pix_fmt, pix_fmt)

    bytes_per_px = BYTES_PER_PIXEL.get(pix_norm)
    if bytes_per_px is None:
        return CheckResult(
            name="bandwidth",
            status=Status.WARN,
            summary=f"неизвестный pixel format {pix_fmt!r} — не могу посчитать bandwidth",
            details={"env": env},
            fix_hint=f"добавить {pix_norm} в BYTES_PER_PIXEL spec",
        )

    raw_bitrate_mbit = width * height * fps * bytes_per_px * 8 / 1_000_000

    usb_speed_mbit = ctx.get("usb_speed_mbit", 0)
    if usb_speed_mbit == 480:
        bus_useful = USB2_USEFUL_MBIT
        bus_name = "USB2"
    elif usb_speed_mbit == 5000:
        bus_useful = USB3_USEFUL_MBIT
        bus_name = "USB3"
    else:
        return CheckResult(
            name="bandwidth",
            status=Status.SKIP,
            summary=f"USB speed неизвестен ({usb_speed_mbit}) — не могу посчитать utilization",
        )

    utilization_pct = raw_bitrate_mbit / bus_useful * 100

    details = {
        "profile": {
            "pix_fmt": pix_fmt,
            "width": width,
            "height": height,
            "fps": fps,
            "bytes_per_pixel": bytes_per_px,
        },
        "raw_bitrate_mbit": round(raw_bitrate_mbit, 1),
        "usb_bus": bus_name,
        "usb_useful_mbit": bus_useful,
        "utilization_pct": round(utilization_pct, 1),
        "warn_threshold_pct": BANDWIDTH_WARN_PCT,
    }

    if utilization_pct > 100:
        return CheckResult(
            name="bandwidth",
            status=Status.FAIL,
            summary=(
                f"{raw_bitrate_mbit:.0f} Mbit/s > {bus_useful} Mbit/s ({bus_name}); "
                f"{utilization_pct:.0f}% — физически не пройдёт"
            ),
            details=details,
            fix_hint="снизить WIDTH/HEIGHT/FPS в /etc/robot/cam-rgb.env или переключить на USB3",
        )

    if utilization_pct > BANDWIDTH_WARN_PCT:
        return CheckResult(
            name="bandwidth",
            status=Status.WARN,
            summary=(
                f"{raw_bitrate_mbit:.0f} Mbit/s = {utilization_pct:.0f}% от {bus_name} "
                f"(порог {BANDWIDTH_WARN_PCT}%)"
            ),
            details=details,
            fix_hint=f"запас < {100 - BANDWIDTH_WARN_PCT}% — для headroom переключить на USB3",
        )

    return CheckResult(
        name="bandwidth",
        status=Status.OK,
        summary=(
            f"{raw_bitrate_mbit:.0f} Mbit/s = {utilization_pct:.0f}% от {bus_name} "
            f"({pix_fmt} {width}x{height}@{fps}fps)"
        ),
        details=details,
    )

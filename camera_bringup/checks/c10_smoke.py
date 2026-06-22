"""c10_smoke — лёгкий end-to-end smoke без побочных эффектов.

В verify-only MVP мы НЕ запускаем ffmpeg (он бы конфликтнул с работающим
rs-stream@color encoder).

Что проверяем (Phase 2: color via mux, не V4L2 rtp-rgb):
  - ffmpeg binary установлен
  - ffmpeg умеет libx264 (codec encoder list)
  - rs-stream.sh установлен и executable (FIFO→RTP encoder script)
  - /run/realsense существует (mux FIFOs + color-snapshot.jpg)
  - realsense-mux.service в systemd inventory (loaded) — color/depth/ir producer
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from shutil import which
from typing import Any

from camera_bringup.check import CheckResult, Status

RS_STREAM_SCRIPT = "/usr/local/bin/rs-stream.sh"
RUN_DIR = "/run/realsense"
SERVICE_NAME = "realsense-mux.service"


def _has_libx264() -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, "ffmpeg exited non-zero"
    if "libx264" in result.stdout:
        return True, "ok"
    return False, "libx264 не в списке encoders"


def _systemd_service_loaded(name: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["systemctl", "show", name, "-p", "LoadState", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    state = (result.stdout or "").strip()
    return state == "loaded", state or "unknown"


def check(ctx: dict[str, Any]) -> CheckResult:
    issues: list[str] = []
    severity = Status.OK

    # 1. ffmpeg
    ffmpeg_path = which("ffmpeg")
    if not ffmpeg_path:
        return CheckResult(
            name="smoke",
            status=Status.FAIL,
            summary="ffmpeg не установлен (apt install ffmpeg)",
            fix_hint="sudo apt install ffmpeg",
        )

    has_libx264, libx264_msg = _has_libx264()
    if not has_libx264:
        issues.append(f"ffmpeg без libx264: {libx264_msg}")
        severity = Status.FAIL

    # 2. rs-stream.sh encoder script
    if not Path(RS_STREAM_SCRIPT).is_file():
        issues.append(f"{RS_STREAM_SCRIPT} отсутствует")
        severity = Status.FAIL
    elif not os.access(RS_STREAM_SCRIPT, os.X_OK):
        issues.append(f"{RS_STREAM_SCRIPT} не executable")
        severity = Status.WARN if severity != Status.FAIL else severity

    # 3. /run/realsense dir
    run_dir = Path(RUN_DIR)
    if not run_dir.is_dir():
        issues.append(f"{RUN_DIR} не существует (создаётся RuntimeDirectory realsense-mux)")
        severity = Status.WARN if severity != Status.FAIL else severity

    # 4. systemd service inventory
    svc_loaded, svc_state = _systemd_service_loaded(SERVICE_NAME)
    if not svc_loaded:
        issues.append(f"{SERVICE_NAME} LoadState={svc_state} (ожидали loaded)")
        severity = Status.FAIL

    details = {
        "ffmpeg_path": ffmpeg_path,
        "ffmpeg_has_libx264": has_libx264,
        "rs_stream_script": RS_STREAM_SCRIPT,
        "rs_stream_script_present": Path(RS_STREAM_SCRIPT).is_file(),
        "run_dir": RUN_DIR,
        "run_dir_present": run_dir.is_dir(),
        "systemd_unit": SERVICE_NAME,
        "systemd_state": svc_state,
    }

    if not issues:
        return CheckResult(
            name="smoke",
            status=Status.OK,
            summary=(
                f"ffmpeg+libx264 ok; {RS_STREAM_SCRIPT} ok; "
                f"{SERVICE_NAME} loaded"
            ),
            details=details,
        )

    return CheckResult(
        name="smoke",
        status=severity,
        summary="; ".join(issues),
        details=details,
    )

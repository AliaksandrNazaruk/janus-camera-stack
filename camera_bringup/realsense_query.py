"""Хелпер: вытащить идентификацию RealSense через pyrealsense2.

Запускает subprocess в ЦЕЛЕВОМ python interpreter'е (тот где установлен
pyrealsense2 — обычно .venv). Это потому что system python (где живёт
camera_bringup) не имеет pyrealsense2 — PEP 668 блокирует system pip,
а заводить его в каждой venv избыточно.

Используется в c11 (check) и f11 (fixer fingerprint).
"""
from __future__ import annotations

import json
import subprocess
from typing import Any

from camera_bringup.spec import PYREALSENSE_PYTHON

# Inline-скрипт который subprocess исполняет. Возвращает JSON в stdout.
# Намеренно minimal — никаких import'ов кроме pyrealsense2.
_QUERY_SCRIPT = """
import json, sys
import pyrealsense2 as rs
ctx = rs.context()
devs = ctx.query_devices()
out = []
for d in devs:
    info = {}
    for key, attr in [
        ('name',     rs.camera_info.name),
        ('serial',   rs.camera_info.serial_number),
        ('firmware', rs.camera_info.firmware_version),
        ('usb_type', rs.camera_info.usb_type_descriptor),
        ('product_id', rs.camera_info.product_id),
        ('product_line', rs.camera_info.product_line),
    ]:
        try:
            info[key] = d.get_info(attr)
        except Exception:
            info[key] = None

    # Calibration intrinsics — для каждого активного sensor:
    # color (RGB) и depth. Запрашиваем default video stream profile и
    # извлекаем rs2_intrinsics. Это factory calibration из EEPROM камеры.
    info['calibration'] = {}
    for sensor in d.query_sensors():
        sname = sensor.get_info(rs.camera_info.name) if sensor.supports(rs.camera_info.name) else 'unknown'
        for sp in sensor.get_stream_profiles():
            try:
                vsp = sp.as_video_stream_profile()
                stype = sp.stream_type().name.lower()  # 'depth', 'color', 'infrared'
                # Берём только первый stream profile per (sensor, stream_type)
                if stype in info['calibration']:
                    continue
                intr = vsp.get_intrinsics()
                info['calibration'][stype] = {
                    'sensor': sname,
                    'width': intr.width,
                    'height': intr.height,
                    'fx': intr.fx,
                    'fy': intr.fy,
                    'ppx': intr.ppx,    # principal point x (cx)
                    'ppy': intr.ppy,    # principal point y (cy)
                    'model': intr.model.name,   # distortion model
                    'coeffs': list(intr.coeffs),
                }
            except Exception:
                continue
    out.append(info)
json.dump({'devices': out}, sys.stdout)
"""


def query_realsense_devices(timeout: float = 10) -> dict[str, Any]:
    """Возвращает {'devices': [...]} с identification каждого устройства,
    или {'error': '...'} если pyrealsense2 недоступен или USB call failed.

    Не raises — все ошибки в structured response.
    """
    try:
        result = subprocess.run(
            [PYREALSENSE_PYTHON, "-c", _QUERY_SCRIPT],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return {"error": f"interpreter not found: {PYREALSENSE_PYTHON}"}
    except subprocess.TimeoutExpired:
        return {"error": f"timeout ({timeout}s) querying RealSense"}

    if result.returncode != 0:
        err = (result.stderr or "").strip().splitlines()
        return {"error": err[-1] if err else f"exit {result.returncode}"}

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": f"unparseable output: {result.stdout[:200]}"}


def primary_device() -> dict[str, Any] | None:
    """Удобный wrapper — возвращает первый device или None."""
    response = query_realsense_devices()
    if "error" in response:
        return None
    devices = response.get("devices", [])
    return devices[0] if devices else None

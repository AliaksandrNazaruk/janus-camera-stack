"""Canonical "spec" — эталонные значения которые проверяют все checks.

Это single source of truth для bringup. Любое изменение здесь = изменение
во всех проверках. Никаких «волшебных чисел» внутри отдельных checks —
все берут отсюда.

Пока заточено под единственный сценарий .10:
  - Intel RealSense D435i (8086:0b3a), используется только RGB sensor
  - V4L2 capture через uvcvideo
  - 640x480 YUYV @ 15fps
  - USB2 порт (480 Mbit)

## Environment overrides

Все hardcoded пути могут быть переопределены через ENV vars (12-factor
compliance — для деплоя на других узлах или в тестовой среде):

  CAMERA_BRINGUP_ROBOT_HOME    — корень проекта (default /home/boris/robot)
  CAMERA_BRINGUP_HOME          — корень L0 (default $ROBOT_HOME/camera_bringup)
  CAMERA_BRINGUP_FINGERPRINT   — fingerprint.json path
  CAMERA_BRINGUP_UDEV_DIR      — udev rules dir (default /etc/udev/rules.d)
  CAMERA_BRINGUP_MODPROBE_CONF — modprobe path (default /etc/modprobe.d/uvcvideo.conf)
  CAMERA_BRINGUP_LOCK_FILE     — flock path (default /run/camera_bringup.lock)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# ── Environment-overridable roots ─────────────────────────────────────

ROBOT_HOME = Path(os.environ.get("CAMERA_BRINGUP_ROBOT_HOME", "/home/boris/robot"))
BRINGUP_HOME = Path(os.environ.get(
    "CAMERA_BRINGUP_HOME",
    str(ROBOT_HOME / "camera_bringup"),
))


# ── Active instance — loaded at import time ──────────────────────────
# L0 = template. Active instance задаётся через ENV CAMERA_BRINGUP_INSTANCE
# (default 'cam-rgb'). Все hardcoded values ниже = re-export из active
# InstanceSpec для обратной совместимости со старыми checks/fixers.
# См. instance.py + instances/<id>.toml.

from camera_bringup.instance import (  # noqa: E402  # после ENV-reading consts выше
    InstanceSpec,
    load_instance,
)

ACTIVE_INSTANCE: InstanceSpec = load_instance()


# ── Hardware identity (re-exported из ACTIVE_INSTANCE для backwards compat) ──

USB_VENDOR_ID = ACTIVE_INSTANCE.hardware.usb_vendor_id
USB_PRODUCT_ID = ACTIVE_INSTANCE.hardware.usb_product_id
USB_INTERFACE_NUM_RGB = ACTIVE_INSTANCE.hardware.usb_interface_num
DEVICE_FRIENDLY_NAME = ACTIVE_INSTANCE.friendly_name or "Intel RealSense D435i"

MIN_FIRMWARE_BCD = ACTIVE_INSTANCE.firmware.min_bcd


# ── Filesystem paths ──────────────────────────────────────────────────

DEV_SYMLINK = os.environ.get(
    "CAMERA_BRINGUP_DEV_SYMLINK",
    f"/dev/{ACTIVE_INSTANCE.dev_symlink_name}",
)
UDEV_RULES_DIR = os.environ.get("CAMERA_BRINGUP_UDEV_DIR", "/etc/udev/rules.d")
UDEV_RULE_NAME = ACTIVE_INSTANCE.udev_rule_filename

MODPROBE_CONF = os.environ.get("CAMERA_BRINGUP_MODPROBE_CONF", "/etc/modprobe.d/uvcvideo.conf")

# Disabled rules — должны оставаться disabled, не конфликтовать
LEGACY_DISABLED_RULES = [
    "99-realsense-d435i.rules.disabled",
    "99-realsense-rgb.rules.disabled",
]

# Старые udev rules которые НЕ должны существовать активно
LEGACY_FORBIDDEN_RULES = [
    "99-realsense-d435i.rules",         # старый подход через ATTRS{interface}
    "99-realsense-rgb.rules",           # старый подход через ATTR{name}
]


# ── USB power management — желаемые значения ──────────────────────────

@dataclass(frozen=True)
class UsbPowerSpec:
    control: str = "on"                # control=on → kernel НЕ suspendит
    autosuspend: int = -1              # -1 = disabled (timeout бесконечный)
    persist: int = 0                   # 0 = НЕ сохранять state при suspend (для RealSense — нужно re-init)
    runtime_status: str = "active"     # должна быть активной всегда


USB_POWER_SPEC = UsbPowerSpec()


# ── uvcvideo module parameters ────────────────────────────────────────

@dataclass(frozen=True)
class UvcVideoSpec:
    nodrop: int = 1                    # не дропать corrupted frames
    timeout: int = 500                 # 500ms USB transfer timeout (агрессивно)
    quirks: int = 128                  # 0x80 — workaround для RealSense FW bug


UVCVIDEO_SPEC = UvcVideoSpec()


# ── V4L2 streaming profile ────────────────────────────────────────────

@dataclass(frozen=True)
class V4L2Spec:
    pixel_format: str = "YUYV"
    width: int = 640
    height: int = 480
    fps: int = 15


# V4L2_SPEC берётся из active instance (за backward compat)
V4L2_SPEC = V4L2Spec(
    pixel_format=ACTIVE_INSTANCE.stream.pixel_format,
    width=ACTIVE_INSTANCE.stream.width,
    height=ACTIVE_INSTANCE.stream.height,
    fps=ACTIVE_INSTANCE.stream.fps,
)


# ── USB bandwidth budget ──────────────────────────────────────────────

# Bytes per pixel for known V4L2 pixel formats (raw frame size estimate).
# Используется в bandwidth check для прикидки нагрузки на USB шину.
BYTES_PER_PIXEL: dict[str, float] = {
    "YUYV": 2.0,
    "MJPG": 0.3,    # эмпирический коэффициент сжатия — для info, не для лимита
    "NV12": 1.5,
    "RGB3": 3.0,
}

# Useful USB bandwidth (после overhead protocol). USB 2.0 spec = 480 Mbit/s,
# но реально доступно ~350-400 Mbit/s. USB 3.0 = 5 Gbit/s, реально ~4 Gbit/s.
USB2_USEFUL_MBIT = 360
USB3_USEFUL_MBIT = 4000

# Если рассчитанная нагрузка превышает этот процент полезной полосы — warn.
BANDWIDTH_WARN_PCT = 60


# ── External tools ────────────────────────────────────────────────────

REQUIRED_TOOLS = {
    "lsusb": "USB device enumeration",
    "udevadm": "udev introspection",
    "v4l2-ctl": "V4L2 device control",
    "ffmpeg": "encoder pipeline (only checked for presence)",
}

OPTIONAL_TOOLS = {
    "usbreset": "manual USB reset (alternative to udev re-trigger)",
}


# ── L0-owned reset tools ──────────────────────────────────────────────
# С 2026-06-14 L0 владеет своими reset инструментами полностью:
#   - hw_reset_realsense.py в camera_bringup/ (был в janus_camera_page/)
#   - dedicated venv в camera_bringup/.venv (не shared с другими сервисами)
# См. CONTRACT.md §1 + §10 migration notes.

HW_RESET_SCRIPT = str(BRINGUP_HOME / "hw_reset_realsense.py")

L0_VENV_DIR = str(BRINGUP_HOME / ".venv")
L0_VENV_PYTHON = str(BRINGUP_HOME / ".venv" / "bin" / "python3")
L0_VENV_PIP = str(BRINGUP_HOME / ".venv" / "bin" / "pip")

# pyrealsense2 модуль должен быть импортируем (для hw_reset)
PYREALSENSE_IMPORT_NAME = "pyrealsense2"


# ── Fingerprint file (per-instance) ──────────────────────────────────

FINGERPRINT_DIR = os.environ.get("CAMERA_BRINGUP_FINGERPRINT_DIR", "/var/lib/camera")
# Default: /var/lib/camera/<instance_id>.json — изолированные baseline per instance
FINGERPRINT_PATH = os.environ.get(
    "CAMERA_BRINGUP_FINGERPRINT",
    str(Path(FINGERPRINT_DIR) / f"{ACTIVE_INSTANCE.instance_id}.json"),
)
FINGERPRINT_SCHEMA_VERSION = 1

# ── Concurrency lock (per-instance) ──────────────────────────────────

# Default per-instance lock: /run/camera_bringup-<instance>.lock
LOCK_FILE = os.environ.get(
    "CAMERA_BRINGUP_LOCK_FILE",
    f"/run/camera_bringup-{ACTIVE_INSTANCE.instance_id}.lock",
)

# ── HMAC secret (для tamper-detection fingerprint) ───────────────────

# Mode 600, root-only file. Создаётся при первом apply --only fingerprint.
# Если файл отсутствует — fingerprint работает без signature (legacy mode).
HMAC_SECRET_PATH = os.environ.get(
    "CAMERA_BRINGUP_HMAC_SECRET",
    "/etc/camera_bringup/secret.key",
)
HMAC_SECRET_DIR = os.path.dirname(HMAC_SECRET_PATH)

# Python interpreter в котором установлен pyrealsense2 (нужно для serial extraction).
# Указывает на L0 dedicated venv (см. L0_VENV_PYTHON ниже).
# DEPRECATED alias — используйте L0_VENV_PYTHON напрямую.
PYREALSENSE_PYTHON = "/home/boris/robot/camera_bringup/.venv/bin/python3"

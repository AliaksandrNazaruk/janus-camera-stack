"""Registry всех checks. Порядок в ALL_CHECKS определяет порядок выполнения и
вывода. Зависимости checks друг от друга идут через `ctx` (общий dict),
который runner передаёт каждому: например c01_usb_enumerate кладёт туда
sysfs path, c02_usb_power его берёт.
"""
from __future__ import annotations

from camera_bringup.check import CheckFn

from .c01_usb_enumerate import check as usb_enumerate
from .c02_usb_power import check as usb_power
from .c03_uvcvideo import check as uvcvideo
from .c04_udev import check as udev
from .c05_dev_symlinks import check as dev_symlinks
from .c06_v4l2 import check as v4l2
from .c07_firmware import check as firmware
from .c08_bandwidth import check as bandwidth
from .c09_reset_tools import check as reset_tools
from .c10_smoke import check as smoke
from .c11_fingerprint import check as fingerprint

# Каждый элемент: (id, function). id используется в --only=<id,id>.
ALL_CHECKS: list[tuple[str, CheckFn]] = [
    ("usb_enumerate", usb_enumerate),
    ("usb_power",     usb_power),
    ("uvcvideo",      uvcvideo),
    ("udev",          udev),
    ("dev_symlinks",  dev_symlinks),
    ("v4l2",          v4l2),
    ("firmware",      firmware),
    ("bandwidth",     bandwidth),
    ("reset_tools",   reset_tools),
    ("smoke",         smoke),
    ("fingerprint",   fingerprint),
]


def get_check(name: str) -> CheckFn:
    for n, fn in ALL_CHECKS:
        if n == name:
            return fn
    raise KeyError(f"unknown check: {name}")

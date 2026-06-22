"""Registry всех fixers. Один fixer покрывает один check (по имени).
"""
from __future__ import annotations

from camera_bringup.fixer import Fixer

from .f02_usb_power import UsbPowerFixer
from .f04_udev import UdevFixer
from .f09_reset_tools import ResetToolsFixer
from .f11_fingerprint import FingerprintFixer

ALL_FIXERS: dict[str, type[Fixer]] = {
    "usb_power":   UsbPowerFixer,
    "udev":        UdevFixer,
    "reset_tools": ResetToolsFixer,
    "fingerprint": FingerprintFixer,
}


def get_fixer(check_name: str) -> type[Fixer] | None:
    return ALL_FIXERS.get(check_name)

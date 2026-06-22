"""Fitness: verify и dry-run apply НЕ должны менять состояние системы.

Запускаем сравниваем mtime критических файлов до и после verify — не должны
поменяться.
"""
from __future__ import annotations

import os

import pytest

pytestmark = [pytest.mark.fitness, pytest.mark.integration]


WATCHED_FILES = [
    # L0-owned (verify не должен трогать, dry-run apply не должен трогать)
    "/etc/udev/rules.d/99-cam-rgb.rules",
    "/etc/udev/rules.d/99-usb-nosuspend-d435i.rules",
    "/etc/modprobe.d/uvcvideo.conf",
    # L0 НЕ должен пересекаться с этими (boundary watchlist)
    "/etc/robot/cam-rgb.env",                              # owned by L4
    "/opt/janus-camera-page/hw_reset_realsense.py",  # deprecated, owned by L4 historical
    "/opt/janus-camera-page/.venv/pyvenv.cfg",                  # shared venv, L0 не трогает
]


def _snapshot_mtimes() -> dict:
    out = {}
    for f in WATCHED_FILES:
        if os.path.exists(f):
            out[f] = os.path.getmtime(f)
    return out


class TestVerifyReadOnly:
    def test_verify_does_not_modify_watched_files(self):
        from camera_bringup.api import L0
        before = _snapshot_mtimes()
        # verify через .status() — full chain
        L0.status()
        L0.postconditions()
        L0.summary()
        after = _snapshot_mtimes()
        assert before == after, (
            f"verify изменил файлы: "
            f"{[f for f in before if before.get(f) != after.get(f)]}"
        )

    def test_dry_run_apply_does_not_modify_watched_files(self):
        from camera_bringup.api import L0
        before = _snapshot_mtimes()
        L0.attempt_recovery(dry_run=True)
        after = _snapshot_mtimes()
        assert before == after, "dry-run apply изменил файлы — bug в Fixer"


class TestBoundaryRespect:
    """L0 не должен модифицировать ничего за пределами своих owned путей.
    См. CONTRACT.md §0 'Owned resources'."""

    def test_apply_dry_run_does_not_target_external_paths(self):
        """Plan'ы всех fixers должны указывать только на L0-owned paths."""
        from camera_bringup.fixers import ALL_FIXERS

        # Допустимые префиксы куда L0 может писать
        from camera_bringup.spec import FINGERPRINT_DIR, HMAC_SECRET_DIR
        ALLOWED_WRITE_PREFIXES = (
            "/opt/janus-camera-page/camera_bringup/",   # L0 source + venv + hw_reset
            FINGERPRINT_DIR,                        # /var/lib/camera/
            HMAC_SECRET_DIR,                        # /etc/camera_bringup/ (HMAC secret)
            "/etc/udev/rules.d/",                   # naming-scope: только наши N rules
            "udevadm",                              # binary name (для kind=run)
            "udevadm control",
            "udevadm trigger",
            "udevadm settle",
            "python3",                              # для python3 -m venv
            "mkdir",
            "chmod",                                # для secret file permissions
        )

        ctx = {"sysfs_path": "/sys/bus/usb/devices/2-2",
               "usb_speed_mbit": 480, "v4l_dev": "/dev/video4"}

        for name, cls in ALL_FIXERS.items():
            fixer = cls()
            actions = fixer.plan(ctx)
            for a in actions:
                target = a.target
                # Допускаем если target начинается с одного из allowed префиксов
                ok = any(target.startswith(p) or target == p.rstrip()
                         for p in ALLOWED_WRITE_PREFIXES)
                assert ok, (
                    f"Fixer {name} action {a.kind} targets {target!r} — "
                    f"вне L0 owned paths! См. CONTRACT.md §0"
                )


class TestVerifyDoesNotTouchSysfs:
    def test_usb_power_check_does_not_write(self, real_camera_required):
        # Перед и после verify usb_power values должны быть identical
        import subprocess

        from camera_bringup.checks.c02_usb_power import check

        def _read_power():
            return subprocess.run(
                ["cat",
                 "/sys/bus/usb/devices/2-2/power/control",
                 "/sys/bus/usb/devices/2-2/power/persist",
                 "/sys/bus/usb/devices/2-2/power/autosuspend"],
                capture_output=True, text=True
            ).stdout

        before = _read_power()
        check({"sysfs_path": "/sys/bus/usb/devices/2-2"})
        after = _read_power()
        assert before == after, "c02 check изменил USB power state"

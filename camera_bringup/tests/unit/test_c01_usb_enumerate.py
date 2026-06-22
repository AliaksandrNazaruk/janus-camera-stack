"""Unit-тесты c01_usb_enumerate.check() с mock'ом /sys/bus/usb/devices.

Покрываем:
  - камера не найдена → FAIL
  - найдена ОДНА на USB2 → WARN (USB2 cable)
  - найдена ОДНА на USB3 → OK
  - найдены ДВЕ → FAIL (race за SYMLINK)
  - ctx правильно populated с sysfs_path и usb_speed_mbit
"""
from __future__ import annotations

from pathlib import Path

import pytest

from camera_bringup.check import Status


def _make_fake_usb_device(base: Path, dev_id: str, vendor: str, product: str,
                          speed: str = "480", power: str = "496mA",
                          bus: str = "2", devnum: str = "2") -> Path:
    """Создаёт fake sysfs USB device dir под base/."""
    dev = base / dev_id
    dev.mkdir(parents=True, exist_ok=True)
    (dev / "idVendor").write_text(vendor)
    (dev / "idProduct").write_text(product)
    (dev / "speed").write_text(speed)
    (dev / "bMaxPower").write_text(power)
    (dev / "busnum").write_text(bus)
    (dev / "devnum").write_text(devnum)
    return dev


class TestUsbEnumerate:
    @pytest.fixture
    def fake_sysfs(self, tmp_path, monkeypatch):
        """Substitute /sys/bus/usb/devices глобом."""
        fake_dir = tmp_path / "usb_devices"
        fake_dir.mkdir()
        # monkeypatch glob inside c01 module
        from camera_bringup.checks import c01_usb_enumerate

        def fake_glob(pattern):
            # pattern = "/sys/bus/usb/devices/*/idProduct"
            return [str(p) for p in fake_dir.glob("*/idProduct")]

        monkeypatch.setattr(c01_usb_enumerate.glob, "glob", fake_glob)
        return fake_dir

    def test_no_camera_found_is_fail(self, fake_sysfs):
        # Empty sysfs — no devices
        from camera_bringup.checks.c01_usb_enumerate import check
        result = check({})
        assert result.status == Status.FAIL
        assert "не найдена" in result.summary

    def test_other_device_does_not_match(self, fake_sysfs):
        # Mouse / random USB
        _make_fake_usb_device(fake_sysfs, "1-1", vendor="046d", product="c52b")
        from camera_bringup.checks.c01_usb_enumerate import check
        result = check({})
        assert result.status == Status.FAIL

    def test_single_d435i_on_usb2_is_warn(self, fake_sysfs):
        _make_fake_usb_device(fake_sysfs, "2-2", vendor="8086", product="0b3a", speed="480")
        from camera_bringup.checks.c01_usb_enumerate import check
        ctx = {}
        result = check(ctx)
        assert result.status == Status.WARN
        assert "USB2" in result.summary
        # ctx должен быть populated
        assert ctx["sysfs_path"].endswith("2-2")
        assert ctx["usb_speed_mbit"] == 480

    def test_single_d435i_on_usb3_is_ok(self, fake_sysfs):
        _make_fake_usb_device(fake_sysfs, "3-1", vendor="8086", product="0b3a", speed="5000")
        from camera_bringup.checks.c01_usb_enumerate import check
        ctx = {}
        result = check(ctx)
        assert result.status == Status.OK
        assert "USB3" in result.summary
        assert ctx["usb_speed_mbit"] == 5000

    def test_two_d435i_is_fail_race_condition(self, fake_sysfs):
        _make_fake_usb_device(fake_sysfs, "2-1", vendor="8086", product="0b3a")
        _make_fake_usb_device(fake_sysfs, "3-1", vendor="8086", product="0b3a", speed="5000")
        from camera_bringup.checks.c01_usb_enumerate import check
        result = check({})
        assert result.status == Status.FAIL
        assert "race" in result.summary.lower() or "несколько" in result.summary.lower() or "2" in result.summary

    def test_unknown_speed_is_warn(self, fake_sysfs):
        # Странный speed (USB 1.1 = 12 Mbit)
        _make_fake_usb_device(fake_sysfs, "1-1", vendor="8086", product="0b3a", speed="12")
        from camera_bringup.checks.c01_usb_enumerate import check
        result = check({})
        # Не OK и не FAIL — unhandled scenario
        assert result.status == Status.WARN
        assert "12" in result.summary or "непонятная" in result.summary.lower()

"""Unit-тесты для c11_fingerprint._compare — pure diff logic.

Покрываем все варианты drift'а (vendor/product/serial/firmware/usb_port).
"""
from __future__ import annotations

from camera_bringup.check import Status
from camera_bringup.checks.c11_fingerprint import _compare


def _make_state(serial="141722072135", firmware="5.16.0.1",
                vendor="8086", product="0b3a",
                sysfs_path="/sys/bus/usb/devices/2-2"):
    return {
        "camera": {
            "vendor_id": vendor,
            "product_id": product,
            "serial": serial,
            "firmware": firmware,
        },
        "host": {
            "sysfs_path": sysfs_path,
        },
    }


class TestCompare:
    def test_identical_no_diffs(self):
        s = _make_state()
        assert _compare(s, s) == []

    def test_serial_change_is_fail(self):
        cur = _make_state(serial="OLD123")
        base = _make_state(serial="NEW456")
        diffs = _compare(cur, base)
        serial_diff = next(d for d in diffs if d["field"] == "camera.serial")
        assert serial_diff["severity"] == Status.FAIL.value

    def test_vendor_change_is_fail(self):
        cur = _make_state(vendor="9999")
        base = _make_state(vendor="8086")
        diffs = _compare(cur, base)
        assert any(d["field"] == "camera.vendor_id" and d["severity"] == "FAIL"
                   for d in diffs)

    def test_product_change_is_fail(self):
        cur = _make_state(product="ffff")
        base = _make_state(product="0b3a")
        diffs = _compare(cur, base)
        assert any(d["field"] == "camera.product_id" and d["severity"] == "FAIL"
                   for d in diffs)

    def test_firmware_change_is_warn(self):
        cur = _make_state(firmware="5.16.0.1")
        base = _make_state(firmware="5.15.0.0")
        diffs = _compare(cur, base)
        fw_diff = next(d for d in diffs if d["field"] == "camera.firmware")
        assert fw_diff["severity"] == Status.WARN.value

    def test_sysfs_path_change_is_warn(self):
        # Replug в другой USB порт
        cur = _make_state(sysfs_path="/sys/bus/usb/devices/3-1")
        base = _make_state(sysfs_path="/sys/bus/usb/devices/2-2")
        diffs = _compare(cur, base)
        path_diff = next(d for d in diffs if d["field"] == "host.sysfs_path")
        assert path_diff["severity"] == Status.WARN.value

    def test_baseline_serial_none_does_not_diff(self):
        # Если в baseline serial=None (старый формат / pyrealsense2 был
        # недоступен при создании), не считаем за diff
        cur = _make_state(serial="141722072135")
        base = _make_state(serial=None)
        diffs = _compare(cur, base)
        assert not any(d["field"] == "camera.serial" for d in diffs)

    def test_multiple_drifts_all_reported(self):
        cur = _make_state(serial="A", firmware="5.16.0.1", sysfs_path="/sys/bus/usb/devices/3-1")
        base = _make_state(serial="B", firmware="5.15.0.0", sysfs_path="/sys/bus/usb/devices/2-2")
        diffs = _compare(cur, base)
        fields = {d["field"] for d in diffs}
        assert "camera.serial" in fields
        assert "camera.firmware" in fields
        assert "host.sysfs_path" in fields

    def test_priority_fail_over_warn_in_diffs(self):
        """Если есть и FAIL и WARN diff — оба возвращаются, агрегацию делает
        check выше. _compare сама просто возвращает все."""
        cur = _make_state(serial="A", firmware="5.16.0.1")
        base = _make_state(serial="B", firmware="5.15.0.0")
        diffs = _compare(cur, base)
        severities = {d["severity"] for d in diffs}
        assert "FAIL" in severities
        assert "WARN" in severities

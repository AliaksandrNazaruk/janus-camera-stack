"""Unit-тесты c02_usb_power.check() с fake sysfs power dir.
"""
from __future__ import annotations

from camera_bringup.check import Status


def _make_power_dir(base, control="on", autosuspend="-1", persist="0",
                    runtime_status="active"):
    """Создаёт fake sysfs power/ dir."""
    p = base / "power"
    p.mkdir(parents=True, exist_ok=True)
    (p / "control").write_text(control)
    (p / "autosuspend").write_text(autosuspend)
    (p / "persist").write_text(persist)
    (p / "runtime_status").write_text(runtime_status)


class TestUsbPower:
    def test_no_sysfs_path_skips(self):
        from camera_bringup.checks.c02_usb_power import check
        result = check({})  # no ctx['sysfs_path']
        assert result.status == Status.SKIP

    def test_canonical_state_is_ok(self, tmp_path):
        _make_power_dir(tmp_path, control="on", autosuspend="-1", persist="0",
                        runtime_status="active")
        from camera_bringup.checks.c02_usb_power import check
        result = check({"sysfs_path": str(tmp_path)})
        assert result.status == Status.OK

    def test_control_auto_with_autosuspend_off_is_warn(self, tmp_path):
        """control=auto + autosuspend=-1 = технически работает (timeout бесконечный),
        но риск если кто-то изменит autosuspend."""
        _make_power_dir(tmp_path, control="auto", autosuspend="-1", persist="0")
        from camera_bringup.checks.c02_usb_power import check
        result = check({"sysfs_path": str(tmp_path)})
        assert result.status == Status.WARN
        assert "control" in result.summary.lower()

    def test_control_auto_with_autosuspend_positive_is_fail(self, tmp_path):
        """control=auto + autosuspend > 0 = камера засуспендится."""
        _make_power_dir(tmp_path, control="auto", autosuspend="60", persist="0")
        from camera_bringup.checks.c02_usb_power import check
        result = check({"sysfs_path": str(tmp_path)})
        assert result.status == Status.FAIL
        assert "60" in result.summary

    def test_persist_1_is_warn(self, tmp_path):
        _make_power_dir(tmp_path, control="on", autosuspend="-1", persist="1")
        from camera_bringup.checks.c02_usb_power import check
        result = check({"sysfs_path": str(tmp_path)})
        assert result.status == Status.WARN
        assert "persist" in result.summary

    def test_runtime_status_not_active_is_fail(self, tmp_path):
        """Если runtime_status='suspended' — камера прямо сейчас спит."""
        _make_power_dir(tmp_path, control="on", autosuspend="-1", persist="0",
                        runtime_status="suspended")
        from camera_bringup.checks.c02_usb_power import check
        result = check({"sysfs_path": str(tmp_path)})
        assert result.status == Status.FAIL

    def test_details_contain_expected_fields(self, tmp_path):
        _make_power_dir(tmp_path)
        from camera_bringup.checks.c02_usb_power import check
        result = check({"sysfs_path": str(tmp_path)})
        assert "control" in result.details
        assert "autosuspend" in result.details
        assert "persist" in result.details
        assert "expected" in result.details

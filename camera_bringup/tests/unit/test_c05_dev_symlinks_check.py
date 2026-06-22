"""Unit-тесты c05_dev_symlinks.check() с mock'ом os.path + udevadm.
"""
from __future__ import annotations

import pytest

from camera_bringup.check import Status


class TestDevSymlinksCheck:
    @pytest.fixture
    def fake_symlink(self, tmp_path, monkeypatch):
        """Создаёт fake /dev/cam-rgb → /dev/video4 в tmp."""
        fake_target = tmp_path / "video4"
        fake_target.write_text("fake device")  # placeholder file
        fake_link = tmp_path / "cam-rgb"
        fake_link.symlink_to(fake_target)

        from camera_bringup.checks import c05_dev_symlinks
        monkeypatch.setattr(c05_dev_symlinks, "DEV_SYMLINK", str(fake_link))
        return fake_link, fake_target

    def _make_udev_props(self, interface="03", capabilities=":capture:",
                          vendor="8086", product="0b3a"):
        return {
            "ID_USB_INTERFACE_NUM": interface,
            "ID_V4L_CAPABILITIES": capabilities,
            "ID_VENDOR_ID": vendor,
            "ID_MODEL_ID": product,
        }

    def _patch_udev(self, monkeypatch, props):
        from camera_bringup.checks import c05_dev_symlinks
        monkeypatch.setattr(c05_dev_symlinks, "_udev_properties", lambda dev: props)

    def test_canonical_state_is_ok(self, fake_symlink, monkeypatch):
        self._patch_udev(monkeypatch, self._make_udev_props())
        from camera_bringup.checks.c05_dev_symlinks import check
        ctx = {}
        result = check(ctx)
        assert result.status == Status.OK
        assert ctx["v4l_dev"] == str(fake_symlink[1])

    def test_missing_symlink_is_fail(self, tmp_path, monkeypatch):
        from camera_bringup.checks import c05_dev_symlinks
        monkeypatch.setattr(c05_dev_symlinks, "DEV_SYMLINK", str(tmp_path / "nonexistent"))
        result = c05_dev_symlinks.check({})
        assert result.status == Status.FAIL
        assert "не существует" in result.summary

    def test_wrong_interface_is_fail(self, fake_symlink, monkeypatch):
        # interface=00 = depth, не RGB
        self._patch_udev(monkeypatch, self._make_udev_props(interface="00"))
        from camera_bringup.checks.c05_dev_symlinks import check
        result = check({})
        assert result.status == Status.FAIL
        assert "interface" in result.summary

    def test_wrong_vendor_is_fail(self, fake_symlink, monkeypatch):
        self._patch_udev(monkeypatch, self._make_udev_props(vendor="ffff"))
        from camera_bringup.checks.c05_dev_symlinks import check
        result = check({})
        assert result.status == Status.FAIL
        assert "ffff" in result.summary

    def test_not_capture_node_is_fail(self, fake_symlink, monkeypatch):
        # control/metadata node — no :capture: in caps
        self._patch_udev(monkeypatch, self._make_udev_props(capabilities=":"))
        from camera_bringup.checks.c05_dev_symlinks import check
        result = check({})
        assert result.status == Status.FAIL
        assert "capabilities" in result.summary or "capture" in result.summary.lower()

    def test_symlink_to_non_video_is_fail(self, tmp_path, monkeypatch):
        target = tmp_path / "random_device"
        target.write_text("x")
        link = tmp_path / "cam-rgb"
        link.symlink_to(target)
        from camera_bringup.checks import c05_dev_symlinks
        monkeypatch.setattr(c05_dev_symlinks, "DEV_SYMLINK", str(link))
        result = c05_dev_symlinks.check({})
        assert result.status == Status.FAIL
        assert "видео" in result.summary.lower() or "videoN" in result.summary or "video" in result.summary.lower()

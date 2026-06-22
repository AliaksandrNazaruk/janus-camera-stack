"""Unit-тесты c06_v4l2.check() с mock'ом v4l2-ctl subprocess.

Тестируем 3 ветки:
  - v4l2-ctl не установлен → FAIL
  - format/resolution/fps поддерживаются + capture ok → OK
  - format не поддерживается → FAIL
  - capture BUSY (другой процесс держит) → OK (специальная семантика)
"""
from __future__ import annotations

import subprocess

from camera_bringup.check import Status

_TYPICAL_V4L2_LIST_FORMATS = """ioctl: VIDIOC_ENUM_FMT
\tType: Video Capture

\t[0]: 'YUYV' (YUYV 4:2:2)
\t\tSize: Discrete 640x480
\t\t\tInterval: Discrete 0.033s (30.000 fps)
\t\t\tInterval: Discrete 0.067s (15.000 fps)
\t\tSize: Discrete 1280x720
\t\t\tInterval: Discrete 0.033s (30.000 fps)
"""


def _fake_run(cmd, **kwargs):
    """Generic fake для subprocess.run."""
    return subprocess.CompletedProcess(args=cmd, returncode=0,
                                       stdout="ok", stderr="")


class TestV4l2Check:
    def test_no_v4l2_ctl_is_fail(self, monkeypatch):
        monkeypatch.setattr("camera_bringup.checks.c06_v4l2.which", lambda x: None)
        from camera_bringup.checks.c06_v4l2 import check
        result = check({})
        assert result.status == Status.FAIL
        assert "v4l-utils" in result.summary or "v4l2-ctl" in result.summary

    def test_canonical_profile_ok(self, monkeypatch):
        monkeypatch.setattr("camera_bringup.checks.c06_v4l2.which", lambda x: "/usr/bin/v4l2-ctl")

        # _list_formats возвращает наш typical output;
        # _capture_test возвращает (True, "ok")
        def fake_list(device):
            return [{
                "format": "YUYV",
                "sizes": [{
                    "width": 640, "height": 480,
                    "fps": [30.0, 15.0],
                }],
            }]

        from camera_bringup.checks import c06_v4l2
        monkeypatch.setattr(c06_v4l2, "_list_formats", fake_list)
        monkeypatch.setattr(c06_v4l2, "_capture_test",
                            lambda dev, count=3: (True, "ok"))

        result = c06_v4l2.check({"v4l_dev": "/dev/video4"})
        assert result.status == Status.OK
        assert "YUYV" in result.summary

    def test_format_not_supported_is_fail(self, monkeypatch):
        monkeypatch.setattr("camera_bringup.checks.c06_v4l2.which", lambda x: "/usr/bin/v4l2-ctl")
        # Только MJPG, нет YUYV
        from camera_bringup.checks import c06_v4l2
        monkeypatch.setattr(c06_v4l2, "_list_formats", lambda d: [
            {"format": "MJPG", "sizes": []}
        ])
        result = c06_v4l2.check({"v4l_dev": "/dev/video4"})
        assert result.status == Status.FAIL
        assert "YUYV" in result.summary

    def test_resolution_not_supported_is_fail(self, monkeypatch):
        monkeypatch.setattr("camera_bringup.checks.c06_v4l2.which", lambda x: "/usr/bin/v4l2-ctl")
        from camera_bringup.checks import c06_v4l2
        # YUYV есть, но только 1920x1080
        monkeypatch.setattr(c06_v4l2, "_list_formats", lambda d: [
            {"format": "YUYV", "sizes": [
                {"width": 1920, "height": 1080, "fps": [30.0]}
            ]}
        ])
        result = c06_v4l2.check({"v4l_dev": "/dev/video4"})
        assert result.status == Status.FAIL

    def test_fps_not_supported_is_warn(self, monkeypatch):
        monkeypatch.setattr("camera_bringup.checks.c06_v4l2.which", lambda x: "/usr/bin/v4l2-ctl")
        from camera_bringup.checks import c06_v4l2
        # 640x480 есть, но только 30 fps (мы хотим 15)
        monkeypatch.setattr(c06_v4l2, "_list_formats", lambda d: [
            {"format": "YUYV", "sizes": [
                {"width": 640, "height": 480, "fps": [30.0]}
            ]}
        ])
        result = c06_v4l2.check({"v4l_dev": "/dev/video4"})
        assert result.status == Status.WARN

    def test_busy_capture_is_ok(self, monkeypatch):
        """Если другой процесс держит устройство — НЕ FAIL, это норма
        когда rtp-rgb работает."""
        monkeypatch.setattr("camera_bringup.checks.c06_v4l2.which", lambda x: "/usr/bin/v4l2-ctl")
        from camera_bringup.checks import c06_v4l2
        monkeypatch.setattr(c06_v4l2, "_list_formats", lambda d: [
            {"format": "YUYV", "sizes": [
                {"width": 640, "height": 480, "fps": [15.0]}
            ]}
        ])
        monkeypatch.setattr(c06_v4l2, "_capture_test",
                            lambda dev, count=3: (False, "BUSY (другой процесс держит устройство)"))
        result = c06_v4l2.check({"v4l_dev": "/dev/video4"})
        assert result.status == Status.OK
        assert "BUSY" in result.summary or "busy" in result.summary.lower() or "skip" in result.summary.lower()

    def test_capture_failure_is_fail(self, monkeypatch):
        monkeypatch.setattr("camera_bringup.checks.c06_v4l2.which", lambda x: "/usr/bin/v4l2-ctl")
        from camera_bringup.checks import c06_v4l2
        monkeypatch.setattr(c06_v4l2, "_list_formats", lambda d: [
            {"format": "YUYV", "sizes": [
                {"width": 640, "height": 480, "fps": [15.0]}
            ]}
        ])
        monkeypatch.setattr(c06_v4l2, "_capture_test",
                            lambda dev, count=3: (False, "VIDIOC_S_FMT: errno=5"))
        result = c06_v4l2.check({"v4l_dev": "/dev/video4"})
        assert result.status == Status.FAIL
        assert "errno" in result.summary or "S_FMT" in result.summary

    def test_no_formats_returned_is_fail(self, monkeypatch):
        """Если v4l2-ctl не вернул форматы (broken device, no perms)."""
        monkeypatch.setattr("camera_bringup.checks.c06_v4l2.which", lambda x: "/usr/bin/v4l2-ctl")
        from camera_bringup.checks import c06_v4l2
        monkeypatch.setattr(c06_v4l2, "_list_formats", lambda d: [])
        result = c06_v4l2.check({"v4l_dev": "/dev/video4"})
        assert result.status == Status.FAIL

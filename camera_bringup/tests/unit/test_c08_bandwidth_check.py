"""Unit-тесты c08_bandwidth.check() с fake env file.

Hexagonal note: c08 читает /etc/robot/cam-rgb.env напрямую через open() —
для unit test'а monkeypatch'аем read_file helper.
"""
from __future__ import annotations

import pytest

from camera_bringup.check import Status


class TestBandwidthCheck:
    @pytest.fixture
    def fake_env(self, tmp_path, monkeypatch):
        """Создаём fake cam-rgb.env и подменяем path."""
        env_file = tmp_path / "cam-rgb.env"

        def fake_read_file(path):
            if path == "/etc/robot/cam-rgb.env":
                return env_file.read_text() if env_file.exists() else None
            from camera_bringup.check import read_file as orig
            return orig(path)

        from camera_bringup.checks import c08_bandwidth
        monkeypatch.setattr(c08_bandwidth, "read_file", fake_read_file)
        return env_file

    def test_typical_640x480_15fps_usb2_is_ok(self, fake_env):
        fake_env.write_text("WIDTH=640\nHEIGHT=480\nFPS=15\nPIX_FMT=yuyv422\n")
        from camera_bringup.checks.c08_bandwidth import check
        result = check({"usb_speed_mbit": 480})
        assert result.status == Status.OK
        # 640*480*15*2*8/1M = 73.7 Mbit
        assert "74" in result.summary or "73" in result.summary

    def test_huge_profile_exceeds_usb2_is_fail(self, fake_env):
        # 1920x1080 @ 60fps YUYV = 1990 Mbit/s — > USB2 capacity (360)
        fake_env.write_text("WIDTH=1920\nHEIGHT=1080\nFPS=60\nPIX_FMT=yuyv422\n")
        from camera_bringup.checks.c08_bandwidth import check
        result = check({"usb_speed_mbit": 480})
        assert result.status == Status.FAIL
        assert "не пройдёт" in result.summary or "exceed" in result.summary.lower() or ">" in result.summary

    def test_high_profile_on_usb3_is_ok(self, fake_env):
        # Тот же 1920x1080@60 на USB3 (4000 Mbit useful) — 1990/4000 = 49% — OK
        fake_env.write_text("WIDTH=1920\nHEIGHT=1080\nFPS=60\nPIX_FMT=yuyv422\n")
        from camera_bringup.checks.c08_bandwidth import check
        result = check({"usb_speed_mbit": 5000})
        assert result.status == Status.OK

    def test_no_usb_speed_skips(self, fake_env):
        fake_env.write_text("WIDTH=640\nHEIGHT=480\nFPS=15\n")
        from camera_bringup.checks.c08_bandwidth import check
        result = check({})
        assert result.status == Status.SKIP

    def test_unknown_pixel_format_is_warn(self, fake_env):
        fake_env.write_text("WIDTH=640\nHEIGHT=480\nFPS=15\nPIX_FMT=alien_format\n")
        from camera_bringup.checks.c08_bandwidth import check
        result = check({"usb_speed_mbit": 480})
        assert result.status == Status.WARN
        assert "alien" in result.summary.lower() or "unknown" in result.summary.lower() or "неизвестный" in result.summary

"""Unit-тесты c10_smoke.check() с моками subprocess + filesystem."""
from __future__ import annotations

import pytest

from camera_bringup.check import Status


class TestSmokeCheck:
    @pytest.fixture
    def all_mocks_ok(self, monkeypatch):
        """Helper: все подсистемы в OK state."""
        monkeypatch.setattr("camera_bringup.checks.c10_smoke.which", lambda x: f"/usr/bin/{x}")
        monkeypatch.setattr("camera_bringup.checks.c10_smoke._has_libx264",
                            lambda: (True, "ok"))
        monkeypatch.setattr("camera_bringup.checks.c10_smoke._systemd_service_loaded",
                            lambda n: (True, "loaded"))
        # Path для cam-wait-capture и /run/cam-rgb — нужно симулировать существование
        import camera_bringup.checks.c10_smoke as c10
        monkeypatch.setattr(c10, "CAM_WAIT_CAPTURE", "/usr/local/bin/cam-wait-capture.sh")
        # Path.is_file будем мокать globally
        monkeypatch.setattr("pathlib.Path.is_file", lambda self: True)
        monkeypatch.setattr("pathlib.Path.is_dir", lambda self: True)
        monkeypatch.setattr("os.access", lambda p, mode: True)

    def test_all_ok(self, all_mocks_ok):
        from camera_bringup.checks.c10_smoke import check
        result = check({})
        assert result.status == Status.OK
        assert "ffmpeg" in result.summary

    def test_no_ffmpeg_is_fail(self, monkeypatch):
        monkeypatch.setattr("camera_bringup.checks.c10_smoke.which", lambda x: None)
        from camera_bringup.checks.c10_smoke import check
        result = check({})
        assert result.status == Status.FAIL
        assert "ffmpeg" in result.summary

    def test_ffmpeg_no_libx264_is_fail(self, monkeypatch):
        monkeypatch.setattr("camera_bringup.checks.c10_smoke.which", lambda x: f"/usr/bin/{x}")
        monkeypatch.setattr("camera_bringup.checks.c10_smoke._has_libx264",
                            lambda: (False, "libx264 не в списке"))
        # Не хотим чтобы c10 валился раньше на других проверках
        monkeypatch.setattr("pathlib.Path.is_file", lambda self: True)
        monkeypatch.setattr("pathlib.Path.is_dir", lambda self: True)
        monkeypatch.setattr("os.access", lambda p, m: True)
        monkeypatch.setattr("camera_bringup.checks.c10_smoke._systemd_service_loaded",
                            lambda n: (True, "loaded"))
        from camera_bringup.checks.c10_smoke import check
        result = check({})
        assert result.status == Status.FAIL
        assert "libx264" in result.summary

    def test_systemd_unit_not_loaded_is_fail(self, monkeypatch):
        monkeypatch.setattr("camera_bringup.checks.c10_smoke.which", lambda x: f"/usr/bin/{x}")
        monkeypatch.setattr("camera_bringup.checks.c10_smoke._has_libx264",
                            lambda: (True, "ok"))
        monkeypatch.setattr("camera_bringup.checks.c10_smoke._systemd_service_loaded",
                            lambda n: (False, "not-found"))
        monkeypatch.setattr("pathlib.Path.is_file", lambda self: True)
        monkeypatch.setattr("pathlib.Path.is_dir", lambda self: True)
        monkeypatch.setattr("os.access", lambda p, m: True)
        from camera_bringup.checks.c10_smoke import check
        result = check({})
        assert result.status == Status.FAIL
        assert "loaded" in result.summary.lower() or "not-found" in result.summary

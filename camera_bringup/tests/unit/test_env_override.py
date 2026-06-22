"""Unit-тесты для ENV-override спецификации.

12-factor compliance — все hardcoded пути могут быть переопределены.
"""
from __future__ import annotations

import importlib


class TestEnvOverride:
    """Каждый ENV var должен override соответствующее значение."""

    def test_robot_home_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CAMERA_BRINGUP_ROBOT_HOME", str(tmp_path))
        # reload spec чтобы перечитать env
        import camera_bringup.spec
        importlib.reload(camera_bringup.spec)
        assert str(camera_bringup.spec.ROBOT_HOME) == str(tmp_path)

    def test_bringup_home_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CAMERA_BRINGUP_HOME", str(tmp_path / "bringup"))
        import camera_bringup.spec
        importlib.reload(camera_bringup.spec)
        assert str(camera_bringup.spec.BRINGUP_HOME) == str(tmp_path / "bringup")

    def test_fingerprint_path_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CAMERA_BRINGUP_FINGERPRINT", str(tmp_path / "fp.json"))
        import camera_bringup.spec
        importlib.reload(camera_bringup.spec)
        assert camera_bringup.spec.FINGERPRINT_PATH == str(tmp_path / "fp.json")

    def test_udev_dir_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CAMERA_BRINGUP_UDEV_DIR", str(tmp_path))
        import camera_bringup.spec
        importlib.reload(camera_bringup.spec)
        assert camera_bringup.spec.UDEV_RULES_DIR == str(tmp_path)

    def test_lock_file_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CAMERA_BRINGUP_LOCK_FILE", str(tmp_path / "lock"))
        import camera_bringup.spec
        importlib.reload(camera_bringup.spec)
        assert camera_bringup.spec.LOCK_FILE == str(tmp_path / "lock")

    def test_defaults_when_no_env(self, monkeypatch):
        """Когда ENV отсутствует — используются разумные defaults
        (per-instance после Batch 4)."""
        for var in ["CAMERA_BRINGUP_ROBOT_HOME", "CAMERA_BRINGUP_HOME",
                    "CAMERA_BRINGUP_FINGERPRINT", "CAMERA_BRINGUP_UDEV_DIR",
                    "CAMERA_BRINGUP_LOCK_FILE", "CAMERA_BRINGUP_INSTANCE"]:
            monkeypatch.delenv(var, raising=False)
        import camera_bringup.spec
        importlib.reload(camera_bringup.spec)
        assert str(camera_bringup.spec.ROBOT_HOME) == "/opt/janus-camera-page"
        assert camera_bringup.spec.UDEV_RULES_DIR == "/etc/udev/rules.d"
        # Default instance = "cam-rgb" — lock file per-instance
        assert camera_bringup.spec.LOCK_FILE == "/run/camera_bringup-cam-rgb.lock"
        # Fingerprint default also per-instance
        assert camera_bringup.spec.FINGERPRINT_PATH == "/var/lib/camera/cam-rgb.json"

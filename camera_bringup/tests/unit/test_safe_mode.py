"""Unit-тесты для F-isolation (SAFE mode)."""
from __future__ import annotations

import pytest


class TestSafeMode:
    @pytest.fixture
    def fake_marker(self, tmp_path, monkeypatch):
        """Override FINGERPRINT_DIR чтобы marker писался в tmp."""
        monkeypatch.setattr("camera_bringup.safe_mode.FINGERPRINT_DIR", str(tmp_path))
        return tmp_path

    def test_initially_not_in_safe(self, fake_marker):
        from camera_bringup.safe_mode import is_safe_mode
        assert is_safe_mode() is False

    def test_enter_creates_marker(self, fake_marker):
        from camera_bringup.safe_mode import enter_safe_mode, is_safe_mode
        info = enter_safe_mode("test reason")
        assert info["reason"] == "test reason"
        assert info["ts"]
        assert is_safe_mode() is True

    def test_exit_removes_marker(self, fake_marker):
        from camera_bringup.safe_mode import enter_safe_mode, exit_safe_mode, is_safe_mode
        enter_safe_mode("test")
        assert is_safe_mode()
        assert exit_safe_mode() is True
        assert not is_safe_mode()

    def test_exit_when_not_in_safe_returns_false(self, fake_marker):
        from camera_bringup.safe_mode import exit_safe_mode
        assert exit_safe_mode() is False

    def test_enter_idempotent_updates_timestamp(self, fake_marker):
        import time

        from camera_bringup.safe_mode import enter_safe_mode
        info1 = enter_safe_mode("first")
        time.sleep(1.1)
        info2 = enter_safe_mode("second")
        assert info1["ts"] != info2["ts"]
        assert info2["reason"] == "second"

    def test_safe_mode_info_returns_none_when_inactive(self, fake_marker):
        from camera_bringup.safe_mode import safe_mode_info
        assert safe_mode_info() is None

    def test_l0_status_returns_safe_in_safe_mode(self, fake_marker):
        from camera_bringup.api import L0, LayerStatus
        from camera_bringup.safe_mode import enter_safe_mode, exit_safe_mode
        enter_safe_mode("test")
        try:
            assert L0.status() == LayerStatus.SAFE
        finally:
            exit_safe_mode()

    def test_attempt_recovery_blocked_in_safe_mode(self, fake_marker):
        from camera_bringup.api import L0
        from camera_bringup.safe_mode import enter_safe_mode, exit_safe_mode
        enter_safe_mode("test apply block")
        try:
            r = L0.attempt_recovery(dry_run=False)
            assert r.attempted is False
            assert any("safe mode" in issue.lower() for issue in r.remaining_issues)
            assert r.requires_human
        finally:
            exit_safe_mode()

    def test_attempt_recovery_dry_run_allowed_in_safe(self, fake_marker):
        """dry_run не модифицирует — разрешён даже в SAFE."""
        from camera_bringup.api import L0
        from camera_bringup.safe_mode import enter_safe_mode, exit_safe_mode
        enter_safe_mode("test")
        try:
            r = L0.attempt_recovery(dry_run=True)
            # dry_run проходит — attempted False (потому что dry_run), но не блок
            assert r.attempted is False
        finally:
            exit_safe_mode()

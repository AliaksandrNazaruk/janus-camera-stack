"""Unit-тесты c04_udev.check() — проверка наличия+совпадения rule с fixture.

Покрываем check function (не только helper _normalize_rule из test_parsers).
"""
from __future__ import annotations

import pytest

from camera_bringup.check import Status


class TestUdevCheck:
    @pytest.fixture
    def fake_udev_dir(self, tmp_path, monkeypatch):
        """Подменяем UDEV_RULES_DIR на tmp."""
        from camera_bringup.checks import c04_udev
        monkeypatch.setattr(c04_udev, "UDEV_RULES_DIR", str(tmp_path))
        return tmp_path

    @pytest.fixture
    def expected_rule(self):
        """Каноническое содержимое от ACTIVE_INSTANCE (не static fixture)."""
        from camera_bringup.spec import ACTIVE_INSTANCE
        return ACTIVE_INSTANCE.render_udev_rule()

    @pytest.fixture
    def rule_filename(self):
        from camera_bringup.spec import UDEV_RULE_NAME
        return UDEV_RULE_NAME

    def test_rule_missing_is_fail(self, fake_udev_dir):
        from camera_bringup.checks.c04_udev import check
        result = check({})
        assert result.status == Status.FAIL
        assert "отсутствует" in result.summary

    def test_rule_matches_expected_is_ok(self, fake_udev_dir, expected_rule, rule_filename):
        (fake_udev_dir / rule_filename).write_text(expected_rule)
        from camera_bringup.checks.c04_udev import check
        result = check({})
        assert result.status == Status.OK

    def test_rule_drift_is_warn(self, fake_udev_dir, rule_filename):
        (fake_udev_dir / rule_filename).write_text(
            'SUBSYSTEM=="video4linux", SYMLINK+="something-else"\n'
        )
        from camera_bringup.checks.c04_udev import check
        result = check({})
        assert result.status == Status.WARN
        assert "расходится" in result.summary or "drift" in result.summary.lower()

    def test_legacy_rule_activated_is_fail(self, fake_udev_dir, expected_rule, rule_filename):
        (fake_udev_dir / rule_filename).write_text(expected_rule)
        (fake_udev_dir / "99-realsense-d435i.rules").write_text("# fake legacy\n")
        from camera_bringup.checks.c04_udev import check
        result = check({})
        assert result.status == Status.FAIL
        assert "legacy" in result.summary.lower()

    def test_normalized_comparison_ignores_comments(self, fake_udev_dir, expected_rule, rule_filename):
        """Если в текущем правиле добавлен комментарий — должно остаться OK."""
        modified = "# additional comment\n" + expected_rule
        (fake_udev_dir / rule_filename).write_text(modified)
        from camera_bringup.checks.c04_udev import check
        result = check({})
        assert result.status == Status.OK

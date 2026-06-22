"""Unit-тесты для L0._derive_status: маппинг set'а CheckResult'ов → LayerStatus.

Это самая важная логика API — определяет в каком состоянии слой.
Покрываем все 5 переходов state machine.
"""
from __future__ import annotations

from camera_bringup.api import L0, LayerStatus
from camera_bringup.check import CheckResult, Status


def _make(name: str, status: Status) -> CheckResult:
    return CheckResult(name=name, status=status, summary="test")


def _checks_with(*statuses: Status):
    """Создать набор check'ов разных statuses (одно имя на каждый)."""
    return [_make(f"check_{i}", s) for i, s in enumerate(statuses)]


class TestDeriveStatus:
    def test_all_ok_is_healthy(self, all_ok_results):
        assert L0._derive_status(all_ok_results) == LayerStatus.HEALTHY

    def test_any_error_is_unknown(self):
        # ERROR overrides everything
        results = _checks_with(Status.OK, Status.OK, Status.ERROR, Status.FAIL)
        assert L0._derive_status(results) == LayerStatus.UNKNOWN

    def test_error_alone_is_unknown(self):
        results = _checks_with(Status.ERROR)
        assert L0._derive_status(results) == LayerStatus.UNKNOWN

    def test_fail_without_fixer_is_broken(self):
        # имя check'а не совпадает ни с одним fixer => не fixable
        results = [_make("nonexistent_check", Status.FAIL)]
        assert L0._derive_status(results) == LayerStatus.BROKEN

    def test_fail_with_fixer_is_drifted(self):
        # 'usb_power' — у нас есть fixer на него
        results = [_make("usb_power", Status.FAIL)]
        assert L0._derive_status(results) == LayerStatus.DRIFTED

    def test_warn_with_fixer_is_drifted(self):
        results = [_make("usb_power", Status.WARN)]
        assert L0._derive_status(results) == LayerStatus.DRIFTED

    def test_warn_without_fixer_is_degraded(self):
        # 'usb_enumerate' WARN — у нас НЕТ fixer на него (физический кабель)
        results = [_make("usb_enumerate", Status.WARN)]
        assert L0._derive_status(results) == LayerStatus.DEGRADED

    def test_mixed_warn_one_fixable_is_drifted(self):
        # Mix: один fixable, один нет — мы можем что-то сделать → DRIFTED
        results = [
            _make("usb_enumerate", Status.WARN),  # no fixer
            _make("usb_power", Status.WARN),      # has fixer
        ]
        assert L0._derive_status(results) == LayerStatus.DRIFTED

    def test_priority_error_over_fail(self):
        # ERROR должен побеждать FAIL
        results = [
            _make("nonexistent", Status.FAIL),
            _make("other", Status.ERROR),
        ]
        assert L0._derive_status(results) == LayerStatus.UNKNOWN

    def test_priority_fail_over_warn(self):
        # FAIL побеждает WARN
        results = [
            _make("usb_power", Status.WARN),
            _make("nonexistent", Status.FAIL),
        ]
        assert L0._derive_status(results) == LayerStatus.BROKEN

    def test_skip_does_not_affect_status(self):
        # SKIP'ed checks игнорируются в расчёте
        results = [
            _make("usb_power", Status.OK),
            _make("uvcvideo", Status.SKIP),  # skip
        ]
        assert L0._derive_status(results) == LayerStatus.HEALTHY

    def test_empty_list_is_healthy(self):
        # Граничный случай — если checks пустые, формально нечего ломаться
        # (это unlikely в реальной жизни но логика должна быть детерминистична)
        assert L0._derive_status([]) == LayerStatus.HEALTHY

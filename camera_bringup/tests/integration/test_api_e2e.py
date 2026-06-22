"""Integration: L0 API на живой системе с реальной камерой.

Запускаются только когда RealSense D435i подключена (см. conftest auto-skip).
"""
from __future__ import annotations

import pytest

from camera_bringup.api import L0, LayerStatus

pytestmark = pytest.mark.integration


class TestL0Status:
    def test_status_returns_layer_status_enum(self):
        status = L0.status()
        assert isinstance(status, LayerStatus)

    def test_status_not_unknown_on_working_system(self):
        """На working dev system status НЕ должен быть UNKNOWN
        (UNKNOWN = exception в check'е = баг bringup'а)."""
        assert L0.status() != LayerStatus.UNKNOWN

    def test_status_not_broken_on_dev_system(self):
        """Если на dev машине BROKEN — значит что-то реально сломано (нет
        камеры, или D435i не одна). Это fitness check для нашей dev среды."""
        status = L0.status()
        if status == LayerStatus.BROKEN:
            issues = L0.requires_human()
            pytest.fail(f"L0 BROKEN, требует human intervention: {issues}")


class TestL0Identity:
    def test_identity_returns_d435i(self):
        ident = L0.identity()
        assert ident is not None, "RealSense identity not detected"
        # Identity is typed dataclass now
        assert "D435" in (ident.product_name or "").upper()
        assert ident.serial, "serial is required"
        assert ident.firmware, "firmware is required"

    def test_baseline_exists_after_bringup(self):
        """Если bringup был apply'нут хотя бы раз, baseline должен существовать."""
        baseline = L0.baseline_identity()
        if baseline is None:
            pytest.skip("baseline ещё не создан (требуется sudo apply --only fingerprint)")
        # Если есть — должен иметь нашу serial
        assert baseline.get("camera", {}).get("serial"), "baseline missing serial"


class TestL0Postconditions:
    def test_guarantees_returns_typed_dataclass(self):
        from camera_bringup.api import ALL_GUARANTEES, Guarantees
        pc = L0.guarantees()
        assert isinstance(pc, Guarantees)
        # to_dict() для compat scenarios
        assert set(pc.to_dict().keys()) == set(ALL_GUARANTEES)

    def test_guarantees_values_are_bool(self):
        pc = L0.guarantees()
        for guarantee, value in pc.to_dict().items():
            assert isinstance(value, bool), f"{guarantee} returned non-bool: {value!r}"

    def test_camera_present_when_camera_detected(self):
        # Attribute access (preferred):
        assert L0.guarantees().CAMERA_PRESENT is True
        # Dict-style (backward compat):
        assert L0.guarantees()["CAMERA_PRESENT"] is True
        # Old name still works:
        assert L0.postconditions()["CAMERA_PRESENT"] is True


class TestL0Recovery:
    def test_dry_run_does_not_attempt(self):
        r = L0.attempt_recovery(dry_run=True)
        assert r.attempted is False
        assert r.applied_fixers == []  # dry-run ничего не applies

    def test_recovery_result_structure(self):
        r = L0.attempt_recovery(dry_run=True)
        # Все поля должны быть list'ами (даже пустыми)
        assert isinstance(r.applied_fixers, list)
        assert isinstance(r.skipped_fixers, list)
        assert isinstance(r.failed_fixers, list)
        assert isinstance(r.unfixed_fixers, list)
        assert isinstance(r.remaining_issues, list)
        assert isinstance(r.requires_human, list)


class TestL0Summary:
    def test_summary_has_required_keys(self):
        s = L0.summary()
        required = {"layer", "status", "checks_total", "status_counts",
                    "identity", "baseline_serial", "requires_human"}
        assert required <= set(s.keys()), f"missing keys: {required - set(s.keys())}"

    def test_summary_status_counts_sum_equals_total(self):
        s = L0.summary()
        assert sum(s["status_counts"].values()) == s["checks_total"]

    def test_summary_is_json_serializable(self):
        import json
        s = L0.summary()
        # Должен сериализоваться без exception (важно для агента и логов)
        json_str = json.dumps(s)
        assert json_str

"""Fitness: conformance между api.py, checks/, fixers/, CONTRACT.md.

Эти тесты ловят drift между формальным контрактом и реализацией. Например:
  - добавили новый check → забыли мапить в GUARANTEES → ловим
  - переименовали check → fixer'у name стал не совпадать → ловим
  - убрали fixer → checks/__init__ продолжает на него ссылаться → ловим

Запускаются быстро (нет IO), должны ВСЕГДА pass.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from camera_bringup.api import (
    ALL_GUARANTEES,
    GUARANTEES,
    HUMAN_REQUIRED,
    L0,
)
from camera_bringup.check import Status
from camera_bringup.checks import ALL_CHECKS
from camera_bringup.fixers import ALL_FIXERS

pytestmark = pytest.mark.fitness


class TestGuaranteesMapping:
    """Каждая guarantee ссылается на существующий check."""

    def test_every_guarantee_references_real_check(self):
        check_names = {name for name, _ in ALL_CHECKS}
        for guarantee, (check_name, _accepted) in GUARANTEES.items():
            assert check_name in check_names, (
                f"Guarantee {guarantee!r} references check {check_name!r} "
                f"которого нет в ALL_CHECKS"
            )

    def test_every_guarantee_has_valid_status_set(self):
        valid = set(Status)
        for guarantee, (_, accepted) in GUARANTEES.items():
            assert isinstance(accepted, set), f"{guarantee}: accepted must be set"
            assert accepted, f"{guarantee}: accepted set is empty"
            assert accepted <= valid, f"{guarantee}: invalid Status in accepted: {accepted - valid}"

    def test_all_guarantees_listed(self):
        """ALL_GUARANTEES должен содержать ВСЕ ключи из GUARANTEES."""
        assert set(ALL_GUARANTEES) == set(GUARANTEES.keys())


class TestFixersMapping:
    """Каждый fixer соответствует существующему check."""

    def test_every_fixer_has_corresponding_check(self):
        check_names = {name for name, _ in ALL_CHECKS}
        for fixer_name in ALL_FIXERS.keys():
            assert fixer_name in check_names, (
                f"Fixer {fixer_name!r} не имеет соответствующего check'а в ALL_CHECKS"
            )

    def test_fixer_class_name_matches(self):
        """Fixer.name (class attribute) должен совпадать с ключом в ALL_FIXERS."""
        for key, cls in ALL_FIXERS.items():
            assert cls.name == key, (
                f"Fixer registered as {key!r} but cls.name == {cls.name!r}"
            )


class TestHumanRequiredMapping:
    """HUMAN_REQUIRED ссылается на существующие checks."""

    def test_every_entry_references_real_check(self):
        check_names = {name for name, _ in ALL_CHECKS}
        for check_name in HUMAN_REQUIRED.keys():
            assert check_name in check_names, (
                f"HUMAN_REQUIRED references unknown check {check_name!r}"
            )

    def test_every_entry_has_valid_status_keys(self):
        # Внутренние keys должны быть Status enum values
        for check_name, status_map in HUMAN_REQUIRED.items():
            for status, reason in status_map.items():
                assert isinstance(status, Status), (
                    f"HUMAN_REQUIRED[{check_name}]: key {status!r} is not Status enum"
                )
                assert isinstance(reason, str) and reason, (
                    f"HUMAN_REQUIRED[{check_name}][{status}]: reason must be non-empty str"
                )


class TestContractDocumentation:
    """CONTRACT.md должен ссылаться на актуальные guarantees."""

    @pytest.fixture
    def contract_text(self):
        contract_path = Path(__file__).resolve().parent.parent.parent / "CONTRACT.md"
        return contract_path.read_text() if contract_path.is_file() else ""

    def test_contract_exists(self, contract_text):
        assert contract_text, "CONTRACT.md must exist at camera_bringup/CONTRACT.md"

    def test_contract_mentions_all_guarantees(self, contract_text):
        """Каждая guarantee должна быть упомянута в CONTRACT.md.
        (Это catch drift — добавили guarantee но не задокументировали.)"""
        for guarantee in ALL_GUARANTEES:
            assert guarantee in contract_text, (
                f"Guarantee {guarantee!r} не упомянута в CONTRACT.md — обнови документ"
            )

    def test_contract_mentions_all_layer_statuses(self, contract_text):
        from camera_bringup.api import LayerStatus
        for status in LayerStatus:
            assert status.value.upper() in contract_text.upper(), (
                f"LayerStatus.{status.name} не упомянут в CONTRACT.md"
            )


class TestStateMachineCoverage:
    """_derive_status должен handle все комбинации statuses предсказуемо."""

    def test_every_status_combo_returns_valid_layer_status(self, make_check_result):
        """Brute-force: для каждой комбинации (status1, status2) проверяем что
        _derive_status возвращает валидный LayerStatus enum."""
        from camera_bringup.api import LayerStatus
        valid = set(LayerStatus)
        for s1 in Status:
            for s2 in Status:
                # используем имена которые ЕСТЬ в fixers чтобы покрыть fixable путь
                results = [
                    make_check_result("usb_power", s1),
                    make_check_result("uvcvideo", s2),
                ]
                derived = L0._derive_status(results)
                assert derived in valid, (
                    f"_derive_status({s1}, {s2}) = {derived!r} не LayerStatus"
                )

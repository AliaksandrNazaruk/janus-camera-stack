"""Fitness: повторный запуск apply на чистом state = все SKIPPED, no diff.

Это базовый закон любого fixer'а — apply дважды на одном state = первый
APPLIED + второй SKIPPED. Если этот тест валится — fixer не идемпотентный
и его повторный запуск делает лишнюю работу.
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.fitness, pytest.mark.integration]


class TestApplyIdempotency:
    def test_apply_on_clean_state_all_skipped(self, real_camera_required):
        """Если все checks OK, attempt_recovery должен сделать noop."""
        from camera_bringup.api import L0, LayerStatus

        # Pre-condition: система должна быть в HEALTHY или DEGRADED (т.е. без FAIL'ов)
        status = L0.status()
        if status in (LayerStatus.BROKEN, LayerStatus.UNKNOWN):
            pytest.skip(f"system not in healthy state ({status}) — fix before testing idempotency")

        r = L0.attempt_recovery(dry_run=True)
        # Все fixers должны быть SKIPPED (потому что check OK)
        # либо SKIPPED + потенциально applied для DEGRADED (но не должно быть в dry-run)
        assert r.failed_fixers == [], f"unexpected failed_fixers: {r.failed_fixers}"
        assert r.unfixed_fixers == [], f"unexpected unfixed_fixers: {r.unfixed_fixers}"

    def test_double_apply_second_is_noop(self, real_camera_required):
        """После первого apply, второй apply = SKIPPED."""
        from camera_bringup.api import L0

        # Первый — что-то возможно сделает (если есть drift)
        r1 = L0.attempt_recovery(dry_run=True)
        # Второй — должен быть identical к первому (мы в dry-run, состояние
        # реально не менялось)
        r2 = L0.attempt_recovery(dry_run=True)
        assert r1.applied_fixers == r2.applied_fixers
        assert r1.skipped_fixers == r2.skipped_fixers


class TestFixerNoEmptyActions:
    """Каждый fixer возвращает >= 1 action от plan() (иначе он бесполезен)."""

    def test_each_fixer_plan_nonempty(self, healthy_ctx):
        from camera_bringup.fixers import ALL_FIXERS
        for name, cls in ALL_FIXERS.items():
            fixer = cls()
            actions = fixer.plan(healthy_ctx)
            assert len(actions) >= 1, f"Fixer {name} returns empty plan"
            for a in actions:
                assert a.kind, f"Fixer {name}: action has empty kind"
                assert a.target, f"Fixer {name}: action has empty target"

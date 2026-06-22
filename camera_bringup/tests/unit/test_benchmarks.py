"""Performance benchmarks для критичных API методов.

Запуск:
    pytest camera_bringup/tests/unit/test_benchmarks.py --benchmark-only
    pytest camera_bringup/tests/unit/test_benchmarks.py --benchmark-only --benchmark-autosave

Regression detection: pytest-benchmark сохраняет snapshots в .benchmarks/,
compare запусков через --benchmark-compare.

Цели (target):
  - L0.status() cached: < 1 ms (cache hit)
  - L0.status() cold: < 500 ms (полный 11-check pass)
  - L0.guarantees(): < 5 ms (cached)
  - L0.snapshot(): < 50 ms (cached)
  - derive_status (pure): < 100 µs
"""
from __future__ import annotations

import pytest

from camera_bringup.api import L0, _cache
from camera_bringup.check import CheckResult, Status

# Skip module если pytest-benchmark не установлен (informational tests)
pytest.importorskip("pytest_benchmark", reason="pytest-benchmark plugin not installed")

pytestmark = pytest.mark.benchmark


class TestStatusCachePerformance:
    def setup_method(self):
        """Прогреем кеш перед бенчмарком."""
        _cache.invalidate()
        L0._run_all_checks()   # populate cache

    def test_status_cached_is_fast(self, benchmark):
        """L0.status() с прогретым cache должен быть < 1 ms."""
        result = benchmark(L0.status)
        # Sanity: должен вернуть валидный enum
        from camera_bringup import LayerStatus
        assert isinstance(result, LayerStatus)

    def test_guarantees_cached_is_fast(self, benchmark):
        result = benchmark(L0.guarantees)
        assert hasattr(result, "CAMERA_PRESENT")

    def test_snapshot_cached_is_reasonable(self, benchmark):
        """snapshot() делает identity() (subprocess) — медленнее, но < 250 ms."""
        result = benchmark(L0.snapshot)
        assert result.layer


class TestDeriveStatusPurePerformance:
    """Pure function — должна быть очень быстрой."""

    def test_derive_status_pure(self, benchmark):
        results = [
            CheckResult(name=f"check_{i}", status=Status.OK, summary="x")
            for i in range(11)
        ]
        # Должно быть < 100 µs
        result = benchmark(L0._derive_status, results)
        from camera_bringup import LayerStatus
        assert result == LayerStatus.HEALTHY

    def test_derive_status_with_mixed(self, benchmark):
        results = [
            CheckResult(name="check_0", status=Status.OK, summary="x"),
            CheckResult(name="usb_power", status=Status.WARN, summary="x"),
            CheckResult(name="other", status=Status.FAIL, summary="x"),
        ]
        benchmark(L0._derive_status, results)

"""Unit-тесты для TTL cache на L0.status()."""
from __future__ import annotations

import time

from camera_bringup.api import _STATUS_CACHE_TTL_S, L0, _cache


class TestStatusCache:
    def setup_method(self):
        _cache.invalidate()

    def teardown_method(self):
        _cache.invalidate()

    def test_first_call_populates_cache(self):
        assert _cache.get() is None
        L0._run_all_checks()
        assert _cache.get() is not None

    def test_second_call_within_ttl_uses_cache(self):
        # Первый вызов — заполняет cache
        L0._run_all_checks()
        first = _cache.get()

        # Сразу второй — должен использовать cache (same identity)
        L0._run_all_checks()
        second = _cache.get()
        assert first[0] is second[0], "cache не используется"

    def test_invalidate_forces_recompute(self):
        L0._run_all_checks()
        first = _cache.get()[0]

        L0.invalidate_cache()
        assert _cache.get() is None

        L0._run_all_checks()
        second = _cache.get()[0]
        # Новый list (другой id)
        assert first is not second

    def test_use_cache_false_bypasses(self):
        L0._run_all_checks()
        cached = _cache.get()[0]

        # use_cache=False должен не использовать cached
        # (но может перезаписать после)
        fresh = L0._run_all_checks(use_cache=False)
        # Длина и имена должны совпасть (всё same пайплайн)
        assert len(fresh) == len(cached)
        assert [r.name for r in fresh] == [r.name for r in cached]

    def test_cache_ttl_expiry(self):
        """После TTL — cache invalidated (но реально ждать TTL долго; делаем
        manual time-travel через monkeypatch внутренних)."""
        L0._run_all_checks()
        assert _cache.get() is not None
        # Сэмулируем что прошло TTL+1 сек
        _cache._ts = time.monotonic() - _STATUS_CACHE_TTL_S - 1
        assert _cache.get() is None, "cache не expired после TTL"

    def test_cache_thread_safe(self):
        """Sanity-check: lock существует и работает."""
        # Базово — _cache имеет lock
        assert hasattr(_cache, '_lock')
        # Можно взять lock несколько раз reentrant
        with _cache._lock:
            with _cache._lock:
                pass

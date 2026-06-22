"""Unit-тесты для flock в fixer.apply_lock.

Защита от concurrent apply — critical для production где cron verify может
race'ить с оператор-apply.
"""
from __future__ import annotations

import multiprocessing
import os
import time

import pytest

from camera_bringup.fixer import LockBusyError, apply_lock


class TestApplyLock:
    @pytest.fixture
    def lock_path(self, tmp_path):
        return str(tmp_path / "test.lock")

    def test_acquires_release_normally(self, lock_path):
        with apply_lock(lock_path):
            assert os.path.exists(lock_path)
            # PID должен быть в файле
            content = open(lock_path).read().strip()
            assert int(content) == os.getpid()

    def test_concurrent_acquire_raises_busy(self, lock_path):
        """Если один процесс держит lock — второй НЕ блокируется, raises."""
        # Берём lock в текущем процессе
        with apply_lock(lock_path):
            # Симулируем второй процесс через child process
            def child(lock_path):
                try:
                    with apply_lock(lock_path):
                        os._exit(0)   # неожиданно получили lock
                except LockBusyError:
                    os._exit(42)      # правильно — busy
                except Exception:
                    os._exit(99)

            p = multiprocessing.Process(target=child, args=(lock_path,))
            p.start()
            p.join(timeout=5)
            assert p.exitcode == 42, f"child exited {p.exitcode} (expected 42=LockBusyError)"

    def test_release_allows_next_acquire(self, lock_path):
        """После release следующий acquire успешен."""
        with apply_lock(lock_path):
            pass  # auto-release
        # Должны мочь взять снова
        with apply_lock(lock_path):
            pass

    def test_blocking_acquire_with_timeout(self, lock_path):
        """blocking=True ждёт до timeout, потом raises."""
        with apply_lock(lock_path):
            # В этом же процессе flock не блокирует, but в субпроцессе будет
            def child(lock_path):
                start = time.monotonic()
                try:
                    with apply_lock(lock_path, blocking=True, timeout=0.5):
                        os._exit(0)
                except LockBusyError:
                    elapsed = time.monotonic() - start
                    if 0.3 < elapsed < 1.0:   # ждал ~0.5s
                        os._exit(42)
                    os._exit(99)

            p = multiprocessing.Process(target=child, args=(lock_path,))
            p.start()
            p.join(timeout=2)
            assert p.exitcode == 42

    def test_exception_inside_block_still_releases(self, lock_path):
        """Если внутри with raise exception, lock должен release."""
        with pytest.raises(RuntimeError, match="test"):
            with apply_lock(lock_path):
                raise RuntimeError("test")
        # После exception — lock free
        with apply_lock(lock_path):
            pass

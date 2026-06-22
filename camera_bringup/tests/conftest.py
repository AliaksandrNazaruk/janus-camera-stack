"""Shared fixtures для unit / integration / fitness tests.

Принципы:
  - unit tests НЕ обращаются к /sys, /dev, subprocess — все factory'и сюда
  - integration tests могут запускать verify/apply, но в read-only режиме
  - destructive tests требуют explicit marker для запуска
"""
from __future__ import annotations

from typing import Any

import pytest

from camera_bringup.check import CheckResult, Status

# ── Factory'и для CheckResult'ов (используются в unit тестах api._derive_status) ──

def make_result(name: str, status: Status, summary: str = "test", **details) -> CheckResult:
    """Factory для CheckResult с минимальным шаблоном."""
    return CheckResult(name=name, status=status, summary=summary, details=details)


@pytest.fixture
def make_check_result():
    """Pytest fixture wrapper."""
    return make_result


@pytest.fixture
def all_ok_results() -> list[CheckResult]:
    """Все 11 checks возвращают OK. Базовый случай HEALTHY."""
    from camera_bringup.checks import ALL_CHECKS
    return [make_result(name, Status.OK) for name, _ in ALL_CHECKS]


@pytest.fixture
def healthy_ctx() -> dict[str, Any]:
    """Минимально полный ctx как если бы все checks отработали."""
    return {
        "sysfs_path": "/sys/bus/usb/devices/2-2",
        "usb_speed_mbit": 480,
        "v4l_dev": "/dev/video4",
    }


# ── Auto-skip integration tests если нет camera ──

def _has_real_camera() -> bool:
    """Проверка наличия RealSense на этом host'е (для пропуска интеграционных
    тестов в CI без железа)."""
    import glob
    for p in glob.glob("/sys/bus/usb/devices/*/idProduct"):
        try:
            with open(p) as f:
                if f.read().strip() == "0b3a":
                    return True
        except OSError:
            continue
    return False


HAS_CAMERA = _has_real_camera()


@pytest.fixture
def real_camera_required():
    if not HAS_CAMERA:
        pytest.skip("RealSense D435i not connected — integration test skipped")


# Marker auto-skip: если test помечен `@pytest.mark.integration` и камеры нет
def pytest_collection_modifyitems(config, items):
    if HAS_CAMERA:
        return
    skip_no_cam = pytest.mark.skip(reason="no RealSense detected")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_no_cam)

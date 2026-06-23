"""Integration: CLI отрабатывает корректно (verify / apply --dry-run / list).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


# Parent of the camera_bringup package, so `python -m camera_bringup` resolves.
# Relative to __file__ (move-invariant) — was a hardcoded "/home/boris/robot",
# which broke when the package moved under janus_camera_page/.
REPO_ROOT = str(Path(__file__).resolve().parents[3])


def _run_cli(*args, timeout=30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "camera_bringup", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=REPO_ROOT,
    )


class TestVerifyCli:
    def test_verify_runs_without_crash(self):
        r = _run_cli("verify")
        # ExitCode 0 (всё OK/WARN) или 1 (FAIL). НЕ 2 (ERROR в check'е = баг).
        assert r.returncode in (0, 1), (
            f"verify exit {r.returncode} (ERROR в check'е?). stderr: {r.stderr}"
        )

    def test_verify_json_is_valid(self):
        r = _run_cli("verify", "--json")
        assert r.returncode in (0, 1)
        data = json.loads(r.stdout)
        assert "checks" in data
        assert "totals" in data
        assert isinstance(data["checks"], list)

    def test_verify_only_filters(self):
        r = _run_cli("verify", "--only", "usb_enumerate")
        # Должен вывести только один check
        # Грубая проверка: в выводе ровно один "[" статус-маркер
        assert r.returncode in (0, 1)
        lines = [line for line in r.stdout.splitlines() if "[" in line and "]" in line]
        assert len(lines) == 1

    def test_verify_unknown_check_exits_2(self):
        r = _run_cli("verify", "--only", "nonexistent")
        assert r.returncode == 2
        assert "unknown" in r.stderr.lower()


class TestApplyDryRunCli:
    def test_apply_dry_run_does_not_require_sudo(self):
        # --dry-run не должен пытаться писать → не должен fail из-за прав
        r = _run_cli("apply", "--dry-run", "--yes-root")
        assert r.returncode in (0, 1), f"dry-run apply failed: {r.stderr}"

    def test_apply_dry_run_json_parses(self):
        r = _run_cli("apply", "--dry-run", "--yes-root", "--json")
        # Стандартный output без 2>&1 — должен быть чистый JSON
        data = json.loads(r.stdout)
        assert isinstance(data, list)
        for item in data:
            assert "name" in item
            assert "status" in item


class TestListCli:
    def test_list_outputs_known_checks(self):
        r = _run_cli("list")
        assert r.returncode == 0
        # Минимум должны быть наши известные checks
        for name in ("usb_enumerate", "usb_power", "fingerprint"):
            assert name in r.stdout

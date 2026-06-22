"""Integration tests for the service-admin CLI (P1 service-control boundary).

Mirrors test_janus_admin_cli / test_encoder_admin_cli: a shim `systemctl` on PATH logs invocations and
returns a controllable exit. Verifies the scoped allowlist (restart only known gateway units; refuse
self + anything else, WITHOUT invoking systemctl) and the reboot rung.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "files" / "service-admin.py"


@pytest.fixture
def shim_systemctl(tmp_path):
    """A shim `systemctl` on PATH that logs args and exits per SYSTEMCTL_MODE (success|fail)."""
    log = tmp_path / "systemctl-log"
    log.write_text("")
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "systemctl"
    shim.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "$@" >> {log}
        case "${{SYSTEMCTL_MODE:-success}}" in
          fail) echo "Job for unit failed" >&2; exit 1 ;;
          *)    exit 0 ;;
        esac
    """))
    shim.chmod(0o755)
    return shim_dir, log


def _env(shim_dir, **extra):
    e = {"PATH": f"{shim_dir}:{os.environ['PATH']}"}
    e.update(extra)
    return e


def _run(env, args, timeout=10):
    return subprocess.run(
        ["python3", str(_SCRIPT_PATH), *args],
        env=env, capture_output=True, text=True, timeout=timeout,
    )


# ── restart allowlist ──────────────────────────────────────────────────

@pytest.mark.parametrize("unit", ["janus", "coturn", "janus-textroom-relay", "janus_camera_page_hook"])
def test_restart_allowlisted_unit(shim_systemctl, unit):
    shim_dir, log = shim_systemctl
    r = _run(_env(shim_dir), ["restart", unit])
    assert r.returncode == 0
    assert f"restart {unit}" in log.read_text()


def test_restart_accepts_dot_service_suffix(shim_systemctl):
    shim_dir, log = shim_systemctl
    r = _run(_env(shim_dir), ["restart", "janus.service"])
    assert r.returncode == 0
    assert "restart janus.service" in log.read_text()


def test_restart_refuses_self_without_invoking_systemctl(shim_systemctl):
    shim_dir, log = shim_systemctl
    r = _run(_env(shim_dir), ["restart", "janus-camera-page"])
    assert r.returncode == 1
    assert log.read_text() == ""              # systemctl NEVER invoked for a refused unit
    assert "refusing to restart self" in r.stderr


def test_restart_rejects_non_allowlisted_unit_without_invoking_systemctl(shim_systemctl):
    shim_dir, log = shim_systemctl
    r = _run(_env(shim_dir), ["restart", "sshd"])
    assert r.returncode == 1
    assert log.read_text() == ""              # defense in depth: unknown unit never reaches systemctl
    assert "not in allowlist" in r.stderr


# ── reboot ──────────────────────────────────────────────────────────────

def test_reboot_invokes_systemctl(shim_systemctl):
    shim_dir, log = shim_systemctl
    r = _run(_env(shim_dir), ["reboot"])
    assert r.returncode == 0
    assert "reboot" in log.read_text()


# ── failure / arg parsing ───────────────────────────────────────────────

def test_systemctl_failure_returns_4(shim_systemctl):
    shim_dir, _log = shim_systemctl
    r = _run(_env(shim_dir, SYSTEMCTL_MODE="fail"), ["restart", "janus"])
    assert r.returncode == 4


def test_no_command_errors(shim_systemctl):
    shim_dir, _log = shim_systemctl
    r = _run(_env(shim_dir), [])
    assert r.returncode != 0                   # argparse: subcommand required

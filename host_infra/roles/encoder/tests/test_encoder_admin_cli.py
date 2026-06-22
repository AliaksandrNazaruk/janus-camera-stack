"""Integration tests для encoder-admin CLI.

Mirrors test_admin_cli.py для janus-admin. Uses mocked systemctl
(via PATH override → shim binary) для isolation.
"""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest


_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "files" / "encoder-admin.py"


@pytest.fixture
def shim_systemctl(tmp_path):
    """Create shim systemctl что записывает invocations + returns controllable exit.

    Returns (path_to_shim_dir, invocation_log_path).
    """
    log = tmp_path / "systemctl-log"
    log.write_text("")
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "systemctl"
    shim.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        # Shim systemctl — logs args + reads exit-mode from env.
        echo "$@" >> {log}
        # Mode controls: SYSTEMCTL_MODE=success (default), fail, unknown
        case "${{SYSTEMCTL_MODE:-success}}" in
          fail)    echo "Unit failed" >&2; exit 1 ;;
          unknown) echo "Unit foo.service could not be found." >&2; exit 5 ;;
          inactive) [[ "$1" == "is-active" ]] && exit 3 ; exit 0 ;;
          show)
            if [[ "$1" == "show" ]]; then
              echo "ActiveEnterTimestamp=Mon 2026-06-14 10:00:00 CEST"
            fi ; exit 0 ;;
          *)       exit 0 ;;
        esac
    """))
    shim.chmod(0o755)
    return shim_dir, log


def _env(shim_dir, **extra):
    e = {
        "PATH": f"{shim_dir}:{os.environ['PATH']}",
        "ENCODER_DEFAULT_INSTANCE": "cam-rgb",
    }
    e.update(extra)
    return e


def _run(env, args, timeout=10):
    return subprocess.run(
        ["python3", str(_SCRIPT_PATH)] + args,
        env=env, capture_output=True, text=True, timeout=timeout,
    )


# ── restart / stop / start ────────────────────────────────────────────

def test_restart_default_instance(shim_systemctl):
    shim_dir, log = shim_systemctl
    r = _run(_env(shim_dir), ["restart"])
    assert r.returncode == 0
    assert "restart rtp-rgb@cam-rgb.service" in log.read_text()


def test_restart_custom_instance(shim_systemctl):
    shim_dir, log = shim_systemctl
    r = _run(_env(shim_dir), ["restart", "--instance", "cam-depth"])
    assert r.returncode == 0
    assert "restart rtp-rgb@cam-depth.service" in log.read_text()


def test_stop(shim_systemctl):
    shim_dir, log = shim_systemctl
    r = _run(_env(shim_dir), ["stop"])
    assert r.returncode == 0
    assert "stop rtp-rgb@cam-rgb.service" in log.read_text()


def test_start(shim_systemctl):
    shim_dir, log = shim_systemctl
    r = _run(_env(shim_dir), ["start"])
    assert r.returncode == 0
    assert "start rtp-rgb@cam-rgb.service" in log.read_text()


# ── Failure modes ──────────────────────────────────────────────────────

def test_systemctl_failure_returns_3(shim_systemctl):
    shim_dir, _log = shim_systemctl
    r = _run(_env(shim_dir, SYSTEMCTL_MODE="fail"), ["restart"])
    assert r.returncode == 3


def test_unknown_unit_returns_2(shim_systemctl):
    shim_dir, _log = shim_systemctl
    r = _run(_env(shim_dir, SYSTEMCTL_MODE="unknown"), ["restart"])
    assert r.returncode == 2


# ── Instance name validation ──────────────────────────────────────────

@pytest.mark.parametrize("bad_instance", [
    "",
    "../etc/passwd",
    "name with spaces",
    "name;injection",
    "name`whoami`",
])
def test_invalid_instance_rejected(shim_systemctl, bad_instance):
    shim_dir, _log = shim_systemctl
    r = _run(_env(shim_dir), ["restart", "--instance", bad_instance])
    assert r.returncode == 1


def test_valid_instance_chars_accepted(shim_systemctl):
    """Alphanumeric + - + _ allowed."""
    shim_dir, log = shim_systemctl
    for inst in ["cam-rgb", "cam_depth", "cam01", "X"]:
        log.write_text("")
        r = _run(_env(shim_dir), ["restart", "--instance", inst])
        assert r.returncode == 0, f"{inst}: {r.stderr}"
        assert f"restart rtp-rgb@{inst}.service" in log.read_text()


# ── status command ────────────────────────────────────────────────────

def test_status_returns_json(shim_systemctl):
    shim_dir, _log = shim_systemctl
    r = _run(_env(shim_dir, SYSTEMCTL_MODE="show"), ["status"])
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["unit"] == "rtp-rgb@cam-rgb.service"
    assert data["instance"] == "cam-rgb"
    assert data["active"] is True
    assert data["active_enter_timestamp"]


def test_status_inactive_service(shim_systemctl):
    shim_dir, _log = shim_systemctl
    r = _run(_env(shim_dir, SYSTEMCTL_MODE="inactive"), ["status"])
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["active"] is False


# ── Args / help ───────────────────────────────────────────────────────

def test_no_args_exit_nonzero(shim_systemctl):
    shim_dir, _log = shim_systemctl
    r = _run(_env(shim_dir), [])
    assert r.returncode != 0


def test_help_lists_all_commands(shim_systemctl):
    shim_dir, _log = shim_systemctl
    r = _run(_env(shim_dir), ["--help"])
    for cmd in ("restart", "stop", "start", "status"):
        assert cmd in r.stdout


def test_default_instance_override_via_env(shim_systemctl):
    """ENCODER_DEFAULT_INSTANCE env overrides default."""
    shim_dir, log = shim_systemctl
    env = _env(shim_dir, ENCODER_DEFAULT_INSTANCE="cam-thermal")
    r = _run(env, ["restart"])
    assert r.returncode == 0
    assert "restart rtp-rgb@cam-thermal.service" in log.read_text()

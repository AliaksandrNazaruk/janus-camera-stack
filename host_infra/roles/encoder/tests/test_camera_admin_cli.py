"""Integration tests для camera-admin CLI (L0 boundary)."""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest


_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "files" / "camera-admin.py"


@pytest.fixture
def shims(tmp_path):
    """Create shim v4l2-ctl + systemctl that log invocations + return controllable."""
    log = tmp_path / "shim-log"
    log.write_text("")
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()

    v4l2 = shim_dir / "v4l2-ctl"
    v4l2.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "v4l2-ctl $@" >> {log}
        case "${{V4L2_MODE:-success}}" in
          fail) echo "VIDIOC_QUERYCAP error" >&2; exit 1 ;;
          info)
            # Respond differently per subcommand
            for arg in "$@"; do
              case "$arg" in
                --info)
                  echo "Driver name : uvcvideo"
                  echo "Card type   : Intel RealSense D435I" ;;
                --list-formats-ext) echo "Index : 0\\nType : Video Capture\\nFmt: YUYV" ;;
                --get-fmt-video) echo "Format Video Capture: Width/Height : 640/480" ;;
                --get-parm) echo "Frames per second: 15.000 (15/1)" ;;
                --list-ctrls) echo "brightness 0x00980900 (int) : min=0 max=255 value=128" ;;
              esac
            done ;;
        esac
        exit 0
    """))
    v4l2.chmod(0o755)

    systemctl = shim_dir / "systemctl"
    systemctl.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "systemctl $@" >> {log}
        case "${{SYSTEMCTL_MODE:-success}}" in
          fail) echo "Failed" >&2; exit 1 ;;
          missing) echo "Unit foo could not be found." >&2; exit 5 ;;
          *) exit 0 ;;
        esac
    """))
    systemctl.chmod(0o755)

    return shim_dir, log


def _env(shim_dir, **extra):
    e = {
        "PATH": f"{shim_dir}:{os.environ['PATH']}",
        "CAMERA_ADMIN_DEFAULT_DEVICE": "/dev/cam-rgb",
        "CAMERA_ADMIN_RESET_UNIT": "realsense-failsafe.service",
    }
    e.update(extra)
    return e


def _run(env, args, timeout=10):
    return subprocess.run(
        ["python3", str(_SCRIPT_PATH)] + args,
        env=env, capture_output=True, text=True, timeout=timeout,
    )


# ── status ────────────────────────────────────────────────────────────

def test_status_returns_json(shims, tmp_path):
    """Status returns valid JSON with device, exists, v4l2_responsive fields."""
    shim_dir, _log = shims
    # Create a fake device file (regular file — is_char_device returns False)
    fake_dev = tmp_path / "fake-cam"
    fake_dev.write_text("")
    r = _run(_env(shim_dir, V4L2_MODE="info"),
             ["--device", "/dev/cam-rgb", "status"])
    # Returns 0 если v4l2_responsive=True, else 2. /dev/cam-rgb may not exist
    # в sandbox — но v4l2-ctl shim always succeeds.
    data = json.loads(r.stdout)
    assert data["device"] == "/dev/cam-rgb"
    assert "exists" in data
    assert "v4l2_responsive" in data


# ── v4l2-formats / v4l2-info / v4l2-ctrls (read-only) ────────────────

def test_v4l2_formats_invokes_correct_command(shims):
    shim_dir, log = shims
    _run(_env(shim_dir, V4L2_MODE="info"), ["v4l2-formats"])
    assert "v4l2-ctl -d /dev/cam-rgb --list-formats-ext" in log.read_text()


def test_v4l2_info_invokes_two_commands(shims):
    shim_dir, log = shims
    _run(_env(shim_dir, V4L2_MODE="info"), ["v4l2-info"])
    content = log.read_text()
    assert "--get-fmt-video" in content
    assert "--get-parm" in content


def test_v4l2_ctrls_invokes_correct_command(shims):
    shim_dir, log = shims
    _run(_env(shim_dir, V4L2_MODE="info"), ["v4l2-ctrls"])
    assert "--list-ctrls" in log.read_text()


def test_v4l2_driver_info_invokes_info_flag(shims):
    shim_dir, log = shims
    _run(_env(shim_dir, V4L2_MODE="info"), ["v4l2-driver-info"])
    content = log.read_text()
    assert "v4l2-ctl -d /dev/cam-rgb --info" in content
    # MUST NOT use --get-fmt-video — это другой subcommand (v4l2-info)
    assert "--get-fmt-video" not in content


def test_v4l2_list_devices_no_device_arg(shims):
    """list-devices is global — should NOT inject -d <device>."""
    shim_dir, log = shims
    _run(_env(shim_dir, V4L2_MODE="info"), ["v4l2-list-devices"])
    content = log.read_text()
    assert "v4l2-ctl --list-devices" in content
    assert "-d /dev/" not in content, "list-devices is global, no device filter"


def test_v4l2_failure_returns_3(shims):
    shim_dir, _log = shims
    r = _run(_env(shim_dir, V4L2_MODE="fail"), ["v4l2-formats"])
    assert r.returncode == 3


def test_v4l2_driver_info_failure_returns_3(shims):
    shim_dir, _log = shims
    r = _run(_env(shim_dir, V4L2_MODE="fail"), ["v4l2-driver-info"])
    assert r.returncode == 3


def test_v4l2_list_devices_failure_returns_3(shims):
    shim_dir, _log = shims
    r = _run(_env(shim_dir, V4L2_MODE="fail"), ["v4l2-list-devices"])
    assert r.returncode == 3


# ── v4l2-set-ctrl: input validation ───────────────────────────────────

def test_set_ctrl_valid(shims):
    shim_dir, log = shims
    r = _run(_env(shim_dir), ["v4l2-set-ctrl", "brightness=128"])
    assert r.returncode == 0
    assert "--set-ctrl brightness=128" in log.read_text()


@pytest.mark.parametrize("bad", [
    "noequals",
    "=onlyvalue",
    "Brightness=128",     # uppercase rejected
    "brightness;rm -rf=128",  # injection
    "brightness=notanumber",
    "../etc=128",         # path traversal in name
])
def test_set_ctrl_rejects_invalid_input(shims, bad):
    shim_dir, _log = shims
    r = _run(_env(shim_dir), ["v4l2-set-ctrl", bad])
    assert r.returncode == 1, f"expected reject of {bad!r}, got rc={r.returncode}"


# ── reset-usb ─────────────────────────────────────────────────────────

def test_reset_usb_invokes_systemctl(shims):
    shim_dir, log = shims
    r = _run(_env(shim_dir), ["reset-usb"])
    assert r.returncode == 0
    assert "systemctl start realsense-failsafe.service" in log.read_text()


def test_reset_usb_unit_missing_returns_2(shims):
    shim_dir, _log = shims
    r = _run(_env(shim_dir, SYSTEMCTL_MODE="missing"), ["reset-usb"])
    assert r.returncode == 2


def test_reset_usb_systemctl_failure_returns_4(shims):
    shim_dir, _log = shims
    r = _run(_env(shim_dir, SYSTEMCTL_MODE="fail"), ["reset-usb"])
    assert r.returncode == 4


def test_reset_usb_custom_unit_via_env(shims):
    """CAMERA_ADMIN_RESET_UNIT env overrides default."""
    shim_dir, log = shims
    env = _env(shim_dir, CAMERA_ADMIN_RESET_UNIT="my-custom-failsafe.service")
    _run(env, ["reset-usb"])
    assert "systemctl start my-custom-failsafe.service" in log.read_text()


# ── Device path validation ────────────────────────────────────────────

@pytest.mark.parametrize("bad_device", [
    "/etc/passwd",
    "/dev/../etc/passwd",
    "/dev/video; rm -rf /",
    "../../dev/video0",
    "/dev/cam-rgb; ls",
    "/dev/SOMETHING",
])
def test_invalid_device_rejected(shims, bad_device):
    shim_dir, _log = shims
    r = _run(_env(shim_dir), ["--device", bad_device, "v4l2-formats"])
    assert r.returncode == 1, f"expected reject {bad_device!r}, got rc={r.returncode}"


def test_valid_device_paths_accepted(shims):
    """Standard /dev/cam-* + /dev/video* allowed."""
    shim_dir, _log = shims
    for d in ["/dev/cam-rgb", "/dev/cam-depth", "/dev/video0", "/dev/video4"]:
        r = _run(_env(shim_dir, V4L2_MODE="info"), ["--device", d, "v4l2-formats"])
        assert r.returncode == 0, f"{d}: rc={r.returncode} stderr={r.stderr}"


# ── Args / help ───────────────────────────────────────────────────────

def test_no_args_exit_nonzero(shims):
    shim_dir, _log = shims
    r = _run(_env(shim_dir), [])
    assert r.returncode != 0


def test_help_lists_all_commands(shims):
    shim_dir, _log = shims
    r = _run(_env(shim_dir), ["--help"])
    for cmd in ("status", "v4l2-formats", "v4l2-info", "v4l2-ctrls",
                "v4l2-set-ctrl", "reset-usb"):
        assert cmd in r.stdout

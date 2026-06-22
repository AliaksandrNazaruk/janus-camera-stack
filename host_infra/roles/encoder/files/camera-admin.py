#!/usr/bin/env python3
"""camera-admin — L0-owned CLI для camera/v4l2 runtime operations.

Same pattern as janus-admin (L3) и encoder-admin (L2): explicit binary
contract так что L4 не shell-out'ит с v4l2-ctl или systemctl unit names.

Commands:
    status                  Device readiness JSON
    v4l2-formats            List supported formats/resolutions/FPS
    v4l2-info               Current capture format + frame parameters
    v4l2-ctrls              List camera controls (brightness, exposure, и т.п.)
    v4l2-set-ctrl K=V       Set single control (K=V form)
    reset-usb               Trigger USB reset (depth camera failsafe)

Default device: /dev/cam-rgb (override via --device).
Sudoers scopes privilege к этому binary. L4 calls via sudo.

Exit codes:
    0   OK
    1   Invalid args / unknown control / parse error
    2   Device not found / not readable
    3   v4l2-ctl operation failed
    4   systemctl operation failed (reset-usb)
    5   Unknown error
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_DEVICE = os.environ.get("CAMERA_ADMIN_DEFAULT_DEVICE", "/dev/cam-rgb")
RESET_USB_UNIT = os.environ.get("CAMERA_ADMIN_RESET_UNIT", "realsense-failsafe.service")

# Input validation: prevent path traversal / injection
_DEVICE_RE = re.compile(r"^/dev/(video\d+|cam-[a-z0-9-]+)$")
_CTRL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

log = logging.getLogger("camera-admin")


def _validate_device(path: str) -> str:
    if not _DEVICE_RE.match(path):
        raise ValueError(f"invalid device path: {path!r}")
    return path


def _v4l2(args: list[str], timeout: int = 8) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["v4l2-ctl"] + args,
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return -1, "", str(exc)


def _systemctl(action: str, unit: str, timeout: int = 90) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["systemctl", action, unit],
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return -1, "", str(exc)


# ── Commands ──────────────────────────────────────────────────────────

def cmd_status(args) -> int:
    try:
        device = _validate_device(args.device)
    except ValueError as exc:
        log.error("%s", exc)
        return 1

    info: dict = {
        "device": device,
        "exists": Path(device).exists(),
        "is_char_device": False,
        "v4l2_responsive": False,
    }
    if info["exists"]:
        try:
            info["is_char_device"] = Path(device).is_char_device()
        except OSError:
            pass
        rc, out, _err = _v4l2(["-d", device, "--info"], timeout=3)
        info["v4l2_responsive"] = rc == 0
        if rc == 0:
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("Driver name"):
                    info["driver"] = line.split(":", 1)[1].strip()
                elif line.startswith("Card type"):
                    info["card"] = line.split(":", 1)[1].strip()

    print(json.dumps(info, indent=2))
    return 0 if info["v4l2_responsive"] else 2


def cmd_v4l2_formats(args) -> int:
    try:
        device = _validate_device(args.device)
    except ValueError as exc:
        log.error("%s", exc)
        return 1
    rc, out, err = _v4l2(["-d", device, "--list-formats-ext"], timeout=8)
    if rc != 0:
        log.error("v4l2-ctl --list-formats-ext failed: %s", err)
        return 3
    sys.stdout.write(out)
    return 0


def cmd_v4l2_info(args) -> int:
    try:
        device = _validate_device(args.device)
    except ValueError as exc:
        log.error("%s", exc)
        return 1
    rc1, fmt_out, fmt_err = _v4l2(["-d", device, "--get-fmt-video"], timeout=5)
    if rc1 != 0:
        log.error("v4l2-ctl --get-fmt-video failed: %s", fmt_err)
        return 3
    rc2, parm_out, parm_err = _v4l2(["-d", device, "--get-parm"], timeout=5)
    if rc2 != 0:
        log.error("v4l2-ctl --get-parm failed: %s", parm_err)
        return 3
    sys.stdout.write(fmt_out)
    sys.stdout.write(parm_out)
    return 0


def cmd_v4l2_ctrls(args) -> int:
    try:
        device = _validate_device(args.device)
    except ValueError as exc:
        log.error("%s", exc)
        return 1
    rc, out, err = _v4l2(["-d", device, "--list-ctrls"], timeout=5)
    if rc != 0:
        log.error("v4l2-ctl --list-ctrls failed: %s", err)
        return 3
    sys.stdout.write(out)
    return 0


def cmd_v4l2_driver_info(args) -> int:
    """v4l2-ctl --info — driver/card capabilities. Used by operator dashboard
    hardware probe к group devices by driver и list video_capture caps."""
    try:
        device = _validate_device(args.device)
    except ValueError as exc:
        log.error("%s", exc)
        return 1
    rc, out, err = _v4l2(["-d", device, "--info"], timeout=3)
    if rc != 0:
        log.error("v4l2-ctl --info failed: %s", err)
        return 3
    sys.stdout.write(out)
    return 0


def cmd_v4l2_list_devices(args) -> int:
    """v4l2-ctl --list-devices — global V4L2 device enumeration с friendly
    USB device labels (groups /dev/video* by driver). No --device arg —
    queries all attached V4L2 capture devices."""
    rc, out, err = _v4l2(["--list-devices"], timeout=5)
    if rc != 0:
        log.error("v4l2-ctl --list-devices failed: %s", err)
        return 3
    sys.stdout.write(out)
    return 0


def cmd_v4l2_set_ctrl(args) -> int:
    try:
        device = _validate_device(args.device)
    except ValueError as exc:
        log.error("%s", exc)
        return 1

    spec = args.control
    if "=" not in spec:
        log.error("control must be K=V (got %r)", spec)
        return 1
    name, value = spec.split("=", 1)
    name = name.strip()
    value = value.strip()
    if not _CTRL_NAME_RE.match(name):
        log.error("invalid control name: %r (must match [a-z][a-z0-9_]*)", name)
        return 1
    try:
        int(value)
    except ValueError:
        log.error("control value must be integer: %r", value)
        return 1

    rc, _out, err = _v4l2(["-d", device, "--set-ctrl", f"{name}={value}"], timeout=8)
    if rc != 0:
        log.error("v4l2-ctl --set-ctrl %s=%s failed: %s", name, value, err)
        return 3
    log.info("set %s=%s on %s", name, value, device)
    return 0


def cmd_reset_usb(args) -> int:
    rc, _out, err = _systemctl("start", RESET_USB_UNIT, timeout=90)
    if rc != 0:
        if "not found" in err.lower() or "could not be found" in err.lower():
            log.error("Reset unit %s not deployed (depth-camera only?)", RESET_USB_UNIT)
            return 2
        log.error("systemctl start %s failed: %s", RESET_USB_UNIT, err)
        return 4
    log.info("USB reset triggered via %s", RESET_USB_UNIT)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="camera-admin",
        description="L0-owned CLI для camera/v4l2 runtime operations",
    )
    parser.add_argument("--verbose", "-v", action="count", default=0)
    parser.add_argument(
        "--device", "-d", default=DEFAULT_DEVICE,
        help=f"V4L2 device path (default: {DEFAULT_DEVICE})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Device readiness JSON")
    sub.add_parser("v4l2-formats", help="List formats/resolutions/FPS")
    sub.add_parser("v4l2-info", help="Current capture format + parm")
    sub.add_parser("v4l2-driver-info", help="Driver/card capabilities (--info)")
    sub.add_parser("v4l2-list-devices", help="Global V4L2 device enumeration (--list-devices)")
    sub.add_parser("v4l2-ctrls", help="List camera controls")
    set_ctrl = sub.add_parser("v4l2-set-ctrl", help="Set single control (K=V)")
    set_ctrl.add_argument("control", help="Control to set, format: name=value")
    sub.add_parser("reset-usb", help=f"Trigger USB reset ({RESET_USB_UNIT})")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    handlers = {
        "status": cmd_status,
        "v4l2-formats": cmd_v4l2_formats,
        "v4l2-info": cmd_v4l2_info,
        "v4l2-driver-info": cmd_v4l2_driver_info,
        "v4l2-list-devices": cmd_v4l2_list_devices,
        "v4l2-ctrls": cmd_v4l2_ctrls,
        "v4l2-set-ctrl": cmd_v4l2_set_ctrl,
        "reset-usb": cmd_reset_usb,
    }
    try:
        return handlers[args.command](args)
    except Exception as exc:
        log.exception("Unexpected error: %s", exc)
        return 5


if __name__ == "__main__":
    sys.exit(main())

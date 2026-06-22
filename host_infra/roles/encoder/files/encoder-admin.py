#!/usr/bin/env python3
"""encoder-admin — L2-owned CLI для encoder runtime operations.

Same pattern as janus-admin (L3): explicit binary contract так что L4
(или другие callers) не shell-out'ят с systemctl unit names.

Commands:
    restart --family F --instance N  Restart <family>@<instance>.service
    stop    [--instance NAME]    Stop unit
    start   [--instance NAME]    Start unit
    status  [--instance NAME]    Print is-active + uptime + ffmpeg PID as JSON

Default instance: color (rs-stream@color). Other sensors: --instance depth/ir1/ir2.
Or --family rtp-v4l2/rtp-rtsp для generic cameras.

Install via Ansible (encoder role). Sudoers entry scopes privilege к этому
binary only.

Exit codes:
    0   OK
    1   Invalid args / unknown instance
    2   Service does not exist
    3   systemctl operation failed
    5   Unknown error
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys

DEFAULT_INSTANCE = os.environ.get("ENCODER_DEFAULT_INSTANCE", "color")

# Unit family templates. Keep the allowlist exhaustive — no shell-out
# поверх arbitrary unit names. Each family represents one encoder pipeline:
#   rs-stream   — FIFO → ffmpeg → RTP    (color/depth/ir1/ir2 — RealSense mux, X3/Phase2)
#   realsense-mux — pyrealsense2 → FIFOs (mux producer, non-instanced)
#   rtp-v4l2    — V4L2 → ffmpeg → RTP    (generic USB webcam/capture, Sprint B2)
#   rtp-rtsp    — RTSP → ffmpeg → RTP    (network IP camera, Sprint B4)
# Phase 2 retired rtp-rgb (V4L2 D435i color) — color теперь rs-stream@color.
UNIT_FAMILIES = {
    "rs-stream":     {"template": "rs-stream@{instance}.service", "instanced": True},
    "realsense-mux": {"template": "realsense-mux.service",        "instanced": False},
    # Sprint B2: generic V4L2 adapter (USB webcam, dashcam, capture card, etc.)
    "rtp-v4l2":      {"template": "rtp-v4l2@{instance}.service",  "instanced": True},
    # Sprint B4: RTSP IP camera adapter (network camera без USB requirement)
    "rtp-rtsp":      {"template": "rtp-rtsp@{instance}.service",  "instanced": True},
}
DEFAULT_FAMILY = "rs-stream"

log = logging.getLogger("encoder-admin")


def _unit_name(family: str, instance: str) -> str:
    """Build systemd unit name with allowlist validation."""
    if family not in UNIT_FAMILIES:
        raise ValueError(f"unknown family {family!r}; allowed: {list(UNIT_FAMILIES)}")
    spec = UNIT_FAMILIES[family]
    if spec["instanced"]:
        if not instance or not all(c.isalnum() or c in "-_" for c in instance):
            raise ValueError(f"invalid instance name: {instance!r}")
        return spec["template"].format(instance=instance)
    # Non-instanced family: ignore --instance arg
    return spec["template"]


def _systemctl(action: str, unit: str, timeout: int = 60) -> tuple[int, str, str]:
    """Run systemctl <action> <unit>. Returns (rc, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["systemctl", action, unit],
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return -1, "", str(exc)


# ── Commands ──────────────────────────────────────────────────────────

def _do_action(action: str, args, timeout: int) -> int:
    """Common impl для restart/stop/start (all just systemctl proxy)."""
    try:
        unit = _unit_name(args.family, args.instance)
    except ValueError as exc:
        log.error("%s", exc)
        return 1

    rc, _out, err = _systemctl(action, unit, timeout=timeout)
    if rc == 0:
        log.info("%s %s OK", action, unit)
        return 0
    # Common failure: unit not loaded
    if "could not be found" in err.lower() or "loaded units" in err.lower():
        log.error("Service does not exist: %s", unit)
        return 2
    log.error("systemctl %s %s failed (rc=%d): %s", action, unit, rc, err)
    return 3


def cmd_restart(args) -> int:
    return _do_action("restart", args, timeout=45)


def cmd_stop(args) -> int:
    return _do_action("stop", args, timeout=15)


def cmd_start(args) -> int:
    return _do_action("start", args, timeout=30)


def cmd_status(args) -> int:
    """Print is-active + uptime + ffmpeg PID as JSON."""
    try:
        unit = _unit_name(args.family, args.instance)
    except ValueError as exc:
        log.error("%s", exc)
        return 1

    is_active_rc, _out, _err = _systemctl("is-active", unit, timeout=5)
    active = is_active_rc == 0

    # Uptime via systemctl show
    rc, out, _err = _systemctl("show", unit, timeout=5)
    uptime_ts = None
    if rc == 0:
        for line in out.splitlines():
            if line.startswith("ActiveEnterTimestamp="):
                uptime_ts = line.split("=", 1)[1] or None
                break

    # ffmpeg PID (best-effort)
    ffmpeg_pid = None
    try:
        pid_result = subprocess.run(
            ["pgrep", "-of", f"ffmpeg.*{args.instance}"],
            capture_output=True, text=True, timeout=3,
        )
        if pid_result.returncode == 0:
            ffmpeg_pid = int(pid_result.stdout.strip().split("\n")[0])
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass

    info = {
        "unit": unit,
        "instance": args.instance,
        "active": active,
        "active_enter_timestamp": uptime_ts,
        "ffmpeg_pid": ffmpeg_pid,
    }
    print(json.dumps(info, indent=2))
    return 0


# ── Main ──────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="encoder-admin",
        description="L2-owned CLI для encoder runtime operations",
    )
    parser.add_argument("--verbose", "-v", action="count", default=0)
    sub = parser.add_subparsers(dest="command", required=True)

    for cmd_name, cmd_help in [
        ("restart", "Restart encoder service"),
        ("stop", "Stop encoder service"),
        ("start", "Start encoder service"),
        ("status", "Print encoder state (JSON)"),
    ]:
        p = sub.add_parser(cmd_name, help=cmd_help)
        p.add_argument(
            "--family", "-f",
            choices=list(UNIT_FAMILIES),
            default=DEFAULT_FAMILY,
            help=f"Unit family (default: {DEFAULT_FAMILY})",
        )
        p.add_argument(
            "--instance", "-i",
            default=DEFAULT_INSTANCE,
            help=f"Instance arg (instanced families). Default: {DEFAULT_INSTANCE}",
        )

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    handlers = {
        "restart": cmd_restart,
        "stop": cmd_stop,
        "start": cmd_start,
        "status": cmd_status,
    }
    try:
        return handlers[args.command](args)
    except Exception as exc:
        log.exception("Unexpected error: %s", exc)
        return 5


if __name__ == "__main__":
    sys.exit(main())

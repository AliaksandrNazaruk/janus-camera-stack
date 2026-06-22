#!/usr/bin/env python3
"""service-admin — L3-owned scoped CLI for systemd SERVICE CONTROL by the L4 control plane.

Replaces the L4 app's broad `sudo -n /bin/systemctl restart <unit>` / `sudo systemctl reboot` with a
SINGLE binary whose sudoers grant is scoped to THIS file. Defense in depth: the CLI itself only
restarts an allowlisted set of gateway units and refuses to restart the L4 service itself — so even a
broadened sudoers grant cannot turn this into arbitrary systemctl. (P1 service-control boundary.)

Mirrors the janus-admin / encoder-admin pattern (bare `systemctl`, resolved via sudo secure_path in
prod). Install via Ansible. Sudoers entry (scoped to THIS binary, NOT full systemctl):
    boris ALL=(root) NOPASSWD: /usr/local/bin/service-admin

Commands:
    restart <unit>   Restart an allowlisted gateway unit. Refuses janus-camera-page (self) + anything
                     not in the allowlist.
    reboot           Reboot the host (the FDIR REBOOT_NODE ladder rung).

Exit codes:
    0   OK
    1   Invalid input (unknown / refused unit, bad args)
    4   systemctl command failed
    5   Unknown / unexpected error
"""
from __future__ import annotations

import argparse
import subprocess
import sys

# Bare `systemctl` — resolved via sudo secure_path in prod (matches janus-admin / encoder-admin).
_SYSTEMCTL = "systemctl"

# Defense in depth: even if the sudoers grant were broadened, this CLI only ever restarts these
# gateway units. Bare names; a trailing ".service" is normalised off before the allowlist check.
ALLOWED_UNITS = frozenset({
    "janus",
    "coturn",
    "janus-textroom-relay",
    "janus_camera_page_hook",
})
# The L4 control-plane app's own unit — never restartable from here (it IS the caller).
SELF_UNIT = "janus-camera-page"

# Exit codes
OK = 0
ERR_INPUT = 1
ERR_SYSTEMCTL = 4
ERR_UNEXPECTED = 5


def _canon(unit: str) -> str:
    """Strip a trailing .service so 'janus' and 'janus.service' both check against the allowlist."""
    return unit[: -len(".service")] if unit.endswith(".service") else unit


def _systemctl(*args: str, timeout: int) -> int:
    try:
        r = subprocess.run([_SYSTEMCTL, *args], capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            sys.stderr.write(r.stderr)
            return ERR_SYSTEMCTL
        return OK
    except FileNotFoundError:
        print("service-admin: systemctl not found", file=sys.stderr)
        return ERR_SYSTEMCTL
    except subprocess.TimeoutExpired:
        print("service-admin: systemctl timed out", file=sys.stderr)
        return ERR_SYSTEMCTL


def cmd_restart(args: argparse.Namespace) -> int:
    unit = args.unit
    canon = _canon(unit)
    if canon == SELF_UNIT:
        print(f"service-admin: refusing to restart self ({SELF_UNIT})", file=sys.stderr)
        return ERR_INPUT
    if canon not in ALLOWED_UNITS:
        print(f"service-admin: unit {unit!r} not in allowlist {sorted(ALLOWED_UNITS)}", file=sys.stderr)
        return ERR_INPUT
    return _systemctl("restart", unit, timeout=60)


def cmd_reboot(args: argparse.Namespace) -> int:
    return _systemctl("reboot", timeout=15)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="service-admin", description="Scoped systemd service control for the L4 control plane.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_restart = sub.add_parser("restart", help="restart an allowlisted gateway unit")
    p_restart.add_argument("unit", help=f"unit to restart (one of {sorted(ALLOWED_UNITS)})")
    p_restart.set_defaults(func=cmd_restart)
    p_reboot = sub.add_parser("reboot", help="reboot the host")
    p_reboot.set_defaults(func=cmd_reboot)
    args = parser.parse_args()
    try:
        return args.func(args)
    except Exception as e:  # top-level guard: any unexpected error -> exit 5
        print(f"service-admin: unexpected error: {e}", file=sys.stderr)
        return ERR_UNEXPECTED


if __name__ == "__main__":
    sys.exit(main())

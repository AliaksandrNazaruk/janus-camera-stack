"""systemd infra adapter.

Unprivileged systemd reads + the BARE-systemctl admin_config path. `show`/`is_active` read state;
`systemctl_action` runs a unit with systemctl rights (no sudo — see infrastructure override.conf).
Privileged service control (restart/reboot) is NOT here — it goes through the scoped `service-admin`
CLI via services/service_control.py (P1 boundary). Pure side-effect primitives — no audit, no HTTP
models; orchestration lives in the application layer.
"""
from __future__ import annotations

import logging
import subprocess
from typing import Dict, Optional

log = logging.getLogger(__name__)


def show(unit: str) -> Optional[Dict[str, str]]:
    """`systemctl show <unit>` parsed to a key=value dict, or None if unavailable."""
    try:
        r = subprocess.run(
            ["systemctl", "show", unit, "--no-page"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode != 0:
            return None
        out: Dict[str, str] = {}
        for line in r.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k] = v
        return out
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


# ── BARE systemctl (no sudo) — admin_config's contract ────────────────────
# DISTINCT from the privileged service control (services/service_control.py → the scoped service-admin
# CLI) on purpose. admin_config runs the unit with systemctl rights (see infrastructure override.conf),
# so it shells out to a *bare* `systemctl` and treats the result as a bool, swallowing exec failures.
# Do NOT fold these into the service-admin path — that's a separate contract (admin_config /
# ROUTE_PURITY_CLOSEOUT.md). The boundary test forbids a sudo+systemctl argv pair, which the bare
# form sidesteps.

def systemctl_action(action: str, unit: str, *, timeout: int = 30) -> bool:
    """`systemctl <action> <unit>` (bare, no sudo) → success bool. Swallows exec failures."""
    try:
        r = subprocess.run(
            ["systemctl", action, unit],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            log.warning("systemctl %s %s failed: %s", action, unit, r.stderr.strip())
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.error("systemctl %s %s exception: %s", action, unit, exc)
        return False


def is_active(unit: str) -> bool:
    """`systemctl is-active <unit>` (bare, 3s timeout) → bool."""
    return systemctl_action("is-active", unit, timeout=3)

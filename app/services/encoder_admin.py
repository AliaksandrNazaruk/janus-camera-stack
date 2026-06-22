"""encoder-admin CLI infra adapter.

Wraps `sudo -n /usr/local/bin/encoder-admin <action> --family X [--instance Y]` (the
approved admin CLI — see test_architecture_fitness) and unit discovery via systemctl.
Pure exec/read primitives — validation / audit / response-shaping live in the
application layer (app/application/encoder_admin.py). Extracted from admin_dashboard (C-04).
"""
from __future__ import annotations

import re
import subprocess
from typing import List, Optional, Tuple

from app.services import system

ENCODER_FAMILIES = {"rtp-v4l2", "rtp-rtsp", "rs-stream", "realsense-mux"}
INSTANCED_FAMILIES = ENCODER_FAMILIES - {"realsense-mux"}
INSTANCE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}$")


def invoke(action: str, family: str, instance: Optional[str], *,
           timeout: int = 45) -> Tuple[int, str]:
    """`sudo -n encoder-admin <action> --family <family> [--instance <instance>]`
    → (returncode, stderr). Raises RuntimeError on an exec failure. --instance is added
    only for instanced families (verbatim from the old route helper)."""
    # Path inlined on the sudo line — test_architecture_fitness requires every
    # /usr/local/bin/*-admin literal to be sudo'd on the same line.
    cmd = ["sudo", "-n", "/usr/local/bin/encoder-admin", action, "--family", family]
    if instance and family in INSTANCED_FAMILIES:
        cmd += ["--instance", instance]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stderr.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        # bare message — the use-case adds the "encoder-admin exec failed: " prefix (verbatim detail)
        raise RuntimeError(str(exc)) from exc


def restart_unit(family: str, instance: str, *, timeout: int) -> None:
    """Restart ONE encoder unit (e.g. rs-stream@<instance>) via the scoped encoder-admin CLI — the
    shared home for the post-tuning-write restart that color_config + sensor_tuning_env each hand-rolled
    (Cycle 6 de-dup). Uses system.run (``sudo``; raises RuntimeError on failure) — NOT invoke()'s
    ``sudo -n``/tuple form — so the callers' domain-error mapping AND their `app.services.system.run`
    test patch-point are preserved verbatim. Path inlined on the sudo line (test_architecture_fitness)."""
    system.run(["sudo", "/usr/local/bin/encoder-admin", "restart",
                "--family", family, "--instance", instance], timeout=timeout)


def discover_units() -> List[Tuple[str, Optional[str]]]:
    """Find all loaded encoder units via `systemctl list-units`.
    Returns list of (family, instance|None) tuples."""
    families_pattern = "|".join(sorted(ENCODER_FAMILIES))
    try:
        r = subprocess.run(
            ["systemctl", "list-units", "--type=service", "--no-pager", "--no-legend", "--all"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode != 0:
            return []
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    pat = re.compile(rf"^\s*((?:{families_pattern})(?:@[a-zA-Z0-9_-]+)?\.service)\s+")
    found: List[Tuple[str, Optional[str]]] = []
    for line in r.stdout.splitlines():
        m = pat.match(line)
        if not m:
            continue
        unit = m.group(1).replace(".service", "")
        if "@" in unit:
            family, instance = unit.split("@", 1)
            found.append((family, instance))
        else:
            found.append((unit, None))
    return found

"""Host network info adapter — primary IP via `hostname -I`.
Extracted from admin_dashboard (C-04 Phase 4). The only remaining route-level subprocess.
"""
from __future__ import annotations

import subprocess
from typing import Optional


def primary_ip() -> Optional[str]:
    try:
        r = subprocess.run(
            ["hostname", "-I"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0:
            parts = r.stdout.strip().split()
            return parts[0] if parts else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None

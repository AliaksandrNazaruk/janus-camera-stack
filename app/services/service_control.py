"""ServiceControlPort — scoped systemd service control for L4 (P1 boundary).

The single app-side adapter that mutates systemd units. Instead of shelling a broad
`sudo -n /bin/systemctl restart <unit>`, it goes through the scoped `service-admin` CLI
(host_infra/roles/janus/files/service-admin.py) whose NOPASSWD sudoers grant is scoped to that ONE
binary and whose internal allowlist bounds which units may restart. There is NO /bin/systemctl
fallback — the host has service-admin (P1 host-first rollout); a fallback would keep the broad path
alive and defeat the boundary. Pure exec primitive; orchestration + audit live in the application layer.
"""
from __future__ import annotations

import subprocess
from typing import Tuple

# Scoped: `sudo -n /usr/local/bin/service-admin ...` (NOPASSWD to this binary only; -n never prompts).
_SERVICE_ADMIN = ["sudo", "-n", "/usr/local/bin/service-admin"]


def restart_unit(unit: str, *, timeout: int = 30) -> Tuple[int, str]:
    """`sudo -n /usr/local/bin/service-admin restart <unit>` → (returncode, stderr).

    Raises RuntimeError on an exec failure (missing binary / timeout) so the caller can map it to a
    500 — matching the prior services/systemd.restart_unit behavior. The unit allowlist is enforced
    inside the CLI (defense in depth); a non-allowlisted unit returns rc=1 with stderr, never raises.
    """
    try:
        r = subprocess.run(
            [*_SERVICE_ADMIN, "restart", unit],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, r.stderr.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        # bare message — the use-case adds the "restart exec failed: " prefix (verbatim detail)
        raise RuntimeError(str(exc)) from exc

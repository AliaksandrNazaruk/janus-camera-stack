"""TURN/STUN allocation probe — replaces TCP-connect false-positive check.

Old `/system/stream-health` TURN check did `socket.connect((host, 3478))` —
returns OK if TCP listener accepts, but doesn't prove:
  - STUN binding returns valid response
  - TURN allocation succeeds with credentials
  - Relay traffic actually flows

This module shells out to `turnutils_stunclient` + `turnutils_uclient` (coturn
package, installed in Debian/Ubuntu base). Both run in subprocess under timeout,
return structured result.

Output: dict with {ok, stun_ok, turn_alloc_ok, mapped_address, error_detail}.
Consumed by routes/system.py for proper health detection.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Any

log = logging.getLogger(__name__)

_STUN_CLIENT = "/usr/bin/turnutils_stunclient"
_TURN_CLIENT = "/usr/bin/turnutils_uclient"


def probe(
    *,
    turn_host: str,
    turn_port: int = 3478,
    turn_user: str | None = None,
    turn_password: str | None = None,
    stun_timeout: int = 5,
    turn_timeout: int = 8,
) -> dict[str, Any]:
    """Probe TURN server real allocation, not just TCP listener.

    Returns dict:
      {
        "ok": bool,                  # overall — STUN OK + TURN alloc OK
        "stun_ok": bool,             # STUN binding response received
        "turn_alloc_ok": bool,       # TURN Allocate succeeded
        "host": str, "port": int,
        "mapped_address": str|None,  # external address per STUN
        "tools_available": bool,
        "error": str|None,
        "error_detail": str|None,
      }
    """
    result: dict[str, Any] = {
        "ok": False,
        "stun_ok": False,
        "turn_alloc_ok": False,
        "host": turn_host,
        "port": turn_port,
        "mapped_address": None,
        "tools_available": True,
        "error": None,
        "error_detail": None,
    }

    # Verify tools installed (coturn package)
    if not shutil.which(_STUN_CLIENT) or not shutil.which(_TURN_CLIENT):
        result["tools_available"] = False
        result["error"] = "turnutils_{stunclient,uclient} missing — install coturn"
        return result

    # 1. STUN binding probe
    try:
        sr = subprocess.run(
            [_STUN_CLIENT, "-p", str(turn_port), turn_host],
            capture_output=True, text=True, timeout=stun_timeout,
        )
        # turnutils_stunclient prints "UDP reflexive addr: <ip>:<port>" on success
        if sr.returncode == 0 and ("reflexive addr" in sr.stdout or "Local address" in sr.stdout):
            result["stun_ok"] = True
            for line in sr.stdout.splitlines():
                if "reflexive addr" in line.lower():
                    result["mapped_address"] = line.split(":", 1)[-1].strip()
                    break
        else:
            result["error"] = "STUN binding failed"
            result["error_detail"] = (sr.stderr or sr.stdout)[:200].strip()
            return result
    except subprocess.TimeoutExpired:
        result["error"] = "STUN probe timeout"
        result["error_detail"] = f"timeout after {stun_timeout}s"
        return result
    except (FileNotFoundError, OSError) as exc:
        result["tools_available"] = False
        result["error"] = "STUN probe execution failed"
        result["error_detail"] = str(exc)
        return result

    # 2. TURN allocation probe (requires credentials).
    # Without credentials we can verify STUN works, but full TURN allocation
    # cannot be tested. That's a documented limitation, not an error.
    if not (turn_user and turn_password):
        result["error"] = "TURN credentials not provided — STUN-only probe"
        # ok=False since alloc not verified, but stun_ok=True
        return result

    try:
        # turnutils_uclient flags:
        #   -t  : use TCP (TURN over TCP). default UDP. We try UDP first.
        #   -u  : user
        #   -w  : password
        #   -m  : number of concurrent peers (1 is enough)
        #   -n  : messages per peer (1 — single allocate/refresh cycle)
        #   -c  : do client-side only (don't expect peer reflection)
        ur = subprocess.run(
            [
                _TURN_CLIENT,
                "-y",            # client-server mode (no peer)
                "-u", turn_user,
                "-w", turn_password,
                "-m", "1",
                "-n", "1",
                "-p", str(turn_port),
                turn_host,
            ],
            capture_output=True, text=True, timeout=turn_timeout,
        )
        # Success indicators: "Total transmit time" + non-zero exchanged bytes
        # OR "Total received bytes" > 0. On failure usually "Cannot allocate"
        # or auth errors with code 401/438.
        out = (ur.stdout + ur.stderr).lower()
        if ur.returncode == 0 and (
            "total transmit time" in out
            or "alloc:" in out
            or "tot_send_bytes" in out
        ):
            result["turn_alloc_ok"] = True
        else:
            result["error"] = "TURN allocation failed"
            # Snippet for diagnosis (truncate)
            result["error_detail"] = (ur.stderr or ur.stdout)[:200].strip()
    except subprocess.TimeoutExpired:
        result["error"] = "TURN allocation timeout"
        result["error_detail"] = f"timeout after {turn_timeout}s"
    except (FileNotFoundError, OSError) as exc:
        result["error"] = "TURN client execution failed"
        result["error_detail"] = str(exc)

    result["ok"] = result["stun_ok"] and result["turn_alloc_ok"]
    return result


def probe_summary(
    *,
    turn_host: str,
    turn_port: int = 3478,
    turn_user: str | None = None,
    turn_password: str | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for health endpoint. Always returns dict, never raises."""
    try:
        return probe(
            turn_host=turn_host,
            turn_port=turn_port,
            turn_user=turn_user,
            turn_password=turn_password,
        )
    except Exception as exc:
        log.exception("TURN probe unexpected error")
        return {
            "ok": False,
            "stun_ok": False,
            "turn_alloc_ok": False,
            "host": turn_host,
            "port": turn_port,
            "mapped_address": None,
            "tools_available": False,
            "error": "probe exception",
            "error_detail": str(exc)[:200],
        }

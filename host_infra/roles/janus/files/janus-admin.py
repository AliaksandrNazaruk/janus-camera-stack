#!/usr/bin/env python3
"""janus-admin — L3-owned CLI for janus runtime operations.

Purpose: provide explicit interface для L4 (control plane) и других callers
вместо того чтобы L4 reach'ал в L3's internals (file writes, systemctl).

Commands:
    restart                  Restart janus.service
    nat-config               Read JSON NAT config from stdin, write jcfg, restart
    status                   Print current jcfg state + lock status

All operations acquire /var/lock/janus-jcfg.lock (shared с NAT updater + TURN rotator).

Why CLI not HTTP daemon:
  - Pure local (no port, no auth needed beyond sudoers)
  - No persistent process, no failure modes того server
  - Subprocess overhead negligible (это admin ops, низкая частота)
  - Sudoers entry scopes privilege to THIS binary, not full systemctl

Install via Ansible. Sudoers entry:
    boris ALL=(root) NOPASSWD: /usr/local/bin/janus-admin

Exit codes:
    0   OK
    1   Invalid input (malformed JSON, missing field)
    2   Lock timeout (another writer holding > 60s)
    3   File mutation failed (jcfg write error)
    4   Service restart failed
    5   Unknown / unexpected error
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Configurable via env (для tests + ops emergency)
JANUS_CFG_PATH = os.environ.get("JANUS_CFG_PATH", "/opt/janus/etc/janus/janus.jcfg")
JCFG_LOCK_PATH = os.environ.get("JCFG_LOCK_PATH", "/var/lock/janus-jcfg.lock")
JCFG_LOCK_TIMEOUT = int(os.environ.get("JCFG_LOCK_TIMEOUT", "60"))
BACKUP_DIR = os.environ.get("JANUS_ADMIN_BACKUP_DIR", "/var/backups/janus-admin")
NAT_BEGIN_MARKER = "# BEGIN NAT AUTO"
NAT_END_MARKER = "# END NAT AUTO"

log = logging.getLogger("janus-admin")


# ── flock context manager (mirrors host_infra writers) ────────────────

@contextlib.contextmanager
def jcfg_lock(timeout: int = JCFG_LOCK_TIMEOUT, path: str = JCFG_LOCK_PATH):
    """Acquire exclusive lock на janus.jcfg writes.

    Same semantics as janus-turn-rotator.py + L4 nat_config._jcfg_lock.
    """
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o644)
    deadline = time.time() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() >= deadline:
                    raise TimeoutError(
                        f"Could not acquire {path} within {timeout}s"
                    )
                time.sleep(0.5)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# ── Atomic write with backup ──────────────────────────────────────────

def atomic_write(path: str, content: str) -> None:
    """Backup existing + atomic rename. Caller MUST hold jcfg_lock()."""
    p = Path(path)
    if p.is_file():
        Path(BACKUP_DIR).mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
        shutil.copy2(p, Path(BACKUP_DIR) / f"{p.name}.{ts}.bak")

    tmp = p.with_suffix(p.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(content)
    os.replace(tmp, p)


# ── NAT block rendering ───────────────────────────────────────────────

def render_nat_block(cfg: dict) -> str:
    """Render NAT block from JSON config. Matches L4's existing rendering."""

    def b(v: bool) -> str:
        return "true" if v else "false"

    # ICE list — enforce wins over ignore (L4 контракт)
    if cfg.get("ice_enforce_list"):
        ice_line = f'  ice_enforce_list = "{cfg["ice_enforce_list"]}"'
    else:
        items = ", ".join(f'"{s}"' for s in cfg.get("ice_ignore_list", []))
        ice_line = f"  ice_ignore_list = [ {items} ]"

    return f"""nat: {{
  ice_tcp = {b(cfg.get("ice_tcp", False))}
  full_trickle = {b(cfg.get("full_trickle", True))}
  ignore_mdns = true
  keep_private_host = {b(cfg.get("keep_private_host", False))}
{ice_line}

  stun_server = "{cfg.get("stun_server", "")}"
  stun_port   = {cfg.get("stun_port", 3478)}

  turn_server = "{cfg.get("turn_server", "")}"
  turn_port   = {cfg.get("turn_port", 3478)}
  turn_type   = "{cfg.get("turn_type", "udp")}"
  turn_user   = "{cfg.get("turn_user", "")}"
  turn_pwd    = "{cfg.get("turn_pwd", "")}"

  nat_1_1_mapping = "{cfg.get("nat_1_1_mapping", "")}"
  min_port = {cfg.get("min_port", 10000)}
  max_port = {cfg.get("max_port", 20000)}
}}"""


def patch_nat_block(jcfg_text: str, new_block: str) -> str:
    """Replace text between BEGIN/END markers с new_block. Raise если markers missing."""
    try:
        start = jcfg_text.index(NAT_BEGIN_MARKER)
        end = jcfg_text.index(NAT_END_MARKER, start)
    except ValueError as exc:
        raise RuntimeError(
            f"Markers '{NAT_BEGIN_MARKER}' / '{NAT_END_MARKER}' not found в janus.jcfg"
        ) from exc

    before = jcfg_text[:start].rstrip()
    after = jcfg_text[end + len(NAT_END_MARKER):].lstrip()
    return f"{before}\n{NAT_BEGIN_MARKER}\n{new_block}\n{NAT_END_MARKER}\n{after}"


# ── Service restart ───────────────────────────────────────────────────

def restart_janus_service() -> bool:
    """systemctl restart janus.service. Returns True on success."""
    try:
        result = subprocess.run(
            ["systemctl", "restart", "janus.service"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            log.error("systemctl restart janus failed: %s", result.stderr.strip())
            return False
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.error("Failed to restart janus: %s", exc)
        return False


# ── Commands ──────────────────────────────────────────────────────────

def cmd_restart(_args) -> int:
    """Restart janus.service. Acquires lock для coordination с other writers."""
    try:
        with jcfg_lock():
            if not restart_janus_service():
                return 4
            log.info("janus.service restarted")
            return 0
    except TimeoutError as exc:
        log.error("%s", exc)
        return 2


def cmd_nat_config(args) -> int:
    """Read JSON NAT config from stdin (or --file), patch jcfg, restart janus.

    Input JSON example:
      {
        "turn_server": "turn.example.com",
        "turn_user": "user",
        "turn_pwd": "...",
        "nat_1_1_mapping": "1.2.3.4",
        ...
      }
    """
    try:
        if args.file and args.file != "-":
            cfg_text = Path(args.file).read_text()
        else:
            cfg_text = sys.stdin.read()
        cfg = json.loads(cfg_text)
    except (OSError, json.JSONDecodeError) as exc:
        log.error("Failed to parse NAT config: %s", exc)
        return 1

    if not isinstance(cfg, dict):
        log.error("NAT config must be JSON object")
        return 1

    try:
        with jcfg_lock():
            if not Path(JANUS_CFG_PATH).exists():
                log.error("%s not found", JANUS_CFG_PATH)
                return 3
            current = Path(JANUS_CFG_PATH).read_text()
            new_block = render_nat_block(cfg)
            try:
                new_text = patch_nat_block(current, new_block)
            except RuntimeError as exc:
                log.error("%s", exc)
                return 3

            atomic_write(JANUS_CFG_PATH, new_text)
            log.info("NAT block updated в %s", JANUS_CFG_PATH)

            if args.no_restart:
                log.warning("--no-restart: jcfg updated, janus НЕ restarted")
                return 0

            if not restart_janus_service():
                return 4
            log.info("janus.service restarted с new NAT config")
    except TimeoutError as exc:
        log.error("%s", exc)
        return 2

    return 0


def cmd_status(_args) -> int:
    """Print jcfg state — presence of NAT markers, lock status."""
    jcfg = Path(JANUS_CFG_PATH)
    info = {
        "jcfg_path": str(jcfg),
        "jcfg_exists": jcfg.exists(),
        "jcfg_size": jcfg.stat().st_size if jcfg.exists() else 0,
        "nat_markers_present": False,
        "lock_path": JCFG_LOCK_PATH,
        "lock_held_by_other": False,
    }
    if jcfg.exists():
        text = jcfg.read_text()
        info["nat_markers_present"] = NAT_BEGIN_MARKER in text and NAT_END_MARKER in text

    # Probe lock without blocking
    try:
        fd = os.open(JCFG_LOCK_PATH, os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
        except BlockingIOError:
            info["lock_held_by_other"] = True
        finally:
            os.close(fd)
    except OSError:
        pass

    print(json.dumps(info, indent=2))
    return 0


# ── Main ──────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="janus-admin",
        description="L3-owned CLI для janus runtime operations",
    )
    parser.add_argument("--verbose", "-v", action="count", default=0)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("restart", help="Restart janus.service (под flock)")

    nat = sub.add_parser("nat-config", help="Update NAT block + restart janus")
    nat.add_argument("--file", "-f", default="-", help="Input JSON file ('-' для stdin)")
    nat.add_argument("--no-restart", action="store_true", help="Не restart janus после update (testing)")

    sub.add_parser("status", help="Print jcfg + lock status as JSON")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    handlers = {
        "restart": cmd_restart,
        "nat-config": cmd_nat_config,
        "status": cmd_status,
    }
    try:
        return handlers[args.command](args)
    except Exception as exc:
        log.exception("Unexpected error: %s", exc)
        return 5


if __name__ == "__main__":
    sys.exit(main())

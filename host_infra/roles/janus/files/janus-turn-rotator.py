#!/usr/bin/env python3
"""janus-turn-rotator — rotate TURN ephemeral credentials в janus.jcfg.

Why: TURN credentials имеют expiry timestamp (e.g. unix=1805907538 → 2027-09-21).
Когда expiry близок — клиенты за симметричным NAT не смогут подключиться.
Этот script ротирует creds за N дней до expiry.

Reads:
  /etc/robot/camera-secrets.env       TURN_SHARED_SECRET=...
  /opt/janus/etc/janus/janus.jcfg     turn_user, turn_pwd, turn_server, turn_port

Writes:
  /opt/janus/etc/janus/janus.jcfg     (in-place patch + atomic via tmp+rename)
  /var/log/janus-turn-rotator.log     (or journald)

Side-effect:
  systemctl restart janus              (if jcfg actually changed)

Exit codes:
  0  — no action needed (expiry > threshold)
  0  — rotated successfully
  1  — would rotate but --check mode
  2  — error (missing secret, malformed jcfg, etc.)

Run modes:
  --dry-run    Print planned action without writing
  --check      Exit 1 если expiry < threshold (для monitoring)
  --force      Rotate now regardless of expiry
  Default      Rotate only if expiry < ROTATE_BEFORE_DAYS days

Schedule: systemd timer запускает раз в день. Idempotent — noop пока не пора.

Pure stdlib — никаких 3rd party dependencies. Тестируется через unittest.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import hashlib
import hmac
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


# ── Configuration (override через ENV) ───────────────────────────────

SECRETS_PATH = os.environ.get(
    "TURN_ROTATOR_SECRETS",
    "/etc/robot/camera-secrets.env",
)
JCFG_PATH = os.environ.get(
    "TURN_ROTATOR_JCFG",
    "/opt/janus/etc/janus/janus.jcfg",
)
DEFAULT_TTL_DAYS = int(os.environ.get("TURN_ROTATOR_TTL_DAYS", "365"))
DEFAULT_ROTATE_BEFORE_DAYS = int(os.environ.get("TURN_ROTATOR_BEFORE_DAYS", "30"))
TURN_USERNAME = os.environ.get("TURN_ROTATOR_USERNAME", "webrtc")

# Shared lock с janus-nat-updater.sh — оба writer'а на janus.jcfg.
# Без него: NAT updater и TURN rotator могут race'нуть на read-modify-write.
JCFG_LOCK_PATH = os.environ.get("JCFG_LOCK_PATH", "/var/lock/janus-jcfg.lock")
JCFG_LOCK_TIMEOUT = int(os.environ.get("JCFG_LOCK_TIMEOUT", "60"))

log = logging.getLogger("turn-rotator")


# ── HMAC credential generation (coturn use-auth-secret protocol) ────

def generate_credentials(shared_secret: str, ttl_days: int, username: str = "webrtc") -> tuple[str, str, int]:
    """Generate ephemeral TURN credentials.

    coturn use-auth-secret protocol:
      username = "<unix_expiry>:<user>"
      password = base64(HMAC-SHA1(username, shared_secret))

    Returns: (turn_user, turn_pwd, expiry_unix)
    """
    expiry = int(time.time()) + ttl_days * 86400
    user = f"{expiry}:{username}"
    digest = hmac.new(
        shared_secret.encode(),
        user.encode(),
        hashlib.sha1,
    ).digest()
    pwd = base64.b64encode(digest).decode()
    return user, pwd, expiry


# ── jcfg parsing (just the turn_* lines) ─────────────────────────────

_TURN_USER_RE = re.compile(r'turn_user\s*=\s*"([^"]*)"')
_TURN_PWD_RE = re.compile(r'turn_pwd\s*=\s*"([^"]*)"')


def parse_current_creds(jcfg_content: str) -> Optional[tuple[str, str, int]]:
    """Extract current turn_user, turn_pwd, expiry from jcfg content.

    Returns: (user, pwd, expiry) или None если turn config отсутствует.
    """
    u = _TURN_USER_RE.search(jcfg_content)
    p = _TURN_PWD_RE.search(jcfg_content)
    if not u or not p:
        return None
    user = u.group(1)
    pwd = p.group(1)
    # Extract expiry from "<expiry>:<username>" format
    if ":" in user:
        try:
            expiry = int(user.split(":", 1)[0])
        except ValueError:
            expiry = 0
    else:
        expiry = 0
    return user, pwd, expiry


def patch_jcfg(jcfg_content: str, new_user: str, new_pwd: str) -> str:
    """Replace turn_user + turn_pwd values in jcfg content. Idempotent."""
    out = _TURN_USER_RE.sub(f'turn_user   = "{new_user}"', jcfg_content)
    out = _TURN_PWD_RE.sub(f'turn_pwd    = "{new_pwd}"', out)
    return out


# ── Secret loading ───────────────────────────────────────────────────

def load_shared_secret(path: str) -> Optional[str]:
    """Read TURN_SHARED_SECRET from env file."""
    try:
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if line.startswith("TURN_SHARED_SECRET="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                return value or None
    except (OSError, PermissionError):
        return None
    return None


# ── File I/O ─────────────────────────────────────────────────────────

@contextlib.contextmanager
def jcfg_lock(timeout: int = JCFG_LOCK_TIMEOUT, path: str = JCFG_LOCK_PATH):
    """Acquire exclusive lock on janus.jcfg writes.

    Coordinated с janus-nat-updater.sh (uses flock на same path).
    Без lock: NAT updater и TURN rotator могут одновременно read-modify-write
    → одна mutation теряется.

    Raises TimeoutError если lock не acquired за timeout seconds.
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
                        f"Could not acquire {path} within {timeout}s — "
                        f"NAT updater или другой rotator process ещё работает"
                    )
                time.sleep(0.5)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def atomic_write(path: str, content: str, *, backup_dir: str = "/var/backups/janus-turn-rotator") -> None:
    """Backup existing + atomic rename for safe rewrite.

    Caller MUST hold jcfg_lock() context — иначе race с NAT updater.
    """
    p = Path(path)
    if p.is_file():
        Path(backup_dir).mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
        shutil.copy2(p, Path(backup_dir) / f"{p.name}.{ts}.bak")

    tmp = p.with_suffix(p.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(content)
    os.replace(tmp, p)


def restart_janus() -> bool:
    """systemctl restart janus. Returns True on success."""
    try:
        result = subprocess.run(
            ["systemctl", "restart", "janus.service"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            log.error("systemctl restart janus failed: %s", result.stderr)
            return False
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.error("Failed to restart janus: %s", exc)
        return False


# ── Main rotation logic ──────────────────────────────────────────────

def should_rotate(current_expiry: int, before_days: int) -> bool:
    """True если expiry < now + before_days."""
    if current_expiry == 0:
        return True  # no current cred = rotate
    deadline = int(time.time()) + before_days * 86400
    return current_expiry <= deadline


def rotate(
    *,
    secrets_path: str = SECRETS_PATH,
    jcfg_path: str = JCFG_PATH,
    ttl_days: int = DEFAULT_TTL_DAYS,
    before_days: int = DEFAULT_ROTATE_BEFORE_DAYS,
    username: str = TURN_USERNAME,
    dry_run: bool = False,
    check_only: bool = False,
    force: bool = False,
    restart: bool = True,
) -> int:
    """Main entry point. Returns exit code.

    0 = no action needed OR rotated successfully
    1 = --check found expiry < threshold (action would be taken)
    2 = error
    """
    # Load secret
    secret = load_shared_secret(secrets_path)
    if not secret:
        log.error("TURN_SHARED_SECRET not found in %s (or empty)", secrets_path)
        return 2

    # ── Optimistic check (without lock — staleness OK для решения idle vs rotate) ──
    try:
        jcfg = Path(jcfg_path).read_text()
    except OSError as exc:
        log.error("Cannot read %s: %s", jcfg_path, exc)
        return 2

    current = parse_current_creds(jcfg)
    current_expiry = current[2] if current else 0
    if current_expiry:
        days_left = (current_expiry - int(time.time())) // 86400
        log.info("Current cred expiry=%d (%d days left)", current_expiry, days_left)
    else:
        log.info("No current cred or invalid expiry — will rotate")

    needs_rotation = force or should_rotate(current_expiry, before_days)

    if check_only:
        if needs_rotation:
            log.warning("ROTATION NEEDED (expiry too close OR forced)")
            return 1
        log.info("OK — no rotation needed")
        return 0

    if not needs_rotation:
        log.info("No action needed — expiry > %d days threshold", before_days)
        return 0

    if dry_run:
        new_user, new_pwd, new_expiry = generate_credentials(secret, ttl_days, username)
        log.info("DRY-RUN: would write %s (new expiry=%d) + restart janus.service",
                 jcfg_path, new_expiry)
        return 0

    # ── Authoritative read-modify-write под lock (coordinated с NAT updater) ──
    try:
        with jcfg_lock():
            # Re-read inside lock — другие writer'ы (NAT updater) могли изменить
            # nat_1_1_mapping за время от optimistic check до сейчас.
            jcfg = Path(jcfg_path).read_text()

            new_user, new_pwd, new_expiry = generate_credentials(secret, ttl_days, username)
            log.info("New cred: expiry=%d (%s)", new_expiry,
                     time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(new_expiry)))

            new_jcfg = patch_jcfg(jcfg, new_user, new_pwd)
            if new_jcfg == jcfg:
                log.error("Patch produced no diff — turn_user/turn_pwd regex didn't match. Bailing.")
                return 2

            atomic_write(jcfg_path, new_jcfg)
            log.info("Wrote %s", jcfg_path)

            if restart:
                if not restart_janus():
                    log.error("janus restart failed — old creds in memory until next restart")
                    return 2
                log.info("janus restarted successfully")
            else:
                log.warning("--no-restart: new creds в jcfg, janus держит старые в памяти. Restart manually.")
    except TimeoutError as exc:
        log.error("%s", exc)
        return 2

    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Rotate TURN ephemeral credentials")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print planned action, don't write")
    parser.add_argument("--check", action="store_true",
                        help="Exit 1 если expiry < threshold (for monitoring)")
    parser.add_argument("--force", action="store_true",
                        help="Rotate regardless of expiry")
    parser.add_argument("--no-restart", action="store_true",
                        help="Don't restart janus after rotation (testing)")
    parser.add_argument("--ttl-days", type=int, default=DEFAULT_TTL_DAYS,
                        help=f"New credential TTL in days (default {DEFAULT_TTL_DAYS})")
    parser.add_argument("--before-days", type=int, default=DEFAULT_ROTATE_BEFORE_DAYS,
                        help=f"Rotate when expiry within N days (default {DEFAULT_ROTATE_BEFORE_DAYS})")
    parser.add_argument("--verbose", "-v", action="count", default=0)
    args = parser.parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    return rotate(
        ttl_days=args.ttl_days,
        before_days=args.before_days,
        dry_run=args.dry_run,
        check_only=args.check,
        force=args.force,
        restart=not args.no_restart,
    )


if __name__ == "__main__":
    sys.exit(main())

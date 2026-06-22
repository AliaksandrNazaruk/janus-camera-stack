"""Secret store — atomic read/write of /etc/robot/camera-secrets.env.

Used by admin_config routes for rotation + masked snapshot. Distinct from
Settings (which reads env at process start — needs explicit reload after
write here, OR services restart).

Format: simple KEY=VALUE lines, hash comments allowed. Stored with mode 0600.

Constraints:
- Atomic writes (.tmp + rename) — avoid partial file if crash mid-write
- Masked snapshot — never expose plaintext in API responses unless explicit reveal
- Per-key timestamps tracked in sidecar /etc/robot/.camera-secrets.timestamps
"""
from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import re
import secrets as _secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, Optional

from app.services.store_safety import StoreCorrupt, atomic_write_text, quarantine_corrupt

log = logging.getLogger(__name__)

SECRETS_FILE = Path(os.environ.get("CAMERA_SECRETS_FILE", "/etc/robot/camera-secrets.env"))
TIMESTAMPS_FILE = Path(os.environ.get("CAMERA_SECRETS_TS_FILE", "/etc/robot/.camera-secrets.timestamps"))

# Keys that are sensitive (mask in snapshots). Anything not in this set
# returned in plain (e.g., TURN_HOST, TURN_REALM are configuration, not secret).
SENSITIVE_KEYS = {
    "TURN_SHARED_SECRET",
    "JANUS_ADMIN_SECRET",
    "STREAMING_ADMIN_KEY",
    "JANUS_STREAMING_ADMIN_KEY",   # production alias
    "STREAMING_RGB_MP_SECRET",
    "TEXTROOM_ROOM_SECRET",
    "INTERNAL_API_SECRET",
    "CAM_ADMIN_TOKEN",
}

# Format generators per key (some Janus keys expect base64url, not hex)
_BASE64URL_KEYS = {
    "STREAMING_ADMIN_KEY",
    "JANUS_STREAMING_ADMIN_KEY",
    "STREAMING_RGB_MP_SECRET",
    "TEXTROOM_ROOM_SECRET",
}


@dataclass(frozen=True)
class MaskedValue:
    key: str
    masked: str       # "ab●●●●xy" or "[unset]"
    is_set: bool
    is_sensitive: bool
    last_rotated_ts: Optional[int]   # unix seconds


def _mask(value: str) -> str:
    if not value:
        return "[unset]"
    if len(value) <= 8:
        return "●●●●●●●●"
    return f"{value[:3]}●●●●●●{value[-3:]}"


def _parse_env_file(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        # Strip optional surrounding quotes
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        out[k.strip()] = v
    return out


@contextlib.contextmanager
def _secrets_lock() -> Iterator[None]:
    """Exclusive flock around a rotate()/set_field() read-modify-write so two concurrent rotations
    don't lost-update each other (each loads the same base, the second clobbers the first's key)."""
    lock = SECRETS_FILE.with_suffix(SECRETS_FILE.suffix + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock), os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _load() -> Dict[str, str]:
    """Parse camera-secrets.env. Absent → {} (first run). Lenient on an individual stray line (skip —
    a hand-edited file with one odd line is not corruption). FAIL CLOSED only on real corruption:
    undecodable bytes, or a non-empty file whose meaningful (non-comment, non-blank) lines yield ZERO
    keys → quarantine + raise (never read a garbled secret file as empty → silent loss on next save)."""
    if not SECRETS_FILE.exists():
        return {}
    try:
        text = SECRETS_FILE.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        q = quarantine_corrupt(SECRETS_FILE, f"undecodable: {e}")
        raise StoreCorrupt(f"{SECRETS_FILE} is not valid UTF-8 ({e}); quarantined {q}") from e
    except OSError as e:
        # access/IO error (permission) is not corruption — degrade + warn (the write path fails too).
        log.warning("secret store %s unreadable (%s) — treating as empty for this read", SECRETS_FILE, e)
        return {}
    parsed = _parse_env_file(text)
    if not parsed:
        meaningful = [ln for ln in text.splitlines()
                      if ln.strip() and not ln.strip().startswith("#")]
        if meaningful:
            q = quarantine_corrupt(SECRETS_FILE, "non-empty file but zero parseable KEY=VALUE lines")
            raise StoreCorrupt(f"{SECRETS_FILE} has content but no parseable keys; quarantined {q}")
    return parsed


def _load_timestamps() -> Dict[str, int]:
    if not TIMESTAMPS_FILE.exists():
        return {}
    out: Dict[str, int] = {}
    for line in TIMESTAMPS_FILE.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        try:
            out[k.strip()] = int(v.strip())
        except ValueError:
            continue
    return out


def _save_timestamps(ts: Dict[str, int]) -> None:
    atomic_write_text(
        TIMESTAMPS_FILE,
        "\n".join(f"{k}={v}" for k, v in sorted(ts.items())) + "\n",
        mode=0o600,
    )


def _save_env(values: Dict[str, str], header_lines: Optional[list] = None) -> None:
    """Atomic durable write (fsync + dir-fsync via store_safety) — preserves header comments."""
    lines: list = []
    if header_lines:
        lines.extend(header_lines)
    else:
        # Re-use existing header if present
        if SECRETS_FILE.exists():
            existing = SECRETS_FILE.read_text(encoding="utf-8").splitlines()
            for line in existing:
                if line.startswith("#") or not line.strip():
                    lines.append(line)
                else:
                    break  # stop at first KEY= line
    seen = set()
    for line in lines:
        m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=", line)
        if m and m.group(1) in values:
            # Replace inline (preserves comments above)
            seen.add(m.group(1))
    # Emit values: keep order of what was already there, append new at end
    if SECRETS_FILE.exists():
        existing = SECRETS_FILE.read_text(encoding="utf-8").splitlines()
        rendered = []
        for line in existing:
            m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=", line)
            if m and m.group(1) in values:
                rendered.append(f"{m.group(1)}={values[m.group(1)]}")
                seen.add(m.group(1))
            else:
                rendered.append(line)
        for k, v in values.items():
            if k not in seen:
                rendered.append(f"{k}={v}")
        out_text = "\n".join(rendered)
    else:
        header = "# Auto-managed by camera-page admin_config\n"
        out_text = header + "\n".join(f"{k}={v}" for k, v in values.items())
    if not out_text.endswith("\n"):
        out_text += "\n"
    atomic_write_text(SECRETS_FILE, out_text, mode=0o600)


# ── Public API ──────────────────────────────────────────────────────────

def snapshot() -> Dict[str, MaskedValue]:
    """Return masked snapshot of all keys in secrets file."""
    values = _load()
    timestamps = _load_timestamps()
    out: Dict[str, MaskedValue] = {}
    for k, v in values.items():
        is_sensitive = k in SENSITIVE_KEYS
        displayed = _mask(v) if is_sensitive else v
        out[k] = MaskedValue(
            key=k,
            masked=displayed,
            is_set=bool(v),
            is_sensitive=is_sensitive,
            last_rotated_ts=timestamps.get(k),
        )
    # Also include known-but-unset sensitive keys (so UI shows "Rotate" button)
    for k in SENSITIVE_KEYS:
        if k not in out:
            out[k] = MaskedValue(
                key=k,
                masked="[unset]",
                is_set=False,
                is_sensitive=True,
                last_rotated_ts=None,
            )
    return out


def reveal(key: str) -> Optional[str]:
    """Return plaintext value (for re-auth-gated reveal). None if key not set."""
    values = _load()
    return values.get(key)


def _generate(key: str) -> str:
    """Generate new value appropriate for the key's format."""
    if key in _BASE64URL_KEYS:
        # Janus textroom/streaming format: base64url 32 random bytes
        return _secrets.token_urlsafe(32)
    # Default: 32 random bytes hex (matches openssl rand -hex 32)
    return _secrets.token_hex(32)


def rotate(key: str) -> str:
    """Generate new secret for key, persist atomically, update timestamp. Returns the new value
    (caller may show it once to admin). flock'd so a concurrent rotate can't lost-update the file."""
    with _secrets_lock():
        values = _load()
        new_value = _generate(key)
        values[key] = new_value
        _save_env(values)
        ts = _load_timestamps()
        ts[key] = int(time.time())
        _save_timestamps(ts)
        return new_value


def set_field(key: str, value: str) -> None:
    """Set a non-secret field (TURN_HOST, TURN_REALM, etc.)."""
    if key in SENSITIVE_KEYS:
        raise ValueError(f"Use rotate() for sensitive keys, not set_field({key!r})")
    with _secrets_lock():
        values = _load()
        values[key] = value
        _save_env(values)

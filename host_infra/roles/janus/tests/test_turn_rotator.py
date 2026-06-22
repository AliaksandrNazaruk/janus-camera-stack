"""Unit tests for janus-turn-rotator.

Loads the script directly via importlib (the deploy artifact is plain Python,
not packaged). Pure stdlib + pytest — no other deps.

Run from host_infra/:
    pytest roles/janus/tests/

Or via Makefile:
    make test
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import multiprocessing
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest


# ── Load script as module ──────────────────────────────────────────────

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "files" / "janus-turn-rotator.py"


@pytest.fixture(scope="session")
def rotator():
    spec = importlib.util.spec_from_file_location("turn_rotator", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── generate_credentials() ────────────────────────────────────────────

def test_generate_credentials_format(rotator):
    """Generated cred matches coturn use-auth-secret protocol."""
    user, pwd, expiry = rotator.generate_credentials("shhh", ttl_days=1, username="webrtc")
    assert user.endswith(":webrtc")
    assert int(user.split(":", 1)[0]) == expiry
    # password must be valid base64-encoded HMAC-SHA1 (20 bytes → 28 chars w/ padding)
    decoded = base64.b64decode(pwd)
    assert len(decoded) == 20  # SHA1 is 160 bits


def test_generate_credentials_deterministic(rotator, monkeypatch):
    """Given fixed time + secret + ttl, output is deterministic (HMAC of known input)."""
    monkeypatch.setattr(rotator.time, "time", lambda: 1_700_000_000)
    user1, pwd1, exp1 = rotator.generate_credentials("secret", ttl_days=365, username="u")
    user2, pwd2, exp2 = rotator.generate_credentials("secret", ttl_days=365, username="u")
    assert user1 == user2 and pwd1 == pwd2 and exp1 == exp2


def test_generate_credentials_hmac_correct(rotator, monkeypatch):
    """Computed pwd matches independent HMAC-SHA1 of expiry:username vs secret."""
    monkeypatch.setattr(rotator.time, "time", lambda: 1_700_000_000)
    user, pwd, expiry = rotator.generate_credentials("mysecret", ttl_days=10, username="alice")

    # Independent computation
    expected_user = f"{expiry}:alice"
    expected_digest = hmac.new(b"mysecret", expected_user.encode(), hashlib.sha1).digest()
    expected_pwd = base64.b64encode(expected_digest).decode()

    assert user == expected_user
    assert pwd == expected_pwd


def test_generate_credentials_different_ttl_different_expiry(rotator, monkeypatch):
    monkeypatch.setattr(rotator.time, "time", lambda: 1_000_000)
    _, _, exp_1d = rotator.generate_credentials("s", ttl_days=1)
    _, _, exp_365d = rotator.generate_credentials("s", ttl_days=365)
    assert exp_365d - exp_1d == 364 * 86400


# ── parse_current_creds() ─────────────────────────────────────────────

def test_parse_current_creds_finds_both(rotator):
    jcfg = '''
    turn_server = "turn.example.com"
    turn_user   = "1812986292:webrtc"
    turn_pwd    = "abc123=="
    '''
    result = rotator.parse_current_creds(jcfg)
    assert result is not None
    user, pwd, expiry = result
    assert user == "1812986292:webrtc"
    assert pwd == "abc123=="
    assert expiry == 1812986292


def test_parse_current_creds_missing_returns_none(rotator):
    jcfg = 'no turn config here'
    assert rotator.parse_current_creds(jcfg) is None


def test_parse_current_creds_malformed_expiry(rotator):
    """user без ':' или с non-integer prefix → expiry=0."""
    jcfg = 'turn_user = "no-colon-here"\nturn_pwd = "x"'
    user, pwd, expiry = rotator.parse_current_creds(jcfg)
    assert expiry == 0


def test_parse_current_creds_only_pwd_returns_none(rotator):
    jcfg = 'turn_pwd = "lonely"'
    assert rotator.parse_current_creds(jcfg) is None


# ── patch_jcfg() ──────────────────────────────────────────────────────

def test_patch_jcfg_replaces_both(rotator):
    jcfg = '''
nat: {
  turn_user   = "old:user"
  turn_pwd    = "OLD=="
}
'''
    out = rotator.patch_jcfg(jcfg, "new:user", "NEW==")
    assert 'turn_user   = "new:user"' in out
    assert 'turn_pwd    = "NEW=="' in out
    assert "old:user" not in out
    assert "OLD==" not in out


def test_patch_jcfg_idempotent(rotator):
    """Calling patch twice with same values → no change after first."""
    jcfg = 'turn_user = "a:b"\nturn_pwd = "c"'
    out1 = rotator.patch_jcfg(jcfg, "x:y", "z")
    out2 = rotator.patch_jcfg(out1, "x:y", "z")
    assert out1 == out2


def test_patch_jcfg_no_match_returns_input(rotator):
    """jcfg without turn lines — patch ничего не делает."""
    jcfg = "general: { enabled = true }"
    out = rotator.patch_jcfg(jcfg, "u:v", "p")
    assert out == jcfg


# ── should_rotate() ───────────────────────────────────────────────────

def test_should_rotate_expiry_zero_always_rotates(rotator):
    """No current cred — always rotate."""
    assert rotator.should_rotate(0, before_days=30) is True


def test_should_rotate_far_future_no(rotator, monkeypatch):
    """Expiry > 30 days в будущем — no rotation."""
    monkeypatch.setattr(rotator.time, "time", lambda: 1_000_000)
    future = 1_000_000 + 60 * 86400  # 60 days away
    assert rotator.should_rotate(future, before_days=30) is False


def test_should_rotate_within_threshold_yes(rotator, monkeypatch):
    monkeypatch.setattr(rotator.time, "time", lambda: 1_000_000)
    soon = 1_000_000 + 10 * 86400  # 10 days
    assert rotator.should_rotate(soon, before_days=30) is True


def test_should_rotate_exactly_at_threshold(rotator, monkeypatch):
    """Boundary: expiry == now + threshold → rotate (<=)."""
    monkeypatch.setattr(rotator.time, "time", lambda: 1_000_000)
    at_threshold = 1_000_000 + 30 * 86400
    assert rotator.should_rotate(at_threshold, before_days=30) is True


def test_should_rotate_in_past(rotator, monkeypatch):
    """Expired creds — definitely rotate."""
    monkeypatch.setattr(rotator.time, "time", lambda: 1_000_000)
    past = 1_000_000 - 86400
    assert rotator.should_rotate(past, before_days=30) is True


# ── load_shared_secret() ──────────────────────────────────────────────

def test_load_shared_secret_finds_key(rotator, tmp_path):
    env = tmp_path / "secrets.env"
    env.write_text(
        "# comment\n"
        "OTHER=foo\n"
        'TURN_SHARED_SECRET="my-secret-value"\n'
        "AFTER=bar\n"
    )
    assert rotator.load_shared_secret(str(env)) == "my-secret-value"


def test_load_shared_secret_no_quotes(rotator, tmp_path):
    env = tmp_path / "secrets.env"
    env.write_text("TURN_SHARED_SECRET=unquoted-value\n")
    assert rotator.load_shared_secret(str(env)) == "unquoted-value"


def test_load_shared_secret_missing_file(rotator):
    assert rotator.load_shared_secret("/nonexistent/path/secrets.env") is None


def test_load_shared_secret_empty_value(rotator, tmp_path):
    env = tmp_path / "secrets.env"
    env.write_text('TURN_SHARED_SECRET=""\n')
    assert rotator.load_shared_secret(str(env)) is None


# ── atomic_write() ────────────────────────────────────────────────────

def test_atomic_write_replaces_file(rotator, tmp_path):
    backup_dir = tmp_path / "backups"
    target = tmp_path / "config.txt"
    target.write_text("original\n")
    rotator.atomic_write(str(target), "new content\n", backup_dir=str(backup_dir))
    assert target.read_text() == "new content\n"


def test_atomic_write_creates_backup(rotator, tmp_path):
    backup_dir = tmp_path / "backups"
    target = tmp_path / "config.txt"
    target.write_text("v1\n")
    rotator.atomic_write(str(target), "v2\n", backup_dir=str(backup_dir))
    backups = list(backup_dir.glob("config.txt.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text() == "v1\n"


def test_atomic_write_no_backup_for_nonexistent(rotator, tmp_path):
    """First write — нет existing file → no backup, just write."""
    backup_dir = tmp_path / "backups"
    target = tmp_path / "new.txt"
    rotator.atomic_write(str(target), "fresh\n", backup_dir=str(backup_dir))
    assert target.read_text() == "fresh\n"
    assert not backup_dir.exists() or not list(backup_dir.glob("*"))


# ── jcfg_lock() ───────────────────────────────────────────────────────

def test_jcfg_lock_basic_acquire_release(rotator, tmp_path):
    lock_file = str(tmp_path / "lock")
    with rotator.jcfg_lock(timeout=1, path=lock_file):
        assert Path(lock_file).exists()
    # после exit lock released — можно acquire снова
    with rotator.jcfg_lock(timeout=1, path=lock_file):
        pass


def test_jcfg_lock_reacquire_after_release(rotator, tmp_path):
    """Sequential acquires работают (lock released cleanly)."""
    lock_file = str(tmp_path / "lock")
    for _ in range(5):
        with rotator.jcfg_lock(timeout=1, path=lock_file):
            pass


def test_jcfg_lock_creates_parent_dir(rotator, tmp_path):
    nested = tmp_path / "var" / "lock" / "test.lock"
    with rotator.jcfg_lock(timeout=1, path=str(nested)):
        assert nested.exists()


def _hold_lock_subprocess(lock_path: str, hold_seconds: float, ready_event):
    """Worker: acquires lock via fcntl directly, signals ready, holds for N seconds."""
    import fcntl as _fcntl
    fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY, 0o644)
    _fcntl.flock(fd, _fcntl.LOCK_EX)
    ready_event.set()
    time.sleep(hold_seconds)
    _fcntl.flock(fd, _fcntl.LOCK_UN)
    os.close(fd)


def test_jcfg_lock_blocks_then_acquires(rotator, tmp_path):
    """Когда другой process holds lock, наш context manager ждёт до timeout."""
    lock_file = str(tmp_path / "contention.lock")
    ready = multiprocessing.Event()
    holder = multiprocessing.Process(
        target=_hold_lock_subprocess, args=(lock_file, 1.0, ready)
    )
    holder.start()
    try:
        assert ready.wait(timeout=2), "holder failed to acquire"
        t0 = time.monotonic()
        with rotator.jcfg_lock(timeout=5, path=lock_file):
            elapsed = time.monotonic() - t0
        # должны были ждать ~1s (минус setup overhead)
        assert 0.5 < elapsed < 2.0, f"expected ~1s wait, got {elapsed:.2f}s"
    finally:
        holder.join(timeout=3)


def test_jcfg_lock_timeout_raises(rotator, tmp_path):
    """Если lock не release'нут в течение timeout — raise TimeoutError."""
    lock_file = str(tmp_path / "timeout.lock")
    ready = multiprocessing.Event()
    holder = multiprocessing.Process(
        target=_hold_lock_subprocess, args=(lock_file, 3.0, ready)
    )
    holder.start()
    try:
        assert ready.wait(timeout=2)
        with pytest.raises(TimeoutError, match="Could not acquire"):
            with rotator.jcfg_lock(timeout=1, path=lock_file):
                pytest.fail("should have raised TimeoutError")
    finally:
        holder.join(timeout=5)


# ── should_rotate ↔ generate_credentials integration ──────────────────

def test_generated_cred_does_not_need_immediate_rotation(rotator, monkeypatch):
    """Сразу после rotation — no immediate re-rotation."""
    monkeypatch.setattr(rotator.time, "time", lambda: 1_000_000)
    _, _, new_expiry = rotator.generate_credentials("s", ttl_days=365)
    # 30 days before threshold — only kicks in last 30 days of 365 day TTL
    assert rotator.should_rotate(new_expiry, before_days=30) is False


def test_generated_cred_short_ttl_needs_rotation_soon(rotator, monkeypatch):
    """TTL=10d с threshold=30d — should_rotate True сразу."""
    monkeypatch.setattr(rotator.time, "time", lambda: 1_000_000)
    _, _, new_expiry = rotator.generate_credentials("s", ttl_days=10)
    assert rotator.should_rotate(new_expiry, before_days=30) is True

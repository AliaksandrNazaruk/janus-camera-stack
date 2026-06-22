"""Tests for app/services/secret_store.py — secret rotation + masking."""
import os

import pytest

from app.services import secret_store


@pytest.fixture
def isolated_secret_files(tmp_path, monkeypatch):
    """Point SECRETS_FILE + TIMESTAMPS_FILE to tmp dir per test."""
    sec = tmp_path / "secrets.env"
    ts = tmp_path / ".secrets.timestamps"
    monkeypatch.setattr(secret_store, "SECRETS_FILE", sec)
    monkeypatch.setattr(secret_store, "TIMESTAMPS_FILE", ts)
    return sec, ts


def test_snapshot_empty_returns_known_sensitive_keys(isolated_secret_files):
    """Empty store still returns all sensitive keys as is_set=False."""
    snap = secret_store.snapshot()
    assert "TURN_SHARED_SECRET" in snap
    assert snap["TURN_SHARED_SECRET"].is_sensitive is True
    assert snap["TURN_SHARED_SECRET"].is_set is False
    assert snap["TURN_SHARED_SECRET"].masked == "[unset]"


def test_mask_hides_middle(isolated_secret_files):
    """Set a value, verify it's masked but first/last 3 chars visible."""
    sec_file, _ = isolated_secret_files
    sec_file.write_text("TURN_SHARED_SECRET=abcdefghijklmnop\n")
    sec_file.chmod(0o600)
    snap = secret_store.snapshot()
    m = snap["TURN_SHARED_SECRET"].masked
    assert m.startswith("abc")
    assert m.endswith("nop")
    assert "●" in m
    assert "defghijklm" not in m   # middle hidden


def test_short_value_fully_masked(isolated_secret_files):
    """Values <=8 chars are fully masked (no leak)."""
    sec_file, _ = isolated_secret_files
    sec_file.write_text("TURN_SHARED_SECRET=short\n")
    snap = secret_store.snapshot()
    m = snap["TURN_SHARED_SECRET"].masked
    assert "short" not in m
    assert "●" in m


def test_rotate_generates_persists_and_timestamps(isolated_secret_files):
    """rotate() returns new value, file contains it, timestamp recorded."""
    sec_file, ts_file = isolated_secret_files
    new_val = secret_store.rotate("STREAMING_ADMIN_KEY")
    assert len(new_val) >= 32
    # Persisted
    content = sec_file.read_text()
    assert f"STREAMING_ADMIN_KEY={new_val}" in content
    # Timestamp recorded
    assert ts_file.exists()
    assert "STREAMING_ADMIN_KEY=" in ts_file.read_text()
    # Snapshot reflects rotation
    snap = secret_store.snapshot()
    assert snap["STREAMING_ADMIN_KEY"].is_set is True
    assert snap["STREAMING_ADMIN_KEY"].last_rotated_ts is not None


def test_rotate_base64url_format_for_streaming_keys(isolated_secret_files):
    """STREAMING_ADMIN_KEY uses base64url (Janus format), not hex."""
    new_val = secret_store.rotate("STREAMING_ADMIN_KEY")
    # base64url char set: A-Z a-z 0-9 - _ (no + / or =)
    assert all(c.isalnum() or c in "-_" for c in new_val)
    assert "+" not in new_val and "/" not in new_val


def test_rotate_hex_format_for_other_keys(isolated_secret_files):
    """TURN_SHARED_SECRET uses hex (matches openssl rand -hex)."""
    new_val = secret_store.rotate("TURN_SHARED_SECRET")
    assert all(c in "0123456789abcdef" for c in new_val)
    assert len(new_val) == 64   # 32 bytes hex


def test_reveal_returns_plaintext(isolated_secret_files):
    sec_file, _ = isolated_secret_files
    sec_file.write_text("INTERNAL_API_SECRET=plaintext-value-here\n")
    assert secret_store.reveal("INTERNAL_API_SECRET") == "plaintext-value-here"
    assert secret_store.reveal("NONEXISTENT_KEY") is None


def test_set_field_rejects_sensitive_keys(isolated_secret_files):
    """set_field refuses sensitive keys (must use rotate)."""
    with pytest.raises(ValueError):
        secret_store.set_field("TURN_SHARED_SECRET", "manual-value")


def test_set_field_persists_non_secret(isolated_secret_files):
    sec_file, _ = isolated_secret_files
    secret_store.set_field("TURN_HOST", "turn.example.com")
    assert "TURN_HOST=turn.example.com" in sec_file.read_text()


def test_atomic_write_preserves_existing_keys(isolated_secret_files):
    """Rotation of a single key preserves other keys."""
    sec_file, _ = isolated_secret_files
    sec_file.write_text(
        "# Comment line\n"
        "TURN_SHARED_SECRET=existing-turn\n"
        "INTERNAL_API_SECRET=existing-internal\n"
    )
    secret_store.rotate("STREAMING_ADMIN_KEY")
    content = sec_file.read_text()
    assert "TURN_SHARED_SECRET=existing-turn" in content
    assert "INTERNAL_API_SECRET=existing-internal" in content
    assert "STREAMING_ADMIN_KEY=" in content
    assert "# Comment line" in content   # header preserved


def test_secret_file_mode_0600_after_rotation(isolated_secret_files):
    sec_file, _ = isolated_secret_files
    secret_store.rotate("TURN_SHARED_SECRET")
    mode = os.stat(sec_file).st_mode & 0o777
    assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"


# ── Cycle 1: corruption fails closed; lenient on a stray line ────────────

def test_load_undecodable_file_quarantines_and_raises(isolated_secret_files):
    sec, _ts = isolated_secret_files
    sec.write_bytes(b"\xff\xfe\x00\x01 not utf-8 bytes")
    with pytest.raises(secret_store.StoreCorrupt):
        secret_store.snapshot()                          # snapshot → _load → undecodable
    assert list(sec.parent.glob("secrets.env.corrupt.*"))   # forensic copy
    assert sec.exists()                                  # original left in place


def test_load_garbage_content_zero_keys_raises(isolated_secret_files):
    sec, _ts = isolated_secret_files
    sec.write_text("this is not\nan env file\njust prose\n")   # non-comment lines, zero KEY=VALUE
    with pytest.raises(secret_store.StoreCorrupt):
        secret_store.snapshot()
    assert list(sec.parent.glob("secrets.env.corrupt.*"))


def test_load_all_comments_is_not_corrupt(isolated_secret_files):
    sec, _ts = isolated_secret_files
    sec.write_text("# just a comment\n\n# another\n")     # non-empty but no MEANINGFUL data lines
    snap = secret_store.snapshot()                        # no raise (legit empty)
    assert all(not v.is_set for v in snap.values())


def test_load_skips_one_stray_line_keeps_valid_keys(isolated_secret_files):
    sec, _ts = isolated_secret_files
    sec.write_text("TURN_HOST=h\nGARBAGE LINE NO EQUALS\nTURN_REALM=r\n")
    snap = secret_store.snapshot()                        # lenient: the stray line is skipped
    assert snap["TURN_HOST"].masked == "h"
    assert snap["TURN_REALM"].masked == "r"


def test_rotate_durable_write_no_tmp_left(isolated_secret_files):
    sec, _ts = isolated_secret_files
    secret_store.rotate("TURN_SHARED_SECRET")
    assert (sec.stat().st_mode & 0o777) == 0o600
    assert not list(sec.parent.glob("*.tmp"))


def test_concurrent_rotations_do_not_lose_updates(isolated_secret_files):
    """flock serialises the read-modify-write: N concurrent rotations of DISTINCT keys all persist.
    Without the lock, the load→modify→save would lost-update some (interleave across the file I/O)."""
    from concurrent.futures import ThreadPoolExecutor
    keys = ["STREAMING_ADMIN_KEY", "JANUS_ADMIN_SECRET", "TURN_SHARED_SECRET",
            "INTERNAL_API_SECRET", "CAM_ADMIN_TOKEN", "TEXTROOM_ROOM_SECRET"]
    with ThreadPoolExecutor(max_workers=len(keys)) as ex:
        list(ex.map(secret_store.rotate, keys))
    snap = secret_store.snapshot()
    for k in keys:
        assert snap[k].is_set, f"{k} lost in a concurrent rotation (RMW not serialised)"

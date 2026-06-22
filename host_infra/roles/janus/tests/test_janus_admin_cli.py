"""Integration tests for janus-admin CLI.

Invokes via subprocess с controlled env (JANUS_CFG_PATH, JCFG_LOCK_PATH,
BACKUP_DIR redirected к tmp). systemctl mocked or skipped.

CLI commands:
    restart       — restart janus.service
    nat-config    — read JSON stdin, patch jcfg, restart
    status        — print JSON state snapshot

Pure pytest — no bash framework dep.
"""
from __future__ import annotations

import json
import multiprocessing
import os
import re
import subprocess
import time
from pathlib import Path

import pytest


_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "files" / "janus-admin.py"


@pytest.fixture
def env(tmp_path):
    """Sandboxed env: redirect paths to tmp."""
    cfg = tmp_path / "janus.jcfg"
    cfg.write_text(
        'general: { enabled = true }\n'
        '# BEGIN NAT AUTO\n'
        'nat: {\n'
        '  turn_user = "old"\n'
        '  nat_1_1_mapping = "10.0.0.1"\n'
        '}\n'
        '# END NAT AUTO\n'
    )
    return {
        "PATH": os.environ["PATH"],
        "JANUS_CFG_PATH": str(cfg),
        "JCFG_LOCK_PATH": str(tmp_path / "jcfg.lock"),
        "JANUS_ADMIN_BACKUP_DIR": str(tmp_path / "backups"),
        "JCFG_LOCK_TIMEOUT": "5",
    }


def _run(env: dict, args: list[str], stdin: str = "", timeout: int = 10) -> subprocess.CompletedProcess:
    """Invoke CLI с args."""
    return subprocess.run(
        ["python3", str(_SCRIPT_PATH)] + args,
        env=env, input=stdin, capture_output=True, text=True, timeout=timeout,
    )


# ── status command (read-only) ─────────────────────────────────────────

def test_status_returns_valid_json(env):
    r = _run(env, ["status"])
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["jcfg_exists"] is True
    assert data["nat_markers_present"] is True
    assert data["lock_held_by_other"] is False


def test_status_detects_held_lock(env):
    """Когда lock is acquired externally, status reports lock_held_by_other=True."""
    import fcntl as _fcntl
    fd = os.open(env["JCFG_LOCK_PATH"], os.O_CREAT | os.O_WRONLY, 0o644)
    _fcntl.flock(fd, _fcntl.LOCK_EX)
    try:
        r = _run(env, ["status"])
        data = json.loads(r.stdout)
        assert data["lock_held_by_other"] is True
    finally:
        _fcntl.flock(fd, _fcntl.LOCK_UN)
        os.close(fd)


def test_status_jcfg_missing(env, tmp_path):
    """jcfg отсутствует → exists=False, markers=False."""
    Path(env["JANUS_CFG_PATH"]).unlink()
    r = _run(env, ["status"])
    data = json.loads(r.stdout)
    assert data["jcfg_exists"] is False
    assert data["nat_markers_present"] is False


def test_status_jcfg_missing_markers(env):
    """jcfg без NAT markers → markers=False но exists=True."""
    Path(env["JANUS_CFG_PATH"]).write_text("no markers here\n")
    r = _run(env, ["status"])
    data = json.loads(r.stdout)
    assert data["jcfg_exists"] is True
    assert data["nat_markers_present"] is False


# ── nat-config command ────────────────────────────────────────────────

def _payload(**overrides) -> str:
    """Build a NAT config JSON payload."""
    base = {
        "turn_server": "turn.example.com",
        "turn_user": "new-user",
        "turn_pwd": "new-pwd",
        "nat_1_1_mapping": "1.2.3.4",
        "min_port": 10000,
        "max_port": 20000,
        "stun_server": "stun.l.google.com",
        "stun_port": 19302,
        "turn_port": 3478,
        "turn_type": "udp",
        "ice_ignore_list": ["docker0"],
        "full_trickle": True,
    }
    base.update(overrides)
    return json.dumps(base)


def test_nat_config_updates_jcfg(env):
    """nat-config patches jcfg между markers."""
    r = _run(env, ["nat-config", "--no-restart"], stdin=_payload())
    assert r.returncode == 0, r.stderr
    content = Path(env["JANUS_CFG_PATH"]).read_text()
    assert 'turn_user   = "new-user"' in content
    assert 'nat_1_1_mapping = "1.2.3.4"' in content


def test_nat_config_creates_backup(env):
    """Backup записан до patch."""
    _run(env, ["nat-config", "--no-restart"], stdin=_payload())
    backups = list(Path(env["JANUS_ADMIN_BACKUP_DIR"]).glob("janus.jcfg.*.bak"))
    assert len(backups) == 1
    # Backup contains OLD content (before patch)
    assert 'nat_1_1_mapping = "10.0.0.1"' in backups[0].read_text()


def test_nat_config_invalid_json_rejected(env):
    """Malformed JSON → exit 1."""
    r = _run(env, ["nat-config", "--no-restart"], stdin="not { json [")
    assert r.returncode == 1
    assert "parse" in r.stderr.lower() or "json" in r.stderr.lower()


def test_nat_config_non_object_rejected(env):
    """JSON array вместо object → exit 1."""
    r = _run(env, ["nat-config", "--no-restart"], stdin='["array", "not", "object"]')
    assert r.returncode == 1


def test_nat_config_missing_markers_fails(env):
    """jcfg без BEGIN/END markers → exit 3."""
    Path(env["JANUS_CFG_PATH"]).write_text("no nat markers\n")
    r = _run(env, ["nat-config", "--no-restart"], stdin=_payload())
    assert r.returncode == 3


def test_nat_config_file_input(env, tmp_path):
    """nat-config -f reads JSON from file."""
    cfg_file = tmp_path / "input.json"
    cfg_file.write_text(_payload(nat_1_1_mapping="5.6.7.8"))
    r = _run(env, ["nat-config", "--no-restart", "-f", str(cfg_file)], stdin="")
    assert r.returncode == 0
    assert 'nat_1_1_mapping = "5.6.7.8"' in Path(env["JANUS_CFG_PATH"]).read_text()


def test_nat_config_jcfg_missing(env):
    """JANUS_CFG_PATH не существует → exit 3."""
    Path(env["JANUS_CFG_PATH"]).unlink()
    r = _run(env, ["nat-config", "--no-restart"], stdin=_payload())
    assert r.returncode == 3


# ── Lock contention ───────────────────────────────────────────────────

def _hold_flock(lock_path: str, hold_seconds: float, ready_event):
    import fcntl as _fcntl
    fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY, 0o644)
    _fcntl.flock(fd, _fcntl.LOCK_EX)
    ready_event.set()
    time.sleep(hold_seconds)
    _fcntl.flock(fd, _fcntl.LOCK_UN)
    os.close(fd)


def test_nat_config_waits_for_lock(env):
    """Когда внешний process holds lock, nat-config waits."""
    ready = multiprocessing.Event()
    holder = multiprocessing.Process(
        target=_hold_flock, args=(env["JCFG_LOCK_PATH"], 1.5, ready)
    )
    holder.start()
    try:
        assert ready.wait(timeout=2)
        t0 = time.monotonic()
        r = _run(env, ["nat-config", "--no-restart"], stdin=_payload(), timeout=10)
        elapsed = time.monotonic() - t0
        assert r.returncode == 0, r.stderr
        assert 1.0 < elapsed < 3.5, f"expected ~1.5s wait, got {elapsed:.2f}s"
    finally:
        holder.join(timeout=3)


def test_nat_config_timeout_on_lock_held_too_long(env):
    """Lock не released → exit 2."""
    env = {**env, "JCFG_LOCK_TIMEOUT": "1"}
    ready = multiprocessing.Event()
    holder = multiprocessing.Process(
        target=_hold_flock, args=(env["JCFG_LOCK_PATH"], 3.0, ready)
    )
    holder.start()
    try:
        assert ready.wait(timeout=2)
        r = _run(env, ["nat-config", "--no-restart"], stdin=_payload(), timeout=10)
        assert r.returncode == 2, f"expected lock timeout (rc=2), got {r.returncode}: {r.stderr}"
    finally:
        holder.join(timeout=5)


# ── Help / argparse ───────────────────────────────────────────────────

def test_no_args_shows_usage(env):
    """Calling without subcommand → exit code != 0, usage printed."""
    r = _run(env, [])
    assert r.returncode != 0
    assert "usage" in (r.stderr.lower() + r.stdout.lower())


def test_help_lists_commands(env):
    r = _run(env, ["--help"])
    assert r.returncode == 0
    out = r.stdout
    for cmd in ("restart", "nat-config", "status"):
        assert cmd in out


# ── Output format ─────────────────────────────────────────────────────

def test_nat_config_rendered_block_well_formed(env):
    """Generated NAT block matches libconfig syntax (libconfig-friendly)."""
    _run(env, ["nat-config", "--no-restart"], stdin=_payload())
    content = Path(env["JANUS_CFG_PATH"]).read_text()
    # extract block между markers
    m = re.search(r"# BEGIN NAT AUTO\n(.*?)\n# END NAT AUTO", content, re.DOTALL)
    assert m, "markers missing после write"
    block = m.group(1)
    assert "nat: {" in block
    assert block.rstrip().endswith("}")

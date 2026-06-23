"""NAT/STUN/TURN configuration management for Janus.

Handles loading, saving, rendering, and patching of NAT configuration
shared between color and depth camera nodes.  Also manages TURN
credential generation and Janus service restarts.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Literal, Optional

import httpx
from pydantic import BaseModel, Field

from app.core.admin import admin_token
from app.core.settings import get_settings
from app.services.system import atomic_write_text, run as run_cmd  # noqa: F401
from app.config import DEVICES, PORTS

logger = logging.getLogger(__name__)

# NOTE (Cycle 10): the jcfg NAT markers + the jcfg flock used to live here. L3 (janus-admin) owns the
# jcfg render + write + flock now (verified Cycle 7A.1 — patch_janus_cfg_with_nat ships JSON to the CLI,
# which acquires /var/lock/janus-jcfg.lock and patches between the markers). L4's copies were dead
# duplicates and were removed; the lock + marker contract is tested in
# host_infra/roles/janus/tests/test_janus_admin_cli.py.


def _env(key: str, fallback: str = "") -> str:
    """Read TURN/STUN defaults from env vars (same source as Settings)."""
    return os.environ.get(key, fallback)


# ── Models ─────────────────────────────────────────────────────────────


# TURN host resolves from a SINGLE source: TURN_HOST env > DEVICES.TURN_HOST
# (shared_config / network_defaults). No second hardcoded literal here — the only
# network default lives in the config module. Production must set TURN_HOST
# explicitly (enforced by startup_checks.production_issues).
class JanusNatConfig(BaseModel):
    stun_server: str = Field(default_factory=lambda: _env("TURN_HOST", DEVICES.TURN_HOST))
    stun_port: int = Field(default=3478)

    turn_server: str = Field(default_factory=lambda: _env("TURN_HOST", DEVICES.TURN_HOST))
    turn_port: int = Field(default=3478)
    turn_type: Literal["udp", "tcp", "tls"] = Field(default="tcp")
    turn_user: str = Field(default_factory=lambda: _env("TURN_USER", "webrtc"))
    turn_pwd: str = Field(default_factory=lambda: _env("TURN_PASS", ""))

    nat_1_1_mapping: str = Field(default="")

    ice_tcp: bool = Field(default=False)
    full_trickle: bool = Field(default=True)
    ice_enforce_list: str = Field(default="", description="Whitelist of interfaces for ICE gathering (substring match). Empty = use ice_ignore_list instead.")
    ice_ignore_list: List[str] = Field(
        default_factory=lambda: ["docker", "veth", "lo", "vmnet", "tailscale"],
        description="Blacklist of interfaces excluded from ICE gathering. Used when ice_enforce_list is empty.",
    )
    keep_private_host: bool = Field(default=False)

    min_port: int = Field(default=40000)
    max_port: int = Field(default=41000)


# ── TURN credentials ───────────────────────────────────────────────────


# (Cycle 10) generate_turn_credentials moved to app/services/turn_credentials.py — a pure TURN-auth
# helper with no coupling to the NAT config store.


# ── Load / Save ────────────────────────────────────────────────────────


def _janus_nat_json() -> Path:
    return get_settings().janus_nat_json


def load_nat_config() -> JanusNatConfig:
    """Return NAT settings shared between cameras.

    Depth camera instances reuse the primary (color) camera's settings.
    If the primary camera is temporarily unreachable we fall back to the
    locally stored config or to the baked-in defaults.
    """
    data: Optional[Dict[str, str]] = None

    if get_settings().camera_type == "depth_camera":
        try:
            response = httpx.get(
                f"http://{DEVICES.HOST_LAN_IP}:{PORTS.COLOR_CAMERA}/janus/nat",
                timeout=3,
                headers={"X-Admin-Token": admin_token()},
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            logger.warning("depth_camera fallback to local config: %s", exc)

    if data is None and _janus_nat_json().exists():
        try:
            data = json.loads(_janus_nat_json().read_text())
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to load %s: %s", _janus_nat_json(), exc)

    if data:
        try:
            return JanusNatConfig.model_validate(data)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Invalid NAT data, using defaults: %s", exc)

    return JanusNatConfig()


def save_nat_config(cfg: JanusNatConfig) -> None:
    atomic_write_text(_janus_nat_json(), cfg.model_dump_json(indent=2))


# ── apply-status sidecar (Cycle 7B.2: closes G1 — "desired persisted but not applied" is now
#    VISIBLE, not silent). The config model + GET /janus/nat stay unchanged; status is a sibling file. ──
_UNKNOWN_STATUS = {"status": "unknown", "diff_hash": None, "failure_stage": None, "updated_at": None}


def _janus_nat_status_json() -> Path:
    """Sidecar beside janus-nat.json recording whether the persisted desired config is actually live."""
    p = _janus_nat_json()
    return p.with_name(p.stem + ".status.json")


def config_diff_hash(cfg: JanusNatConfig) -> str:
    """Stable hash binding a status record to a specific desired config."""
    return "sha256:" + hashlib.sha256(cfg.model_dump_json().encode()).hexdigest()


def write_apply_status(status: str, *, diff_hash: str, failure_stage: Optional[str] = None) -> None:
    """Record the apply status of the persisted desired config (pending → applied/failed). Atomic.
    Callers treat this as BEST EFFORT — a status-write failure must never break the update operation."""
    record = {"status": status, "diff_hash": diff_hash,
              "failure_stage": failure_stage, "updated_at": time.time()}
    atomic_write_text(_janus_nat_status_json(), json.dumps(record, indent=2))


def read_apply_status() -> dict:
    """Read the apply-status sidecar. Fail-SAFE: missing/corrupt → status 'unknown' (an operational
    indicator, not a secret — the operator just re-applies). Never raises; a status read can't break GET."""
    path = _janus_nat_status_json()
    if not path.exists():
        return dict(_UNKNOWN_STATUS)
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return dict(_UNKNOWN_STATUS)


# ── Apply via the L3 janus-admin CLI ───────────────────────────────────
# (Cycle 10: render_nat_block was removed — L3 renders the jcfg NAT block itself; L4 ships the config as
# JSON to `janus-admin nat-config`. The renderer was a dead duplicate, tested in L3's test_janus_admin_cli.)


class JanusAdminError(RuntimeError):
    """A `janus-admin` CLI call failed. Carries the L3 exit code when the binary ran and returned
    non-zero (0 ok / 1 invalid / 2 lock-timeout / 3 jcfg-fail / 4 restart-fail / 5 unknown — see
    host_infra/roles/janus/files/janus-admin.py); ``exit_code`` is None when the binary could not be
    invoked at all (missing / timed out). Subclasses RuntimeError so existing `except RuntimeError`
    callers keep working. (Cycle 7B: surfaces the exit code L4 used to collapse — gap G6.)"""

    def __init__(self, message: str, *, exit_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def patch_janus_cfg_with_nat(cfg: JanusNatConfig, *, no_restart: bool = False) -> None:
    """Patch the NAT block (and, unless ``no_restart``, restart janus) via the L3-owned janus-admin CLI.

    L3 (`janus-admin nat-config`) owns the jcfg render + write + flock + restart. With ``no_restart`` the
    CLI patches the jcfg and STOPS (no restart) — Cycle 7B uses this to split the apply from the restart
    so the operation has distinct, observable stages and a single (non-doubled) restart.

    See host_infra/roles/janus/files/janus-admin.py.
    """
    cmd = ["sudo", "/usr/local/bin/janus-admin", "nat-config"]
    if no_restart:
        cmd.append("--no-restart")
    payload = json.dumps(cfg.model_dump(), default=str)
    try:
        result = subprocess.run(
            cmd, input=payload, capture_output=True, text=True, timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise JanusAdminError(f"janus-admin invocation failed: {exc}") from exc

    if result.returncode != 0:
        raise JanusAdminError(
            f"janus-admin nat-config exit={result.returncode}: {result.stderr.strip()}",
            exit_code=result.returncode,
        )


# ── Service restarts ───────────────────────────────────────────────────


def restart_janus() -> None:
    """Restart janus via the L3-owned janus-admin CLI (replaces sudo systemctl). Maps a hung/absent CLI
    (TimeoutExpired / FileNotFoundError) to JanusAdminError — symmetric with patch_janus_cfg_with_nat
    (Cycle 7B closes gap G3: those used to escape the route's `except RuntimeError` unmapped)."""
    try:
        result = subprocess.run(
            ["sudo", "/usr/local/bin/janus-admin", "restart"],
            capture_output=True, text=True, timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise JanusAdminError(f"janus-admin restart invocation failed: {exc}") from exc
    if result.returncode != 0:
        raise JanusAdminError(
            f"janus-admin restart exit={result.returncode}: {result.stderr.strip()}",
            exit_code=result.returncode,
        )


def restart_depth_camera_janus() -> None:
    try:
        url = f"http://{DEVICES.DEPTH_CAMERA_IP}:{PORTS.COLOR_CAMERA}/janus/restart"
        response = httpx.post(url, timeout=10, headers={"X-Admin-Token": admin_token()})
        if response.status_code != 200:
            raise RuntimeError(f"Failed to restart janus: {response.text}")
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Failed to restart janus: {exc}") from exc

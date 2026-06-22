"""B1-2 — read-only effective runtime-config builder.

Assembles the effective runtime config from live sources (Settings, allocation
state, per-sensor running probe, color tuning.env) at READ time. Reuses the
secret-exclusion discipline of ``get_client_rtc_config`` — never echoes a TURN
password / shared secret / admin token. READ-ONLY: no writes, no restarts.

Design: ``docs/design/B1_RUNTIME_CONFIG.md`` §5. The effective view augments the
config contract with two things the config model omits (spec §5.2):
  - ``runtime_active`` per sensor (live ``is_running`` probe, separate from the
    desired ``enabled``); ``None`` = probe failed/indeterminate.
  - non-secret ``turn`` connection facts (admin-gated disclosure; no password).
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

from pydantic import BaseModel

from app.config.runtime_schema import (
    DiagnosticsRuntimeConfig,
    RuntimeConfig,  # noqa: F401  (re-exported as the contract base; effective mirrors it)
    StreamRuntimeConfig,
    WebRtcRuntimeConfig,
    is_canonical_sensor_key,
)
from app.core.settings import get_settings

log = logging.getLogger(__name__)


class EffectiveStreamProfile(StreamRuntimeConfig):
    # Desired (``enabled`` = Allocation.desired_active) vs live running state are
    # reported separately (spec §5.2). None = probe failed/indeterminate.
    runtime_active: Optional[bool] = None


class EffectiveTurnInfo(BaseModel):
    """Non-secret TURN connection facts (admin-gated disclosure; spec §5.1).
    Deliberately excludes the password / shared secret."""
    host: str
    username: str = ""
    udp_port: int
    tls_port: Optional[int] = None


class EffectiveRuntimeConfig(BaseModel):
    version: int = 1
    webrtc: WebRtcRuntimeConfig
    turn: Optional[EffectiveTurnInfo] = None
    stream_profiles: Dict[str, EffectiveStreamProfile]
    diagnostics: DiagnosticsRuntimeConfig


def _color_tuning() -> dict:
    """Color resolution/fps/bitrate/gop from rs-color.tuning.env — the only sensor
    with a runtime tuning surface (depth/IR use Initialize-time defaults). Returns
    {} (schema defaults) on any read error."""
    try:
        from app.services.env_store import read_env
        env = read_env()
        w = int(env.get("WIDTH", "640"))
        h = int(env.get("HEIGHT", "480"))
        out = {"resolution": f"{w}x{h}", "fps": int(env.get("FPS", "15")),
               "bitrate_kbps": int(env.get("BITRATE_KBPS", "900"))}
        gop_raw = env.get("GOP")
        if gop_raw:
            out["gop_frames"] = int(gop_raw)
        return out
    except Exception as e:  # pragma: no cover — defensive
        log.debug("effective: color tuning read failed: %s", e)
        return {}


def build_effective() -> EffectiveRuntimeConfig:
    """Build the read-only effective runtime config. No writes, no restarts."""
    s = get_settings()
    has_turn_creds = bool(s.turn_pass) or bool(s.turn_shared_secret)

    # ICE policy is the CONDITIONAL effective value: depth-camera deployments force
    # relay regardless of the setting (janus.py:148-154); else settings.ice_policy.
    ice_policy = s.ice_policy if s.ice_policy in ("all", "relay") else "all"
    if s.camera_type == "depth_camera":
        ice_policy = "relay"
    webrtc = WebRtcRuntimeConfig(
        ice_policy=ice_policy,
        # Clamp the displayed TTL into the B1 policy band so the effective view
        # always validates; an out-of-band live value is flagged by /validate.
        turn_credential_ttl_seconds=max(300, min(3600, int(s.turn_cred_ttl))),
        turn_enabled=bool(s.turn_host) and has_turn_creds,   # derived
        stun_enabled=bool(s.turn_host),                       # derived
    )

    turn = (
        EffectiveTurnInfo(host=s.turn_host, username=s.turn_user,
                          udp_port=s.turn_port, tls_port=s.turn_tls_port)
        if s.turn_host else None
    )

    diagnostics = DiagnosticsRuntimeConfig(
        health_stream_stale_ms=int(s.watchdog_stale_ms),
        reboot_allowed=bool(s.watchdog_reboot_enabled),
    )

    color_tune = _color_tuning()
    profiles: Dict[str, EffectiveStreamProfile] = {}
    try:
        from app.services import mountpoint_allocator as alloc
        from app.services import sensor_lifecycle
        allocations = alloc.list_allocations()
    except Exception as e:  # pragma: no cover — defensive
        log.warning("effective: allocation read failed: %s", e)
        allocations = {}

    for key, a in allocations.items():
        if not is_canonical_sensor_key(key):
            # Defensive: never surface a legacy/garbage key in the effective view.
            log.warning("effective: skipping non-canonical allocation key %r", key)
            continue
        sensor = key.split(":", 1)[1]
        try:
            runtime_active = sensor_lifecycle.is_running(sensor)
        except Exception:
            runtime_active = None
        profiles[key] = EffectiveStreamProfile(
            sensor_key=key,
            enabled=bool(a.desired_active),
            mountpoint_id=int(a.mp_id),
            rtp_port=int(a.rtp_port),
            runtime_active=runtime_active,
            **(color_tune if sensor == "color" else {}),
        )

    return EffectiveRuntimeConfig(
        webrtc=webrtc, turn=turn, stream_profiles=profiles, diagnostics=diagnostics
    )

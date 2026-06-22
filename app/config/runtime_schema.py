"""B1 runtime config — typed schema + diff/impact data shapes.

Design: ``docs/design/B1_RUNTIME_CONFIG.md``.

SCOPE (B1): typed schema + validation model + diff/impact shapes ONLY. B1 does
NOT apply / restart / write / expose secrets / provide a UI (spec §1.3, §9).

This module is the pure typed contract + the *intrinsic* validators that need no
external state:
  - R1  version pin (``version == 1``)
  - R2  canonical sensor key ``<serial>:<sensor>`` (reject any ``local:*`` sentinel)
  - R4  TURN credential TTL bound 300..3600 (B1-introduced policy)
  - R5  reconnect ordering (max >= base)
  - R7  bitrate bound 100..8000

Cross-state validators (R3 derived-TURN, R6 SDK-catalog modes, R6b depth/IR
read-only, R8 allocation invariants, R9 secret fields, R10 firewall/bind fields,
R11 production re-check) and the read-only endpoints live in B1-2/B1-3 — NOT here.

Read-only / allocator-owned / derived fields (``enabled``, ``mountpoint_id``,
``rtp_port``, ``codec``, ``encoder``, ``reboot_allowed``, ``turn_enabled``,
``stun_enabled``, ``fdir_enabled`` …) appear in the model because the effective
view surfaces them; the *write-rejection* of those fields is validate-endpoint
logic (B1-3), not a schema concern.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# Canonical sensor key (FDIR-KEY-001): "<serial>:<sensor>", serial = [A-Za-z0-9]+,
# sensor in {color,depth,ir1,ir2}. "local" is the pre-migration sentinel → rejected.
SENSORS = ("color", "depth", "ir1", "ir2")
SENSOR_KEY_RE = re.compile(r"^[A-Za-z0-9]+:(color|depth|ir1|ir2)$")
_RESOLUTION_RE = re.compile(r"^\d{2,5}x\d{2,5}$")
LOCAL_SENTINEL = "local"


def is_canonical_sensor_key(key: str) -> bool:
    """True iff ``key`` is a canonical ``<serial>:<sensor>`` key and not a
    ``local:*`` sentinel (R2 / FDIR-KEY-001)."""
    return bool(SENSOR_KEY_RE.match(key)) and key.split(":", 1)[0] != LOCAL_SENTINEL


class ApplyImpact(str, Enum):
    """Blast-radius classification of a runtime-config change. B1 *classifies*;
    it does not apply (spec §7). ``REJECTED`` is a per-change impact for a
    parseable write to a read-only/unsettable field."""
    HOT = "HOT"
    NEW_SESSIONS_ONLY = "NEW_SESSIONS_ONLY"
    RESTART_ENCODER = "RESTART_ENCODER"
    RECREATE_MOUNTPOINT = "RECREATE_MOUNTPOINT"
    RESTART_JANUS = "RESTART_JANUS"
    DEPLOYMENT_ONLY = "DEPLOYMENT_ONLY"
    REJECTED = "REJECTED"


# ── Config models ───────────────────────────────────────────────────────────

class WebRtcRuntimeConfig(BaseModel):
    model_config = {"extra": "forbid"}

    ice_policy: Literal["all", "relay"] = "all"
    # R4 — B1-introduced policy bound (short-lived relay creds). NOT enforced in
    # code today; B1 adds the 300..3600 bound as policy.
    turn_credential_ttl_seconds: int = Field(default=3600, ge=300, le=3600)
    # Derived / read-only in B1 (no off-switch in code) — surfaced, not settable.
    turn_enabled: bool = True
    stun_enabled: bool = True
    # Client-owned (config.js). None = "use the client default" (the server has no
    # default — spec corrected the draft's invented 8). Range applies when set.
    max_reconnect_attempts: Optional[int] = Field(default=None, ge=3, le=50)
    reconnect_base_delay_ms: int = Field(default=300, ge=100, le=5000)
    reconnect_max_delay_ms: int = Field(default=15000, ge=2000, le=60000)

    @model_validator(mode="after")
    def _reconnect_ordering(self) -> "WebRtcRuntimeConfig":  # R5
        if self.reconnect_max_delay_ms < self.reconnect_base_delay_ms:
            raise ValueError("reconnect_max_delay_ms must be >= reconnect_base_delay_ms")
        return self


class StreamRuntimeConfig(BaseModel):
    model_config = {"extra": "forbid"}

    sensor_key: str
    enabled: bool = True              # = Allocation.desired_active; read-only/derived in B1
    resolution: str = "640x480"      # "WxH"; runtime-settable for color only (R6b, B1-3)
    fps: int = Field(default=15, ge=1, le=120)
    codec: Literal["h264"] = "h264"                          # fixed constant (read-only)
    encoder: Literal["libx264", "h264_v4l2m2m"] = "libx264"  # read-only
    bitrate_kbps: int = Field(default=900, ge=100, le=8000)  # R7
    gop_frames: int = Field(default=15, ge=1, le=300)        # B1 advisory bound; UNIT = frames
    mountpoint_id: int = Field(ge=1, le=65535)               # allocator-owned (read-only)
    rtp_port: int = Field(ge=1024, le=65535)                 # allocator-owned (read-only)

    @field_validator("resolution")
    @classmethod
    def _resolution_shape(cls, v: str) -> str:
        if not _RESOLUTION_RE.match(v):
            raise ValueError(f"resolution must be 'WxH', got {v!r}")
        return v

    @model_validator(mode="after")
    def _sensor_key_canonical(self) -> "StreamRuntimeConfig":  # R2
        if not is_canonical_sensor_key(self.sensor_key):
            raise ValueError(
                f"sensor_key {self.sensor_key!r} is not canonical '<serial>:<sensor>' "
                f"(serial [A-Za-z0-9]+, sensor in {SENSORS}); legacy 'local:*' rejected"
            )
        return self


class DiagnosticsRuntimeConfig(BaseModel):
    model_config = {"extra": "forbid"}

    # Read-only/derived SAFETY GATE — never settable in B1 (write-rejection in B1-3).
    reboot_allowed: bool = False
    health_stream_stale_ms: int = Field(default=10000, ge=500, le=30000)
    debug_stats_enabled: bool = False    # derived / client-side
    telemetry_enabled: bool = True       # derived (always-on)
    telemetry_interval_seconds: int = Field(default=5, ge=1, le=60)  # client-side advisory
    fdir_enabled: bool = True            # derived (always-on)
    auto_recovery_enabled: bool = True   # derived (always-on)


class RuntimeConfig(BaseModel):
    model_config = {"extra": "forbid"}

    version: Literal[1] = 1              # R1
    webrtc: WebRtcRuntimeConfig
    stream_profiles: Dict[str, StreamRuntimeConfig]
    diagnostics: DiagnosticsRuntimeConfig

    @model_validator(mode="after")
    def _keys_canonical(self) -> "RuntimeConfig":  # R2 at the map-key level
        for key, prof in self.stream_profiles.items():
            if not is_canonical_sensor_key(key):
                raise ValueError(
                    f"stream_profiles key {key!r} is not a canonical sensor key (R2); "
                    "legacy 'local:*' rejected"
                )
            if prof.sensor_key != key:
                raise ValueError(
                    f"stream_profiles[{key!r}].sensor_key={prof.sensor_key!r} "
                    "must equal its map key"
                )
        return self


# ── Validate-response shapes (used by B1-3; defined here as the contract) ─────

class DiffEntry(BaseModel):
    """One proposed change: ``path`` from→to, its backing ``source``, and the
    classified ``impact``."""
    model_config = {"populate_by_name": True}

    path: str
    from_: Any = Field(default=None, alias="from")  # 'from' is a Python keyword
    to: Any = None
    source: str
    impact: ApplyImpact


class ValidationErrorEntry(BaseModel):
    """A hard rejection reason (secret/forbidden/legacy-key/unsettable field).
    Distinct from per-change ``REJECTED`` impact — overall-payload rejections live
    here, never as a top-level ``impact:["REJECTED"]`` (spec §6)."""
    path: Optional[str] = None
    message: str


class ValidationResponse(BaseModel):
    valid: bool
    diff: List[DiffEntry] = Field(default_factory=list)
    impact: List[ApplyImpact] = Field(default_factory=list)
    errors: List[ValidationErrorEntry] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    # B2-0 (journal-only): when a valid patch is validated, /validate persists a
    # redacted "validated" revision and echoes its id + the stable diff hash. Both
    # are None for invalid/empty patches (nothing to journal). No apply exists yet.
    revision_id: Optional[str] = None
    diff_hash: Optional[str] = None


class ApplyResponse(BaseModel):
    """AE-1 — result of POST /apply for a NEW_SESSIONS_ONLY revision. ``status`` is the
    orchestration Outcome; the route maps it to an HTTP code."""
    status: str
    revision_id: str
    changed: bool = False
    verified: bool = False
    detail: str = ""
    applied: List[Dict[str, Any]] = Field(default_factory=list)

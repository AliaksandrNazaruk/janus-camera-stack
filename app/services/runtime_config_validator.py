"""B1-3 — dry-run runtime-config validator.

Accepts a partial runtime-config patch and returns a diff, per-change
ApplyImpact classification, errors, and warnings. STRICTLY READ-ONLY: it builds
the effective config, validates the *proposed* values, classifies blast radius,
and writes/restarts NOTHING (spec §1.3, §6, §7).

Rule coverage here (the rest are intrinsic to the schema — B1-1):
  R2  canonical key (patch keys)        R6   supported modes (color/depth/IR via SDK catalog)
  R3  relay ⇒ TURN available (warning)  R6b  depth/IR tuning write → REJECTED
  R8  allocation invariants (audit)     R9   secret fields → error
  R10 firewall/bind/deploy fields → err R11  production posture → informational warnings
Value ranges (R4/R5/R7) are enforced by re-validating the merged sub-model.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from app.config.runtime_schema import (
    ApplyImpact as AI,
    DiffEntry,
    DiagnosticsRuntimeConfig,
    StreamRuntimeConfig,
    ValidationErrorEntry,
    ValidationResponse,
    WebRtcRuntimeConfig,
    is_canonical_sensor_key,
)
from app.services.runtime_config_builder import build_effective

log = logging.getLogger(__name__)

# Secret env names that must never be settable via runtime-config (R9).
try:
    from app.services.secret_store import SENSITIVE_KEYS
except Exception:  # pragma: no cover
    SENSITIVE_KEYS = {"TURN_SHARED_SECRET", "JANUS_ADMIN_SECRET", "INTERNAL_API_SECRET", "CAM_ADMIN_TOKEN"}

# Deployment-only / env-level names that are not runtime-config (R10).
_DEPLOYMENT_FIELDS = {"HOST_LAN_IP", "VIEWER_TOKENS", "CAMERA_ENV", "JANUS_BIND", "FIREWALL"}

# Per-field policy: name -> (impact, source). REJECTED ⇒ also an error.
_WEBRTC_POLICY: Dict[str, Tuple[AI, str]] = {
    "ice_policy": (AI.NEW_SESSIONS_ONLY, "Settings.ice_policy (env ICE_POLICY)"),
    "turn_credential_ttl_seconds": (AI.NEW_SESSIONS_ONLY, "Settings.turn_cred_ttl (env TURN_CRED_TTL)"),
    "turn_enabled": (AI.REJECTED, "derived (nat_cfg) — read-only"),
    "stun_enabled": (AI.REJECTED, "derived (nat_cfg) — read-only"),
    "max_reconnect_attempts": (AI.DEPLOYMENT_ONLY, "client config.js — no server apply path"),
    "reconnect_base_delay_ms": (AI.DEPLOYMENT_ONLY, "client config.js — no server apply path"),
    "reconnect_max_delay_ms": (AI.DEPLOYMENT_ONLY, "client config.js — no server apply path"),
}
_DIAG_POLICY: Dict[str, Tuple[AI, str]] = {
    "health_stream_stale_ms": (AI.DEPLOYMENT_ONLY, "Settings.watchdog_stale_ms (env) — needs restart"),
    "reboot_allowed": (AI.REJECTED, "Settings.watchdog_reboot_enabled — read-only safety gate"),
    "debug_stats_enabled": (AI.REJECTED, "client-side; no server toggle"),
    "telemetry_enabled": (AI.REJECTED, "always-on; no opt-out"),
    "telemetry_interval_seconds": (AI.DEPLOYMENT_ONLY, "client config.js — no server apply path"),
    "fdir_enabled": (AI.REJECTED, "always-on; no disable flag"),
    "auto_recovery_enabled": (AI.REJECTED, "always-on; no toggle"),
}
# Color stream fields that DO have a runtime apply path (rs-color.tuning.env).
_STREAM_TUNABLE = {"resolution", "fps", "bitrate_kbps", "gop_frames"}
# Stream fields that are read-only everywhere.
_STREAM_READONLY: Dict[str, str] = {
    "codec": "fixed H.264 constant — read-only",
    "encoder": "encoder preset is color-tuning.env only — modeled read-only",
    "enabled": "= Allocation.desired_active; toggled via lifecycle — read-only",
    "mountpoint_id": "allocator-owned — read-only",
    "rtp_port": "allocator-owned — read-only",
    "sensor_key": "derived from the map key — read-only",
}


def _err(errors: List[ValidationErrorEntry], path: Optional[str], msg: str) -> None:
    errors.append(ValidationErrorEntry(path=path, message=msg))


def _merged_errors(model_cls, base: dict, patch: dict, prefix: str) -> List[ValidationErrorEntry]:
    """Re-validate (base + patch) through the sub-model; return field range/policy
    violations (R4/R5/R7) as ValidationErrorEntry. Only fields present in `patch`
    are reported (so unrelated base issues are not surfaced)."""
    try:
        model_cls(**{**base, **patch})
        return []
    except ValidationError as e:
        out: List[ValidationErrorEntry] = []
        patch_fields = set(patch)
        for err in e.errors():
            loc = err.get("loc") or ()
            field = loc[0] if loc else None
            # surface field errors for patched fields, plus model-level (R5) errors
            if field in patch_fields or not loc:
                out.append(ValidationErrorEntry(
                    path=(prefix + "." + ".".join(str(x) for x in loc)) if loc else prefix,
                    message=err.get("msg", "invalid value"),
                ))
        return out


def _color_modes() -> Optional[Dict[str, set]]:
    """Per-sensor supported (WxH, fps) sets from the RealSense SDK catalog (R6).
    Returns None if the catalog can't be queried (then R6 emits a warning, not an
    error). The V4L2 color path is RETIRED and never consulted here."""
    try:
        from app.services.realsense_catalog import query_catalog
        cat = query_catalog()
        out: Dict[str, set] = {}
        for sd in cat.get("sensors", []):
            # map catalog sensor name → our sensor token, best-effort
            name = str(sd.get("name", "")).lower()
            if "color" in name or "rgb" in name:
                token = "color"
            elif "depth" in name:
                token = "depth"
            elif "infrared" in name or name.endswith("ir") or "ir" in name:
                token = "ir1"
            else:
                continue
            modes = out.setdefault(token, set())
            for m in sd.get("modes", []):
                modes.add((f"{m.get('width')}x{m.get('height')}", int(m.get("fps", 0))))
        return out
    except Exception as e:  # pragma: no cover — no device / SDK absent
        log.debug("R6: catalog query failed: %s", e)
        return None


def validate(patch: Any) -> ValidationResponse:
    diff: List[DiffEntry] = []
    errors: List[ValidationErrorEntry] = []
    warnings: List[str] = []

    if not isinstance(patch, dict):
        _err(errors, None, "patch must be a JSON object")
        return ValidationResponse(valid=False, errors=errors)

    eff = build_effective()

    # ── R1 version ───────────────────────────────────────────────────
    if "version" in patch and patch["version"] != 1:
        _err(errors, "version", "version must be 1 (R1)")

    # ── R9/R10: forbidden top-level keys ─────────────────────────────
    allowed_top = {"version", "webrtc", "stream_profiles", "diagnostics"}
    for k in patch:
        ku = str(k).upper()
        kl = str(k).lower()
        # R10 deployment/env fields first (VIEWER_TOKENS contains "token" but is a
        # deployment field, not a secret — classify it as deployment-only).
        if ku in _DEPLOYMENT_FIELDS or any(s in kl for s in ("firewall", "bind", "host_lan", "viewer_token", "camera_env")):
            _err(errors, k, f"{k}: deployment-only field — not runtime-config (R10)")
        elif ku in SENSITIVE_KEYS or any(s in kl for s in ("secret", "password", "pwd", "admin_token")):
            _err(errors, k, f"{k}: secret field — not settable via runtime-config (R9)")
        elif k not in allowed_top:
            _err(errors, k, f"{k}: unknown top-level field")

    # ── webrtc ───────────────────────────────────────────────────────
    wp = patch.get("webrtc") or {}
    if wp:
        errors.extend(_merged_errors(WebRtcRuntimeConfig, eff.webrtc.model_dump(), wp, "webrtc"))  # R4/R5
        # R3: relay requires derived TURN availability
        if wp.get("ice_policy") == "relay" and not eff.webrtc.turn_enabled:
            warnings.append("webrtc.ice_policy=relay but TURN is not available — clients would get no candidates (R3)")
        for field, to in wp.items():
            if field not in _WEBRTC_POLICY:
                _err(errors, f"webrtc.{field}", f"unknown webrtc field {field!r}")
                continue
            frm = getattr(eff.webrtc, field, None)
            if frm == to:
                continue
            impact, source = _WEBRTC_POLICY[field]
            if impact is AI.REJECTED:
                _err(errors, f"webrtc.{field}", f"webrtc.{field} is read-only/derived — not settable (REJECTED)")
            diff.append(DiffEntry(path=f"webrtc.{field}", **{"from": frm}, to=to, source=source, impact=impact))

    # ── diagnostics ──────────────────────────────────────────────────
    dp = patch.get("diagnostics") or {}
    if dp:
        errors.extend(_merged_errors(DiagnosticsRuntimeConfig, eff.diagnostics.model_dump(), dp, "diagnostics"))
        for field, to in dp.items():
            if field not in _DIAG_POLICY:
                _err(errors, f"diagnostics.{field}", f"unknown diagnostics field {field!r}")
                continue
            frm = getattr(eff.diagnostics, field, None)
            if frm == to:
                continue
            impact, source = _DIAG_POLICY[field]
            if impact is AI.REJECTED:
                _err(errors, f"diagnostics.{field}", f"diagnostics.{field} is read-only — not settable (REJECTED)")
            diff.append(DiffEntry(path=f"diagnostics.{field}", **{"from": frm}, to=to, source=source, impact=impact))

    # ── stream_profiles ──────────────────────────────────────────────
    sp = patch.get("stream_profiles") or {}
    modes = _color_modes() if sp else None
    for key, prof in sp.items():
        if not is_canonical_sensor_key(key):
            _err(errors, f"stream_profiles.{key}",
                 f"sensor key {key!r} is not canonical '<serial>:<sensor>' / legacy 'local:*' (R2)")
            continue
        sensor = key.split(":", 1)[1]
        cur = eff.stream_profiles.get(key)
        base = cur.model_dump() if cur is not None else {"sensor_key": key, "mountpoint_id": 1, "rtp_port": 1024}
        base.pop("runtime_active", None)  # not a StreamRuntimeConfig field
        errors.extend(_merged_errors(StreamRuntimeConfig, base, prof or {}, f"stream_profiles.{key}"))
        for field, to in (prof or {}).items():
            path = f"stream_profiles.{key}.{field}"
            frm = getattr(cur, field, None) if cur is not None else None
            if frm == to:
                continue
            if field in _STREAM_READONLY:
                _err(errors, path, f"{field} is {_STREAM_READONLY[field]} (REJECTED)")
                diff.append(DiffEntry(path=path, **{"from": frm}, to=to, source=_STREAM_READONLY[field], impact=AI.REJECTED))
            elif field in _STREAM_TUNABLE:
                if sensor != "color":
                    _err(errors, path, f"{field} has no runtime API for {sensor} — Initialize-time default only (R6b, REJECTED)")
                    diff.append(DiffEntry(path=path, **{"from": frm}, to=to, source="Initialize-time default (no runtime API)", impact=AI.REJECTED))
                else:
                    # R6: validate color resolution/fps against the SDK catalog
                    if field in ("resolution", "fps") and modes is not None and "color" in modes:
                        res = to if field == "resolution" else (getattr(cur, "resolution", "640x480") if cur else "640x480")
                        fps = to if field == "fps" else (getattr(cur, "fps", 15) if cur else 15)
                        if (str(res), int(fps)) not in modes["color"]:
                            _err(errors, path, f"color mode {res}@{fps} not in SDK catalog (R6)")
                    elif field in ("resolution", "fps") and modes is None:
                        warnings.append(f"{path}: could not verify against SDK catalog (device/SDK unavailable)")
                    diff.append(DiffEntry(path=path, **{"from": frm}, to=to,
                                          source="rs-color.tuning.env", impact=AI.RESTART_ENCODER))
            else:
                _err(errors, path, f"unknown stream field {field!r}")

    # ── R8: allocation invariants (audit of current state) ───────────
    mps = [p.mountpoint_id for p in eff.stream_profiles.values()]
    ports = [p.rtp_port for p in eff.stream_profiles.values()]
    if len(mps) != len(set(mps)):
        warnings.append("allocation invariant: duplicate mountpoint_id in current state (R8)")
    if len(ports) != len(set(ports)):
        warnings.append("allocation invariant: duplicate rtp_port in current state (R8)")

    # ── R11: production posture (informational — current process, not the patch) ──
    try:
        from app.core.settings import get_settings
        from app.core.startup_checks import production_issues
        for issue in production_issues(get_settings()):
            warnings.append("production: " + issue)
    except Exception:  # pragma: no cover
        pass

    impact = list(dict.fromkeys(d.impact for d in diff))  # dedup, preserve order
    return ValidationResponse(valid=(len(errors) == 0), diff=diff, impact=impact,
                              errors=errors, warnings=warnings)

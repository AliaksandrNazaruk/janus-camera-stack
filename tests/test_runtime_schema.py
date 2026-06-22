"""B1-1 — runtime_schema model validation (intrinsic validators R1/R2/R4/R5/R7).

Schema-only: no endpoints, no builder, no cross-state rules. Those are B1-2/B1-3.
"""
import pytest
from pydantic import ValidationError

from app.config.runtime_schema import (
    ApplyImpact,
    DiagnosticsRuntimeConfig,
    DiffEntry,
    RuntimeConfig,
    StreamRuntimeConfig,
    ValidationErrorEntry,
    ValidationResponse,
    WebRtcRuntimeConfig,
    is_canonical_sensor_key,
)

SERIAL = "141722072135"


def _stream(key=f"{SERIAL}:color", **over):
    base = dict(sensor_key=key, mountpoint_id=1305, rtp_port=5004)
    base.update(over)
    return StreamRuntimeConfig(**base)


def _config(**over):
    base = dict(
        webrtc=WebRtcRuntimeConfig(),
        stream_profiles={f"{SERIAL}:color": _stream()},
        diagnostics=DiagnosticsRuntimeConfig(),
    )
    base.update(over)
    return RuntimeConfig(**base)


# ── happy path + defaults ────────────────────────────────────────────

def test_valid_config_and_defaults():
    c = _config()
    assert c.version == 1
    assert c.webrtc.ice_policy == "all"                       # corrected default (not relay)
    assert c.webrtc.turn_credential_ttl_seconds == 3600
    assert c.webrtc.max_reconnect_attempts is None            # client-owned, no server default
    assert c.webrtc.reconnect_base_delay_ms == 300
    assert c.webrtc.reconnect_max_delay_ms == 15000
    assert c.diagnostics.health_stream_stale_ms == 10000      # corrected default
    assert c.diagnostics.reboot_allowed is False              # safe-by-default gate
    s = c.stream_profiles[f"{SERIAL}:color"]
    assert s.codec == "h264" and s.encoder == "libx264" and s.gop_frames == 15


# ── R1 version pin ───────────────────────────────────────────────────

def test_r1_version_must_be_1():
    with pytest.raises(ValidationError):
        _config(version=2)


# ── R2 canonical sensor key ──────────────────────────────────────────

def test_r2_helper():
    assert is_canonical_sensor_key(f"{SERIAL}:color")
    assert is_canonical_sensor_key(f"{SERIAL}:depth")
    assert not is_canonical_sensor_key("local:color")
    assert not is_canonical_sensor_key("local:depth")
    assert not is_canonical_sensor_key(":color")
    assert not is_canonical_sensor_key(f"{SERIAL}:bogus")
    assert not is_canonical_sensor_key(f"{SERIAL} :color")  # whitespace

def test_r2_rejects_legacy_local_color_stream():
    with pytest.raises(ValidationError, match="local"):
        _stream(key="local:color")

def test_r2_rejects_any_local_sentinel():
    with pytest.raises(ValidationError):
        _stream(key="local:depth")

def test_r2_rejects_non_canonical_map_key():
    with pytest.raises(ValidationError, match="canonical"):
        _config(stream_profiles={"local:color": _stream(key="local:color")})

def test_r2_map_key_must_equal_sensor_key():
    # stream's sensor_key disagrees with its map key
    with pytest.raises(ValidationError, match="must equal its map key"):
        _config(stream_profiles={f"{SERIAL}:color": _stream(key=f"{SERIAL}:depth")})


# ── R4 ttl bound ─────────────────────────────────────────────────────

@pytest.mark.parametrize("ttl,ok", [(300, True), (3600, True), (1800, True), (299, False), (3601, False)])
def test_r4_ttl_bounds(ttl, ok):
    if ok:
        assert WebRtcRuntimeConfig(turn_credential_ttl_seconds=ttl).turn_credential_ttl_seconds == ttl
    else:
        with pytest.raises(ValidationError):
            WebRtcRuntimeConfig(turn_credential_ttl_seconds=ttl)


# ── R5 reconnect ordering ────────────────────────────────────────────

def test_r5_reconnect_ordering_ok():
    WebRtcRuntimeConfig(reconnect_base_delay_ms=500, reconnect_max_delay_ms=15000)

def test_r5_reconnect_ordering_violation():
    with pytest.raises(ValidationError, match="reconnect_max_delay_ms"):
        WebRtcRuntimeConfig(reconnect_base_delay_ms=5000, reconnect_max_delay_ms=2000)


# ── R7 bitrate bound ─────────────────────────────────────────────────

@pytest.mark.parametrize("br,ok", [(100, True), (8000, True), (900, True), (99, False), (8001, False)])
def test_r7_bitrate_bounds(br, ok):
    if ok:
        assert _stream(bitrate_kbps=br).bitrate_kbps == br
    else:
        with pytest.raises(ValidationError):
            _stream(bitrate_kbps=br)


# ── literals + resolution shape ──────────────────────────────────────

def test_ice_policy_literal():
    WebRtcRuntimeConfig(ice_policy="relay")
    with pytest.raises(ValidationError):
        WebRtcRuntimeConfig(ice_policy="none")

def test_codec_encoder_literals():
    with pytest.raises(ValidationError):
        _stream(codec="vp8")
    with pytest.raises(ValidationError):
        _stream(encoder="nvenc")

def test_resolution_shape():
    _stream(resolution="1280x720")
    with pytest.raises(ValidationError, match="WxH"):
        _stream(resolution="big")


# ── extra fields forbidden (baseline for R9/R10 secret/firewall keys) ─

def test_extra_field_forbidden_webrtc():
    with pytest.raises(ValidationError):
        WebRtcRuntimeConfig(TURN_SHARED_SECRET="x")  # secret-ish unknown field

def test_extra_field_forbidden_config():
    with pytest.raises(ValidationError):
        _config(firewall_rules=["x"])


# ── response shapes ──────────────────────────────────────────────────

def test_apply_impact_values():
    assert {i.value for i in ApplyImpact} == {
        "HOT", "NEW_SESSIONS_ONLY", "RESTART_ENCODER", "RECREATE_MOUNTPOINT",
        "RESTART_JANUS", "DEPLOYMENT_ONLY", "REJECTED",
    }

def test_diff_entry_from_alias_serializes():
    d = DiffEntry(path="webrtc.ice_policy", **{"from": "all"}, to="relay",
                  source="settings.ice_policy", impact=ApplyImpact.NEW_SESSIONS_ONLY)
    dumped = d.model_dump(by_alias=True)
    assert dumped["from"] == "all" and dumped["to"] == "relay"
    assert "from_" not in dumped

def test_validation_response_defaults():
    r = ValidationResponse(valid=True)
    assert r.diff == [] and r.impact == [] and r.errors == [] and r.warnings == []

def test_validation_error_entry():
    e = ValidationErrorEntry(path="stream_profiles.x", message="rejected")
    assert e.path == "stream_profiles.x" and e.message == "rejected"

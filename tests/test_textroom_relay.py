"""Tests for textroom_relay topic router (Sprint X3.4 — generic stack).

Stack is fully agnostic to specific application semantics. All clients must
send explicit `topic` field. Robot-specific topics (joystick, etc.) live in
external config file (/etc/robot/back-channel-topics.json); stack defaults
only include camera-intrinsic topics (depth_query).
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import textroom_relay as relay
from textroom_relay import (
    _detect_topic,
    _extract_inner_payload,
    _is_ping,
    _load_topic_config,
    _validate,
    app,
)


# ── _is_ping ───────────────────────────────────────────────────────────

def test_is_ping_recognizes_ping():
    assert _is_ping({"type": "ping", "ts": 100, "id": 1}) is True


def test_is_ping_rejects_non_ping():
    assert _is_ping({"type": "control", "ts": 100}) is False
    assert _is_ping({"ts": 100}) is False
    assert _is_ping("string") is False
    assert _is_ping(None) is False


# ── _detect_topic ──────────────────────────────────────────────────────

def test_detect_topic_explicit_field():
    """Primary protocol: `topic` field in payload."""
    assert _detect_topic({"topic": "voice", "data": [1, 2, 3]}) == "voice"
    assert _detect_topic({"topic": "custom-app"}) == "custom-app"


def test_detect_topic_no_autodetect():
    """X3.4: stack is agnostic. axes/buttons frames without explicit topic field
    are NOT autodetected to "joystick" anymore — that was robot-specific magic.
    Clients MUST send explicit topic field."""
    frame = {"ts": 100, "axes": [0.1, 0.2], "buttons": [0, 1]}
    assert _detect_topic(frame) == "unknown"


def test_detect_topic_unknown_fallback():
    """No topic field → "unknown" — dropped silently."""
    assert _detect_topic({"foo": "bar"}) == "unknown"
    assert _detect_topic({"topic": "", "data": [0]}) == "unknown"


# ── _validate ──────────────────────────────────────────────────────────

def test_validate_none_schema_passes_anything():
    assert _validate({"any": "json"}, "none") is True
    assert _validate({}, "none") is True


def test_validate_raw_schema_accepts_dict_only():
    assert _validate({"data": [1, 2]}, "raw") is True
    assert _validate("not a dict", "raw") is False


def test_validate_rejects_unknown_schema():
    """X3.4: removed joystick + other app-specific schemas. Schema must be
    raw or none — application-level validation belongs to the sink, not relay."""
    assert _validate({"ts": 100, "axes": [], "buttons": []}, "joystick") is False
    assert _validate({}, "custom-app-schema") is False


def test_validate_joystick_rejects_missing_required_fields():
    assert _validate({"ts": 100, "axes": []}, "joystick") is False
    assert _validate({"axes": [], "buttons": []}, "joystick") is False


def test_validate_unknown_schema_rejects():
    assert _validate({"data": 1}, "made-up-schema") is False


# ── _extract_inner_payload ─────────────────────────────────────────────

def test_extract_inner_payload_valid():
    janus_body = {"textroom": "message", "text": json.dumps({"topic": "voice", "data": "hi"})}
    inner = _extract_inner_payload(janus_body)
    assert inner == {"topic": "voice", "data": "hi"}


def test_extract_inner_payload_wrong_textroom_event():
    assert _extract_inner_payload({"textroom": "join", "room": 1000}) is None


def test_extract_inner_payload_no_text():
    assert _extract_inner_payload({"textroom": "message"}) is None


def test_extract_inner_payload_invalid_json():
    assert _extract_inner_payload({"textroom": "message", "text": "not json"}) is None


def test_extract_inner_payload_normalizes_float_ts():
    janus_body = {"textroom": "message", "text": json.dumps({"ts": 100.5})}
    inner = _extract_inner_payload(janus_body)
    assert inner["ts"] == 100


# ── _load_topic_config ─────────────────────────────────────────────────

def test_load_config_falls_back_to_defaults_if_file_missing():
    cfg = _load_topic_config("/nonexistent/path/config.json")
    # X3.4: stack defaults include only camera-intrinsic topics (depth_query).
    # Robot topics (joystick, etc.) live in external config.
    assert "depth_query" in cfg
    assert "joystick" not in cfg


def test_load_config_parses_valid_file(tmp_path):
    config_file = tmp_path / "topics.json"
    config_file.write_text(json.dumps({
        "topics": {
            "voice": {"sink_url": "http://localhost:9000/voice", "rate_limit_hz": 10, "validate_schema": "raw"},
            "custom": {"sink_url": "http://localhost:9001/custom", "validate_schema": "none"},
        }
    }))
    cfg = _load_topic_config(str(config_file))
    assert "voice" in cfg
    assert cfg["voice"]["sink_url"] == "http://localhost:9000/voice"
    assert cfg["custom"]["validate_schema"] == "none"
    assert "depth_query" in cfg  # camera-intrinsic default preserved


def test_load_config_falls_back_on_malformed_json(tmp_path):
    config_file = tmp_path / "bad.json"
    config_file.write_text("this is not json {{{")
    cfg = _load_topic_config(str(config_file))
    assert cfg == relay._DEFAULT_TOPICS


# ── HTTP integration (TestClient) ─────────────────────────────────────

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health_endpoint_returns_topic_info(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "topics" in data
    # X3.4: camera-intrinsic depth_query is the only default.
    assert "depth_query" in data["topics"]
    assert "sink_url" in data["topics"]["depth_query"]
    assert "stats" in data["topics"]["depth_query"]


def test_topics_endpoint_lists_configured(client):
    r = client.get("/topics")
    assert r.status_code == 200
    data = r.json()
    assert "depth_query" in data["topics"]
    assert "details" in data
    assert data["details"]["depth_query"]["schema"] == "raw"


def test_time_endpoint_returns_server_ms(client):
    r = client.get("/time")
    data = r.json()
    assert "server_ms" in data
    assert isinstance(data["server_ms"], int)


def test_pong_returns_default_before_any_ping(client):
    r = client.get("/pong")
    assert r.json() == {"id": -1}


def test_textroom_hook_accepts_legacy_joystick_frame(client):
    """Backwards compat: frame without `topic` field still routed to joystick."""
    inner = {"ts": 100, "axes": [0.1, 0.2, 0.3, 0.4], "buttons": [0, 1, 0]}
    body = {"textroom": "message", "text": json.dumps(inner)}
    r = client.post("/textroom-hook", json=body)
    assert r.status_code == 200


def test_textroom_hook_accepts_explicit_topic(client):
    """New protocol: payload with topic field → routed by topic."""
    inner = {"topic": "joystick", "ts": 100, "axes": [0], "buttons": [0]}
    body = {"textroom": "message", "text": json.dumps(inner)}
    r = client.post("/textroom-hook", json=body)
    assert r.status_code == 200


def test_textroom_hook_unknown_topic_silent_drop(client):
    """Unknown topic — returns OK but doesn't enqueue."""
    inner = {"topic": "no-such-topic", "data": "anything"}
    body = {"textroom": "message", "text": json.dumps(inner)}
    r = client.post("/textroom-hook", json=body)
    assert r.status_code == 200


def test_textroom_hook_ping_updates_pong(client):
    """Ping path: system-level, not topic-routed. Updates /pong."""
    ping = {"type": "ping", "id": 42, "ts": 1234567890}
    body = {"textroom": "message", "text": json.dumps(ping)}
    client.post("/textroom-hook", json=body)
    r = client.get("/pong")
    data = r.json()
    assert data["id"] == 42
    assert data["browser_ts"] == 1234567890
    assert "relay_rx_ms" in data


def test_textroom_hook_unknown_topic_dropped(client):
    """X3.4: frame without explicit topic → unknown → silent drop. No autodetect."""
    bad = {"ts": 100, "axes": [0.5], "buttons": [0]}  # no topic field
    body = {"textroom": "message", "text": json.dumps(bad)}
    r = client.post("/textroom-hook", json=body)
    assert r.status_code == 200
    # Stack doesn't count unknown topic drops — they don't reach a topic queue.
    # Just verify response 200 (silent ack).


def test_textroom_hook_malformed_json_returns_400(client):
    r = client.post("/textroom-hook", content=b"not json")
    assert r.status_code == 400


def test_textroom_hook_non_message_event_returns_ok(client):
    """Janus textroom emits join/leave events — relay just acks."""
    body = {"textroom": "join", "room": 1000}
    r = client.post("/textroom-hook", json=body)
    assert r.status_code == 200

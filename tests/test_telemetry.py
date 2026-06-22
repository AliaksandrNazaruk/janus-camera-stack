"""Tests for the telemetry ingestion endpoint."""
from __future__ import annotations

import pytest


class TestTelemetryEndpoint:
    """POST /telemetry — client WebRTC stats ingestion."""

    @pytest.mark.asyncio
    async def test_ice_connected_returns_204(self, client):
        resp = await client.post("/telemetry", json={
            "event": "ice_connected",
            "session_id": "test-session-1",
            "camera": "color",
            "ice_connect_ms": 1200.5,
            "time_to_first_frame_ms": 3500.0,
        })
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_stats_report_returns_204(self, client):
        resp = await client.post("/telemetry", json={
            "event": "stats_report",
            "session_id": "test-session-2",
            "camera": "depth",
            "packets_received": 10000,
            "packets_lost": 50,
            "frames_decoded": 900,
            "jitter": 0.012,
        })
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_minimal_payload_accepted(self, client):
        resp = await client.post("/telemetry", json={
            "event": "stats_report",
        })
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_ice_failed_accepted(self, client):
        resp = await client.post("/telemetry", json={
            "event": "ice_failed",
            "session_id": "test-session-3",
            "camera": "color",
        })
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_invalid_event_type_rejected(self, client):
        resp = await client.post("/telemetry", json={
            "event": "invalid_event_type",
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_extra_fields_accepted(self, client):
        resp = await client.post("/telemetry", json={
            "event": "ice_connected",
            "ice_connect_ms": 500,
            "extra": {"custom_field": "value", "debug": True},
        })
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_missing_body_rejected(self, client):
        resp = await client.post("/telemetry")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_candidate_fields_accepted(self, client):
        resp = await client.post("/telemetry", json={
            "event": "ice_connected",
            "ice_connect_ms": 800,
            "local_candidate": {"type": "relay", "protocol": "udp", "address": "10.0.0.1", "port": 50000},
            "remote_candidate": {"type": "host", "protocol": "udp", "address": "192.168.1.10", "port": 40001},
        })
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_camera_label_separates_series(self, client):
        # A3 observability: color and depth must not overwrite each other's gauge.
        from prometheus_client import REGISTRY
        await client.post("/telemetry", json={
            "event": "stats_report", "camera": "color",
            "packets_received": 100, "packets_lost": 5,
        })
        await client.post("/telemetry", json={
            "event": "stats_report", "camera": "depth",
            "packets_received": 100, "packets_lost": 1,
        })
        color = REGISTRY.get_sample_value(
            "camstack_client_packet_loss_ratio", {"camera": "color"})
        depth = REGISTRY.get_sample_value(
            "camstack_client_packet_loss_ratio", {"camera": "depth"})
        assert color is not None and depth is not None
        assert color != depth  # separate series, no overwrite
        assert color == pytest.approx(5 / 105)
        assert depth == pytest.approx(1 / 101)


class TestTelemetryAuth:
    """A3 — /telemetry is viewer-auth gated when VIEWER_TOKENS is set (production).

    Dev mode (VIEWER_TOKENS unset, the default in the test env) is a pass-through:
    the TestTelemetryEndpoint cases above already prove that path returns 204.
    """

    _TOKEN = "viewer-secret-token-aaaa"

    @pytest.fixture
    def gated(self, monkeypatch):
        from app.core import viewer_auth
        monkeypatch.setattr(viewer_auth, "VIEWER_TOKENS", [self._TOKEN])

    @pytest.mark.asyncio
    async def test_no_token_rejected(self, client, gated):
        resp = await client.post("/telemetry", json={"event": "stats_report", "camera": "color"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_token_rejected(self, client, gated):
        resp = await client.post(
            "/telemetry", json={"event": "stats_report", "camera": "color"},
            headers={"X-Viewer-Token": "wrong-token"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_header_token_accepted(self, client, gated):
        resp = await client.post(
            "/telemetry", json={"event": "stats_report", "camera": "color"},
            headers={"X-Viewer-Token": self._TOKEN},
        )
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_valid_query_token_accepted(self, client, gated):
        resp = await client.post(
            f"/telemetry?token={self._TOKEN}",
            json={"event": "stats_report", "camera": "color"},
        )
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_malformed_payload_rejected_even_when_authed(self, client, gated):
        # Auth passes but body is invalid → 422 (not a crash, not accepted as truth).
        resp = await client.post(
            "/telemetry", json={"event": "bogus_event"},
            headers={"X-Viewer-Token": self._TOKEN},
        )
        assert resp.status_code == 422

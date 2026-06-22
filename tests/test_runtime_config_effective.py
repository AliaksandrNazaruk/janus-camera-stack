"""B1-2 — effective runtime-config builder + GET /effective (read-only, no secrets)."""
import json

import pytest

from app.config.runtime_schema import (
    DiagnosticsRuntimeConfig,
    WebRtcRuntimeConfig,
    is_canonical_sensor_key,
)
from app.services import runtime_config_builder as builder
from app.services.runtime_config_builder import (
    EffectiveRuntimeConfig,
    EffectiveStreamProfile,
    build_effective,
)

SERIAL = "141722072135"


def _all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k).lower()
            yield from _all_keys(v)
    elif isinstance(obj, list):
        for x in obj:
            yield from _all_keys(x)


# ── builder ──────────────────────────────────────────────────────────

def test_build_effective_canonical_keys_and_runtime_active(monkeypatch):
    from app.services import mountpoint_allocator as alloc
    from app.services import sensor_lifecycle
    A = alloc.Allocation
    monkeypatch.setattr(alloc, "list_allocations", lambda *a, **k: {
        f"{SERIAL}:color": A(mp_id=1305, rtp_port=5004, desired_active=True),
        f"{SERIAL}:depth": A(mp_id=1306, rtp_port=5006, desired_active=False),
    })
    monkeypatch.setattr(sensor_lifecycle, "is_running",
                        lambda s: True if s == "color" else None)

    eff = build_effective()
    assert set(eff.stream_profiles) == {f"{SERIAL}:color", f"{SERIAL}:depth"}
    assert all(is_canonical_sensor_key(k) for k in eff.stream_profiles)
    color = eff.stream_profiles[f"{SERIAL}:color"]
    assert color.enabled is True and color.mountpoint_id == 1305 and color.rtp_port == 5004
    assert color.runtime_active is True
    assert eff.stream_profiles[f"{SERIAL}:depth"].runtime_active is None  # probe None


def test_build_effective_drops_legacy_keys(monkeypatch):
    from app.services import mountpoint_allocator as alloc
    from app.services import sensor_lifecycle
    A = alloc.Allocation
    monkeypatch.setattr(alloc, "list_allocations", lambda *a, **k: {
        "local:color": A(mp_id=1305, rtp_port=5004, desired_active=True),   # legacy
        f"{SERIAL}:depth": A(mp_id=1306, rtp_port=5006, desired_active=True),
    })
    monkeypatch.setattr(sensor_lifecycle, "is_running", lambda s: None)
    eff = build_effective()
    assert "local:color" not in eff.stream_profiles          # legacy never surfaced
    assert f"{SERIAL}:depth" in eff.stream_profiles


def test_build_effective_has_no_secret_keys(monkeypatch):
    from app.services import mountpoint_allocator as alloc
    from app.services import sensor_lifecycle
    A = alloc.Allocation
    monkeypatch.setattr(alloc, "list_allocations", lambda *a, **k: {
        f"{SERIAL}:color": A(mp_id=1305, rtp_port=5004, desired_active=True)})
    monkeypatch.setattr(sensor_lifecycle, "is_running", lambda s: True)
    eff = build_effective()
    keys = set(_all_keys(eff.model_dump()))
    # Precise secret-key markers — NOT broad "credential"/"token" (those would
    # false-match the legitimate non-secret `turn_credential_ttl_seconds`).
    for bad in ("password", "_secret", "shared_secret", "turn_pass", "turn_pwd", "admin_token"):
        assert not any(bad in k for k in keys), f"effective exposes secret-ish key matching {bad!r}"
    # TURN host (non-secret) may be present; the password/secret must not be.


# ── endpoint ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_effective_endpoint_admin_gated(client):
    resp = await client.get("/api/v1/admin/runtime-config/effective")
    assert resp.status_code in (401, 403, 503)  # no admin token → rejected


@pytest.mark.asyncio
async def test_effective_endpoint_returns_config(admin_client, monkeypatch):
    fake = EffectiveRuntimeConfig(
        webrtc=WebRtcRuntimeConfig(),
        stream_profiles={f"{SERIAL}:color": EffectiveStreamProfile(
            sensor_key=f"{SERIAL}:color", mountpoint_id=1305, rtp_port=5004, runtime_active=True)},
        diagnostics=DiagnosticsRuntimeConfig(),
    )
    monkeypatch.setattr("app.routes.runtime_config.build_effective", lambda: fake)
    resp = await admin_client.get("/api/v1/admin/runtime-config/effective")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 1
    assert f"{SERIAL}:color" in body["stream_profiles"]
    assert body["stream_profiles"][f"{SERIAL}:color"]["runtime_active"] is True
    # secret-free over the wire too
    assert not any(b in json.dumps(body).lower() for b in ("shared_secret", "turn_pass", "password"))


@pytest.mark.asyncio
async def test_effective_endpoint_is_read_only(admin_client):
    # only GET /effective exists under the prefix; no POST/PUT/DELETE in B1-2
    for method in ("post", "put", "delete"):
        resp = await getattr(admin_client, method)("/api/v1/admin/runtime-config/effective")
        assert resp.status_code in (404, 405)

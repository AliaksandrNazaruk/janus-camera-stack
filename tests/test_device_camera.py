"""Characterization tests for routes/device_camera.py — the parameterized per-(serial, sensor)
camera endpoints (/cameras/{serial}/{sensor}/*).

Phase 2A (STRICT_ARCHITECTURE_HARDENING / DEVICE_CAMERA_THINNING): these PIN the current behavior of
all 9 endpoints — status codes, payloads, audit events, and the color→camera delegation — BEFORE the
2A extraction (sensor_tuning_env adapter + application/device_camera use-cases). They must keep
passing byte-identically across the refactor; if one needs editing to make the refactor pass, that is
a behavior change and must be justified.

Patches target the names exactly where device_camera uses them, so they keep working after extraction
re-points those names.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.routes import device_camera as dc
from app.services.device_registry import SensorEntry
from app.services.sensor_lifecycle import LifecycleError, UnsupportedSensor

pytestmark = pytest.mark.asyncio

SERIAL = "987654321"
CAMS = f"/cameras/{SERIAL}"


def _entry(sensor="depth", *, provisioning_supported=True, running=True, mountpoint_id=1306,
           label="Depth"):
    return SensorEntry(sensor=sensor, label=label, provisioning_supported=provisioning_supported,
                       running=running, mountpoint_id=mountpoint_id)


def _cfg(**over):
    base = dict(width=640, height=480, fps=15, bitrate_kbps=1000, gop=None, preset="veryfast",
                tune="zerolatency", snapshot_fps=0, port=5006, rotation=0)
    base.update(over)
    return dc.CameraStreamConfig(**base)


# ── resolve / require guards (→ resolve_running_sensor use-case in 2A) ─────────

async def test_unknown_sensor_404(client):
    with patch("app.services.device_registry.resolve_sensor", return_value=None):
        r = await client.get(f"{CAMS}/depth/modes")
    assert r.status_code == 404
    assert "dashboard.html" in r.json()["detail"]


async def test_not_provisionable_501(client):
    with patch("app.services.device_registry.resolve_sensor", return_value=_entry(provisioning_supported=False)):
        r = await client.get(f"{CAMS}/depth/modes")
    assert r.status_code == 501
    assert "cannot be provisioned" in r.json()["detail"]


async def test_stopped_pipeline_409(client):
    with patch("app.services.device_registry.resolve_sensor", return_value=_entry(running=False)):
        r = await client.get(f"{CAMS}/depth/modes")
    assert r.status_code == 409
    assert "stopped" in r.json()["detail"]


# ── initialize ────────────────────────────────────────────────────────────────

async def test_initialize_success_payload_and_audit(admin_client):
    alloc = MagicMock(mp_id=1306, rtp_port=5006)
    with patch("app.services.device_registry.resolve_sensor", return_value=_entry()), \
         patch("app.services.sensor_lifecycle.initialize", return_value=(True, "started", alloc)), \
         patch("app.services.audit_log.emit") as emit:
        r = await admin_client.post(f"{CAMS}/depth/initialize")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "running": True, "message": "started", "mountpoint_id": 1306, "rtp_port": 5006,
        "viewer_url": f"/cameras/{SERIAL}/depth/viewer.html",
        "config_url": f"/cameras/{SERIAL}/depth/camera_config.html",
    }
    assert emit.called and "initialize" in emit.call_args.kwargs["action"]


async def test_initialize_unsupported_sensor_501(admin_client):
    with patch("app.services.device_registry.resolve_sensor", return_value=_entry()), \
         patch("app.services.sensor_lifecycle.initialize", side_effect=UnsupportedSensor("no depth pipe")), \
         patch("app.services.audit_log.emit"):
        r = await admin_client.post(f"{CAMS}/depth/initialize")
    assert r.status_code == 501
    assert "no depth pipe" in r.json()["detail"]


async def test_initialize_lifecycle_error_500(admin_client):
    with patch("app.services.device_registry.resolve_sensor", return_value=_entry()), \
         patch("app.services.sensor_lifecycle.initialize", side_effect=LifecycleError("boom")), \
         patch("app.services.audit_log.emit"):
        r = await admin_client.post(f"{CAMS}/depth/initialize")
    assert r.status_code == 500
    assert "boom" in r.json()["detail"]


async def test_initialize_requires_admin(client):
    r = await client.post(f"{CAMS}/depth/initialize")
    assert r.status_code == 403


# ── stop ──────────────────────────────────────────────────────────────────────

async def test_stop_success(admin_client):
    with patch("app.services.device_registry.resolve_sensor", return_value=_entry()), \
         patch("app.services.sensor_lifecycle.stop", return_value=(False, "stopped ok")), \
         patch("app.services.audit_log.emit"):
        r = await admin_client.post(f"{CAMS}/depth/stop")
    assert r.status_code == 200
    assert r.json() == {"running": False, "message": "stopped ok"}


async def test_stop_requires_admin(client):
    r = await client.post(f"{CAMS}/depth/stop")
    assert r.status_code == 403


# ── modes / sensors — delegate to camera.py (color logic), pass through ────────

async def test_modes_builds_response_from_v4l2(client):
    # 2B-3: device_camera builds CameraModesResponse from the v4l2 service directly (no camera.py handler).
    raw = {"pixel_format": "YUYV", "device": "/dev/video0",
           "modes": [{"width": 640, "height": 480, "fps": [30, 15]}]}
    with patch("app.services.device_registry.resolve_sensor", return_value=_entry()), \
         patch.object(dc, "list_v4l2_modes", return_value=raw) as lm:
        r = await client.get(f"{CAMS}/depth/modes")
    assert r.status_code == 200
    body = r.json()
    assert body["pixel_format"] == "YUYV" and body["device"] == "/dev/video0"
    assert body["modes"][0]["width"] == 640 and body["modes"][0]["fps"] == [30, 15]
    assert lm.called


async def test_sensors_queries_catalog_directly(client):
    with patch("app.services.device_registry.resolve_sensor", return_value=_entry()), \
         patch.object(dc, "rs_query_catalog", return_value={"sensors": ["depth"]}) as gs:
        r = await client.get(f"{CAMS}/depth/sensors")
    assert r.status_code == 200 and r.json() == {"sensors": ["depth"]}
    assert gs.called


async def test_sensors_sdk_error_503(client):
    with patch("app.services.device_registry.resolve_sensor", return_value=_entry()), \
         patch.object(dc, "rs_query_catalog", side_effect=RuntimeError("no SDK")):
        r = await client.get(f"{CAMS}/depth/sensors")
    assert r.status_code == 503 and "no SDK" in r.json()["detail"]


# ── rotation — no auth, no resolve; reads the tuning env directly ──────────────

async def test_rotation_reads_env_no_auth(client):
    with patch("app.services.env_store.read_env", return_value={"ROTATION": "90"}):
        r = await client.get(f"{CAMS}/depth/rotation")
    assert r.status_code == 200 and r.json() == {"rotation": 90}


async def test_rotation_defaults_zero_when_env_missing(client):
    with patch("app.services.env_store.read_env", side_effect=FileNotFoundError):
        r = await client.get(f"{CAMS}/depth/rotation")
    assert r.status_code == 200 and r.json() == {"rotation": 0}


# ── config GET — color delegates, non-color reads rs-{sensor}.tuning.env ───────

async def test_get_config_color_delegates(admin_client):
    sentinel = _cfg(width=1920, height=1080)
    with patch("app.services.device_registry.resolve_sensor", return_value=_entry(sensor="color")), \
         patch.object(dc, "read_color_config", return_value=sentinel):
        r = await admin_client.get(f"{CAMS}/color/config")
    assert r.status_code == 200 and r.json()["width"] == 1920 and r.json()["height"] == 1080


async def test_get_config_noncolor_reads_env(admin_client):
    env = {"WIDTH": "1280", "HEIGHT": "720", "FPS": "30", "BITRATE_KBPS": "1500",
           "PRESET": "fast", "TUNE": "film", "ROTATION": "180", "PORT": "5008"}
    with patch("app.services.device_registry.resolve_sensor", return_value=_entry()), \
         patch("app.services.env_store.read_env", return_value=env):
        r = await admin_client.get(f"{CAMS}/depth/config")
    assert r.status_code == 200
    body = r.json()
    assert body["width"] == 1280 and body["height"] == 720 and body["fps"] == 30
    assert body["bitrate_kbps"] == 1500 and body["rotation"] == 180 and body["port"] == 5008


# ── config POST — color delegates; non-color writes env + restarts encoder ─────

async def test_post_config_noncolor_writes_and_restarts(admin_client):
    payload = dict(width=848, height=480, fps=15, bitrate_kbps=1200, gop=None, preset="veryfast",
                   tune="zerolatency", snapshot_fps=0, port=5006, rotation=90)
    with patch("app.services.device_registry.resolve_sensor", return_value=_entry()), \
         patch("app.services.env_store.read_env", return_value={}), \
         patch("app.services.env_store.write_env_atomic") as wr, \
         patch("app.services.system.run") as run:
        r = await admin_client.post(f"{CAMS}/depth/config", json=payload)
    assert r.status_code == 200 and r.json()["rotation"] == 90 and r.json()["width"] == 848
    assert wr.called
    # encoder restart for this sensor's rs-stream instance
    args = run.call_args.args[0]
    assert "encoder-admin" in " ".join(args) and "restart" in args and "depth" in args


async def test_post_config_write_failure_500(admin_client):
    """De-leak target (2A): today _write_rs_sensor_config raises HTTPException(500) directly; after
    extraction the adapter raises a domain error the route maps to 500 — this 500 must be preserved."""
    payload = dict(width=848, height=480, fps=15, bitrate_kbps=1200, gop=None, preset="veryfast",
                   tune="zerolatency", snapshot_fps=0, port=5006, rotation=0)
    with patch("app.services.device_registry.resolve_sensor", return_value=_entry()), \
         patch("app.services.env_store.read_env", return_value={}), \
         patch("app.services.env_store.write_env_atomic", side_effect=OSError("disk full")):
        r = await admin_client.post(f"{CAMS}/depth/config", json=payload)
    assert r.status_code == 500


async def test_post_config_requires_admin(client):
    payload = dict(width=848, height=480, fps=15, bitrate_kbps=1200, gop=None, preset="veryfast",
                   tune="zerolatency", snapshot_fps=0, port=5006, rotation=0)
    r = await client.post(f"{CAMS}/depth/config", json=payload)
    assert r.status_code == 403


# ── HTML views (rendering STAYS in the route — D2) ─────────────────────────────

async def test_camera_config_html_renders_200(client):
    with patch("app.services.device_registry.resolve_sensor", return_value=_entry()):
        r = await client.get(f"{CAMS}/depth/camera_config.html")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


async def test_viewer_html_no_mountpoint_409(client):
    with patch("app.services.device_registry.resolve_sensor", return_value=_entry(mountpoint_id=None)):
        r = await client.get(f"{CAMS}/depth/viewer.html")
    assert r.status_code == 409
    assert "mountpoint" in r.json()["detail"]


async def test_viewer_html_renders_200(client):
    with patch("app.services.device_registry.resolve_sensor", return_value=_entry()), \
         patch("app.services.env_store.read_env", return_value={}):
        r = await client.get(f"{CAMS}/depth/viewer.html")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")

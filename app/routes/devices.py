"""Device dashboard route — lists discovered RealSense devices and their
sensor provisioning state. Operator entry point for multi-sensor topology.

URL: /api/v1/devices/dashboard.html

Provisioning of depth/ir is a Sprint X2 task; until then 'Initialize'
buttons are disabled with explainer tooltip.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.core.admin import require_admin
from app.core.settings import get_settings
from app.services.device_registry import get_registry

from jinja2 import Environment, FileSystemLoader, select_autoescape

# Mounted at /cameras/* — matched by gateway service "cameras" which forwards
# /api/v1/cameras/dashboard.html to /cameras/dashboard.html upstream (see
# api_gateway_service docker-compose.yml SERVICE_MAP_JSON: prefix="/cameras").
# Shares this prefix with device_camera.router (per-(serial,sensor) routes).
router = APIRouter(tags=["devices"], prefix="/cameras")

_jinja_env = Environment(
    loader=FileSystemLoader(str(get_settings().templates_dir)),
    autoescape=select_autoescape(["html", "htm"]),
)


@router.get("/registry.json", summary="Device + sensor registry (JSON)")
def get_registry_json() -> JSONResponse:
    devices = get_registry()
    return JSONResponse(content={
        "devices": [
            {
                "serial": d.serial,
                "name": d.name,
                "firmware": d.firmware,
                "sensors": [asdict(s) for s in d.sensors],
            } for d in devices
        ]
    })


@router.get("/dashboard.html", include_in_schema=False)
def devices_dashboard(request: Request) -> HTMLResponse:
    style_nonce = getattr(request.state, "style_nonce", "")
    settings = get_settings()
    tmpl = _jinja_env.get_template("devices_dashboard.html")
    return HTMLResponse(tmpl.render(
        cam_type=settings.camera_type,
        style_nonce=style_nonce,
        devices=get_registry(),
    ))


# ── Stream allocations (Sprint X4) ────────────────────────────────────
# Cross-cutting list of all (serial, sensor) allocations with desired_active
# (boot reconciler intent) and runtime_active (live encoder probe).
#
# Toggle endpoints are NOT here — use the existing generative
# /api/v1/cameras/{serial}/{sensor}/initialize (POST) and /stop (POST).
# Those already persist desired_active=True/False via sensor_lifecycle
# (Sprint X4 wiring), so a separate "enable/disable" surface would be
# a redundant duplicate with inconsistent URL hierarchy.

class StreamState(BaseModel):
    serial: str
    sensor: str
    mp_id: int
    rtp_port: int
    desired_active: bool
    runtime_active: Optional[bool] = None


class StreamsResponse(BaseModel):
    streams: List[StreamState]
    state_path: str


@router.get(
    "/streams",
    response_model=StreamsResponse,
    summary="List all stream allocations with desired/runtime state",
    dependencies=[Depends(require_admin)],
)
def list_streams() -> StreamsResponse:
    from app.services import mountpoint_allocator as _alloc
    from app.services import sensor_lifecycle as _lc

    out: List[StreamState] = []
    for key, alloc in sorted(_alloc.list_allocations().items()):
        if ":" in key:
            serial, sensor = key.split(":", 1)
        else:
            serial, sensor = "", key
        out.append(StreamState(
            serial=serial,
            sensor=sensor,
            mp_id=alloc.mp_id,
            rtp_port=alloc.rtp_port,
            desired_active=alloc.desired_active,
            runtime_active=_lc.is_running(sensor),
        ))
    return StreamsResponse(streams=out, state_path=str(_alloc.DEFAULT_STATE_PATH))

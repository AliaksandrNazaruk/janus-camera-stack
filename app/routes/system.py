"""Core system routes: health probes, status, relay proxy, service restart."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from app.core.admin import require_admin
from app.core.dependencies import require_api_key
from app.core.settings import get_settings
from app.middleware.rate_limit import require_admin_rate_limit
from app.services import janus
from app.services import relay_proxy
from app.services.system import service_restart, systemd_brief

router = APIRouter(tags=["system"])
# Boot-time constant — FastAPI route paths must be static at decoration time.
_CAM_TYPE = get_settings().camera_type


# ── Response models ──

class HealthResponse(BaseModel):
    ok: bool = Field(..., description="All critical subsystems are healthy.")
    mode: str = Field("nominal", description="Current system operating mode.")
    janus_reachable: bool = Field(True, description="Janus REST API responds.")
    stream_active: bool = Field(True, description="Primary mountpoint has recent video.")
    details: Dict[str, Any] = Field(default_factory=dict, description="Per-check breakdown.")


class ActionResponse(BaseModel):
    ok: bool
    message: str


# ── Health probes ──

@router.get(
    "/livez",
    summary="Liveness probe — process responsive (no upstream dep check)",
    response_class=JSONResponse,
)
def livez() -> JSONResponse:
    """Shallow check for k8s livenessProbe / Docker HEALTHCHECK.
    Returns OK if the FastAPI process is responsive. Does NOT verify
    upstream services (Janus, encoders) — that's readyz/healthz.
    """
    return JSONResponse({"ok": True}, status_code=200)


@router.get(
    "/readyz",
    summary="Readiness probe — service can accept client traffic",
    response_class=JSONResponse,
)
def readyz() -> JSONResponse:
    """Ready check for k8s readinessProbe. Returns OK if Janus is
    reachable (even if no stream yet — clients can still negotiate).
    Returns 503 if Janus unreachable so Service stops routing to the pod.
    """
    settings = get_settings()
    # A1: in production, refuse readiness if security config is broken (defense
    # in depth — startup already enforces this, but readyz makes it observable).
    from app.core.settings import is_production
    if is_production():
        from app.core.startup_checks import production_issues
        issues = production_issues(settings)
        if issues:
            return JSONResponse(
                {"ok": False, "error": "insecure production config", "issues": issues},
                status_code=503,
            )
    # H-02: a corrupt topology store is a degraded state — surface it and fail
    # readiness (the gateway can no longer trust its binding set) instead of letting
    # reads 500 opaquely or silently serve an empty fleet.
    from app.services import stream_binding_store as _sbs
    _store = _sbs.store_corruption_status()
    if _store.get("topology_store_corrupt"):
        return JSONResponse({"ok": False, **_store}, status_code=503)
    # Cycle 14A: surface allocator corruption as a NON-FATAL signal. Unlike the topology store,
    # a corrupt allocator is fail-SAFE (live encoder streams keep running), so it must NOT fail
    # readiness — but it IS a degraded state (a corrupt allocator silently reads as "no desired
    # active streams"), so we expose `allocator_state` in the body for operator visibility.
    from app.services import mountpoint_allocator as _alloc
    _alloc_status = _alloc.allocator_corruption_status()
    try:
        janus.janus_summary(settings.janus_mount_id)
        return JSONResponse(
            {"ok": True, "janus_url": settings.janus_url, **_alloc_status}, status_code=200)
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": str(exc)[:120], "janus_url": settings.janus_url,
             **_alloc_status},
            status_code=503,
        )


@router.get(
    "/healthz",
    response_model=HealthResponse,
    summary="Service health probe (deep check)",
)
def healthz() -> HealthResponse:
    from app.services import system_mode as smode

    settings = get_settings()
    details: Dict[str, Any] = {}

    janus_ok = False
    stream_ok = False
    try:
        summary = janus.janus_summary(settings.janus_mount_id)
        janus_ok = True
        age = summary.get("video_age_ms")
        stream_ok = age is not None and isinstance(age, (int, float)) and age <= settings.watchdog_stale_ms
        details["video_age_ms"] = age
        details["mountpoint_id"] = summary.get("mountpoint_id")
    except Exception as exc:
        details["janus_error"] = str(exc)

    mode = smode.current_mode()
    details["mode_info"] = smode.mode_info()
    overall = janus_ok and stream_ok and mode != smode.SystemMode.SAFE

    return HealthResponse(
        ok=overall,
        mode=mode.value,
        janus_reachable=janus_ok,
        stream_active=stream_ok,
        details=details,
    )


@router.get("/health/stream", summary="End-to-end stream health (media-level)")
def health_stream() -> JSONResponse:
    from app.services import system_mode as smode

    settings = get_settings()
    checks: Dict[str, Any] = {}

    # 1. RTP ingest freshness
    rtp_fresh = False
    try:
        summary = janus.janus_summary(settings.janus_mount_id)
        video_age = summary.get("video_age_ms")
        rtp_fresh = (
            video_age is not None
            and isinstance(video_age, (int, float))
            and video_age <= settings.watchdog_stale_ms
        )
        checks["rtp_ingest"] = {"ok": rtp_fresh, "video_age_ms": video_age, "threshold_ms": settings.watchdog_stale_ms}
    except Exception as exc:
        checks["rtp_ingest"] = {"ok": False, "error": str(exc)}

    # 2. Client telemetry
    client_reporting = False
    try:
        from prometheus_client import REGISTRY

        # A3: client_* gauges are now labelled by camera, so get_sample_value()
        # with no labels returns None. Aggregate across cameras: total frames
        # (sum) and worst-case packet loss (max).
        def _agg(metric_name: str, reduce):
            vals = [
                s.value
                for m in REGISTRY.collect()
                for s in m.samples
                if s.name == metric_name
            ]
            return reduce(vals) if vals else 0

        frames = _agg("camstack_client_frames_decoded_total", sum)
        loss = _agg("camstack_client_packet_loss_ratio", max)
        client_reporting = frames > 0
        checks["client_telemetry"] = {
            "ok": client_reporting,
            "frames_decoded": frames,
            "packet_loss_ratio": round(loss, 4) if loss else 0,
            "note": "no client connected yet" if not client_reporting else None,
        }
    except Exception as exc:
        logging.warning("client telemetry metrics unavailable: %s", exc)
        checks["client_telemetry"] = {"ok": False, "note": "metrics unavailable"}

    # 3. System mode
    mode = smode.current_mode()
    mode_ok = mode not in (smode.SystemMode.SAFE,)
    checks["system_mode"] = {"ok": mode_ok, "mode": mode.value}

    # 4. Recovery ladder
    try:
        from app.services.recovery_ladder import get_ladder
        ladder = get_ladder()
        level = ladder.status()["current_level"] if ladder else 0
        checks["recovery_ladder"] = {"ok": level <= 2, "level": level}
    except Exception as exc:
        logging.warning("recovery ladder status unavailable: %s", exc)
        checks["recovery_ladder"] = {"ok": True, "level": 0}

    # 5. TURN server probe — STUN binding + TURN allocation (NOT just TCP connect).
    # Was a TCP-connect check — false positive if TCP open but STUN/Allocate fail.
    # Evidence level: tools_available + stun_ok + turn_alloc_ok.
    #
    # P1-NET-001: prefer HMAC ephemeral creds from TURN_SHARED_SECRET (same path
    # browsers use via /client-config) over static turn_user/turn_pass. Production
    # uses coturn use-auth-secret — static creds always fail with 401.
    try:
        from app.services.turn_probe import probe_summary
        from app.services.nat_config import load_nat_config
        from app.services.turn_credentials import generate_turn_credentials
        turn_host = settings.turn_host if hasattr(settings, "turn_host") else None
        turn_port = settings.turn_port if hasattr(settings, "turn_port") else 3478

        turn_user: Optional[str] = None
        turn_password: Optional[str] = None
        cred_source = "unset"
        if settings.turn_shared_secret:
            nat_cfg = load_nat_config()
            # Short TTL — probe is throw-away, not worth letting it linger.
            turn_user, turn_password = generate_turn_credentials(
                shared_secret=settings.turn_shared_secret,
                user=getattr(nat_cfg, "turn_user", None) or "probe",
                ttl=300,
            )
            cred_source = "shared_secret_hmac"
        elif getattr(settings, "turn_pass", None):
            turn_user = getattr(settings, "turn_user", None)
            turn_password = settings.turn_pass
            cred_source = "static"

        if turn_host:
            result = probe_summary(
                turn_host=turn_host,
                turn_port=int(turn_port),
                turn_user=turn_user,
                turn_password=turn_password,
            )
            result["cred_source"] = cred_source
            checks["turn_server"] = result
        else:
            checks["turn_server"] = {"ok": True, "note": "no TURN configured"}
    except Exception as exc:
        checks["turn_server"] = {"ok": False, "error": str(exc)}

    stream_usable = rtp_fresh and mode_ok
    return JSONResponse(
        status_code=200 if stream_usable else 503,
        content={"stream_usable": stream_usable, "checks": checks},
    )


# ── Full system status ──

@router.get("/status", summary="Full system status snapshot", dependencies=[Depends(require_admin), Depends(require_admin_rate_limit)])
def system_status() -> JSONResponse:
    import time as _time
    from app.services import system_mode as smode
    from app.services.recovery_ladder import get_ladder

    settings = get_settings()

    health_data: Dict[str, Any] = {}
    try:
        h = healthz()
        health_data = {"ok": h.ok, "mode": h.mode, "janus_reachable": h.janus_reachable, "stream_active": h.stream_active}
    except Exception as exc:
        health_data = {"ok": False, "error": str(exc)}

    ladder_data: Dict[str, Any] = {}
    try:
        ladder = get_ladder()
        ladder_data = ladder.status() if ladder else {"current_level": 0}
    except Exception:
        logging.debug("recovery ladder status unavailable", exc_info=True)
        ladder_data = {"current_level": 0}

    svc: Dict[str, Any] = {}
    try:
        svc = systemd_brief(settings.service_name)
    except Exception:
        logging.debug("systemd_brief unavailable", exc_info=True)
        svc = {"active": False, "since": None, "restarts": 0}

    cfg = {
        "camera_type": settings.camera_type,
        "janus_mount_id": settings.janus_mount_id,
        "watchdog_enabled": settings.watchdog_enabled,
        "snapshot_watchdog_enabled": settings.snapshot_watchdog_enabled,
        "watchdog_interval_sec": settings.watchdog_interval_sec,
        "watchdog_stale_ms": settings.watchdog_stale_ms,
        "ice_policy": settings.ice_policy,
    }

    return JSONResponse(content={
        "timestamp": _time.time(),
        "camera_type": settings.camera_type,
        "health": health_data,
        "mode": smode.mode_info(),
        "recovery_ladder": ladder_data,
        "service": svc,
        "settings": cfg,
    })


# ── Relay proxy ──

@router.get("/relay/time", summary="Relay server time for clock-sync")
async def relay_time():
    try:
        return await relay_proxy.relay_get("time")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"relay unreachable: {e}") from e


@router.get("/relay/pong", summary="Last joystick ping/pong result")
async def relay_pong():
    try:
        return await relay_proxy.relay_get("pong")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"relay unreachable: {e}") from e


# ── Service restart ──

@router.post(
    "/action/restart",
    response_model=ActionResponse,
    dependencies=[Depends(require_api_key)],
    summary="Restart the camera systemd service",
)
def restart_service() -> ActionResponse:
    service_restart()
    return ActionResponse(ok=True, message="service restarted")


# ── API root + favicon ──

@router.get(
    "/api/v1",
    response_model=HealthResponse,
    summary="API root",
)
def camera_api_root() -> HealthResponse:
    return HealthResponse(ok=True)


@router.get(
    "/api/v1/sensor_types",
    summary="List registered sensor types (built-in + plugins)",
)
def list_sensor_types_endpoint() -> Dict[str, Any]:
    """Sprint B5: introspection endpoint that shows registered sensor types.

    Useful for dashboard population, debug ("did my plugin load?"), and
    integration tests.
    """
    try:
        from app.services.sensor_registry import list_sensor_types
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"registry unavailable: {exc}") from exc
    types = []
    for t in list_sensor_types():
        types.append({
            "key": t.key,
            "label": t.label,
            "encoder_family": t.encoder_family,
            "encoder_instance_pattern": t.encoder_instance_pattern,
            "requires_producer": t.requires_producer,
            "is_dynamic_mountpoint": t.is_dynamic_mountpoint,
            "defaults": {
                "pix_fmt": t.default_pix_fmt,
                "width": t.default_width,
                "height": t.default_height,
                "fps": t.default_fps,
                "bitrate_kbps": t.default_bitrate_kbps,
            },
        })
    return {"count": len(types), "types": types}


@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    settings = get_settings()
    path = Path(settings.static_dir) / "favicon.ico"
    try:
        if path.is_file():
            return FileResponse(str(path), headers={"Cache-Control": "public, max-age=86400"})
    except Exception as exc:
        logging.warning("favicon not accessible: %s", exc)
    return Response(status_code=204)

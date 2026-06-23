"""Static asset and HTML template serving routes.

Serves Janus JS library, streamer, gamepad driver, player framework scripts,
and rendered HTML views (color_view, depth_view, ir_view).

Uses Jinja2 directly (not Starlette TemplateResponse) for version-proof
rendering with autoescape enabled.
"""
from __future__ import annotations

import json
import logging  # noqa: F401
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response  # noqa: F401

from app.core.http_prefix import _api_prefix_from_request
from app.core.settings import get_settings
from app.core.viewer_auth import require_viewer

router = APIRouter(tags=["templates"])

# Boot-time constant — FastAPI route paths must be static at decoration time.
_CAM_TYPE = get_settings().camera_type

# Jinja2 env with autoescape — used directly, bypasses Starlette wrapper
# to avoid TemplateResponse API differences across Starlette versions.
_jinja_env = Environment(
    loader=FileSystemLoader(str(get_settings().templates_dir)),
    autoescape=select_autoescape(["html", "htm"]),
)


def _serve_template_file(filename: str, media_type: str = "application/javascript") -> FileResponse:
    """Serve a single file from the templates directory."""
    settings = get_settings()
    path = Path(settings.templates_dir) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found")
    return FileResponse(str(path), media_type=media_type)


def _render_jinja(template_name: str, **ctx) -> HTMLResponse:
    """Render a Jinja2 template and return an HTMLResponse."""
    tmpl = _jinja_env.get_template(template_name)
    return HTMLResponse(tmpl.render(**ctx))


# _api_prefix_from_request moved to app/core/http_prefix.py (Phase 2B-5) — imported above.


def _render_template_response(filename: str, request: Request) -> HTMLResponse:
    """Render color/depth view template.

    Sprint B1: when stack_default_joystick_mode != 'off', serve the robot
    wrapper variant from templates/robot_overlay/ which includes joystick
    scripts. Generic stack mode serves templates/<filename> directly without
    joystick scripts. Browser URL stays the same — server picks the template.
    """
    settings = get_settings()
    joystick_mode = settings.stack_default_joystick_mode

    # Robot wrapper dispatch: if operator opted into joystick AND robot
    # overlay template exists, render that instead of generic.
    if joystick_mode != "off":
        robot_path = Path(settings.templates_dir) / "robot_overlay" / filename
        if robot_path.exists():
            target_template = f"robot_overlay/{filename}"
        else:
            target_template = filename  # robot wrapper missing — fall back to generic
    else:
        target_template = filename

    html_path = Path(settings.templates_dir) / target_template
    if not html_path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found")
    style_nonce = getattr(request.state, "style_nonce", "")
    return _render_jinja(
        target_template,
        cam_type=settings.camera_type,
        joystick_mode=joystick_mode,
        stream_id=settings.janus_color_stream_id,
        stream_name="RealSense RGB",
        depth_features_script=False,
        style_nonce=style_nonce,
        api_prefix=_api_prefix_from_request(request),
    )


def _render_color_view_variant(
    stream_id: int,
    stream_name: str,
    request: Request,
    joystick: bool = True,
    depth_features: bool = False,
) -> HTMLResponse:
    settings = get_settings()
    html_path = Path(settings.templates_dir) / "color_view.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="color_view.html not found")
    style_nonce = getattr(request.state, "style_nonce", "")
    return _render_jinja(
        "color_view.html",
        cam_type=settings.camera_type,
        stream_id=stream_id,
        stream_name=stream_name,
        joystick_mode="always" if joystick else "off",
        depth_features_script=depth_features,
        style_nonce=style_nonce,
        api_prefix=_api_prefix_from_request(request),
    )


# ── Janus JS library ──

@router.get("/janus.js", include_in_schema=False, response_model=None)
def janus_js():
    # Self-hosted only — FAIL CLOSED (review P0-2). No CDN fallback: an offline/edge
    # gateway must never reach out to the internet to serve a script, and a missing
    # local janus.js is a deploy error to surface, not silently paper over with a CDN.
    settings = get_settings()
    janus_path = Path(settings.templates_dir) / "janus.js"
    if janus_path.exists():
        return FileResponse(str(janus_path), media_type="application/javascript")
    raise HTTPException(status_code=503, detail="janus.js missing from the deployment (no CDN fallback)")


# ── JS assets ──

@router.get("/streamer.js", include_in_schema=False)
def streaming_js() -> FileResponse:
    return _serve_template_file("streamer.js")


@router.get("/depth_features.js", include_in_schema=False)
def depth_features_js() -> FileResponse:
    return _serve_template_file("depth_features.js")


# P0-SEC-001: viewer auth bootstrap script. Loaded first in HTML head — wraps
# fetch/EventSource/WebSocket so subsequent player code transparently sends
# the viewer token. NOT viewer-gated itself (must be reachable for token
# discovery). Pure client-side logic; serving it leaks no secret.
@router.get("/viewer_auth_bootstrap.js", include_in_schema=False)
def viewer_auth_bootstrap_js() -> FileResponse:
    return _serve_template_file("viewer_auth_bootstrap.js")


# Sprint B1 — robot wrapper static files: served under /robot_overlay/ prefix
# from new templates/robot_overlay/ directory. Legacy /gripper_reticle.js,
# /gamepaddriver.js, /player/app/joystick_service.js aliases preserved for
# backward compat — they resolve to the new location.

@router.get("/robot_overlay/{filename:path}", include_in_schema=False)
def robot_overlay_js(filename: str) -> FileResponse:
    """Serve files from templates/robot_overlay/ directory."""
    if ".." in filename or filename.startswith("/"):
        raise HTTPException(status_code=404, detail="Invalid path")
    settings = get_settings()
    file_path = Path(settings.templates_dir) / "robot_overlay" / filename
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"robot_overlay/{filename} not found")
    suffix = file_path.suffix.lower()
    media_type = {".js": "application/javascript", ".html": "text/html",
                  ".json": "application/json", ".css": "text/css"}.get(suffix, "application/octet-stream")
    return FileResponse(str(file_path), media_type=media_type)


# Backward-compat aliases: existing prod URLs keep working.
@router.get("/gripper_reticle.js", include_in_schema=False)
def gripper_reticle_js() -> FileResponse:
    settings = get_settings()
    new_path = Path(settings.templates_dir) / "robot_overlay" / "gripper_reticle.js"
    if new_path.is_file():
        return FileResponse(str(new_path), media_type="application/javascript")
    return _serve_template_file("gripper_reticle.js")


@router.get("/gamepaddriver.js", include_in_schema=False)
def gamepad_js() -> FileResponse:
    settings = get_settings()
    new_path = Path(settings.templates_dir) / "robot_overlay" / "gamepaddriver.js"
    if new_path.is_file():
        return FileResponse(str(new_path), media_type="application/javascript")
    return _serve_template_file("gamepaddriver.js")


# Backward-compat alias: legacy /player/app/joystick_service.js → new robot_overlay/.
@router.get("/player/app/joystick_service.js", include_in_schema=False)
def joystick_service_js_legacy() -> FileResponse:
    settings = get_settings()
    new_path = Path(settings.templates_dir) / "robot_overlay" / "joystick_service.js"
    if new_path.is_file():
        return FileResponse(str(new_path), media_type="application/javascript")
    legacy = Path(settings.templates_dir) / "player" / "app" / "joystick_service.js"
    if legacy.is_file():
        return FileResponse(str(legacy), media_type="application/javascript")
    raise HTTPException(status_code=404, detail="joystick_service.js not found")


@router.get("/gamepad_config.json", include_in_schema=False)
def gamepad_config() -> JSONResponse:
    settings = get_settings()
    cfg_path = Path(settings.templates_dir) / "gamepad_config.json"
    if not cfg_path.exists():
        raise HTTPException(status_code=404, detail="gamepad_config.json not found")
    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return JSONResponse(content=data)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Invalid JSON in gamepad_config.json: {e}")


# Note: /static/* served by StaticFiles mount in app/core/app.py (system-wide).
# Legacy /api/v1/{cam_type}/static/* wrapper removed Sprint X4 — caller must
# use absolute /static/<path>.


# ── Player framework scripts ──

def _player_script_response(path: str) -> FileResponse:
    if ".." in path or path.startswith("/"):
        raise HTTPException(status_code=404, detail="Invalid path")
    settings = get_settings()
    base = Path(settings.templates_dir) / "player"
    file_path = (base / path).resolve()
    if not file_path.is_file() or not file_path.is_relative_to(base):
        raise HTTPException(status_code=404, detail=f"Player script not found: {path}")
    return FileResponse(
        str(file_path),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get("/player/{path:path}", include_in_schema=False)
def player_script(path: str) -> FileResponse:
    return _player_script_response(path)


# ── HTML views ──

# Sprint X4 — color_view.html keeps legacy {cam_type}-prefixed routes per
# CONTRACT.md exception (operator-facing URL preserved):
#   /api/v1/color_camera/color_view.html
#   /api/v1/depth_camera/color_view.html
# Plus system-wide alias /color_view.html for new clients.
@router.get(f"/api/v1/{_CAM_TYPE}/color_view.html", include_in_schema=False)
@router.get("/color_view.html", include_in_schema=False)
def color_view(request: Request) -> HTMLResponse:
    return _render_template_response("color_view.html", request)


# Sprint AB2: operator-facing camera configuration page (resolution, fps,
# rotation, bitrate, preset). Authentication is enforced at API layer —
# this page just serves HTML/JS. JS prompts for X-Admin-Token + persists
# in sessionStorage. POSTs /config which triggers encoder-admin restart.
@router.get("/camera_config.html", include_in_schema=False)
def camera_config_page(request: Request) -> HTMLResponse:
    settings = get_settings()
    style_nonce = getattr(request.state, "style_nonce", "")
    cfg_template = Path(settings.templates_dir) / "camera_config.html"
    if not cfg_template.exists():
        raise HTTPException(status_code=404, detail="camera_config.html not found")
    return _render_jinja(
        "camera_config.html",
        cam_type=settings.camera_type,
        style_nonce=style_nonce,
    )


# Admin config page (Phase 1) — secret rotation + NAT mapping + apply.
# HTML served here, API routes in app/routes/admin_config.py with require_admin.
@router.get("/admin_config.html", include_in_schema=False)
def admin_config_page(request: Request) -> HTMLResponse:
    settings = get_settings()
    style_nonce = getattr(request.state, "style_nonce", "")
    script_nonce = getattr(request.state, "script_nonce", "")
    tpl = Path(settings.templates_dir) / "admin_config.html"
    if not tpl.exists():
        raise HTTPException(status_code=404, detail="admin_config.html not found")
    return _render_jinja(
        "admin_config.html",
        cam_type=settings.camera_type,
        style_nonce=style_nonce,
        script_nonce=script_nonce,
    )


# Camera Hosts page — unified add-by-IP onboarding (local == remote).
# HTML served here; API in app/routes/stream_bindings.py (require_admin).
# gateway_lan_ip lets the add-form recognise the local gateway address and point
# the operator at the built-in cam10 host instead of minting a bogus remote node.
@router.get("/camera_hosts.html", include_in_schema=False)
def camera_hosts_page(request: Request) -> HTMLResponse:
    settings = get_settings()
    style_nonce = getattr(request.state, "style_nonce", "")
    script_nonce = getattr(request.state, "script_nonce", "")
    tpl = Path(settings.templates_dir) / "camera_hosts.html"
    if not tpl.exists():
        raise HTTPException(status_code=404, detail="camera_hosts.html not found")
    from app.services import node_provisioner
    return _render_jinja(
        "camera_hosts.html",
        cam_type=settings.camera_type,
        style_nonce=style_nonce,
        script_nonce=script_nonce,
        gateway_lan_ip=node_provisioner.GATEWAY_LAN_IP,
    )


# Gateway Operator Console (design-system ui kit) — the elevated fleet-ops console.
# Page is plain HTML (like camera_hosts.html); the data it reads (/api/v1/ui/fleet)
# and every action it triggers (/api/v1/admin/*) are admin-gated server-side.
# Assets are precompiled + self-hosted under /static/console (scripts/build_console.sh).
@router.get("/console.html", include_in_schema=False)          # canonical (console.your-domain.example/console.html)
@router.get("/operator_console.html", include_in_schema=False)  # alias
def operator_console_page(request: Request) -> HTMLResponse:
    settings = get_settings()
    style_nonce = getattr(request.state, "style_nonce", "")
    tpl = Path(settings.templates_dir) / "operator_console.html"
    if not tpl.exists():
        raise HTTPException(status_code=404, detail="operator_console.html not found")
    # cache-bust on asset rebuild: stamp with app.js mtime (best-effort).
    asset_v = "1"
    try:
        bundle = Path(settings.static_dir) / "console" / "app.js"
        asset_v = str(int(bundle.stat().st_mtime))
    except Exception:
        pass
    return _render_jinja(
        "operator_console.html",
        style_nonce=style_nonce,
        asset_v=asset_v,
    )


# Mountpoint preview (Phase 2) — generic player for arbitrary mountpoint ID.
# Operator clicks "View" in operator_dashboard, opens this in a new tab.
# P0-SEC-001: viewer-gated, otherwise any LAN client can enumerate mountpoints
# (?token= in URL since this loads via window.open w/o ability to set headers).
@router.get("/preview/{mp_id}", include_in_schema=False, dependencies=[Depends(require_viewer)])
def preview_mountpoint(mp_id: int, request: Request) -> HTMLResponse:
    if mp_id < 1 or mp_id > 65535:
        raise HTTPException(status_code=400, detail="mp_id must be 1-65535")
    return _render_color_view_variant(
        stream_id=mp_id,
        stream_name=f"Mountpoint #{mp_id}",
        request=request,
        joystick=False,
        depth_features=False,
    )


# Operator dashboard (Phase 2) — unified services + mountpoints + audit view.
# HTML here, API routes in app/routes/admin_dashboard.py with require_admin.
@router.get("/multiview.html", include_in_schema=False)
def multiview_page(request: Request) -> HTMLResponse:
    """Sprint X4 Phase 5: split-screen viewer for 1/2/3/4 simultaneous streams.
    Each cell iframes /preview/{mp_id} so existing viewer auth applies."""
    settings = get_settings()
    style_nonce = getattr(request.state, "style_nonce", "")
    script_nonce = getattr(request.state, "script_nonce", "")
    tpl = Path(settings.templates_dir) / "multiview.html"
    if not tpl.exists():
        raise HTTPException(status_code=404, detail="multiview.html not found")
    return _render_jinja(
        "multiview.html",
        cam_type=settings.camera_type,
        style_nonce=style_nonce,
        script_nonce=script_nonce,
    )


@router.get("/console_legacy.html", include_in_schema=False)
def console_page(request: Request) -> HTMLResponse:
    """LEGACY Sprint-X4 SPA console (overview/streams/mountpoints/fdir/encoders/
    hardware/audit/admin/soak). Superseded at /console.html by the design-system
    operator console (operator_console_page); kept here for fallback/diagnostics."""
    settings = get_settings()
    style_nonce = getattr(request.state, "style_nonce", "")
    script_nonce = getattr(request.state, "script_nonce", "")
    tpl = Path(settings.templates_dir) / "console.html"
    if not tpl.exists():
        raise HTTPException(status_code=404, detail="console.html not found")
    return _render_jinja(
        "console.html",
        cam_type=settings.camera_type,
        style_nonce=style_nonce,
        script_nonce=script_nonce,
    )


@router.get("/soak.html", include_in_schema=False)
def soak_viewer_page(request: Request) -> HTMLResponse:
    """Operator-facing CSV viewer for scripts/soak_*.csv runs."""
    settings = get_settings()
    style_nonce = getattr(request.state, "style_nonce", "")
    script_nonce = getattr(request.state, "script_nonce", "")
    tpl = Path(settings.templates_dir) / "soak.html"
    if not tpl.exists():
        raise HTTPException(status_code=404, detail="soak.html not found")
    return _render_jinja(
        "soak.html",
        cam_type=settings.camera_type,
        style_nonce=style_nonce,
        script_nonce=script_nonce,
    )


@router.get("/operator_dashboard.html", include_in_schema=False)
def operator_dashboard_page() -> RedirectResponse:
    # Retired in favour of the unified console (overview/streams/mountpoints+CRUD/fdir/
    # encoders/hardware/audit/admin/settings/soak) — the single dashboard hub.
    return RedirectResponse(url="/console.html", status_code=307)


if _CAM_TYPE == "depth_camera":
    # Sprint X4 — depth_view.html keeps legacy {cam_type}-prefixed route per
    # CONTRACT.md exception (operator-facing URL preserved):
    #   /api/v1/depth_camera/depth_view.html
    # Plus system-wide alias /depth_view.html.
    @router.get(f"/api/v1/{_CAM_TYPE}/depth_view.html", include_in_schema=False)
    @router.get("/depth_view.html", include_in_schema=False)
    def depth_view(request: Request) -> HTMLResponse:
        settings = get_settings()
        style_nonce = getattr(request.state, "style_nonce", "")
        depth_template = Path(settings.templates_dir) / "depth_view.html"
        if depth_template.exists():
            return _render_jinja("depth_view.html", cam_type=settings.camera_type, style_nonce=style_nonce, stream_id=settings.janus_depth_stream_id, api_prefix=_api_prefix_from_request(request))
        return _render_color_view_variant(settings.janus_depth_stream_id, "RealSense Depth", request, joystick=False, depth_features=True)

    # Sprint X4 — ir_view.html dropped {cam_type} legacy decorator (not in
    # the 3 grandfathered URLs). System-wide /ir_view.html remains.
    @router.get("/ir_view.html", include_in_schema=False)
    def ir_view(request: Request) -> HTMLResponse:
        settings = get_settings()
        style_nonce = getattr(request.state, "style_nonce", "")
        ir_template = Path(settings.templates_dir) / "ir_view.html"
        if ir_template.exists():
            return _render_jinja("ir_view.html", cam_type=settings.camera_type, style_nonce=style_nonce, stream_id=settings.janus_ir_stream_id)
        return _render_color_view_variant(settings.janus_ir_stream_id, "RealSense IR", request, joystick=False)

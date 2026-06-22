from fastapi import FastAPI

from app.core.settings import get_settings
from app.routes import admin_config, admin_dashboard, camera, depth_events, device_camera, devices, fdir, janus, metrics, runtime_config, stream_bindings, system, telemetry, templates, ui_viewmodel


def register_routes(app: FastAPI) -> None:
    app.include_router(system.router)
    app.include_router(templates.router)
    app.include_router(camera.router)
    app.include_router(janus.router)
    app.include_router(fdir.router)
    app.include_router(metrics.router)
    app.include_router(telemetry.router)
    # admin config page (Phase 1) — secret rotation + jcfg re-render + restart
    app.include_router(admin_config.router)
    # admin dashboard (Phase 2) — services + mountpoints + audit log aggregate
    app.include_router(admin_dashboard.router)
    # B1: runtime-config control plane — read-only effective + dry-run validate
    app.include_router(runtime_config.router)
    # G6: gateway topology API — nodes + stream-bindings CRUD (+ ensure-janus)
    app.include_router(stream_bindings.router)
    # Operator console (design_system ui kit) read-only view-model
    app.include_router(ui_viewmodel.router)
    # Sprint X1: parameterized per-(serial,sensor) URLs + device dashboard.
    # Backcompat: legacy /api/v1/color_camera/camera_config.html etc remain
    # under camera.router/templates.router — these new routes are additive.
    app.include_router(device_camera.router)
    app.include_router(devices.router)
    # Sprint X3.2 — SSE for depth_query responses (textroom round-trip)
    app.include_router(depth_events.router)

    # Depth routes: depth queries + realsense_mux proxy (depth_camera node only)
    # depth_map/load is also registered here (works for both camera types)
    from app.routes import depth
    app.include_router(depth.router)

    if get_settings().camera_type == "color_camera":
        from app.routes import depth_proxy
        app.include_router(depth_proxy.router)


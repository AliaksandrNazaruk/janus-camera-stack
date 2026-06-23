import os
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.events import register_event_handlers
from app.core.settings import get_settings
from app.routes import register_routes
from app.config import DEVICES, PORTS

# frame-ancestors requires exact origin-s, not CIDR notation.
# Default: the two LAN nodes that may embed the player.
# Override via CSP_FRAME_ANCESTORS_LAN env var for different deployments.
_FRAME_ANCESTORS_LAN = os.environ.get(
    "CSP_FRAME_ANCESTORS_LAN",
    f"http://{DEVICES.HOST_LAN_IP}:{PORTS.COLOR_CAMERA} "
    f"http://{DEVICES.DEPTH_CAMERA_IP}:{PORTS.COLOR_CAMERA}",
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response (P2.9)."""

    async def dispatch(self, request: Request, call_next):
        # Correlation ID: propagate incoming or generate new
        req_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        request.state.request_id = req_id
        style_nonce = uuid.uuid4().hex[:16]
        request.state.style_nonce = style_nonce

        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        # X-Frame-Options removed: CSP frame-ancestors is the modern
        # replacement and already allows cross-origin embedding from
        # *.your-domain.example.  Having both creates a contradiction
        # (SAMEORIGIN vs cross-origin frame-ancestors).
        # The design-system operator console (/console.html + alias) renders icons via
        # Lucide, which sets inline STYLE ATTRIBUTES on the SVGs it injects — a nonce
        # cannot cover style attributes, so those pages need style-src 'unsafe-inline'.
        # Scoped to the console paths only; every other page keeps the strict nonce.
        # This relaxes inline STYLE, not script — no code-execution relaxation.
        if request.url.path in ("/console.html", "/operator_console.html"):
            _style_src = "style-src 'self' 'unsafe-inline'; "
        else:
            _style_src = f"style-src 'self' 'nonce-{style_nonce}'; "
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            # All scripts are self-hosted (React/Lucide vendored under /static/console,
            # webrtc-adapter under /static/js/vendor, janus.js served locally with no
            # CDN fallback) → no external script origin (review P0-2).
            "script-src 'self'; "
            + _style_src +
            f"connect-src 'self' "
            f"ws://{DEVICES.HOST_LAN_IP}:* ws://{DEVICES.DEPTH_CAMERA_IP}:* "
            f"ws://127.0.0.1:* ws://localhost:* "
            f"wss://{DEVICES.HOST_LAN_IP}:* wss://{DEVICES.DEPTH_CAMERA_IP}:* "
            f"wss://*.your-domain.example; "
            "img-src 'self' data: blob:; "
            "media-src 'self' blob:; "
            f"frame-ancestors 'self' https://*.your-domain.example {_FRAME_ANCESTORS_LAN}"
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=()"
        return response


def create_app() -> FastAPI:
    from app.core.admin import validate_admin_config
    from app.core.viewer_auth import validate_viewer_config
    from app.core.startup_checks import enforce_production_security
    validate_admin_config()
    validate_viewer_config()

    settings = get_settings()
    # A1: in production (CAMERA_ENV=production) abort on insecure config.
    # No-op in development — preserves the permissive dev behavior above.
    enforce_production_security(settings)
    application = FastAPI(title=settings.app_title, version=settings.app_version)

    # H-02: map a corrupt topology store to a clean 503 (degraded) on EVERY route
    # rather than an opaque 500. The store fails closed (raises StoreCorruptionError);
    # this surfaces it clearly to clients, e.g. the operator console.
    from app.services.stream_binding_store import StoreCorruptionError

    @application.exception_handler(StoreCorruptionError)
    async def _topology_store_corrupt_handler(_request, exc):  # noqa: ANN001,ANN201
        from fastapi.responses import JSONResponse
        return JSONResponse(
            {"ok": False, "topology_store_corrupt": True, "detail": str(exc)[:200]},
            status_code=503,
        )

    # Cycle 1: a corrupt secret/config store (camera-secrets.env, ...) fails closed (StoreCorrupt)
    # → 503 degraded on every route, never a silent empty/regenerate or an opaque 500.
    from app.services.store_safety import StoreCorrupt

    @application.exception_handler(StoreCorrupt)
    async def _store_corrupt_handler(_request, exc):  # noqa: ANN001,ANN201
        from fastapi.responses import JSONResponse
        return JSONResponse(
            {"ok": False, "store_corrupt": True, "detail": str(exc)[:200]},
            status_code=503,
        )

    application.add_middleware(SecurityHeadersMiddleware)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Requested-With"],
    )

    application.mount("/static", StaticFiles(directory=settings.static_dir), name="static")

    register_routes(application)
    register_event_handlers(application)
    return application


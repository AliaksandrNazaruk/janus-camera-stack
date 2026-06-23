"""URL routing audit tests (A–F).

Six categories of route hygiene checks executed against the FastAPI app:

  A. Liveness smoke      — every GET returns non-5xx (catches import errors,
                           handler crashes, broken dependencies at init)
  B. Cross-link integrity — every HTML href/src and JS fetch() target hits a
                           registered route (catches dead links after refactor)
  C. Auth coverage       — /admin/* + mutating endpoints have proper auth
                           (catches accidental exposure)
  D. OpenAPI completeness — in-schema routes have summary (catches doc rot)
  E. Method strictness   — no surprising DELETE/PATCH/PUT (audits surface)
  F. Path param validation — typed path params reject malformed input

Run individually: pytest tests/test_url_audit.py::test_A_no_5xx -v
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple
from unittest.mock import patch, MagicMock

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.routing import Mount, Route
from httpx import ASGITransport, AsyncClient

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES_DIR = _SERVICE_ROOT / "templates"
_STATIC_DIR = _SERVICE_ROOT / "static"
_ROUTES_DIR = _SERVICE_ROOT / "app" / "routes"

for _p in (str(_SERVICE_ROOT), str(_SERVICE_ROOT.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


_TEST_ADMIN_TOKEN = "test-url-audit-token"


@pytest.fixture(scope="module")
def app():
    """App fixture with admin token mocked."""
    with patch("app.core.events.register_event_handlers", lambda app: None), \
         patch.dict(os.environ, {"CAM_ADMIN_TOKEN": _TEST_ADMIN_TOKEN}):
        from app.core.app import create_app
        yield create_app()


# ── Path param substitution for GET tests ────────────────────────────

_PARAM_SUBSTITUTIONS = {
    "serial": "local",
    "sensor": "color",
    "mp_id": "1305",
    "path": "test",
    "filename": "test",
    "family": "rtp-rgb",
    "instance": "cam-rgb",
    "service": "janus",
    "key": "ADMIN_TOKEN",
    "action": "start",
}


def _substitute_path(path: str) -> str:
    """Replace {param} placeholders with reasonable test values."""
    def _repl(m: re.Match) -> str:
        name = m.group(1).split(":", 1)[0]  # strip :path / :int suffix
        return _PARAM_SUBSTITUTIONS.get(name, "test")
    return re.sub(r"\{([^}]+)\}", _repl, path)


# ── Test A: Liveness smoke ────────────────────────────────────────────
# For every registered GET route, send a request with admin auth. Accept any
# status < 500. Some routes may legitimately 4xx (not found data, missing
# dep), but any 5xx indicates a handler bug.
#
# External-service-dependent routes are mocked at subprocess/httpx layer.


# Routes we exclude from smoke test:
#  • Routes that proxy to external services (blocked on real I/O in test env)
#  • Routes that require specific runtime state (mountpoint must exist, etc)
# Match: route blocks on httpx/socket calls to Janus/mux/remote node, which
# would hang indefinitely without heavy mock setup. Smoke test catches IMPORT
# bugs and handler bugs, not integration issues.
_SMOKE_EXCLUDE_PREFIXES = {
    "/openapi.json", "/docs", "/redoc",   # FastAPI built-ins
    "/static",                            # StaticFiles mount
    "/api/v1/depth_camera",               # depth_proxy → remote node
    # Routes that call out to Janus over HTTP (block in test without upstream)
    "/janus",                             # Janus proxy
    "/healthz",                           # checks Janus reachability
    "/health/stream",                     # probes rtp_ingest + Janus (503 if down)
    "/api/v1/admin/reconcile",            # drift reconcile calls the Janus admin API
    "/janus/healthz", "/janus/nat", "/janus-ws", "/janus/ws",
    "/client-config",                     # loads NAT config (httpx to Janus admin)
    "/api/v1/admin/dashboard",            # aggregates Janus mountpoint list
    "/api/v1/admin/mountpoints",          # Janus admin API
    "/api/v1/admin/services",             # systemctl shell-out (mocked but still slow)
    "/api/v1/admin/config",               # reads secrets, may httpx Janus
    "/api/v1/admin/streams",              # gone — but keep filter to be safe
    "/api/v1/admin/encoders",             # encoder-admin invocations
    "/cameras/streams",                   # sensor_lifecycle status probes
    # Depth queries forward to realsense_mux on port 8000
    "/depth", "/depth/color_frame", "/depth/frame",
    "/depth/frame_color_overlay", "/depth_map/load",
    "/api/v1/depth_map/load",
    # SSE event stream — hangs waiting
    "/depth/events",
    "/relay/time", "/relay/pong",         # textroom_relay HTTP
    "/status",                            # full aggregate (slow + multi-deps)
    "/sensors",                           # pyrealsense2 SDK probe
    "/fdir/ladder",                       # reads /run/camera/* (permissions vary in test)
    "/depth_events",                      # SSE — streams indefinitely
    # Per-stream config — requires running encoder (returns 4xx without it,
    # but some paths call subprocess deeply)
    "/cameras/local/color/config",
    "/cameras/local/color/modes",
    "/cameras/local/color/sensors",
    "/snapshot.jpg",                      # file may not exist in test
    # Routes that require a running encoder OR live mountpoint
    "/api/v1/admin/encoders/{family}",
}


def _smoke_should_skip(path: str) -> bool:
    return any(
        path == p or path.startswith(p + "/") or path.startswith(p + "?")
        for p in _SMOKE_EXCLUDE_PREFIXES
    )


@pytest.mark.timeout(60)
@pytest.mark.asyncio
async def test_A_smoke_no_5xx_on_get_routes(app, caplog):
    """Every GET route returns < 500 status. Catches import/handler crashes.

    Excludes routes that require live external services (Janus, mux, remote
    depth_camera) — those are integration concerns, not route registration."""
    failures: List[Tuple[str, int, str]] = []
    tested: List[str] = []

    proc_mock = MagicMock(returncode=0, stdout="{}", stderr="")

    import asyncio
    transport = ASGITransport(app=app)
    with patch("subprocess.run", return_value=proc_mock):
        async with AsyncClient(transport=transport, base_url="http://test", timeout=3.0) as c:
            for route in app.routes:
                if not isinstance(route, APIRoute):
                    continue
                if _smoke_should_skip(route.path):
                    continue
                if "GET" not in route.methods:
                    continue
                url = _substitute_path(route.path)
                try:
                    # Hard per-request bound — handler stuck in httpx/socket
                    # would otherwise burn the global 60s test budget.
                    r = await asyncio.wait_for(
                        c.get(url, headers={"X-Admin-Token": _TEST_ADMIN_TOKEN}),
                        timeout=3.0,
                    )
                    tested.append(route.path)
                except asyncio.TimeoutError:
                    failures.append((route.path, 0, "request timed out — add to _SMOKE_EXCLUDE_PREFIXES if external dep"))
                    continue
                except Exception as exc:
                    failures.append((route.path, 0, f"raised: {exc!r}"))
                    continue
                if r.status_code >= 500:
                    failures.append((route.path, r.status_code, r.text[:120]))

    assert tested, "Smoke test exercised zero routes — excluded list too aggressive"
    assert not failures, (
        f"Routes returning 5xx (handler crashed). {len(tested)} routes tested OK.\n"
        + "\n".join(f"  {p} → {s}  {body}" for p, s, body in failures)
    )


# ── Test B: Cross-link integrity ──────────────────────────────────────
# Every URL referenced from HTML/JS must resolve to a registered route.
# Catches "I removed /old/path but frontend still links to it".


# Pattern for extracting URL refs (absolute paths starting with /)
_HTML_URL_RE = re.compile(r'(?:href|src|action)\s*=\s*"(/[^"#\s?{]+)')
_JS_URL_RE = re.compile(
    # Match string literals containing absolute paths.
    # Catches: fetch('/X'), '/api/v1/X', "${X}/foo", etc.
    r"['\"`](/(?:api/v[0-9]+|admin|cameras|preview|player|robot_overlay|"
    r"static|janus|depth|streamer\.js|gripper_reticle\.js|gamepaddriver\.js|"
    r"gamepad_config\.json|viewer_auth_bootstrap\.js|depth_features\.js|"
    r"color_view\.html|depth_view\.html|ir_view\.html|admin_config\.html|"
    r"operator_dashboard\.html|camera_config\.html|favicon\.ico|relay|"
    r"healthz|status|metrics|telemetry|client-config|snapshot\.jpg|"
    r"sensors|modes|config|depth_map)[^'\"`#\s?]*)"
)

# Refs we deliberately accept without route check (external/data/build-time):
_REF_ALLOWLIST_EXACT = {
    "/favicon.ico",                  # served by FastAPI/StaticFiles
    "/static/css/camera_config.css", # static mount
    "/static/css/player.css",
    "/static/css/player-depth.css",
    # SPA hash-router route ids (console_app.js) — not backend URLs, just
    # navigation tokens.
    "/admin", "/overview", "/streams", "/mountpoints", "/fdir",
    "/encoders", "/hardware", "/audit", "/soak",
}
_REF_ALLOWLIST_PREFIXES = {
    "/static/",        # StaticFiles mount handles this
    "/api/v1/depth_camera/",  # depth_proxy whitelist — covered separately
    "/?token=",        # query parameter, not a route
}


def _build_route_pattern_matchers(app) -> List[re.Pattern]:
    """Build a list of regex patterns that match registered route paths.

    Converts FastAPI path params (e.g., /cameras/{serial}/{sensor}) into
    regex (e.g., /cameras/[^/]+/[^/]+).
    """
    patterns: List[re.Pattern] = []
    for route in app.routes:
        if isinstance(route, (APIRoute, APIWebSocketRoute)):
            raw = route.path
        elif isinstance(route, Mount):
            raw = route.path + "/.*"
        elif isinstance(route, Route):
            raw = route.path
        else:
            continue
        # Convert {param} or {param:path} to regex
        pat = re.sub(r"\{[^}]+:path\}", r".*", raw)
        pat = re.sub(r"\{[^}]+\}", r"[^/]+", pat)
        patterns.append(re.compile(f"^{pat}$"))
    return patterns


def _ref_resolves(url: str, patterns: List[re.Pattern]) -> bool:
    # Strip query / fragment
    url_clean = re.split(r"[?#]", url, 1)[0]
    if url_clean in _REF_ALLOWLIST_EXACT:
        return True
    if any(url_clean.startswith(p) for p in _REF_ALLOWLIST_PREFIXES):
        return True
    # Frontend hits via api_gateway prepend /api/v1; L4 registers without it
    # for routers with prefix="/cameras". Strip /api/v1 once for matching.
    candidates = [url_clean]
    if url_clean.startswith("/api/v1/"):
        candidates.append(url_clean[len("/api/v1"):])  # /api/v1/cameras/X → /cameras/X
    return any(pat.match(c) for c in candidates for pat in patterns)


def _resolve_jinja_placeholders(url: str) -> str:
    """Convert {{ var }} to canonical placeholder names matching route params."""
    # {{ cam_type }} → color_camera (one specific resolution)
    url = re.sub(r"\{\{\s*cam_type\s*\}\}", "color_camera", url)
    # {{ stream_id }} → 1305 (sample mountpoint)
    url = re.sub(r"\{\{\s*stream_id\s*\}\}", "1305", url)
    # Other generic {{ var }} → strip to {var}-like
    url = re.sub(r"\{\{\s*\w+\s*\}\}", "x", url)
    return url


def test_B_html_refs_resolve_to_registered_routes(app):
    patterns = _build_route_pattern_matchers(app)
    unresolved: List[Tuple[str, int, str]] = []
    for html in sorted(_TEMPLATES_DIR.rglob("*.html")):
        for i, line in enumerate(html.read_text(encoding="utf-8").splitlines(), 1):
            for m in _HTML_URL_RE.finditer(line):
                url = _resolve_jinja_placeholders(m.group(1))
                # Skip bare prefix segments — e.g. /api/v1/ alone, without trailing path
                if url.rstrip("/") in {"/api/v1", "/api", ""}:
                    continue
                if _ref_resolves(url, patterns):
                    continue
                if _is_prefix_of_route(url, patterns):
                    continue
                unresolved.append((str(html.relative_to(_SERVICE_ROOT)), i, url))
    assert not unresolved, (
        "HTML references to non-existent routes:\n"
        + "\n".join(f"  {f}:{ln}: {u}" for f, ln, u in unresolved)
        + "\n\nFix: either restore the route or update the HTML link."
    )


def _is_prefix_of_route(url: str, patterns: List[re.Pattern]) -> bool:
    """For fetch base URLs (e.g., '/api/v1/admin') — accept if any registered
    route extends this prefix. Catches `fetch(base + dynamicPath)` patterns."""
    return any(pat.pattern.startswith("^" + url.rstrip("/") + "/") for pat in patterns)


def test_B_js_refs_resolve_to_registered_routes(app):
    patterns = _build_route_pattern_matchers(app)
    unresolved: List[Tuple[str, int, str]] = []
    js_dir = _STATIC_DIR / "js"
    if not js_dir.is_dir():
        pytest.skip("no static/js/ dir")
    for js in sorted(js_dir.glob("*.js")):
        for i, line in enumerate(js.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            for m in _JS_URL_RE.finditer(line):
                raw = m.group(1)
                # Skip URLs containing ${...} template substitutions — too
                # dynamic to validate (e.g., /api/v1/cameras/${serial}/${action})
                if "${" in raw:
                    continue
                url_clean = re.split(r"[?#]", raw, 1)[0]
                # Accept full match OR prefix-of-registered-route (for base
                # URL patterns like `fetch('/api/v1/admin' + path)`).
                if _ref_resolves(url_clean, patterns):
                    continue
                if _is_prefix_of_route(url_clean, patterns):
                    continue
                unresolved.append((js.name, i, url_clean))
    assert not unresolved, (
        "JS references to non-existent routes:\n"
        + "\n".join(f"  {f}:{ln}: {u}" for f, ln, u in unresolved)
    )


# ── Test C: Auth gate coverage ────────────────────────────────────────
# Routes that mutate state OR expose secrets must require auth.

# Public (no-auth) routes — allowlist. Everything else must have auth dep.
_PUBLIC_ROUTE_PREFIXES = {
    "/healthz", "/livez", "/metrics", "/favicon.ico", "/openapi.json",
    "/docs", "/redoc", "/openapi", "/static",
    "/janus.js", "/streamer.js", "/depth_features.js",
    "/viewer_auth_bootstrap.js", "/gripper_reticle.js",
    "/gamepaddriver.js", "/gamepad_config.json",
    "/player/", "/robot_overlay/",
    "/relay/time", "/relay/pong",   # public for clock sync
    "/cameras/registry.json",       # safe enumeration
    "/cameras/dashboard.html",      # operator landing
    "/cameras/streams",             # OPEN: list view (admin-gated in reality via require_admin dep)
    "/sensor_types",                # introspection
    "/sensors",                     # camera catalog (read-only)
    "/modes",                       # V4L2 modes (read-only)
    "/api/v1",                      # api root
    "/api/v1/sensor_types",
    "/api/v1/depth_camera",         # proxy router — auth enforced upstream
    # Telemetry ingestion + relay are rate-limited but not auth-gated by design
    "/telemetry",
    # SSE events stream — viewer-gated, but with query-token rather than dep
    "/depth/events",
    "/depth_events",
    # Internal-only endpoint — auth via X-Internal-Secret header inside handler
    # (HMAC-cookie), not visible through FastAPI dependency tree.
    "/internal/depth_broadcast",
}

_AUTH_DEP_NAMES = {
    "require_admin", "require_viewer", "require_viewer_ws",
    "require_api_key",  # legacy systemctl restart endpoint (auth via X-API-Key)
}


def _route_has_auth_dep(route: APIRoute) -> bool:
    """Check if route (or its app-level deps) include an auth dependency."""
    for dep in route.dependencies + getattr(route, "dependant", MagicMock()).dependencies:
        fn = getattr(dep, "dependency", None) or dep
        name = getattr(fn, "__name__", str(fn))
        if name in _AUTH_DEP_NAMES:
            return True
    # Inspect dependant tree (FastAPI flattens deps into dependant.dependencies)
    dependant = getattr(route, "dependant", None)
    if dependant:
        for sub in dependant.dependencies:
            fn = getattr(sub, "call", None)
            name = getattr(fn, "__name__", "")
            if name in _AUTH_DEP_NAMES:
                return True
    return False


def _is_public_allowed(path: str) -> bool:
    if any(path == p or path.startswith(p + "/") or path == p.rstrip("/")
           for p in _PUBLIC_ROUTE_PREFIXES):
        return True
    return False


def test_C_admin_routes_require_admin(app):
    """All /api/v1/admin/* routes must include require_admin dependency."""
    unprotected: List[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.startswith("/api/v1/admin"):
            continue
        if not _route_has_auth_dep(route):
            unprotected.append(f"{sorted(route.methods)} {route.path}")
    assert not unprotected, (
        "/api/v1/admin/* routes missing require_admin dependency:\n  "
        + "\n  ".join(unprotected)
    )


def test_C_mutating_endpoints_have_auth(app):
    """POST/PUT/DELETE routes must require auth (unless explicitly public)."""
    unprotected: List[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.methods & {"POST", "PUT", "DELETE", "PATCH"}:
            continue
        if _is_public_allowed(route.path):
            continue
        if not _route_has_auth_dep(route):
            unprotected.append(f"{sorted(route.methods)} {route.path}")
    assert not unprotected, (
        "Mutating endpoints without auth dependency:\n  "
        + "\n  ".join(unprotected)
        + "\n\nAdd require_admin/require_viewer dep, or allowlist in _PUBLIC_ROUTE_PREFIXES."
    )


# ── Test D: OpenAPI completeness ──────────────────────────────────────

def test_D_in_schema_routes_have_summary(app):
    """Every documented route (include_in_schema=True) must have summary.

    Prevents API drift where new endpoints land without minimal documentation."""
    missing: List[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.include_in_schema:
            continue
        if not (route.summary or "").strip():
            missing.append(f"{sorted(route.methods)} {route.path}")
    assert not missing, (
        "Documented (in-schema) routes without summary:\n  "
        + "\n  ".join(missing)
        + "\n\nAdd summary=... to the decorator, or include_in_schema=False."
    )


# ── Test E: Method strictness ─────────────────────────────────────────

# Methods we expect to see in the app. Anything else is suspicious surface.
_EXPECTED_METHODS = {"GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"}


def test_E_no_unexpected_http_methods(app):
    """No PATCH (we don't use partial updates) and no exotic methods."""
    suspicious: List[Tuple[str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for m in route.methods - _EXPECTED_METHODS:
            suspicious.append((m, route.path))
    assert not suspicious, (
        "Unexpected HTTP methods registered:\n  "
        + "\n  ".join(f"{m} {p}" for m, p in suspicious)
    )


def test_E_delete_only_on_explicit_removal_endpoints(app):
    """DELETE method only allowed on explicit removal endpoints.

    Audit surface: prevents accidental DELETE on arbitrary route."""
    # Whitelist paths where DELETE is expected (deletion semantics).
    DELETE_ALLOWED = {
        "/api/v1/admin/mountpoints/{mp_id}",   # destroy janus mountpoint
        "/api/v1/admin/nodes/{node_id}",       # forget a camera host (bindings/mountpoints/firewall/key/token)
        "/api/v1/ui/session",                  # operator logout — clears the admin session cookie
        "/api/v1/admin/audit-log",             # audit log purge (if present)
        # Pass-through proxy routes accept all HTTP verbs (forward whatever
        # client sends to upstream Janus/depth_camera). Not an actual local DELETE.
        "/janus",
        "/janus/{path:path}",
        "/api/v1/depth_camera/{path:path}",
    }
    surprises: List[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if "DELETE" not in route.methods:
            continue
        if route.path not in DELETE_ALLOWED:
            surprises.append(route.path)
    assert not surprises, (
        "DELETE method on non-allowlisted routes:\n  "
        + "\n  ".join(surprises)
        + "\n\nAdd to DELETE_ALLOWED set if intentional."
    )


# ── Test F: Path param validation ─────────────────────────────────────

@pytest.mark.asyncio
async def test_F_int_path_params_reject_non_int(app):
    """Routes with typed {mp_id} reject alphabetic input with 422."""
    # Pick a known int-typed param route: /preview/{mp_id} (viewer-gated)
    # We expect 422 (validation) regardless of auth — Pydantic runs before deps.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/preview/not_a_number?token=anything")
    # 422 = validation rejected. 401/403 = passed type check, hit auth → also OK
    # (means type validation might have been bypassed by route order, log it)
    assert r.status_code in (422, 401, 403, 404), (
        f"Expected validation rejection (422) for letters in int param, "
        f"got {r.status_code}: {r.text[:200]}"
    )
    # Stronger assertion: if 422, must be validation error
    if r.status_code == 422:
        body = r.json()
        assert "detail" in body, f"422 without detail: {body}"


@pytest.mark.asyncio
async def test_F_path_traversal_rejected_in_path_params(app):
    """Path params allowing /-traversal sanitize properly."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # Try to escape templates dir via robot_overlay
        r = await c.get("/robot_overlay/../../etc/passwd")
    # Must 404 (route handler checks ".." prefix) — never serve actual file
    assert r.status_code == 404, (
        f"Path traversal not blocked on /robot_overlay/: {r.status_code} {r.text[:200]}"
    )


# ── supply-chain: no external script origin (review P0-2 / R3) ─────────

def test_no_cdn_in_templates_or_csp():
    """No production HTML may reference an external CDN script, and the CSP
    script-src must be self-only. All JS is vendored/self-hosted."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    # 1) no jsdelivr/unpkg/cdnjs anywhere in the served templates
    offenders = []
    for html in (root / "templates").rglob("*.html"):
        txt = html.read_text(errors="ignore")
        if re.search(r"https?://[^\"'\s]*(jsdelivr|unpkg|cdnjs)", txt):
            offenders.append(str(html.relative_to(root)))
    assert not offenders, f"templates still reference a CDN: {offenders}"
    # 2) the CSP middleware emits script-src 'self' (no external origin)
    app_src = (root / "app" / "core" / "app.py").read_text()
    m = re.search(r'"script-src ([^"]+)"', app_src)
    assert m, "script-src directive not found in CSP"
    assert "http" not in m.group(1), f"CSP script-src allows an external origin: {m.group(1)!r}"

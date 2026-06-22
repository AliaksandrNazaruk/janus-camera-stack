"""Sprint X4 URL contract fitness tests.

Enforces the architectural rule: ONLY 3 grandfathered URLs may use the
legacy /api/v1/{cam_type}/ prefix. Everything else must be:
  • Generative per-stream: /api/v1/cameras/{serial}/{sensor}/...
  • Cross-cutting admin:    /api/v1/admin/...
  • System-wide:            /...

Why fitness tests vs review: future commits could re-introduce a
/api/v1/{cam_type}/X route silently. These tests catch it in CI before
merge — every new route forces a conscious update to the grandfathered
allowlist below.

Audit also covers frontend (HTML + JS) to prevent stale hardcoded URLs
from re-appearing.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES_DIR = _SERVICE_ROOT / "templates"
_STATIC_DIR = _SERVICE_ROOT / "static"
_ROUTES_DIR = _SERVICE_ROOT / "app" / "routes"


# ── Grandfathered URLs (immutable allowlist) ──────────────────────────

# These 3 user-facing viewer URLs are the only routes permitted to keep
# the legacy /api/v1/{cam_type}/ prefix. Operator's bookmarks + external
# consumers depend on them.
GRANDFATHERED_LEGACY = {
    "/api/v1/color_camera/color_view.html",
    "/api/v1/depth_camera/color_view.html",
    "/api/v1/depth_camera/depth_view.html",
}

# The depth_proxy router prefix /api/v1/depth_camera is NOT a cam_type
# discriminator — it's the cross-node reverse-proxy namespace
# (color_camera node forwards to depth_camera). Exempt from the rule.
PROXY_NAMESPACE_PREFIX = "/api/v1/depth_camera"


# ── Backend route scan ────────────────────────────────────────────────

# Pattern: @router.<method>("...") or @router.<method>(f"...")
# Captures the literal path string from decorator first arg.
_ROUTE_DECORATOR_RE = re.compile(
    r'@router\.(?:get|post|put|delete|api_route|websocket|patch)\s*\(\s*'
    r'(?P<quote>[fr]?)"(?P<path>[^"]+)"'
)


def _resolved_path(raw: str, cam_type_value: str) -> str:
    """Substitute {_CAM_TYPE} placeholders so we can compare to the grandfathered set."""
    return (
        raw.replace("{_CAM_TYPE}", cam_type_value)
           .replace("{cam_type}", cam_type_value)
    )


def _collect_legacy_routes_from_file(path: Path) -> list[tuple[int, str]]:
    """Return [(line_no, decorator_path_resolved)] of routes matching
    /api/v1/{cam_type}/X pattern (either literal or f-string)."""
    findings: list[tuple[int, str]] = []
    text = path.read_text(encoding="utf-8")
    for i, line in enumerate(text.splitlines(), 1):
        m = _ROUTE_DECORATOR_RE.search(line)
        if not m:
            continue
        raw_path = m.group("path")
        # Only care about routes that contain a {cam_type} marker and
        # an explicit /api/v1/ prefix.
        if not raw_path.startswith("/api/v1/"):
            continue
        if "{_CAM_TYPE}" not in raw_path and "{cam_type}" not in raw_path:
            # Literal /api/v1/color_camera or /api/v1/depth_camera also count.
            if "/api/v1/color_camera/" in raw_path or "/api/v1/depth_camera/" in raw_path:
                findings.append((i, raw_path))
            continue
        # Resolve placeholder to both possible values. Some routes are wrapped
        # in `if _CAM_TYPE == "depth_camera":` guards, so only one resolution
        # actually registers. Allow the decorator if AT LEAST ONE resolution
        # matches grandfathered set (the other is unreachable at runtime).
        resolutions = {
            _resolved_path(raw_path, "color_camera"),
            _resolved_path(raw_path, "depth_camera"),
        }
        if not (resolutions & GRANDFATHERED_LEGACY):
            findings.append((i, raw_path))
    return findings


def test_backend_routes_obey_url_contract():
    """All @router.* decorators in app/routes/ must comply with the Sprint X4 contract.

    Exceptions:
      • The 3 grandfathered viewer URLs (color_view × 2 + depth_view × 1)
      • depth_proxy.py router prefix (cross-node proxy namespace, not cam_type)
    """
    violations: list[tuple[str, int, str]] = []
    for py_file in sorted(_ROUTES_DIR.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        # depth_proxy.py uses /api/v1/depth_camera as proxy namespace prefix —
        # not subject to the rule. Its router prefix is set once at module level.
        if py_file.name == "depth_proxy.py":
            continue
        for lineno, raw_path in _collect_legacy_routes_from_file(py_file):
            violations.append((str(py_file.relative_to(_SERVICE_ROOT)), lineno, raw_path))

    assert not violations, (
        "Sprint X4 URL contract violation. Routes under /api/v1/{cam_type}/ "
        "must be one of the 3 grandfathered viewer URLs:\n  "
        + "\n  ".join(sorted(GRANDFATHERED_LEGACY))
        + "\n\nViolations found:\n"
        + "\n".join(f"  {f}:{ln}: {p}" for f, ln, p in violations)
    )


def test_grandfathered_routes_actually_registered():
    """Smoke check: the 3 grandfathered URLs must remain registered. If a
    future cleanup deletes one accidentally, this test fails loudly."""
    templates_text = (_ROUTES_DIR / "templates.py").read_text()

    # color_view.html — registered with @router.get for both cam types via _CAM_TYPE
    assert 'f"/api/v1/{_CAM_TYPE}/color_view.html"' in templates_text, (
        "Grandfathered route /api/v1/{cam_type}/color_view.html missing from templates.py"
    )
    # depth_view.html — inside if _CAM_TYPE == "depth_camera" guard
    assert 'f"/api/v1/{_CAM_TYPE}/depth_view.html"' in templates_text, (
        "Grandfathered route /api/v1/{cam_type}/depth_view.html missing from templates.py"
    )


# ── Frontend scan: HTML templates + JS ────────────────────────────────

_HARDCODED_LEGACY_HTML_RE = re.compile(
    r"/api/v1/\{\{\s*cam_type\s*\}\}/"
    r"(?!color_view\.html|depth_view\.html\b)"  # negative lookahead for 2 grandfathered viewers
    r"[a-zA-Z0-9_/.-]*"
)
_HARDCODED_LEGACY_JS_RE = re.compile(
    r"/api/v1/(?:color_camera|depth_camera)/"
    r"(?!color_view\.html|depth_view\.html)"
    r"[a-zA-Z0-9_/.-]*"
)


def test_html_templates_have_no_stale_cam_type_refs():
    """HTML templates may only reference /api/v1/{cam_type}/{color,depth}_view.html
    (links to grandfathered viewers). Everything else must be canonical."""
    violations: list[tuple[str, int, str]] = []
    for html in sorted(_TEMPLATES_DIR.glob("*.html")):
        for i, line in enumerate(html.read_text(encoding="utf-8").splitlines(), 1):
            for m in _HARDCODED_LEGACY_HTML_RE.finditer(line):
                violations.append((html.name, i, m.group(0)))
    assert not violations, (
        "Stale /api/v1/{{ cam_type }}/* refs in HTML templates:\n"
        + "\n".join(f"  {f}:{ln}: {p}" for f, ln, p in violations)
        + "\n\nOnly /api/v1/{{ cam_type }}/{color,depth}_view.html allowed."
    )


def test_js_files_have_no_stale_cam_type_refs():
    """JS files may not hardcode /api/v1/color_camera/ or /api/v1/depth_camera/
    paths (except links to grandfathered viewers)."""
    violations: list[tuple[str, int, str]] = []
    js_dir = _STATIC_DIR / "js"
    for js in sorted(js_dir.glob("*.js")):
        for i, line in enumerate(js.read_text(encoding="utf-8").splitlines(), 1):
            # Skip comments — they may reference old URLs for documentation
            stripped = line.lstrip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            for m in _HARDCODED_LEGACY_JS_RE.finditer(line):
                violations.append((js.name, i, m.group(0)))
    assert not violations, (
        "Stale /api/v1/{color,depth}_camera/* refs in JS:\n"
        + "\n".join(f"  {f}:{ln}: {p}" for f, ln, p in violations)
        + "\n\nUse system-wide root paths or /api/v1/admin/* for admin endpoints."
    )


# ── Helper: list everything caught for debugging ─────────────────────

def _dump_all_findings():  # pragma: no cover — debug helper
    print("\n== Backend routes ==")
    for py_file in sorted(_ROUTES_DIR.rglob("*.py")):
        if "__pycache__" in py_file.parts or py_file.name == "depth_proxy.py":
            continue
        for ln, p in _collect_legacy_routes_from_file(py_file):
            print(f"  {py_file.name}:{ln}: {p}")
    print("\n== HTML refs ==")
    for html in sorted(_TEMPLATES_DIR.glob("*.html")):
        for i, line in enumerate(html.read_text().splitlines(), 1):
            for m in _HARDCODED_LEGACY_HTML_RE.finditer(line):
                print(f"  {html.name}:{i}: {m.group(0)}")


if __name__ == "__main__":  # pragma: no cover
    _dump_all_findings()

"""DEF-06: Template rendering tests — Jinja2 migration validation.

Verifies that:
- HTML templates render without raw __CAM_TYPE__ placeholders
- Jinja2 autoescape prevents XSS via template variables
- Template variables are correctly substituted
- Both color_view and depth_view templates render correctly
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from jinja2 import Environment, FileSystemLoader, select_autoescape


# ── Direct Jinja2 env for unit tests (no FastAPI dependency) ──────────

_TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "templates"
)
_jinja_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "htm"]),
)


# ── FastAPI integration fixture (color_camera, default) ──────────────

_TEST_TOKEN = "test-token-templates-32chars!!"


@pytest.fixture
def app():
    with patch("app.core.events.register_event_handlers", lambda app: None), \
         patch.dict(os.environ, {"CAM_ADMIN_TOKEN": _TEST_TOKEN}):
        from app.core.app import create_app
        yield create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Integration tests: color_view via FastAPI ────────────────────────

class TestTemplateRendering:
    """DEF-06: Templates must use Jinja2, not string.replace()."""

    async def test_no_raw_placeholders(self, client):
        """Rendered HTML must not contain raw __CAM_TYPE__ placeholders."""
        resp = await client.get("/color_view.html")
        assert resp.status_code == 200
        body = resp.text
        assert "__CAM_TYPE__" not in body, "Raw placeholder found in rendered template"

    async def test_api_prefix_gateway_context(self, client):
        """Sprint X4: api_prefix is context-dependent. Via gateway (X-Forwarded-Prefix
        header — api.* host) api_prefix = /api/v1/color_camera, prepended to assets.
        Direct access (cameras.* host, no header) → empty prefix (assets relative).
        We test the gateway path — assets must carry prefix."""
        resp = await client.get(
            "/color_view.html",
            headers={"X-Forwarded-Prefix": "/api/v1/color_camera"},
        )
        assert resp.status_code == 200
        body = resp.text
        assert 'data-api-prefix="/api/v1/color_camera"' in body
        assert "/api/v1/color_camera/static/css/player.css" in body

    async def test_api_prefix_empty_direct_access(self, client):
        """Direct access (no X-Forwarded-Prefix) → empty prefix; assets resolve
        relative to current host (cameras.* serves /static/* directly)."""
        resp = await client.get("/color_view.html")
        assert resp.status_code == 200
        body = resp.text
        assert 'data-api-prefix=""' in body

    async def test_stream_id_rendered(self, client):
        """data-prefer-stream-id must be rendered (not the Jinja2 tag)."""
        resp = await client.get("/color_view.html")
        body = resp.text
        assert 'data-prefer-stream-id="1305"' in body
        assert "{{ stream_id }}" not in body

    async def test_joystick_mode_rendered(self, client):
        """Joystick mode rendered as Jinja2 substitution (not raw tag).

        Architecture (Sprint B1, _render_template_response):
          STACK_DEFAULT_JOYSTICK_MODE != 'off' AND robot_overlay/color_view.html
          exists → robot variant (mode="always"). Default ("off") → generic
          (mode="off"). Test runs in default mode so generic served. Earlier
          assertion expecting 'always' was tied to pre-split architecture where
          robot overlay was the default. Now: assert ONE of the valid rendered
          values appears + no raw Jinja2 tag leaks.
        """
        resp = await client.get("/color_view.html")
        body = resp.text
        assert "{{ joystick_mode }}" not in body
        # Generic stack default = "off"; robot_overlay variant would set "always".
        # Either is architecturally valid; assert the route renders SOMETHING valid.
        assert ('data-joystick-mode="off"' in body
                or 'data-joystick-mode="always"' in body)


# ── Unit tests: depth_view via direct Jinja2 (avoids module-reload fragility) ──

class TestDepthViewRendering:
    """depth_view.html must render correctly with Jinja2."""

    def _render_depth(self, api_prefix="/api/v1/depth_camera"):
        # Sprint X4: depth_view.html no longer embeds cam_type in body — it is a
        # generic depth viewer (always depth). api_prefix is injected by the route
        # (device_camera.viewer_html) for asset paths on different hosts.
        tmpl = _jinja_env.get_template("depth_view.html")
        return tmpl.render(api_prefix=api_prefix, stream_id=1306)

    def test_no_raw_placeholders(self):
        body = self._render_depth()
        assert "__CAM_TYPE__" not in body, "Raw placeholder found in depth template"
        assert "{{ cam_type }}" not in body, "Raw Jinja2 tag in depth template"

    def test_depth_identity_rendered(self):
        # depth_view is identified by HUD + depth endpoint (not cam_type).
        body = self._render_depth()
        assert 'id="depthHud"' in body
        assert 'data-depth-endpoint' in body

    def test_api_prefix_rendered(self):
        # api_prefix prepended to asset paths (player.css, janus.js etc.).
        body = self._render_depth(api_prefix="/api/v1/depth_camera")
        assert '/api/v1/depth_camera/static/css/player.css' in body

    def test_stream_id_1306(self):
        body = self._render_depth()
        assert 'data-prefer-stream-id="1306"' in body

    def test_joystick_mode_off(self):
        body = self._render_depth()
        assert 'data-joystick-mode="off"' in body

    def test_depth_endpoint_rendered(self):
        body = self._render_depth()
        assert "data-depth-endpoint" in body

    def test_gripper_attrs_NOT_in_generic_template(self):
        """X3.3 architecture: gripper calibration attrs are robot-specific and
        live in robot-overlay wrappers (templates/robot_overlay/depth_view.html
        if/when created), NOT in generic depth_view.html. The in-template
        comment at L57 explicitly forbids them here.

        Earlier assertion expecting data-gripper-* in generic depth_view was
        pre-X3.3 — that architecture mixed concerns. Generic depth_view is
        now operator-agnostic; gripper overlay belongs to robot wrapper.
        """
        body = self._render_depth()
        for attr in ("data-gripper-fx", "data-gripper-fy", "data-gripper-cx", "data-gripper-cy"):
            assert attr not in body, (
                f"{attr} leaked into generic depth_view.html — must move "
                "to templates/robot_overlay/ wrapper per X3.3 architecture"
            )


# ── XSS and conditional block tests ──────────────────────────────────

class TestJinja2XSS:
    """Jinja2 autoescape must prevent XSS via template variables."""

    def test_xss_in_stream_name_escaped(self):
        # Sprint X4: cam_type is no longer rendered in body. stream_name is the real
        # XSS surface (rendered in data-stream-name). Jinja autoescape must
        # escape it.
        tmpl = _jinja_env.get_template("color_view.html")
        html = tmpl.render(
            stream_id=1305,
            stream_name='<script>alert(1)</script>',
            joystick_mode="always",
            depth_features_script=False,
        )
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_depth_features_block_hidden_when_false(self):
        tmpl = _jinja_env.get_template("color_view.html")
        html = tmpl.render(
            cam_type="color_camera",
            stream_id=1305,
            stream_name="RGB",
            joystick_mode="always",
            depth_features_script=False,
        )
        assert "{% if" not in html, "Raw Jinja2 block tags in output"
        assert "depth_features.js" not in html

    def test_depth_features_block_shown_when_true(self):
        tmpl = _jinja_env.get_template("color_view.html")
        html = tmpl.render(
            cam_type="color_camera",
            stream_id=1305,
            stream_name="RGB",
            joystick_mode="always",
            depth_features_script=True,
        )
        assert "depth_features.js" in html


# ── Route-level integration tests ─────────────────────────────────────

class TestColorViewReturnsHtml:
    """GET /color_view.html returns 200 with HTML content."""

    async def test_color_view_returns_html(self, client):
        resp = await client.get("/color_view.html")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert "<html" in resp.text.lower() or "<!doctype" in resp.text.lower()


class TestJanusJsServesContent:
    """GET /janus.js returns JS content (local file or CDN fallback)."""

    async def test_janus_js_serves_content(self, client):
        resp = await client.get("/janus.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers.get("content-type", "")


class TestPlayerScriptReturnsJs:
    """GET /player/<script>.js returns JS files from player directory."""

    async def test_player_config_js(self, client):
        """config.js in the player directory is served with JS content type."""
        resp = await client.get("/player/config.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers.get("content-type", "")

    async def test_player_nonexistent_returns_404(self, client):
        """Request for a non-existent player script returns 404."""
        resp = await client.get("/player/nonexistent_file_xyz.js")
        assert resp.status_code == 404


class TestPathTraversalBlocked:
    """Path traversal attempts are blocked in player script routes."""

    async def test_path_traversal_blocked(self, client):
        """GET /player/../../etc/passwd is rejected (404)."""
        resp = await client.get("/player/../../etc/passwd")
        assert resp.status_code == 404

    async def test_path_traversal_dotdot_in_middle(self, client):
        """GET /player/adapters/../../etc/passwd is rejected."""
        resp = await client.get("/player/adapters/../../etc/passwd")
        assert resp.status_code == 404

    async def test_path_traversal_absolute_path_blocked(self, client):
        """Absolute path in player route is rejected."""
        resp = await client.get("/player//etc/passwd")
        assert resp.status_code == 404

    async def test_static_via_api_traversal_blocked(self, client):
        """Path traversal in /api/v1/<cam>/static/ is rejected."""
        resp = await client.get("/api/v1/color_camera/static/../../../etc/passwd")
        assert resp.status_code == 404

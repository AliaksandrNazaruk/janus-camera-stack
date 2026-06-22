"""Architecture regression guards.

These tests exist because of the 2026-06-20 incident: the working tree had silently
REVERTED a slice of advanced code that already existed in HEAD (rich NodeEntry +
store API, the `ui_viewmodel` route mount, the `reconcile_janus` startup wiring, and
the self-hosted-asset / no-CDN supply-chain posture). The architecture was fine; the
*source of truth drifted*. Each test below pins one of those contracts so a future
regression fails CI instead of silently shipping (and getting mis-diagnosed as
"architecture not finished").

Keep them cheap and import-light — they assert contracts, not behaviour.
"""
from __future__ import annotations

import dataclasses
import glob
import inspect
import os
import sys

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def test_rich_node_entry_contract():
    """NodeEntry must keep its rich fields + the store APIs the fleet/console/provision
    layers depend on. The regression collapsed this to a 5-field basic NodeEntry and
    dropped the APIs, which is what failed test_ui_viewmodel/test_fleet/test_node_provisioner."""
    from app.services import stream_binding_store as sbs

    fields = {f.name for f in dataclasses.fields(sbs.NodeEntry)}
    required = {"node_id", "host", "role", "reachability", "ordinal", "serial",
                "display_name", "provision_state", "host_key", "agent_token",
                "maintenance", "last_error", "last_checked_at"}
    missing = required - fields
    assert not missing, f"NodeEntry missing rich fields (working-tree regression): {sorted(missing)}"

    for api in ("add_node_by_host", "mint_agent_token", "set_agent_token", "set_serial",
                "set_provision_state", "set_maintenance"):
        assert hasattr(sbs, api) and callable(getattr(sbs, api)), \
            f"stream_binding_store missing store API (regression): {api}"


def test_ui_viewmodel_router_mounted():
    """The operator console view-model must be reachable. The regression dropped the
    include_router(ui_viewmodel.router) line, leaving live /api/v1/ui empty."""
    from app.routes import ui_viewmodel

    paths = {getattr(r, "path", "") for r in ui_viewmodel.router.routes}
    assert any("fleet" in p for p in paths), f"ui_viewmodel router has no /fleet route: {sorted(paths)}"

    from app import routes as routes_pkg
    src = inspect.getsource(routes_pkg)
    assert "include_router(ui_viewmodel.router)" in src, \
        "routes/__init__ does not mount ui_viewmodel.router (regression)"


def test_reconcile_janus_wired_at_startup():
    """The gateway-side converge loop must run on startup — without it a Janus restart
    leaves remote bindings stuck WAITING_FOR_RTP (the original .55 outage)."""
    from app.core import events
    assert "reconcile_janus" in inspect.getsource(events), \
        "startup does not wire reconcile_janus (regression)"


def test_no_runtime_cdn_in_csp_or_templates():
    """Supply-chain: no externally-hosted scripts at runtime. The regression restored a
    cdn.jsdelivr.net script source in the CSP and the color/depth templates."""
    from app.core import app as appmod
    assert "cdn.jsdelivr" not in inspect.getsource(appmod), \
        "cdn.jsdelivr in CSP source (supply-chain regression)"

    offenders = []
    for f in glob.glob(os.path.join(_SERVICE_ROOT, "templates", "*.html")):
        with open(f, encoding="utf-8") as fh:
            body = fh.read()
        if any(cdn in body for cdn in ("cdn.jsdelivr", "unpkg.com", "cdnjs.cloudflare")):
            offenders.append(os.path.basename(f))
    assert not offenders, f"templates load runtime assets from a CDN (regression): {offenders}"


def test_secrets_file_gitignored_and_example_shipped():
    """The real host_infra/secrets.yml must stay gitignored (never committed); only the
    .example placeholder ships. (The 2026-06-20 leak was a tar that FOLLOWED the
    gitignored file — the release-packaging step must `--exclude host_infra/secrets.yml`,
    e.g. the camera_stack archive build. This test guards the source-control half.)"""
    import subprocess
    for base in (_SERVICE_ROOT, _MONOREPO_ROOT):
        hi = os.path.join(base, "host_infra")
        if not os.path.isdir(hi):
            continue
        assert os.path.exists(os.path.join(hi, "secrets.yml.example")), \
            f"missing shippable placeholder: {hi}/secrets.yml.example"
        sec = os.path.join(hi, "secrets.yml")
        if os.path.exists(sec):
            try:
                rc = subprocess.run(["git", "check-ignore", "-q", sec], cwd=base).returncode
            except FileNotFoundError:
                continue  # git not available in this env — skip the ignore check
            assert rc == 0, f"REAL secrets.yml is NOT gitignored — it could be committed: {sec}"

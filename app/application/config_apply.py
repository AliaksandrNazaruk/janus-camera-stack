"""Use-case: apply config — re-render jcfg, restart Janus + relay, audit.
Extracted from admin_config (route-purity Phase 5). Restart goes through the scoped service-admin CLI
(services/service_control — the SINGLE destructive service-control path, shared with services_admin +
recovery; Cycle 2 consolidation), NOT the bare systemctl path. The ApplyResponse model lives here.
"""
from __future__ import annotations

from typing import List

from pydantic import BaseModel

from app.services import jcfg_renderer, service_control
from app.services.audit_log import audit


def _restart_ok(unit: str, *, timeout: int) -> bool:
    """Restart a unit via the scoped service-admin port. Exec failure (RuntimeError) or a non-zero rc
    is swallowed to a bool — apply continues + reports it in errors (matches the prior bare-systemctl
    behavior; service-admin normalises a trailing .service + allowlists janus/relay/hook)."""
    try:
        rc, _stderr = service_control.restart_unit(unit, timeout=timeout)
        return rc == 0
    except RuntimeError:
        return False


class ConfigRenderFailed(Exception):
    """jcfg render failed. Route maps to 500 with detail=errors — a list[str], preserved as a LIST
    (not joined to a string): the apply contract returns structured render errors."""

    def __init__(self, errors: list) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


class ApplyResponse(BaseModel):
    rendered: list[str]
    janus_restarted: bool
    relay_restarted: bool
    errors: list[str] = []


def apply(restart_janus: bool = True, restart_relay: bool = True) -> ApplyResponse:
    errors: List[str] = []
    rendered_paths: List[str] = []

    # 1. Re-render templates
    try:
        result = jcfg_renderer.render()
        rendered_paths = [str(p) for p in result.rendered]
        if result.skipped_templates:
            errors.append(f"Skipped templates: {', '.join(result.skipped_templates)}")
    except RuntimeError as exc:
        errors.append(f"render failed: {exc}")
        audit("admin_config.apply.render_failed", {"error": str(exc)})
        raise ConfigRenderFailed(errors)

    janus_ok = False
    relay_ok = False

    # 2. Restart Janus (scoped service-admin CLI; service-admin normalises the .service suffix)
    if restart_janus:
        janus_ok = _restart_ok("janus", timeout=30)
        if not janus_ok:
            errors.append("janus restart failed — see journalctl -u janus")

    # 3. Restart relay (if present — try both known unit names)
    if restart_relay:
        relay_ok = _restart_ok("janus-textroom-relay", timeout=15) \
                 or _restart_ok("janus_camera_page_hook", timeout=15)
        if not relay_ok:
            errors.append("relay restart failed (or not installed)")

    audit("admin_config.apply", {
        "rendered": len(rendered_paths),
        "janus_restarted": janus_ok,
        "relay_restarted": relay_ok,
        "errors": errors,
    })

    return ApplyResponse(
        rendered=rendered_paths,
        janus_restarted=janus_ok,
        relay_restarted=relay_ok,
        errors=errors,
    )

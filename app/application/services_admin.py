"""Use-cases for the systemd 'services' admin routes.

Orchestration only — validation, dispatch, polling, audit, result shaping. Side
effects go through the `systemd` / `encoder_admin` infra adapters. (Extracted from
app/routes/admin_dashboard.py, C-04; behavior preserved verbatim, incl. audit strings.)
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel

from app.services import encoder_admin, service_control, systemd
from app.services.audit_log import audit


# ── domain errors (the route maps these to HTTP; this layer stays FastAPI-free) ──
class RestartSelfRefused(Exception):
    """Refusing to restart the app's own service from its handler. Route maps to 400."""


class ServiceNotRestartable(Exception):
    """Service not in the restartable allowlist. Route maps to 400 (message verbatim)."""


class RestartMethodUnknown(Exception):
    """Internal: the dispatch table named an unknown restart method. Route maps to 500."""


class RestartExecFailed(Exception):
    """The restart command (systemctl / encoder-admin) failed. Route maps to 500 (message verbatim)."""


# ── models (moved from admin_dashboard; shape unchanged) ──────────────────
class ServiceState(BaseModel):
    name: str
    active: bool
    state: str            # "active" | "inactive" | "failed" | "absent" | "unknown"
    enabled: bool
    sub_state: Optional[str] = None
    main_pid: Optional[int] = None
    memory_bytes: Optional[int] = None


class RestartResponse(BaseModel):
    service: str
    method: str           # "encoder-admin" | "systemctl"
    ok: bool
    new_state: str
    stderr: Optional[str] = None


# ── policy (moved verbatim) ───────────────────────────────────────────────
KNOWN_SERVICES = [
    "janus",
    "janus-camera-page",
    "janus-textroom-relay",      # installer-created
    "janus_camera_page_hook",    # legacy prod alias (Ansible-managed)
    "coturn",
    "realsense-mux",
    "rs-stream@color",           # Phase 2: color encoder (was rtp-rgb@cam-rgb)
]

# Services restartable from UI + how. encoder-admin entries dispatch to the CLI.
RESTART_DISPATCH: Dict[str, Optional[Tuple[str, str]]] = {
    "janus":                    ("systemctl", "janus"),
    "janus-textroom-relay":     ("systemctl", "janus-textroom-relay"),
    "janus_camera_page_hook":   ("systemctl", "janus_camera_page_hook"),
    "coturn":                   ("systemctl", "coturn"),
    "realsense-mux":            ("encoder-admin", "realsense-mux"),
    "rs-stream@color":          ("encoder-admin", "rs-stream@color"),
}
SELF_SERVICE = "janus-camera-page"


def parse_family_instance(unit: str) -> Tuple[str, Optional[str]]:
    """Split rs-stream@color → ('rs-stream', 'color'); plain unit → (unit, None)."""
    if "@" in unit:
        family, instance = unit.split("@", 1)
        return family, instance
    return unit, None


# ── use-cases ─────────────────────────────────────────────────────────────
def service_state(name: str) -> ServiceState:
    info = systemd.show(name)
    if info is None:
        return ServiceState(name=name, active=False, state="absent", enabled=False)
    if info.get("LoadState", "") in ("not-found", "masked"):
        return ServiceState(name=name, active=False, state="absent", enabled=False)
    active = info.get("ActiveState", "unknown")
    enabled = info.get("UnitFileState", "") in ("enabled", "enabled-runtime", "static")
    pid_raw = info.get("MainPID", "0")
    pid = int(pid_raw) if pid_raw.isdigit() and int(pid_raw) > 0 else None
    mem_raw = info.get("MemoryCurrent", "")
    mem = int(mem_raw) if mem_raw.isdigit() else None
    return ServiceState(
        name=name, active=(active == "active"), state=active, enabled=enabled,
        sub_state=info.get("SubState", "") or None, main_pid=pid, memory_bytes=mem,
    )


def service_states() -> List[ServiceState]:
    return [service_state(name) for name in KNOWN_SERVICES]


def restart_service(service: str) -> RestartResponse:
    if service == SELF_SERVICE:
        audit("admin_dashboard.restart.refused_self", {"service": service})
        raise RestartSelfRefused(
            f"Refusing to restart self ({SELF_SERVICE}) from its own request handler — use ssh + systemctl")
    dispatch = RESTART_DISPATCH.get(service)
    if dispatch is None:
        audit("admin_dashboard.restart.unknown", {"service": service})
        raise ServiceNotRestartable(
            f"Service {service!r} not in restartable allowlist: {list(RESTART_DISPATCH)}")
    method, unit = dispatch

    try:
        if method == "encoder-admin":
            family, instance = parse_family_instance(unit)
            rc, stderr_out = encoder_admin.invoke("restart", family, instance)
        elif method == "systemctl":
            rc, stderr_out = service_control.restart_unit(unit)   # P1: scoped service-admin CLI
        else:
            raise RestartMethodUnknown(f"unknown method: {method}")
    except RuntimeError as exc:
        audit("admin_dashboard.restart.failed",
              {"service": service, "method": method, "error": str(exc)[:120]})
        raise RestartExecFailed(f"restart exec failed: {exc}") from exc

    time.sleep(1.5)                       # brief poll so the response reflects new state
    new = service_state(service)
    ok = rc == 0 and new.active
    audit("admin_dashboard.restart" + (".ok" if ok else ".failed"),
          {"service": service, "method": method, "rc": rc,
           "new_state": new.state, "stderr": stderr_out[:200]})
    if rc != 0:
        return RestartResponse(service=service, method=method, ok=False,
                               new_state=new.state, stderr=stderr_out or f"command exit code {rc}")
    return RestartResponse(service=service, method=method, ok=ok,
                           new_state=new.state, stderr=stderr_out if stderr_out else None)

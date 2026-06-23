from __future__ import annotations

from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app.core.admin import require_admin
from app.core.settings import get_settings
from app.core.viewer_auth import (
    extract_viewer_token,
    require_viewer,
    require_viewer_ws,
    viewer_id_for_token,
)
from app.middleware.rate_limit import require_admin_rate_limit, require_rate_limit
from app.services import janus, janus_proxy
from app.application.janus_nat import update_nat_config as update_nat_config_uc
from app.application import janus_restart as janus_restart_uc
from app.application.operations import canonical_status
from app.services.operation_journal import OperationConflict
from app.services.nat_config import (
    JanusNatConfig,
    load_nat_config,
    read_apply_status,
    restart_janus,
)
from app.services.turn_credentials import generate_turn_credentials
from app.config import PORTS

router = APIRouter(tags=["janus"])
ADMIN_DEPENDENCY = Depends(require_admin)
ADMIN_RATE_LIMIT = Depends(require_admin_rate_limit)
# P0-SEC-001: viewer gate for endpoints that leak TURN creds or stream data
# to unauthenticated clients. Dev mode (VIEWER_TOKENS unset) → no-op.
VIEWER_DEPENDENCY = Depends(require_viewer)
# Boot-time constant — FastAPI route paths must be static at decoration time.
_CAM_TYPE = get_settings().camera_type


class IceServer(BaseModel):
    urls: List[str]
    username: Optional[str] = None
    credential: Optional[str] = None
    credentialType: Literal["password"] = "password"


class ClientRtcConfig(BaseModel):
    iceServers: List[IceServer]
    iceTransportPolicy: Literal["all", "relay"] = "relay"
    sdpSemantics: Literal["unified-plan"] = "unified-plan"
    bundlePolicy: Literal["balanced", "max-bundle", "max-compat"] = "balanced"
    rtcpMuxPolicy: Literal["require"] = "require"


class JanusHealthResponse(BaseModel):
    ok: bool
    mount_id: int


# ── Janus health ──


@router.get(
    "/janus/healthz",
    response_model=JanusHealthResponse,
    summary="Check Janus mount availability",
    description="Queries janus.plugin.streaming to confirm the mount exists and is enabled.",
    dependencies=[Depends(require_rate_limit)],
)
def janus_healthz() -> JanusHealthResponse:
    settings = get_settings()
    try:
        data = janus.streaming_info(settings.janus_mount_id)
    except Exception:
        return JanusHealthResponse(ok=False, mount_id=settings.janus_mount_id)
    if not isinstance(data, dict):
        return JanusHealthResponse(ok=False, mount_id=settings.janus_mount_id)
    info = data.get("data", {})
    if not isinstance(info, dict):
        return JanusHealthResponse(ok=False, mount_id=settings.janus_mount_id)
    mount = info.get("info", {})
    if not isinstance(mount, dict):
        mount = {}
    return JanusHealthResponse(ok=mount.get("enabled") is not None, mount_id=settings.janus_mount_id)


# ── Client WebRTC config ──


@router.get(
    "/client-config",
    response_model=ClientRtcConfig,
    summary="WebRTC ICE configuration for browser clients",
    description=(
        "Returns STUN/TURN configuration derived from Janus NAT settings so that the "
        "web client (`color_view.html`) can establish media both locally and remotely."
    ),
    dependencies=[VIEWER_DEPENDENCY],
)
def get_client_rtc_config(request: Request) -> ClientRtcConfig:
    settings = get_settings()
    nat_cfg = load_nat_config()

    ice_servers: List[IceServer] = []

    # STUN (for reflexive candidates)
    stun_url = f"stun:{nat_cfg.stun_server}:{nat_cfg.stun_port}"
    ice_servers.append(IceServer(urls=[stun_url]))

    # ── TURN (multi-transport failover) ──
    turn_host = nat_cfg.turn_server
    turn_port = nat_cfg.turn_port
    turn_tls_port_env = settings.turn_tls_port  # was raw env read, now via Settings

    turn_urls_all: List[str] = []
    if nat_cfg.turn_type in {"udp", "tcp"}:
        turn_urls_all.append(f"turn:{turn_host}:{turn_port}?transport=udp")
        turn_urls_all.append(f"turn:{turn_host}:{turn_port}?transport=tcp")
    if nat_cfg.turn_type == "tls" or turn_tls_port_env:
        tls_port = int(turn_tls_port_env) if turn_tls_port_env else 443
        turn_urls_all.append(f"turns:{turn_host}:{tls_port}?transport=tcp")

    if turn_urls_all:
        turn_shared_secret = settings.turn_shared_secret
        # P1-SEC-002: derive TURN username from viewer token so coturn access
        # logs correlate relay traffic to a specific session. Falls back to
        # nat_cfg.turn_user in dev mode (no token) — preserves existing UX.
        viewer_token = extract_viewer_token(request)
        if viewer_token:
            turn_user = f"{nat_cfg.turn_user}-{viewer_id_for_token(viewer_token, turn_shared_secret)}"
        else:
            turn_user = nat_cfg.turn_user
        if turn_shared_secret:
            eph_user, eph_cred = generate_turn_credentials(
                shared_secret=turn_shared_secret,
                user=turn_user,
                ttl=settings.turn_cred_ttl,
            )
            ice_servers.append(
                IceServer(urls=turn_urls_all, username=eph_user, credential=eph_cred)
            )
        else:
            ice_servers.append(
                IceServer(urls=turn_urls_all, username=turn_user, credential=nat_cfg.turn_pwd)
            )

    # Depth camera behind double NAT → force relay-only ICE
    if settings.camera_type == "depth_camera":
        policy: Literal["all", "relay"] = "relay"
    elif settings.ice_policy == "relay":
        policy = "relay"
    else:
        policy = "all"

    return ClientRtcConfig(iceServers=ice_servers, iceTransportPolicy=policy)


# ── NAT config CRUD ──


@router.get(
    "/janus/nat",
    response_model=JanusNatConfig,
    dependencies=[ADMIN_DEPENDENCY, ADMIN_RATE_LIMIT],
    summary="Read Janus NAT/STUN/TURN settings",
    description="Loads the JSON stored at `/etc/robot/janus-nat.json`.",
)
def get_janus_nat_config():
    cfg = load_nat_config()
    return cfg.model_copy(update={"turn_pwd": "***" if cfg.turn_pwd else ""})


@router.get(
    "/janus/nat/status",
    dependencies=[ADMIN_DEPENDENCY, ADMIN_RATE_LIMIT],
    summary="Read whether the persisted NAT/TURN config is actually applied to live Janus",
    description=(
        "Returns the apply-status sidecar: `status` (pending|applied|failed|unknown) + `diff_hash` + "
        "`failure_stage` + `updated_at`. Lets an operator see desired≠applied (a partial-apply / "
        "crash-mid-apply) instead of trusting that the stored config is live. Read-only."
    ),
)
def get_janus_nat_status() -> dict:
    status = read_apply_status()
    # Project the domain sidecar status onto the canonical admin-operation vocabulary (Cycle 8B) so
    # the operator gets the same status word here as from /operations and the apply endpoints.
    status["operation_status"] = canonical_status(status.get("status", "")).value
    return status


if _CAM_TYPE == "color_camera":
    @router.post(
        "/janus/nat",
        dependencies=[ADMIN_DEPENDENCY, ADMIN_RATE_LIMIT],
        summary="Update Janus NAT/STUN/TURN settings",
        description=(
            "Runs the NAT/TURN update operation: persist desired → patch janus.jcfg (no restart) → "
            "restart local Janus → restart the depth node (best-effort). 200 returns the masked config "
            "(+ `warnings` if the best-effort depth restart failed); a stage failure returns 500 with a "
            "structured body (`failure_stage`, `desired_persisted`, `local_applied`, `local_restarted`, "
            "`depth_restarted`, `exit_code`)."
        ),
    )
    def update_janus_nat_config(new_cfg: JanusNatConfig):
        # Thin HTTP adapter over the application use-case (Cycle 7B). The operation — keep-password,
        # persist, patch (no_restart), the single restart, best-effort depth — lives in
        # app/application/janus_nat; the route only masks the secret + maps the result to HTTP.
        result = update_nat_config_uc(new_cfg)
        masked = result.config.model_copy(
            update={"turn_pwd": "***" if result.config.turn_pwd else ""})
        if not result.ok:
            return JSONResponse(status_code=500, content={
                "detail": result.detail,
                "operation_status": result.operation_status.value,   # canonical (Cycle 8B)
                "failure_stage": result.failure_stage,
                "desired_persisted": result.desired_persisted,
                "local_applied": result.local_applied,
                "local_restarted": result.local_restarted,
                "depth_restarted": result.depth_restarted,
                "exit_code": result.exit_code,
            })
        body = masked.model_dump()
        if result.warnings:
            body["warnings"] = result.warnings   # depth best-effort failed but local succeeded
        return JSONResponse(status_code=200, content=body)


# ── Janus restart ──


@router.post("/janus/restart", summary="Restart Janus service", description="Restarts the Janus service.", dependencies=[ADMIN_DEPENDENCY, ADMIN_RATE_LIMIT])
def _restart_janus() -> None:
    # SYNCHRONOUS (200 = restart done). Kept unchanged: a depth-peer machine client
    # (restart_depth_camera_janus) + the NAT op's depth stage depend on these semantics. For a
    # non-blocking, observable restart use POST /janus/restart-tracked (Cycle 13).
    try:
        restart_janus()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/janus/restart-tracked",
    summary="Start a tracked (non-blocking) Janus restart — 202 + operation_id",
    description=(
        "Bounded admin operation (Cycle 13): starts a local Janus restart in the background and "
        "returns 202 with an `operation_id`; poll `GET /api/v1/admin/operations/{operation_id}` "
        "for running/succeeded/failed. ONE running Janus restart at a time (409 if busy). The sync "
        "`POST /janus/restart` is unchanged (machine clients rely on its 200=done semantics)."
    ),
    dependencies=[ADMIN_DEPENDENCY, ADMIN_RATE_LIMIT],
)
def _restart_janus_tracked() -> JSONResponse:
    try:
        op_id = janus_restart_uc.start_tracked_restart()
    except OperationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JSONResponse(status_code=202, content={
        "operation_id": op_id,
        "operation_status": "running",
        "status_url": f"/api/v1/admin/operations/{op_id}",
    })


# ── Proxies ──


@router.api_route(
    "/janus",
    methods=["GET", "POST", "PUT", "DELETE"],
    dependencies=[Depends(require_rate_limit), VIEWER_DEPENDENCY],
    summary="HTTP proxy to the Janus core API",
    description="Transparently forwards REST calls to the upstream Janus (`/janus`) endpoint used by the web client.",
)
async def proxy_janus_root(request: Request) -> Response:
    return await janus_proxy.forward_request(request)


@router.api_route(
    "/janus/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE"],
    dependencies=[Depends(require_rate_limit), VIEWER_DEPENDENCY],
    include_in_schema=False,
)
async def proxy_janus_subpath(request: Request, path: str) -> Response:
    return await janus_proxy.forward_request(request, subpath=path)


@router.websocket("/janus-ws")
@router.websocket("/janus/ws")
async def janus_ws_proxy(client_ws: WebSocket) -> None:
    from app.services.ws_proxy import proxy_websocket

    # P0-SEC-001: WebSocket auth happens before accept. Reject unauthorized
    # with policy violation code so client distinguishes from generic errors.
    if not await require_viewer_ws(client_ws):
        await client_ws.close(code=1008)  # policy violation
        return

    settings = get_settings()
    upstream_url = settings.janus_ws_backends.get("1", f"ws://127.0.0.1:{PORTS.JANUS_WS}/janus-ws")
    await proxy_websocket(client_ws, upstream_url, pass_subprotocol=True, label="janus-ws")

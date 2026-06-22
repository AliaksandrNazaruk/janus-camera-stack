"""Stream-binding endpoints (Cycle 5 split): list / create / ensure-janus / remove + the per-binding
operator-console lifecycle (restart / stop / tuning get+set / fdir). Thin adapters over
app.application.stream_bindings; shared anchors + helpers read back through ``_core.``."""
from __future__ import annotations

import app.routes.stream_bindings as _core
from fastapi import APIRouter, Depends, HTTPException

from app.core.admin import require_admin
from app.middleware.rate_limit import require_admin_rate_limit
from app.application.stream_bindings import (
    AllocationConflict,
    BindingInvalid,
    BindingNodeNotFound,
    BindingNotFound,
    CreateBindingCommand,
    EnsureJanusCommand,
    EnsureJanusLocalRejected,
    GetTuningCommand,
    InvalidRotation,
    ListBindingsCommand,
    LocalBindingNotCreatable,
    LocalBindingNotRemovable,
    LocalFdirNotToggleable,
    LocalTuningRejected,
    NoTuningFields,
    NodeAgentError,
    RemoveBindingCommand,
    RestartBindingCommand,
    SetFdirCommand,
    SetTuningCommand,
    StopBindingCommand,
    UnsupportedSensorError,
    create_binding as create_binding_uc,
    ensure_janus,
    get_tuning,
    get_modes,
    list_bindings as list_bindings_uc,
    remove_binding,
    restart_binding,
    set_fdir,
    set_tuning,
    stop_binding,
)

from .contracts import (
    BindingCreateRequest,
    BindingOut,
    EnsureJanusResponse,
    FdirToggleRequest,
    TuningRequest,
)

router = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(require_admin)])
_RL = Depends(require_admin_rate_limit)


@router.get("/stream-bindings", summary="List stream bindings (local projections + remote)")
def get_stream_bindings(include_rtp_age: bool = False) -> dict:
    pairs = list_bindings_uc(
        ListBindingsCommand(include_rtp_age=include_rtp_age,
                            bind_state_path=_core.BIND_STATE_PATH, alloc_state_path=_core.ALLOC_STATE_PATH),
        rtp_age_fn=_core._rtp_age)
    return {"bindings": [_core._binding_out(b, rtp_age_ms=age).model_dump() for b, age in pairs]}


@router.post("/stream-bindings", dependencies=[_RL], summary="Create a remote stream binding")
def create_stream_binding(req: BindingCreateRequest) -> BindingOut:
    cmd = CreateBindingCommand(
        node_id=req.node_id, sensor=req.sensor, mountpoint_id=req.mountpoint_id, rtp_port=req.rtp_port,
        payload_type=req.payload_type, codec=req.codec, rtp_iface=req.rtp_iface,
        bind_state_path=_core.BIND_STATE_PATH, alloc_state_path=_core.ALLOC_STATE_PATH)
    try:
        binding = create_binding_uc(cmd)
    except LocalBindingNotCreatable as e:
        raise HTTPException(status_code=400, detail=str(e))
    except BindingNodeNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except AllocationConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except BindingInvalid as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _core._binding_out(binding)


@router.post("/stream-bindings/{binding_id}/ensure-janus", dependencies=[_RL],
             summary="Create/confirm the Janus mountpoint for a remote binding")
def ensure_janus_for_binding(binding_id: str) -> EnsureJanusResponse:
    cmd = EnsureJanusCommand(binding_id=binding_id,
                             bind_state_path=_core.BIND_STATE_PATH, alloc_state_path=_core.ALLOC_STATE_PATH)
    try:
        result = ensure_janus(cmd)
    except BindingNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except EnsureJanusLocalRejected as e:
        raise HTTPException(status_code=400, detail=str(e))
    return EnsureJanusResponse(status=result.status, mountpoint_id=result.mountpoint_id,
                               iface=result.iface, detail=result.detail)


@router.post("/stream-bindings/{binding_id}/remove", dependencies=[_RL],
             summary="Remove a remote binding (+ best-effort Janus teardown)")
def remove_stream_binding(binding_id: str) -> dict:
    cmd = RemoveBindingCommand(binding_id=binding_id,
                               bind_state_path=_core.BIND_STATE_PATH, alloc_state_path=_core.ALLOC_STATE_PATH)
    try:
        result = remove_binding(cmd)
    except BindingNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except LocalBindingNotRemovable as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"binding_id": result.binding_id, "removed": result.removed}


# ── per-binding lifecycle (operator console) ───────────────────────────

@router.post("/stream-bindings/{binding_id}/restart", dependencies=[_RL],
             summary="Restart one stream (remote: node-agent; local: encoder restart)")
def restart_stream_binding(binding_id: str) -> dict:
    cmd = RestartBindingCommand(binding_id=binding_id,
                                bind_state_path=_core.BIND_STATE_PATH, alloc_state_path=_core.ALLOC_STATE_PATH)
    try:
        result = restart_binding(cmd)
    except BindingNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.detail)
    return {"binding_id": result.binding_id, "ok": result.ok, "detail": result.detail}


@router.post("/stream-bindings/{binding_id}/stop", dependencies=[_RL],
             summary="Stop one stream (deliberate; pair with maintenance so FDIR won't restart it)")
def stop_stream_binding(binding_id: str) -> dict:
    cmd = StopBindingCommand(binding_id=binding_id,
                             bind_state_path=_core.BIND_STATE_PATH, alloc_state_path=_core.ALLOC_STATE_PATH)
    try:
        result = stop_binding(cmd)
    except BindingNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except UnsupportedSensorError as e:
        raise HTTPException(status_code=400, detail=f"unsupported sensor: {e}")
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.detail)
    return {"binding_id": result.binding_id, "ok": result.ok, "detail": result.detail}


@router.get("/stream-bindings/{binding_id}/tuning", dependencies=[_RL],
            summary="Read a REMOTE stream's encoder tuning (resolution/fps/rotation/bitrate)")
def get_binding_tuning(binding_id: str) -> dict:
    cmd = GetTuningCommand(binding_id=binding_id,
                           bind_state_path=_core.BIND_STATE_PATH, alloc_state_path=_core.ALLOC_STATE_PATH)
    try:
        return get_tuning(cmd)
    except BindingNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except LocalTuningRejected as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NodeAgentError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/stream-bindings/{binding_id}/modes", dependencies=[_RL],
            summary="List a REMOTE stream's supported encoder modes (resolution/fps)")
def get_binding_modes(binding_id: str) -> dict:
    cmd = GetTuningCommand(binding_id=binding_id, bind_state_path=_core.BIND_STATE_PATH,
                           alloc_state_path=_core.ALLOC_STATE_PATH)
    try:
        return get_modes(cmd)
    except BindingNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except LocalTuningRejected as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NodeAgentError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/stream-bindings/{binding_id}/tuning", dependencies=[_RL],
             summary="Set a REMOTE stream's encoder tuning + restart its encoder")
def set_binding_tuning(binding_id: str, req: TuningRequest) -> dict:
    cmd = SetTuningCommand(binding_id=binding_id, tuning=req.model_dump(exclude_none=True),
                           bind_state_path=_core.BIND_STATE_PATH, alloc_state_path=_core.ALLOC_STATE_PATH)
    try:
        return set_tuning(cmd)
    except BindingNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (LocalTuningRejected, InvalidRotation, NoTuningFields) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NodeAgentError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/stream-bindings/{binding_id}/fdir", dependencies=[_RL],
             summary="Enable/disable FDIR for one remote binding")
def set_binding_fdir(binding_id: str, req: FdirToggleRequest) -> BindingOut:
    cmd = SetFdirCommand(binding_id=binding_id, enabled=req.enabled,
                         bind_state_path=_core.BIND_STATE_PATH, alloc_state_path=_core.ALLOC_STATE_PATH)
    try:
        nb = set_fdir(cmd)
    except BindingNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except LocalFdirNotToggleable as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _core._binding_out(nb)

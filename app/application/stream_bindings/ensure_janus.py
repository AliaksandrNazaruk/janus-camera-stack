"""Use-case: create/confirm the Janus mountpoint for a REMOTE binding. Verbatim from
routes/stream_bindings.ensure_janus_for_binding (Phase 10.4). The Janus work is delegated to
binding_provision.ensure_janus (which owns the session/handle + control-plane contract); on
success the binding moves to WAITING_FOR_RTP. Local bindings are provisioned by the camera
lifecycle, not here. (No 502 path: a failed provision returns 200 carrying the outcome status.)"""
from __future__ import annotations

from app.services import binding_provision
from app.services import stream_binding_store as sbs
from app.services.audit_log import audit

from app.application.stream_bindings.commands import EnsureJanusCommand
from app.application.stream_bindings.results import (
    BindingNotFound,
    EnsureJanusLocalRejected,
    EnsureJanusResult,
)


def ensure_janus(cmd: EnsureJanusCommand) -> EnsureJanusResult:
    b = sbs.get_binding(cmd.binding_id, state_path=cmd.bind_state_path,
                        alloc_state_path=cmd.alloc_state_path)
    if b is None:
        raise BindingNotFound(cmd.binding_id)
    if b.mode != sbs.StreamMode.REMOTE_PRODUCER:
        raise EnsureJanusLocalRejected()

    from app.services.sensor_lifecycle import MP_DEFAULT_SECRET
    outcome = binding_provision.ensure_janus(b, mp_secret=MP_DEFAULT_SECRET)
    if outcome.ok:
        try:
            sbs.set_status(cmd.binding_id, sbs.StreamStatus.WAITING_FOR_RTP.value,
                           state_path=cmd.bind_state_path)
        except Exception:  # pragma: no cover
            pass
    audit("stream_bindings.binding.ensure_janus",
          {"binding_id": cmd.binding_id, "status": outcome.status.value, "mp": outcome.mountpoint_id})
    return EnsureJanusResult(status=outcome.status.value, mountpoint_id=outcome.mountpoint_id,
                             iface=outcome.iface, detail=outcome.detail)

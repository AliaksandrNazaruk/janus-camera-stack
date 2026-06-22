"""Use-case: enable/disable FDIR for one REMOTE stream binding. Verbatim from
routes/stream_bindings.set_binding_fdir (Phase 10.2). Local projection bindings have no
per-binding toggle (local FDIR = the cam10 watchdog ladder). Returns the updated
StreamBinding; the route renders it as BindingOut."""
from __future__ import annotations

from app.services import stream_binding_store as sbs
from app.services.audit_log import audit

from app.application.stream_bindings.commands import SetFdirCommand
from app.application.stream_bindings.results import BindingNotFound, LocalFdirNotToggleable


def set_fdir(cmd: SetFdirCommand) -> "sbs.StreamBinding":
    b = sbs.get_binding(cmd.binding_id, state_path=cmd.bind_state_path,
                        alloc_state_path=cmd.alloc_state_path)
    if b is None:
        raise BindingNotFound(cmd.binding_id)
    if b.mode != sbs.StreamMode.REMOTE_PRODUCER:
        raise LocalFdirNotToggleable()
    try:
        nb = sbs.set_fdir_enabled(cmd.binding_id, cmd.enabled, state_path=cmd.bind_state_path)
    except KeyError:
        raise BindingNotFound(cmd.binding_id)
    audit("stream_bindings.binding.fdir", {"binding_id": cmd.binding_id, "enabled": cmd.enabled})
    return nb

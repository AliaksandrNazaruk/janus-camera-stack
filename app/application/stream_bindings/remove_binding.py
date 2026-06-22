"""Use-case: remove a REMOTE stream binding (+ best-effort Janus mountpoint teardown).
Verbatim from routes/stream_bindings.remove_stream_binding (Phase 10.3). Local projections
can't be removed here (manage via the camera lifecycle)."""
from __future__ import annotations

import logging

from app.services import janus_admin
from app.services import stream_binding_store as sbs
from app.services.audit_log import audit

from app.application.stream_bindings.commands import RemoveBindingCommand
from app.application.stream_bindings.results import (
    BindingNotFound,
    LocalBindingNotRemovable,
    RemoveBindingResult,
)

log = logging.getLogger("stream_bindings")


def remove_binding(cmd: RemoveBindingCommand) -> RemoveBindingResult:
    b = sbs.get_binding(cmd.binding_id, state_path=cmd.bind_state_path,
                        alloc_state_path=cmd.alloc_state_path)
    if b is None:
        raise BindingNotFound(cmd.binding_id)
    if b.mode != sbs.StreamMode.REMOTE_PRODUCER:
        raise LocalBindingNotRemovable()

    # best-effort Janus teardown: janus_admin.destroy_mountpoint is @_with_handle-decorated,
    # so it creates the session + attaches the streaming handle internally — calling it with only
    # mp_id/mp_secret is correct. A failure (e.g. Janus unreachable) is swallowed and the store
    # removal proceeds. (Established pattern; cf. sensor_lifecycle's teardown.)
    try:
        from app.services.sensor_lifecycle import MP_DEFAULT_SECRET
        janus_admin.destroy_mountpoint(mp_id=b.janus.mountpoint_id, mp_secret=MP_DEFAULT_SECRET)
    except Exception as e:
        log.warning("destroy_mountpoint(%d) for %s: %s — proceeding",
                    b.janus.mountpoint_id, cmd.binding_id, e)

    removed = sbs.remove_binding(cmd.binding_id, state_path=cmd.bind_state_path)
    audit("stream_bindings.binding.remove", {"binding_id": cmd.binding_id, "removed": removed})
    return RemoveBindingResult(binding_id=cmd.binding_id, removed=removed)

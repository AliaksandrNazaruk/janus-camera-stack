"""Use-case: stop one stream binding (deliberate). Remote stops are made to STICK by marking the
binding offline + setting desired_up=False (the Start/Stop intent). The monitor gates recovery on
`desired_up AND fdir.enabled`, so a stopped binding is not auto-restarted — without disabling FDIR
(recovery stays a separate, independently-toggled concern)."""
from __future__ import annotations

from app.services import node_client
from app.services import stream_binding_store as sbs
from app.services.audit_log import audit

from app.application.stream_bindings.commands import StopBindingCommand
from app.application.stream_bindings.results import (
    BindingNotFound,
    BindingOpResult,
    UnsupportedSensorError,
)


def stop_binding(cmd: StopBindingCommand) -> BindingOpResult:
    b = sbs.get_binding(cmd.binding_id, state_path=cmd.bind_state_path,
                        alloc_state_path=cmd.alloc_state_path)
    if b is None:
        raise BindingNotFound(cmd.binding_id)

    if b.mode == sbs.StreamMode.LOCAL_PRODUCER:
        # Serial-aware local stop: flips the allocation's desired_active so the
        # projection reflects offline (a bare encoder stop would not). binding_id
        # is the allocator key '{serial}:{sensor}'.
        from app.services import sensor_lifecycle
        serial = cmd.binding_id.rsplit(":", 1)[0]
        try:
            # sensor_lifecycle.stop() returns the RUNNING state, not a success flag:
            # it returns (False, "…stopped") on success and raises LifecycleError on
            # failure. So success = it returned without raising; res_ok = not running.
            running, detail = sensor_lifecycle.stop(serial, b.sensor)
            res_ok = not running
        except sensor_lifecycle.UnsupportedSensor as e:
            raise UnsupportedSensorError(str(e))
        except sensor_lifecycle.LifecycleError as e:
            res_ok, detail = False, str(e)
    else:
        client = node_client.get_node_client(b.node_id, state_path=cmd.bind_state_path)
        r = client.stop_stream(b.node_id, b.sensor)
        res_ok, detail = r.ok, r.detail
        if res_ok:
            try:                          # reflect the deliberate stop in the stored status
                sbs.set_status(cmd.binding_id, sbs.StreamStatus.CONFIGURED_OFFLINE.value,
                               state_path=cmd.bind_state_path)
                # A stop must STICK without touching FDIR: set desired_up=False (the Start/Stop
                # intent). The monitor now gates recovery on `desired_up AND fdir.enabled`, so a
                # stopped binding is never auto-restarted even with FDIR left enabled — and the
                # reconcile leaves its mountpoint down. FDIR (recovery) stays whatever it was, shown
                # in its own column; Start (desired_up=True) resumes.
                sbs.set_desired_up(cmd.binding_id, False, state_path=cmd.bind_state_path)
            except Exception:  # pragma: no cover
                pass

    audit("stream_bindings.binding.stop", {"binding_id": cmd.binding_id, "ok": res_ok},
          outcome="success" if res_ok else "failure")
    return BindingOpResult(binding_id=cmd.binding_id, ok=res_ok, detail=detail)

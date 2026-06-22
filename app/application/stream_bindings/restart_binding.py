"""Use-case: restart one stream binding — RESUMES a stopped binding (symmetric local + remote).
LOCAL_PRODUCER → re-assert desired_active=True + bounce encoder; REMOTE → desired_up=True +
re-ensure the gateway mountpoint + bounce the node-agent stream."""
from __future__ import annotations

from app.services import node_client
from app.services import stream_binding_store as sbs
from app.services.audit_log import audit

from app.application.stream_bindings.commands import RestartBindingCommand
from app.application.stream_bindings.results import BindingNotFound, BindingOpResult


def restart_binding(cmd: RestartBindingCommand) -> BindingOpResult:
    b = sbs.get_binding(cmd.binding_id, state_path=cmd.bind_state_path,
                        alloc_state_path=cmd.alloc_state_path)
    if b is None:
        raise BindingNotFound(cmd.binding_id)

    if b.mode == sbs.StreamMode.LOCAL_PRODUCER:
        # cam10 projection. Re-assert "active" intent FIRST (desired_active=True),
        # then bounce the encoder. A bare encoder restart left desired_active=False,
        # so a restart of a stopped-but-live stream showed online RTP yet a stuck
        # 'configured_offline' status (and no Stop button) — the operator's bug.
        from app.services import sensor_lifecycle
        serial = cmd.binding_id.rsplit(":", 1)[0]
        try:
            sensor_lifecycle.set_desired(serial, b.sensor, True)
            sensor_lifecycle._encoder_action("restart", "rs-stream", b.sensor)
            res_ok, detail = True, f"restarted rs-stream@{b.sensor}"
        except Exception as e:  # noqa: BLE001
            res_ok, detail = False, f"local restart failed: {e}"
    else:
        # Mirror local: Restart RESUMES a stopped binding. Re-assert desired_up=True FIRST (durable
        # across gateway restarts), re-ensure the gateway mountpoint (a Stopped binding's listener
        # was torn down — best-effort), then bounce the node encoder.
        try:
            sbs.set_desired_up(cmd.binding_id, True, state_path=cmd.bind_state_path)
        except Exception:  # pragma: no cover
            pass
        try:
            from app.services import binding_provision
            from app.services.sensor_lifecycle import MP_DEFAULT_SECRET
            binding_provision.ensure_janus(b, mp_secret=MP_DEFAULT_SECRET)
        except Exception as e:  # noqa: BLE001 — best-effort; the node bounce still proceeds
            audit("stream_bindings.binding.restart_ensure_failed",
                  {"binding_id": cmd.binding_id, "error": str(e)[:120]}, outcome="failure")
        client = node_client.get_node_client(b.node_id, state_path=cmd.bind_state_path)
        r = client.restart_stream(b.node_id, b.sensor)
        res_ok, detail = r.ok, r.detail

    audit("stream_bindings.binding.restart", {"binding_id": cmd.binding_id, "ok": res_ok},
          outcome="success" if res_ok else "failure")
    return BindingOpResult(binding_id=cmd.binding_id, ok=res_ok, detail=detail)

"""Use-case: activate the LOCAL gateway camera's sensors (cam10), SYNCHRONOUS. Verbatim from
routes/stream_bindings._activate_local_streams (Phase 12.1). Per-sensor error channel (no raise) —
returns the same {node_id, sensors, started, poll, results} dict the route returned."""
from __future__ import annotations

from typing import Optional

from app.services import mountpoint_allocator
from app.services import stream_binding_store as sbs
from app.services.audit_log import audit

from app.application.stream_bindings.commands import ActivateLocalCommand


def _local_serial(alloc_state_path) -> Optional[str]:
    """Resolve the gateway's own camera serial — allocations FIRST (identity of record for
    already-onboarded streams; avoids the camera-swap clobber + the slow/throwing probe), device
    probe as fallback. Returns a REAL serial or None (never the 'local' sentinel). review H1."""
    try:
        allocs = mountpoint_allocator.list_allocations(state_path=alloc_state_path)
    except Exception:  # pragma: no cover - defensive
        allocs = {}
    counts: dict = {}
    for key in allocs:
        serial = key.rsplit(":", 1)[0] if ":" in key else ""
        if serial and serial != mountpoint_allocator.LOCAL_SERIAL:
            counts[serial] = counts.get(serial, 0) + 1
    if counts:  # most allocations wins; deterministic tie-break by serial
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    from app.services import device_registry
    return device_registry.local_serial()


def activate_local(cmd: ActivateLocalCommand) -> dict:
    from app.services import sensor_lifecycle
    serial = _local_serial(cmd.alloc_state_path)
    results = []
    for sensor in cmd.sensors:
        if serial is None and sensor != "color":
            results.append({"sensor": sensor, "ok": False, "mountpoint_id": None,
                            "detail": "no local camera serial resolved (attach camera / probe "
                                      "failed) — cannot activate a non-color sensor"})
            continue
        use_serial = serial if serial is not None else mountpoint_allocator.LOCAL_SERIAL
        try:
            running, msg, alloc = sensor_lifecycle.initialize(use_serial, sensor)
            results.append({"sensor": sensor, "ok": bool(running), "detail": msg,
                            "mountpoint_id": (alloc.mp_id if alloc else None)})
        except sensor_lifecycle.UnsupportedSensor as e:
            results.append({"sensor": sensor, "ok": False, "mountpoint_id": None,
                            "detail": f"unsupported sensor: {e}"})
        except sensor_lifecycle.LifecycleError as e:
            results.append({"sensor": sensor, "ok": False, "mountpoint_id": None,
                            "detail": str(e)})
    ok_all = bool(results) and all(r["ok"] for r in results)
    audit("stream_bindings.node.activate_streams_local",
          {"node_id": sbs.LOCAL_NODE_ID, "serial": serial, "sensors": cmd.sensors,
           "results": [{"sensor": r["sensor"], "ok": r["ok"]} for r in results]},
          outcome="success" if ok_all else "failure")
    return {"node_id": sbs.LOCAL_NODE_ID, "sensors": cmd.sensors,
            "started": True, "poll": None, "results": results}

"""initialize_sensor / stop_sensor — start/stop a (serial, sensor) encoder pipeline, FastAPI-free.

Resolves the sensor, drives sensor_lifecycle, emits the audit event, and returns a plain result the
route shapes into the HTTP payload. Re-raises sensor_lifecycle's domain errors (UnsupportedSensor →
501, LifecycleError → 500) and SensorUnknown (→ 404) for the route to map. The audit-on-failure /
audit-on-success behavior is preserved verbatim from the old route handlers. audit_log /
sensor_lifecycle are called module-qualified so a test can patch them at the source.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.services import audit_log, sensor_lifecycle
from app.application.device_camera.resolve import resolve_or_raise


@dataclass
class InitResult:
    running: bool
    message: str
    mountpoint_id: Optional[int]
    rtp_port: Optional[int]


@dataclass
class StopResult:
    running: bool
    message: str


def initialize_sensor(serial: str, sensor: str, *, source_ip=None, request_id=None) -> InitResult:
    """Resolve + start the pipeline + audit. Raises SensorUnknown (resolve) / UnsupportedSensor (501,
    audited) / LifecycleError (500, audited)."""
    resolve_or_raise(serial, sensor)
    action = f"POST /cameras/{serial}/{sensor}/initialize"
    target = f"sensor:{serial}:{sensor}"
    try:
        running, msg, alloc = sensor_lifecycle.initialize(serial, sensor)
    except sensor_lifecycle.UnsupportedSensor as e:
        audit_log.emit(action=action, target=target, outcome="failure",
                       source_ip=source_ip, request_id=request_id, user="admin",
                       details={"error": "unsupported_sensor", "msg": str(e)})
        raise
    except sensor_lifecycle.LifecycleError as e:
        audit_log.emit(action=action, target=target, outcome="error",
                       source_ip=source_ip, request_id=request_id, user="admin",
                       details={"error": "lifecycle_error", "msg": str(e)})
        raise
    audit_log.emit(action=action, target=target, outcome="success",
                   source_ip=source_ip, request_id=request_id, user="admin",
                   details={"mp_id": alloc.mp_id if alloc else None})
    return InitResult(running=running, message=msg,
                      mountpoint_id=alloc.mp_id if alloc else None,
                      rtp_port=alloc.rtp_port if alloc else None)


def stop_sensor(serial: str, sensor: str, *, source_ip=None, request_id=None) -> StopResult:
    """Resolve + stop the pipeline + audit. Raises SensorUnknown / UnsupportedSensor (501, NOT
    audited — matches the prior handler) / LifecycleError (500, audited)."""
    resolve_or_raise(serial, sensor)
    action = f"POST /cameras/{serial}/{sensor}/stop"
    target = f"sensor:{serial}:{sensor}"
    try:
        running, msg = sensor_lifecycle.stop(serial, sensor)
    except sensor_lifecycle.UnsupportedSensor:
        raise
    except sensor_lifecycle.LifecycleError as e:
        audit_log.emit(action=action, target=target, outcome="error",
                       source_ip=source_ip, request_id=request_id, user="admin",
                       details={"msg": str(e)})
        raise
    audit_log.emit(action=action, target=target, outcome="success",
                   source_ip=source_ip, request_id=request_id, user="admin")
    return StopResult(running=running, message=msg)

"""resolve_running_sensor — the (serial, sensor) guard, as a FastAPI-free use-case.

Raises domain errors the route maps to 404 / 501 / 409. The error message IS the operator-facing HTTP
detail (the route forwards `str(e)` unchanged), so the strings live here verbatim — moved out of
routes/device_camera.py's `_resolve_or_404` / `_require_running`. device_registry is called
module-qualified so a test can patch `device_registry.resolve_sensor` at the source.
"""
from __future__ import annotations

from app.services import device_registry
from app.services.device_registry import SensorEntry


class SensorUnknown(Exception):
    """No such (serial, sensor) on this node → route maps to 404."""

    def __init__(self, serial: str, sensor: str) -> None:
        self.serial, self.sensor = serial, sensor
        super().__init__(f"unknown (serial={serial}, sensor={sensor}) — see /cameras/dashboard.html")


class SensorNotProvisionable(Exception):
    """Sensor exists but has no pipeline implementation on this node → route maps to 501."""

    def __init__(self, serial: str, sensor: str) -> None:
        self.serial, self.sensor = serial, sensor
        super().__init__(
            f"sensor '{sensor}' on device {serial} cannot be provisioned on this node — "
            "depth/IR streams require pyrealsense2 → ffmpeg pipeline (Sprint X3). "
            "See /cameras/dashboard.html for supported sensors.")


class SensorStopped(Exception):
    """Pipeline is provisionable but not running → route maps to 409."""

    def __init__(self, serial: str, sensor: str) -> None:
        self.serial, self.sensor = serial, sensor
        super().__init__(
            f"sensor '{sensor}' pipeline is stopped. "
            f"POST /cameras/{serial}/{sensor}/initialize to start it. "
            "See /cameras/dashboard.html for state.")


def resolve_or_raise(serial: str, sensor: str) -> SensorEntry:
    """The (serial, sensor) must be known. Raises SensorUnknown otherwise."""
    entry = device_registry.resolve_sensor(serial, sensor)
    if entry is None:
        raise SensorUnknown(serial, sensor)
    return entry


def resolve_running_sensor(serial: str, sensor: str) -> SensorEntry:
    """Known + provisionable + running. Raises SensorUnknown / SensorNotProvisionable / SensorStopped
    (the route maps these to 404 / 501 / 409 with the message unchanged)."""
    entry = resolve_or_raise(serial, sensor)
    if not entry.provisioning_supported:
        raise SensorNotProvisionable(serial, sensor)
    if not entry.running:
        raise SensorStopped(serial, sensor)
    return entry

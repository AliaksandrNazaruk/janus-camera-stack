"""Sensor pipeline lifecycle (Sprint X3).

Initialize/stop full encoder pipeline for (serial, sensor). Phase 2: all sensors
stream through the pyrealsense2 mux producer + a per-sensor rs-stream@ consumer:
  color    — mux color.fifo → rs-stream@color → RTP → Janus static mp 1305
  depth    — mux depth.fifo → rs-stream@depth → RTP → Janus dynamic mp
  ir1      — mux ir1.fifo   → rs-stream@ir1   → RTP → Janus dynamic mp
  ir2      — mux ir2.fifo   → rs-stream@ir2   → RTP → Janus dynamic mp

For depth/IR: mountpoint_id and rtp_port are allocated dynamically at
initialize-time via mountpoint_allocator, registered via janus_admin
HTTP create_mountpoint (no jcfg edit). Allocations persist in JSON so
re-initialize gets the same IDs (stable viewer URLs).

A-04 (Phase 4): the implementation is split into focused modules — ``errors`` (domain exceptions),
``encoder_admin`` (the scoped-sudo encoder-admin port + readiness probes), ``contract_env`` (the
contract/tuning env store), and ``pipeline`` (initialize/stop + the cross-process lock). This package
facade re-exports the stable public API + the externally-used helpers + the re-exported allocator
symbols, so every caller keeps importing ``app.services.sensor_lifecycle.<name>`` unchanged.
"""
from app.services.mountpoint_allocator import (
    LOCAL_SERIAL,
    Allocation,
    allocate,
    ensure,
    get_allocation,
    migrate_color_key,
    set_desired,
)
from app.services.sensor_lifecycle.contract_env import (
    _contract_path,
    _ensure_default_tuning_env,
    _tuning_path,
    _write_contract_env,
)
from app.services.sensor_lifecycle.encoder_admin import (
    _ENCODER_ADMIN_CMD,
    _encoder_action,
    _encoder_status,
    encoder_running,
    is_running,
    mux_running,
)
from app.services.sensor_lifecycle.errors import LifecycleError, UnsupportedSensor
from app.services.sensor_lifecycle.pipeline import (
    _SENSOR_LOCK_DIR,
    _SENSOR_LOCK_TIMEOUT,
    _SENSOR_META,
    COLOR_ENCODER_INSTANCE,
    COLOR_MP_ID,
    COLOR_RTP_PORT,
    MP_DEFAULT_SECRET,
    _initialize_locked,
    _sensor_lock,
    _stop_locked,
    initialize,
    stop,
)

__all__ = [
    "COLOR_ENCODER_INSTANCE",
    # pipeline orchestration + constants
    "COLOR_MP_ID",
    "COLOR_RTP_PORT",
    "LOCAL_SERIAL",
    "MP_DEFAULT_SECRET",
    # encoder-admin port + readiness
    "_ENCODER_ADMIN_CMD",
    "_SENSOR_LOCK_DIR",
    "_SENSOR_LOCK_TIMEOUT",
    "_SENSOR_META",
    "Allocation",
    # exceptions
    "LifecycleError",
    "UnsupportedSensor",
    # contract/tuning env store
    "_contract_path",
    "_encoder_action",
    "_encoder_status",
    "_ensure_default_tuning_env",
    "_initialize_locked",
    "_sensor_lock",
    "_stop_locked",
    "_tuning_path",
    "_write_contract_env",
    # re-exported mountpoint_allocator symbols (callers use sensor_lifecycle.<name>)
    "allocate",
    "encoder_running",
    "ensure",
    "get_allocation",
    "initialize",
    "is_running",
    "migrate_color_key",
    "mux_running",
    "set_desired",
    "stop",
]

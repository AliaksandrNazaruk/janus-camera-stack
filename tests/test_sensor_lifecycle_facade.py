"""Facade-contract characterization for app.services.sensor_lifecycle (Phase 4 / A-04).

The module is being split into a package (errors / encoder_admin / contract_env / pipeline) behind an
UNCHANGED facade. 28 callers + the test suite reference these names as ``sensor_lifecycle.<name>`` —
this test locks that contract so the split can't silently drop a re-export. Verified green against the
pre-split module; it must stay green across the split.
"""
from __future__ import annotations

import pytest

from app.services import sensor_lifecycle as sl

# Public API + externally-used helpers + re-exported allocator symbols that callers reference as
# sensor_lifecycle.<name>. (The privates here ARE referenced externally — e.g. restart_binding and
# node_client call sensor_lifecycle._encoder_action; tests use sensor_lifecycle._sensor_lock.)
_FACADE_CALLABLES = [
    "initialize", "stop", "is_running", "mux_running", "encoder_running",
    "_encoder_action", "_encoder_status", "_sensor_lock",
    "_write_contract_env", "_contract_path", "_tuning_path", "_ensure_default_tuning_env",
    "set_desired", "allocate", "ensure", "get_allocation", "migrate_color_key",
]
_FACADE_NAMES = [
    "COLOR_MP_ID", "COLOR_RTP_PORT", "COLOR_ENCODER_INSTANCE",
    "_SENSOR_META", "MP_DEFAULT_SECRET", "_ENCODER_ADMIN_CMD", "LOCAL_SERIAL",
]


@pytest.mark.parametrize("name", _FACADE_CALLABLES)
def test_facade_exposes_callable(name):
    assert callable(getattr(sl, name)), f"sensor_lifecycle.{name} must be a callable on the facade"


@pytest.mark.parametrize("name", _FACADE_NAMES)
def test_facade_exposes_name(name):
    assert hasattr(sl, name), f"sensor_lifecycle.{name} must be exposed on the facade"


def test_facade_exposes_exceptions_and_allocation_type():
    assert issubclass(sl.UnsupportedSensor, Exception)
    assert issubclass(sl.LifecycleError, Exception)
    assert isinstance(sl.Allocation, type)  # the re-exported allocation type


def test_facade_pins_color_static_identity():
    """Color's static mountpoint identity + the encoder-admin command are part of the contract
    (jcfg + 28 callers depend on these exact values); pin them across the split."""
    assert sl.COLOR_MP_ID == 1305
    assert sl.COLOR_RTP_PORT == 5004
    assert sl.COLOR_ENCODER_INSTANCE == "color"
    assert set(sl._SENSOR_META) == {"color", "depth", "ir1", "ir2"}
    assert sl._ENCODER_ADMIN_CMD == ["sudo", "/usr/local/bin/encoder-admin"]

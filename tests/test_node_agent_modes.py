"""Probe-side mode enumeration (host_infra/node-bundle/probe/realsense_probe_cli.py).

The agent's GET /modes returns these so the gateway console can offer a real resolution/fps dropdown
for a REMOTE node. Hardware-free: fake pyrealsense2 profile objects exercise the grouping logic
(by sensor, by resolution, fps sorted desc; infrared → ir1/ir2 by stream index)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_PROBE = (Path(__file__).resolve().parent.parent
          / "host_infra" / "node-bundle" / "probe" / "realsense_probe_cli.py")
_spec = importlib.util.spec_from_file_location("realsense_probe_cli", _PROBE)
probe_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe_mod)


class _StreamType:
    def __init__(self, name):
        self._n = name

    def __str__(self):
        return "stream." + self._n


class _VideoProfile:
    def __init__(self, w, h):
        self._w, self._h = w, h

    def width(self):
        return self._w

    def height(self):
        return self._h


class _Profile:
    def __init__(self, stype, w, h, fps, index=0):
        self._st, self._w, self._h, self._fps, self._idx = stype, w, h, fps, index

    def is_video_stream_profile(self):
        return True

    def as_video_stream_profile(self):
        return _VideoProfile(self._w, self._h)

    def fps(self):
        return self._fps

    def stream_type(self):
        return _StreamType(self._st)

    def stream_index(self):
        return self._idx


class _Sensor:
    def __init__(self, profiles):
        self._p = profiles

    def get_stream_profiles(self):
        return self._p


class _Device:
    def __init__(self, sensors):
        self._s = sensors

    def query_sensors(self):
        return self._s


def test_modes_grouped_by_sensor_and_resolution():
    dev = _Device([_Sensor([
        _Profile("color", 1280, 720, 30),
        _Profile("color", 1280, 720, 15),
        _Profile("color", 640, 480, 30),
        _Profile("depth", 848, 480, 90),
    ])])
    modes = probe_mod._modes_for_device(dev)
    assert "color" in modes and "depth" in modes
    color = {(m["width"], m["height"]): m["fps"] for m in modes["color"]}
    assert color[(1280, 720)] == [30, 15]          # fps merged + sorted desc
    assert color[(640, 480)] == [30]
    assert modes["depth"][0] == {"width": 848, "height": 480, "fps": [90]}


def test_infrared_split_into_ir1_ir2_by_index():
    dev = _Device([_Sensor([
        _Profile("infrared", 640, 480, 30, index=1),
        _Profile("infrared", 640, 480, 30, index=2),
    ])])
    modes = probe_mod._modes_for_device(dev)
    assert "ir1" in modes and "ir2" in modes


def test_non_video_profile_skipped():
    class _AudioProfile(_Profile):
        def is_video_stream_profile(self):
            return False
    dev = _Device([_Sensor([_AudioProfile("color", 0, 0, 0)])])
    assert probe_mod._modes_for_device(dev) == {}

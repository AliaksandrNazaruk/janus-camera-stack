"""Device inventory (Phase 3A of admin_dashboard split, C-04): v4l2/realsense probing.

Read-only, low-risk. The assertions were first run against the old admin_dashboard helpers
to lock behavior; here re-pointed to services/v4l2.py + application/device_inventory.py with
the SAME assertions (preservation proof). No Janus code touched.
"""
from __future__ import annotations

import inspect
import os
import sys

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.services import v4l2
from app.application import device_inventory as di


class _Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


_LIST_DEVICES = (
    "Logitech Webcam (usb-0000:01:00.0-1.2):\n"
    "\t/dev/video0\n"
    "\t/dev/video1\n"
    "\n"
    "RealSense D435 (usb-0000:01:00.0-2):\n"
    "\t/dev/video2\n"
    "\t/dev/media0\n"          # controller node — must be dropped
)


def test_parse_list_devices():
    groups = v4l2.parse_list_devices(_LIST_DEVICES)
    assert len(groups) == 2
    assert groups[0]["label"] == "Logitech Webcam" and groups[0]["bus"] == "usb-0000:01:00.0-1.2"
    assert groups[0]["devices"] == ["/dev/video0", "/dev/video1"]
    assert groups[1]["devices"] == ["/dev/video2"]      # /dev/media0 dropped


def test_probe_device_formats(monkeypatch):
    def run(cmd, **k):
        if "v4l2-driver-info" in cmd:
            return _Done(stdout="Capabilities\nVideo Capture\nStreaming\n0xdeadbeef\n")
        return _Done(stdout="[0]: 'YUYV' (YUYV 4:2:2)\n\tSize: Discrete 640x480\n\tSize: Discrete 1280x720\n")
    monkeypatch.setattr(v4l2.subprocess, "run", run)
    caps, formats = v4l2.probe_device_formats("/dev/video0")
    assert caps == ["video_capture", "streaming"]
    assert formats == ["YUYV 640x480", "YUYV 1280x720"]


def test_list_v4l2_devices_camera_admin_path(monkeypatch):
    monkeypatch.setattr(v4l2.subprocess, "run", lambda *a, **k: _Done(returncode=0, stdout=_LIST_DEVICES))
    devs = di.list_v4l2_devices(probe_formats=False)
    assert [d.path for d in devs] == ["/dev/video0", "/dev/video1", "/dev/video2"]
    assert devs[0].label == "Logitech Webcam" and devs[0].bus == "usb-0000:01:00.0-1.2"
    assert all(d.is_capture for d in devs)             # probe_formats=False -> assume capture


def test_enumerate_devices_fallback_glob(monkeypatch):
    def boom(*_a, **_k):
        raise FileNotFoundError()
    monkeypatch.setattr(v4l2.subprocess, "run", boom)
    monkeypatch.setattr(v4l2.Path, "glob", lambda self, pat: [v4l2.Path("/dev/video9")])
    devs = v4l2.enumerate_devices(probe_formats=False)
    assert devs == [{"path": "/dev/video9", "label": "(unknown device)", "bus": None,
                     "capabilities": [], "formats": [], "is_capture": True}]


# realsense: use-case maps services.realsense_probe.probe() -> response model
class _Prof:
    def __init__(self):
        self.stream, self.format, self.width, self.height, self.fps, self.index = "depth", "Z16", 640, 480, 30, 0


class _RsDev:
    def __init__(self):
        self.serial, self.name, self.firmware = "141722072135", "Intel RealSense D435", "5.13"
        self.product_id, self.usb_port, self.physical_port = "0B07", "2-1", "phys"
        self.profiles = [_Prof()]


class _Result:
    devices = [_RsDev()]
    available = True
    error = None


def test_list_realsense_devices_maps_probe(monkeypatch):
    from app.services import realsense_probe
    monkeypatch.setattr(realsense_probe, "probe", lambda include_profiles=True: _Result())
    resp = di.list_realsense_devices(include_profiles=True)
    assert resp.available is True and resp.error is None and len(resp.devices) == 1
    d = resp.devices[0]
    assert d.serial == "141722072135" and d.name == "Intel RealSense D435" and d.firmware == "5.13"
    assert len(d.profiles) == 1 and d.profiles[0].stream == "depth" and d.profiles[0].width == 640


def test_routes_delegate_no_subprocess():
    from app.routes import admin_dashboard as ad
    for fn in (ad.list_v4l2_devices, ad.list_realsense_devices):
        src = inspect.getsource(fn)
        assert "device_inventory." in src and "subprocess" not in src

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.settings import get_settings
from app.services.system import run

log = logging.getLogger(__name__)


def list_v4l2_modes(dev: str | None = None) -> Dict[str, Any]:
    device = dev or get_settings().camera_device
    output = run(["sudo", "/usr/local/bin/camera-admin", "--device", device,
                  "v4l2-formats"], timeout=8)
    blocks = output.split("Type: Video Capture")
    modes = []
    for block in blocks:
        if "'YUYV'" not in block:
            continue
        for match in re.finditer(
            r"Size:\s+Discrete\s+(\d+)x(\d+)(.*?)(?=Size:|$)", block, re.S
        ):
            width, height = int(match.group(1)), int(match.group(2))
            tail = match.group(3)
            fps = set()
            for fps_match in re.finditer(
                r"Interval:\s+Discrete\s+([0-9.]+)s\s+\(([\d.]+)\s+fps\)", tail
            ):
                fps.add(int(round(float(fps_match.group(2)))))
            modes.append({"width": width, "height": height, "fps": sorted(fps, reverse=True)})
    return {"pixel_format": "YUYV", "device": device, "modes": modes}


def is_supported(mode_list: Dict[str, Any], width: int, height: int, fps: int) -> bool:
    for mode in mode_list["modes"]:
        if mode["width"] == width and mode["height"] == height and fps in mode["fps"]:
            return True
    return False


def v4l2_current(dev: str | None = None) -> Dict[str, Any]:
    device = dev or get_settings().camera_device
    # camera-admin v4l2-info returns concatenated --get-fmt-video + --get-parm output
    combined = run(["sudo", "/usr/local/bin/camera-admin", "--device", device,
                    "v4l2-info"], timeout=8)
    fmt = prm = combined
    fmt_match = re.search(r"Width/Height\s*:\s*(\d+)/(\d+)", fmt)
    width, height = (int(fmt_match.group(1)), int(fmt_match.group(2))) if fmt_match else (None, None)
    pix_match = re.search(r"Pixel\s*Format\s*:\s*'([A-Z0-9]+)'", fmt)
    pix_fmt = pix_match.group(1) if pix_match else None
    fps_match = re.search(r"Frames per second\s*:\s*([\d.]+)", prm)
    fps = int(round(float(fps_match.group(1)))) if fps_match else None
    return {"width": width, "height": height, "fps": fps, "pixfmt": pix_fmt}


def list_v4l2_ctrls(dev: str | None = None) -> Dict[str, Any]:
    device = dev or get_settings().camera_device
    output = run(["sudo", "/usr/local/bin/camera-admin", "--device", device,
                  "v4l2-ctrls"], timeout=8)
    controls = {}
    for line in output.splitlines():
        match = re.match(
            r"^(\w[\w\-]*)\s+\((\w+)\)\s*:\s*min=([-0-9]+)\s+max=([-0-9]+)\s+step=([-0-9]+)\s+default=([-0-9]+)\s+value=([-0-9]+)",
            line.strip(),
        )
        if not match:
            fallback = re.match(r"^(\w[\w\-]*)\s+\((\w+)\)\s*:\s*(.*)$", line.strip())
            if fallback:
                name, typ, tail = fallback.groups()
                controls[name] = {"type": typ, "raw": tail}
            continue
        name, typ, vmin, vmax, step, default, value = match.groups()
        controls[name] = {
            "type": typ,
            "min": int(vmin),
            "max": int(vmax),
            "step": int(step),
            "default": int(default),
            "value": int(value),
        }
    return {"device": device, "controls": controls}


# ── device enumeration (moved from admin_dashboard, C-04 Phase 3A) ──────────

def parse_list_devices(output: str) -> List[Dict[str, Any]]:
    """Parse `v4l2-ctl --list-devices` output into [{label, bus, devices:[paths]}].
    Only /dev/video* (capture) nodes are kept; /dev/media* controller nodes dropped."""
    groups: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for raw in output.splitlines():
        if not raw.strip():
            current = None
            continue
        if not raw.startswith((" ", "\t")):
            line = raw.rstrip(":").strip()
            label = line
            bus = None
            if "(" in line and line.endswith(")"):
                label = line[: line.rindex("(")].strip()
                bus = line[line.rindex("(") + 1: -1]
            current = {"label": label, "bus": bus, "devices": []}
            groups.append(current)
        else:
            dev = raw.strip()
            if dev.startswith("/dev/video") and current is not None:
                current["devices"].append(dev)
    return groups


def probe_device_formats(dev_path: str, limit_formats: int = 4) -> Tuple[List[str], List[str]]:
    """Returns (capabilities, formats_summary) for a /dev/video* node (via camera-admin)."""
    caps: List[str] = []
    formats: List[str] = []
    try:
        r = subprocess.run(
            ["sudo", "/usr/local/bin/camera-admin", "--device", dev_path, "v4l2-driver-info"],
            capture_output=True, text=True, timeout=4,
        )
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                line = line.strip()
                if line.startswith("Capabilities"):
                    continue
                if line.startswith(("0x")):
                    continue
                if line in (
                    "Video Capture", "Streaming", "Read/Write",
                    "Metadata Capture", "Video Output",
                ):
                    caps.append(line.lower().replace(" ", "_"))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        r = subprocess.run(
            ["sudo", "/usr/local/bin/camera-admin", "--device", dev_path, "v4l2-formats"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            current_fmt = None
            for line in r.stdout.splitlines():
                line = line.strip()
                if line.startswith("[") and "'" in line:
                    s = line.split("'")
                    if len(s) >= 2:
                        current_fmt = s[1]
                elif line.startswith("Size:") and current_fmt:
                    parts = line.split()
                    if len(parts) >= 3:
                        dims = parts[-1]
                        formats.append(f"{current_fmt} {dims}")
                        if len(formats) >= limit_formats:
                            break
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return caps, formats


def enumerate_devices(probe_formats: bool = False) -> List[Dict[str, Any]]:
    """List attached V4L2 capture devices as raw dicts (path/label/bus/capabilities/
    formats/is_capture). probe_formats=true triggers a per-device format probe; falls
    back to a bare /dev/video* glob if camera-admin is unavailable."""
    devices: List[Dict[str, Any]] = []
    try:
        r = subprocess.run(
            ["sudo", "/usr/local/bin/camera-admin", "v4l2-list-devices"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            for g in parse_list_devices(r.stdout):
                for dev_path in g.get("devices", []):
                    caps: List[str] = []
                    formats: List[str] = []
                    if probe_formats:
                        caps, formats = probe_device_formats(dev_path)
                    is_capture = "video_capture" in caps if probe_formats else True
                    devices.append({
                        "path": dev_path, "label": g.get("label", "Unknown"),
                        "bus": g.get("bus"), "capabilities": caps,
                        "formats": formats, "is_capture": is_capture,
                    })
            return devices
    except (FileNotFoundError, subprocess.TimeoutExpired):
        log.debug("v4l2-ctl unavailable — fallback to raw glob")

    for dev_path in sorted(Path("/dev").glob("video*")):
        devices.append({
            "path": str(dev_path), "label": "(unknown device)",
            "bus": None, "capabilities": [], "formats": [], "is_capture": True,
        })
    return devices



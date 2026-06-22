"""RealSense per-sensor mode catalog.

Queries the Intel RealSense SDK (pyrealsense2) to enumerate all sensors
attached to the host and their supported video stream profiles. Result
is consumed by the camera-config UI to populate richer mode pickers
than raw V4L2 (which only sees one sub-device of the multi-sensor device).

If pyrealsense2 is unavailable or no device is connected, callers should
fall back to v4l2.list_v4l2_modes().
"""
from __future__ import annotations

from typing import Any, Dict, List


def query_catalog() -> Dict[str, Any]:
    """Return per-sensor profile catalog from pyrealsense2.

    Returns a dict matching the JSON contract consumed by /sensors:
        {
          "device": "Intel RealSense D435I",
          "serial": "141722072135",
          "firmware": "5.16.0.1",
          "sensors": [
            {
              "key": "color", "label": "RGB Camera",
              "formats": ["YUYV", "RGB8"],
              "modes": [{"width": W, "height": H, "fps": F, "format": "YUYV"}, ...]
            },
            ...
          ]
        }

    Raises:
        RuntimeError: if pyrealsense2 not installed or no device found.
    """
    try:
        import pyrealsense2 as rs
    except ImportError as e:
        raise RuntimeError("pyrealsense2 not installed") from e

    ctx = rs.context()
    devices = list(ctx.query_devices())
    if not devices:
        raise RuntimeError("no RealSense device found")

    dev = devices[0]
    try:
        name = dev.get_info(rs.camera_info.name)
    except Exception:
        name = "RealSense"
    try:
        serial = dev.get_info(rs.camera_info.serial_number)
    except Exception:
        serial = ""
    try:
        firmware = dev.get_info(rs.camera_info.firmware_version)
    except Exception:
        firmware = ""

    # Group raw profiles by sensor purpose. Stereo Module exposes both depth
    # and IR streams; RGB Camera exposes color. We split into functional keys
    # (color/depth/ir) so the UI can present a sensor selector independent
    # of physical module boundaries.
    sensors_dict: Dict[str, Dict[str, Any]] = {}

    for sensor in dev.query_sensors():
        try:
            sensor_name = sensor.get_info(rs.camera_info.name)
        except Exception:
            sensor_name = "sensor"

        for profile in sensor.get_stream_profiles():
            try:
                vp = profile.as_video_stream_profile()
            except Exception:
                continue
            if vp is None:
                continue

            stream_type = vp.stream_type()
            fmt = str(vp.format()).replace("format.", "")
            w = vp.width()
            h = vp.height()
            fps = vp.fps()

            if stream_type == rs.stream.color:
                key = "color"
                label = "RGB Camera"
            elif stream_type == rs.stream.depth:
                key = "depth"
                label = "Depth (Z16)"
            elif stream_type == rs.stream.infrared:
                key = "ir"
                # IR sensors come in left (index 1) and right (index 2)
                try:
                    idx = vp.stream_index()
                except Exception:
                    idx = 0
                label = f"IR{idx}" if idx else "IR"
                key = f"ir{idx}" if idx else "ir"
            else:
                # skip motion / pose / fisheye for now — not encoder-compatible
                continue

            if key not in sensors_dict:
                sensors_dict[key] = {
                    "key": key,
                    "label": label,
                    "sensor_name": sensor_name,
                    "formats": set(),
                    "modes_index": {},  # (w, h, fps, fmt) -> mode dict (dedupe)
                }
            sensors_dict[key]["formats"].add(fmt)
            mkey = (w, h, fps, fmt)
            if mkey not in sensors_dict[key]["modes_index"]:
                sensors_dict[key]["modes_index"][mkey] = {
                    "width": w, "height": h, "fps": fps, "format": fmt,
                }

    # Materialize to JSON-safe list, sort
    out_sensors: List[Dict[str, Any]] = []
    sensor_order = ["color", "depth", "ir", "ir1", "ir2"]
    sorted_keys = sorted(
        sensors_dict.keys(),
        key=lambda k: (sensor_order.index(k) if k in sensor_order else 99, k),
    )
    for k in sorted_keys:
        s = sensors_dict[k]
        modes = list(s["modes_index"].values())
        modes.sort(key=lambda m: (-m["width"] * m["height"], -m["fps"]))
        out_sensors.append({
            "key": s["key"],
            "label": s["label"],
            "sensor_name": s["sensor_name"],
            "formats": sorted(s["formats"]),
            "modes": modes,
        })

    return {
        "device": name,
        "serial": serial,
        "firmware": firmware,
        "sensors": out_sensors,
    }

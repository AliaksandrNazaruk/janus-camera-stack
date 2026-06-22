#!/usr/bin/env python3
"""Standalone RealSense probe for the camera-node bootstrap.

Bundle-local and dependency-light (only pyrealsense2) — it does NOT import the
gateway app. Prints a JSON device inventory (serial + sensors). With --require,
exits non-zero when no camera is present, so the bootstrap can refuse to deploy
the stack onto an empty host (probe-first → clean teardown on no_camera).
"""
from __future__ import annotations

import argparse
import json
import sys


def _sensor_name(p) -> str:
    """Map a stream profile to the agent's sensor key (color/depth/ir1/ir2)."""
    st = str(p.stream_type()).split(".")[-1].lower()
    if st == "infrared":
        try:
            return "ir%d" % (p.stream_index() or 1)
        except Exception:
            return "ir1"
    return st


def _modes_for_device(d) -> dict:
    """{sensor: [{width,height,fps:[...]}]} from the SDK stream profiles — the authoritative
    supported-modes source for a RealSense camera (enumeration only; does not open a stream, so
    it is safe while the mux holds the device)."""
    by_sensor: dict = {}
    try:
        for s in d.query_sensors():
            for p in s.get_stream_profiles():
                try:
                    if not p.is_video_stream_profile():
                        continue
                    vp = p.as_video_stream_profile()
                    res = by_sensor.setdefault(_sensor_name(p), {}).setdefault(
                        (vp.width(), vp.height()), set())
                    res.add(int(p.fps()))
                except Exception:
                    continue
    except Exception:
        return {}
    return {
        sensor: [{"width": w, "height": h, "fps": sorted(f, reverse=True)}
                 for (w, h), f in sorted(rm.items())]
        for sensor, rm in by_sensor.items()
    }


def probe(include_modes: bool = False) -> dict:
    try:
        import pyrealsense2 as rs
    except Exception as e:  # pragma: no cover - hardware/runtime dependent
        return {"available": False, "error": f"pyrealsense2 import failed: {e}", "devices": []}
    try:
        ctx = rs.context()
        devices = []
        for d in ctx.devices:
            try:
                serial = d.get_info(rs.camera_info.serial_number)
                name = d.get_info(rs.camera_info.name)
            except Exception:
                serial, name = None, None
            sensors = set()
            try:
                for s in d.query_sensors():
                    for p in s.get_stream_profiles():
                        sensors.add(str(p.stream_type()).split(".")[-1].lower())
            except Exception:
                pass
            entry = {"serial": serial, "name": name, "sensors": sorted(sensors)}
            if include_modes:
                entry["modes"] = _modes_for_device(d)
            devices.append(entry)
        return {"available": len(devices) > 0, "error": None, "devices": devices}
    except Exception as e:  # pragma: no cover - hardware/runtime dependent
        return {"available": False, "error": str(e), "devices": []}


def main() -> None:
    ap = argparse.ArgumentParser(description="Standalone RealSense probe (node bootstrap)")
    ap.add_argument("--json", action="store_true", help="print JSON inventory")
    ap.add_argument("--require", action="store_true", help="exit non-zero if no camera found")
    ap.add_argument("--modes", action="store_true",
                    help="include per-sensor supported {width,height,fps} modes")
    args = ap.parse_args()
    result = probe(include_modes=args.modes)
    if args.json or not args.require:
        print(json.dumps(result, indent=2))
    if args.require and not result["available"]:
        sys.exit(2)


if __name__ == "__main__":
    main()

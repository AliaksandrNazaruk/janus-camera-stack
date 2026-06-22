"""Example sensor plugin: generic USB webcam (V4L2-attached).

Demonstrates registering custom sensor type. Copy this file to
/etc/robot/plugins.d/ + edit defaults, restart camera-page service —
new sensor option appears in dashboard.

Distinct from built-in `color` sensor (which is D435i-specific) — `webcam`
key uses generic V4L2 adapter, defaults to /dev/video0 unless override.
"""
from app.services.sensor_registry import SensorType, register_sensor_type

register_sensor_type(SensorType(
    key="webcam",
    label="USB Webcam (generic V4L2)",
    encoder_family="rtp-v4l2",
    encoder_instance_pattern="webcam-{index}",   # webcam-0, webcam-1, etc.
    default_pix_fmt="",          # blank = auto-detect (rtp-v4l2.sh probes device)
    default_width=640,
    default_height=480,
    default_fps=30,
    default_bitrate_kbps=1500,
    is_dynamic_mountpoint=True,
))

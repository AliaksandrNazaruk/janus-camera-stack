# Camera Adapter Contract

Generic stack supports multiple camera sources via pluggable **adapters**.
Each adapter implements the same contract: capture frames from hardware →
encode to H264 → push RTP to a Janus mountpoint.

L4 lifecycle code knows nothing about specific adapters — only that they
implement the contract.

## Adapter Taxonomy (current)

| Adapter | Hardware | Process | systemd unit |
|---|---|---|---|
| `rs-stream` | RealSense color/depth/IR via FIFO consumer | Bash + ffmpeg | `rs-stream@<instance>.service` |
| `realsense-mux` | RealSense producer (pyrealsense2 → FIFO) | Python + pyrealsense2 | `realsense-mux.service` |
| `rtp-rgb` (retired for on-board D435i) | Legacy D435i color via V4L2 sub-device — superseded by `rs-stream@color` (mux path) | Bash + ffmpeg | `rtp-rgb@<instance>.service` |
| `rtp-v4l2` (NEW B2) | Generic V4L2 device (USB webcam, IP camera USB driver, etc.) | Bash + ffmpeg | `rtp-v4l2@<instance>.service` |
| `rtp-rtsp` (NEW B4) | IP camera RTSP URL | Bash + ffmpeg | `rtp-rtsp@<instance>.service` |
| `rtp-gst` (future) | GStreamer pipeline (any source) | Bash + gst-launch | `rtp-gst@<instance>.service` |

## Adapter Contract

Every adapter MUST:

### 1. Lifecycle
- Implement `start.sh <instance>` (or equivalent ExecStart) that:
  - Loads config from env files `/etc/robot/<family>-<instance>.tuning.env` + `.contract.env`
  - Captures frames from the hardware source
  - Encodes to H264 baseline profile (browser-compatible)
  - Pushes RTP to 127.0.0.1:${PORT} (UDP)
  - Runs forever — exits cleanly on SIGTERM
- Implement `stop` via standard systemd kill (SIGTERM, 5sec timeout)

### 2. Configuration contract
Adapter reads these env vars (set by L4 lifecycle via contract.env + tuning.env):
```
# Required:
PORT          — RTP destination port on Janus (L4-allocated)

# Usually required:
WIDTH         — frame width in pixels
HEIGHT        — frame height in pixels
FPS           — frames per second

# Optional (adapter-specific):
BITRATE_KBPS  — H264 bitrate target
GOP           — keyframe interval
PRESET        — x264 preset (ultrafast/veryfast/...)
TUNE          — x264 tune (zerolatency/film/...)
ROTATION      — 0|90|180|270 (passthrough OR adapter-side rotate)
PIX_FMT       — input pixel format (adapter-specific; e.g. yuyv422, rgb24, gray)
```

### 3. Output contract
- H264 baseline profile (`-profile:v baseline`)
- yuv420p pixel format (`-pix_fmt yuv420p`)
- RTP encapsulation (`-f rtp`)
- Packet size ≤1200 bytes (`pkt_size=1200`)
- fmtp: `profile-level-id=42e01f;packetization-mode=1;level-asymmetry-allowed=1`
- Destination: `rtp://127.0.0.1:${PORT}`

### 4. Encoder family registration
Add to the `encoder-admin.py` `UNIT_FAMILIES` dict:
```python
UNIT_FAMILIES = {
    ...
    "rtp-v4l2": {"template": "rtp-v4l2@{instance}.service", "instanced": True},
}
```

### 5. systemd unit template
- Type=simple
- User=boris (or appropriate user with /dev/video* access)
- ExecStart=/usr/local/bin/<adapter-script>.sh %i
- Restart=on-failure
- KillSignal=SIGTERM
- TimeoutStopSec=5

## Existing implementations

### realsense-mux + rs-stream (color/depth/IR)
- `realsense-mux.py` runs the pyrealsense2 pipeline, writes color/depth/IR to FIFOs
  (`RS_ENABLE_COLOR=1` enables the color stream → `/run/realsense/color.fifo`)
- `rs-stream@<sensor>.service` runs ffmpeg reading specific FIFO → RTP
  (e.g. `rs-stream@color` → `/usr/local/bin/rs-stream.sh color` → Janus MP 1305 port 5004,
  with JPEG snapshot at `/run/realsense/color-snapshot.jpg` for the health watchdog)
- Refcount mux through systemd `Requires=realsense-mux.service`
- `rs.align` in the mux provides aligned depth so click-to-depth works on the color viewer

### rtp-rgb (legacy D435i color — RETIRED for on-board camera)
`host_infra/roles/encoder/files/rtp-rgb.sh`
- Historical path: hardcoded to the D435i RGB sub-device (`/dev/cam-rgb` udev symlink),
  YUYV422 capture, JPEG snapshot + stale-snapshot watchdog
- On-board D435i color now flows via the realsense-mux FIFO → `rs-stream@color` (above)

### rtp-v4l2 (Sprint B2)
`host_infra/roles/encoder/files/rtp-v4l2.sh`
- Generic — works with any V4L2 device, not only the D435i
- Auto-detects pixel format from `v4l2-ctl --list-formats-ext`
- Configurable device path (env DEVICE)
- Optional snapshot (off by default)
- Use case: USB webcam, dashcam, capture card, IP camera USB driver

### rtp-rtsp (NEW Sprint B4)
`host_infra/roles/encoder/files/rtp-rtsp.sh`
- Connect to an RTSP URL (IP camera, NVR, network capture)
- TCP transport default (reliable); UDP option (lower latency)
- 5sec socket timeout with systemd restart on disconnect
- Optional downscale + FPS limit (default passthrough)
- Use case: existing IP camera infrastructure, ONVIF cameras,
  RTSP streams from NVR systems

## Adding your own adapter

Two-step:

### Step 1: Write the script
```bash
# host_infra/roles/encoder/files/rtp-myadapter.sh
#!/usr/bin/env bash
set -euo pipefail

INSTANCE="${1:?usage: $0 <instance>}"
for f in "/etc/robot/rtp-myadapter-$INSTANCE.tuning.env" \
         "/etc/robot/rtp-myadapter-$INSTANCE.contract.env"; do
  [ -f "$f" ] && . "$f"
done

# Your capture/encode logic here. Must output RTP to 127.0.0.1:$PORT
exec ffmpeg -y -nostdin -hide_banner -loglevel warning \
  ... your capture pipeline ... \
  -c:v libx264 -preset veryfast -tune zerolatency \
  -pix_fmt yuv420p -profile:v baseline \
  -b:v ${BITRATE_KBPS}k \
  -f rtp "rtp://127.0.0.1:${PORT}?pkt_size=1200"
```

### Step 2: Register with encoder-admin
Edit `host_infra/roles/encoder/files/encoder-admin.py`:
```python
UNIT_FAMILIES = {
    ...
    "rtp-myadapter": {"template": "rtp-myadapter@{instance}.service", "instanced": True},
}
```

### Step 3: Create systemd template + env files
- `host_infra/roles/encoder/files/rtp-myadapter@.service`
- `/etc/robot/rtp-myadapter-<instance>.tuning.env`
- `/etc/robot/rtp-myadapter-<instance>.contract.env` (PORT)

### Step 4: Wire to L4 (optional)
For dashboard integration:
- Extend `device_registry.py` to recognize your sensor type
- Extend `sensor_lifecycle.py` `initialize/stop` to invoke encoder-admin
- Map sensor key → adapter family

For simple deployment (no dashboard), the operator manually starts:
```bash
sudo systemctl start rtp-myadapter@<instance>
```

## Sensor type plugins (Sprint B5)

Stack supports plugin-registered sensor types — operator can add new
sensors to the dashboard without editing L4 code. Plugins live in `/etc/robot/plugins.d/*.py`
and call `register_sensor_type()` at import.

### Plugin format

```python
# /etc/robot/plugins.d/my_thermal_camera.py
from app.services.sensor_registry import SensorType, register_sensor_type

register_sensor_type(SensorType(
    key="thermal",
    label="Thermal Camera (FLIR Lepton)",
    encoder_family="rtp-v4l2",
    encoder_instance_pattern="thermal-{index}",
    default_pix_fmt="gray",
    default_width=160,
    default_height=120,
    default_fps=9,
    default_bitrate_kbps=500,
    is_dynamic_mountpoint=True,
))
```

### Deployment

```bash
sudo mkdir -p /etc/robot/plugins.d
sudo cp janus_camera_page/app/plugins/example_usb_webcam.py /etc/robot/plugins.d/
sudo systemctl restart janus-camera-page

# Verify plugin loaded:
sudo journalctl -u janus-camera-page | grep "sensor plugin"
# Expected: "Loaded 1 sensor plugin(s)"
```

### Built-in types

Pre-registered (no plugin needed):
- `color` — D435i RGB (static mountpoint 1305, port 5004)
- `depth` — RealSense depth via rs-stream (dynamic alloc)
- `ir1`, `ir2` — RealSense IR via rs-stream (dynamic alloc)

### SensorType fields

| Field | Purpose |
|---|---|
| `key` | Unique identifier ("color", "thermal", "webcam-front") |
| `label` | Human-readable name shown in the dashboard |
| `encoder_family` | Which adapter family to invoke ("rtp-v4l2", "rtp-rtsp", etc.) |
| `encoder_instance_pattern` | Template for the systemd unit instance (substitutions: {serial}, {index}) |
| `requires_producer` | Optional producer service (e.g., "realsense-mux" for depth/IR) |
| `default_pix_fmt` | Initial PIX_FMT written to tuning.env |
| `default_width/height/fps` | Initial dimensions |
| `default_bitrate_kbps` | Initial H264 bitrate |
| `is_dynamic_mountpoint` | True = allocator picks mp_id/port, False = static jcfg |
| `static_mp_id/static_rtp_port` | If is_dynamic_mountpoint=False, use these |

### What plugins CAN do
- Register custom sensor types
- Bring their own discover_fn (callback that detects devices physically)
- Override built-in types (re-registering same key)

### What plugins CAN'T do (yet)
- Modify lifecycle behavior (initialize/stop logic) — uses standard flow
- Add custom REST routes (L4 routes are static)
- Add custom Prometheus metrics (use global instrument)

## Future adapter types

- **RTSP** (Sprint B4): connect to an IP camera RTSP URL, transcode to H264 baseline
- **GStreamer** (future): wrap a gst-launch pipeline for exotic sources
- **VAAPI/v4l2m2m** (future): hardware-accelerated encoding (Phase 3)
- **MIPI CSI** (future): Pi camera module 2/3 (direct sensor, no V4L2 emulation)

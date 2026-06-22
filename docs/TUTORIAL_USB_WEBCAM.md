# Tutorial: USB Webcam → Browser Stream (no RealSense)

End-to-end guide for deploying the stack without RealSense — works with any USB
webcam (Logitech C920, generic budget webcam, dashcam, capture card, IP
camera USB driver, etc.).

**Time:** ~10 minutes from cloned repo to browser-visible stream.

**Assumed:** Linux box (Pi5, Ubuntu, Debian, etc.), root/sudo access, USB
webcam plugged in, network connectivity.

## Step 1: Identify your camera

```bash
# List V4L2 devices:
v4l2-ctl --list-devices
```

Expected output looks like:
```
HD Pro Webcam C920 (usb-xhci_hcd.0-1):
        /dev/video0
        /dev/video1
```

The first `/dev/videoN` listed under your camera is usually the capture device.
Note this path — we'll reference it as `$DEVICE`.

## Step 2: Check supported formats

```bash
v4l2-ctl --device /dev/video0 --list-formats-ext
```

Expected:
```
[0]: 'YUYV' (YUYV 4:2:2)
        Size: Discrete 640x480
                Interval: Discrete 0.033s (30.000 fps)
        Size: Discrete 1280x720
                Interval: Discrete 0.033s (30.000 fps)
[1]: 'MJPG' (Motion-JPEG, compressed)
        ...
```

Pick the desired resolution and FPS. Common safe values: **640×480@30fps** (most
permissive), **1280×720@30fps** (HD).

## Step 3: Install dependencies (one-time)

```bash
sudo apt-get update
sudo apt-get install -y \
    python3-venv python3-pip \
    ffmpeg v4l-utils \
    libsrtp2-dev \
    git jq
```

## Step 4: Clone + setup

```bash
cd /opt   # or wherever you keep services
sudo git clone <your-fork-or-this-repo> camera-stack
cd camera-stack

# Python venv for the camera-page service
sudo python3 -m venv .venv
source .venv/bin/activate
sudo pip install -r janus_camera_page/requirements.txt
```

## Step 5: Generate secrets

```bash
sudo mkdir -p /etc/robot
sudo tee /etc/robot/camera-secrets.env > /dev/null <<EOF
CAM_ADMIN_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
INTERNAL_API_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
# TURN credentials — fill if using TURN server:
TURN_SHARED_SECRET=
TURN_PASS=
EOF
sudo chmod 0640 /etc/robot/camera-secrets.env

# Note the CAM_ADMIN_TOKEN value — needed for browser admin:
sudo grep CAM_ADMIN_TOKEN /etc/robot/camera-secrets.env
```

## Step 6: Install Janus WebRTC Gateway

```bash
# Quick install via apt (Debian/Ubuntu — may be older version):
sudo apt-get install -y janus

# OR build from source (more recent, recommended):
# https://janus.conf.meetecho.com/docs/install.html
```

Verify:
```bash
systemctl status janus
# Janus should respond:
curl http://127.0.0.1:8088/janus/info | jq .
```

## Step 7: Configure Janus streaming mountpoint

Edit `/opt/janus/etc/janus/janus.plugin.streaming.jcfg`:
```
general: {
  enabled = "true";
  json = "true";
  admin_key = "your-streaming-admin-key";  # random — store in /etc/robot/camera-secrets.env
};

webcam-stream : {
  type = "rtp";
  id = "1400";          # mountpoint ID — choose unique value
  description = "USB webcam test";
  videobitrate = 0;
  videobufferkf = true;
  media = (
    {
      type = "video";
      mid = "v";
      label = "video";
      port = "5020";    # RTP port — must match contract.env in step 8
      pt = "96";
      codec = "h264";
      fmtp = "profile-level-id=42e01f;packetization-mode=1;level-asymmetry-allowed=1";
      iface = "127.0.0.1";
    }
  );
};

sudo systemctl restart janus
```

## Step 8: Configure rtp-v4l2 adapter

```bash
# Tuning config (operator-tunable):
sudo cp host_infra/roles/encoder/files/rtp-v4l2-example.tuning.env \
        /etc/robot/rtp-v4l2-webcam.tuning.env

# Edit to match your camera:
sudo $EDITOR /etc/robot/rtp-v4l2-webcam.tuning.env
```

Edit relevant fields:
```bash
DEVICE="/dev/video0"   # from step 1
WIDTH="640"            # from step 2
HEIGHT="480"
FPS="30"
BITRATE_KBPS="1500"
# Leave PIX_FMT="" — script auto-detects
```

```bash
# Contract config (port matching Janus):
sudo tee /etc/robot/rtp-v4l2-webcam.contract.env > /dev/null <<EOF
PORT="5020"
EOF
```

## Step 9: Install boundary CLIs + adapter script + systemd

```bash
# Adapter script + encoder admin:
sudo cp host_infra/roles/encoder/files/rtp-v4l2.sh /usr/local/bin/
sudo cp host_infra/roles/encoder/files/encoder-admin.py /usr/local/bin/encoder-admin
sudo cp host_infra/roles/encoder/files/camera-admin.py /usr/local/bin/camera-admin
sudo chmod 0755 /usr/local/bin/{rtp-v4l2.sh,encoder-admin,camera-admin}

# Sudoers (allow boris user to invoke admin without password):
sudo tee /etc/sudoers.d/encoder-admin > /dev/null <<EOF
$USER ALL=(root) NOPASSWD: /usr/local/bin/encoder-admin
EOF
sudo tee /etc/sudoers.d/camera-admin > /dev/null <<EOF
$USER ALL=(root) NOPASSWD: /usr/local/bin/camera-admin
EOF
sudo chmod 0440 /etc/sudoers.d/{encoder-admin,camera-admin}
sudo visudo -c   # validate syntax

# systemd unit:
sudo cp host_infra/roles/encoder/files/rtp-v4l2@.service /etc/systemd/system/
sudo systemctl daemon-reload
```

## Step 10: Start encoder

```bash
sudo /usr/local/bin/encoder-admin start --family rtp-v4l2 --instance webcam

# Check it's running:
sudo /usr/local/bin/encoder-admin status --family rtp-v4l2 --instance webcam | jq .
# Expected: active=true, ffmpeg_pid > 0
```

If failed, debug:
```bash
sudo journalctl -u rtp-v4l2@webcam -n 50
# Common errors:
# - "device not found"      — check DEVICE path
# - "Permission denied"     — add user to video group: usermod -a -G video $USER
# - "Connection refused"    — Janus not running on port 5020
# - "Format not supported"  — try explicit PIX_FMT='yuyv422' or 'mjpeg'
```

## Step 11: Start camera-page service (optional — only needed for the dashboard UI)

If you want dashboard + per-sensor config UI:

```bash
sudo cp janus_camera_page/infrastructure/color_node/systemd/janus-camera-page.service /etc/systemd/system/
sudo cp -r janus_camera_page/infrastructure/color_node/systemd/janus-camera-page.service.d /etc/systemd/system/
sudo cp janus_camera_page/infrastructure/color_node/systemd/janus_camera_page_hook.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start janus-camera-page janus_camera_page_hook

# Verify:
curl http://localhost:8900/healthz | jq .
```

## Step 12: Open browser

Simple test player (without the camera-page dashboard):

```html
<!-- save as test.html, open in browser -->
<!DOCTYPE html>
<html>
<head><title>Webcam test</title></head>
<body>
<video id="v" autoplay playsinline muted></video>
<script src="https://path/to/janus.js"></script>
<script>
Janus.init({debug: 'all', callback: () => {
  const janus = new Janus({
    server: 'ws://localhost:8188/',
    success: () => {
      janus.attach({
        plugin: 'janus.plugin.streaming',
        success: (h) => {
          h.send({message: {request: 'watch', id: 1400}});  // your mountpoint id
        },
        onmessage: (msg, jsep) => {
          if (jsep) {
            h.createAnswer({jsep, media: {audioRecv:false, videoRecv:true},
              success: (s) => h.send({message:{request:'start'}, jsep:s})});
          }
        },
        onremotestream: (stream) => {
          document.getElementById('v').srcObject = stream;
        }
      });
    }
  });
}});
</script>
</body>
</html>
```

Or just open `dashboard.html` if the camera-page service is deployed.

## Verification checklist

- [ ] `v4l2-ctl --list-devices` shows your camera
- [ ] `systemctl is-active rtp-v4l2@webcam` → active
- [ ] `sudo journalctl -u rtp-v4l2@webcam | grep -E "format=|size="` shows correct format detected
- [ ] `sudo ss -unp | grep :5020` shows ffmpeg sending RTP to Janus
- [ ] Browser shows video stream

## Troubleshooting

### "Device or resource busy"

Another process holds the device. Find it:
```bash
sudo fuser -v /dev/video0
```

Common culprits: GNOME Cheese, OBS, another ffmpeg instance.

### Video quality bad

Increase bitrate:
```bash
sudo $EDITOR /etc/robot/rtp-v4l2-webcam.tuning.env
# Set: BITRATE_KBPS="3000"   (was 1500)
sudo systemctl restart rtp-v4l2@webcam
```

Or higher resolution (check supported per step 2):
```bash
# Set:
WIDTH="1280"
HEIGHT="720"
```

### Multiple webcams

Each gets its own instance + port + mountpoint:

```bash
# Camera 1:
sudo cp .../rtp-v4l2-example.tuning.env /etc/robot/rtp-v4l2-cam1.tuning.env
sudo cp .../rtp-v4l2-example.contract.env /etc/robot/rtp-v4l2-cam1.contract.env
# Edit: DEVICE=/dev/video0, PORT=5020
sudo systemctl start rtp-v4l2@cam1

# Camera 2:
sudo cp .../rtp-v4l2-example.tuning.env /etc/robot/rtp-v4l2-cam2.tuning.env
sudo cp .../rtp-v4l2-example.contract.env /etc/robot/rtp-v4l2-cam2.contract.env
# Edit: DEVICE=/dev/video2, PORT=5022
sudo systemctl start rtp-v4l2@cam2

# Add 2nd mountpoint in Janus jcfg, restart janus.
```

## Next steps

- **Dashboard:** install camera-page service for visual sensor management
- **Configure rotation/bitrate via UI:** open `/api/v1/cameras/<serial>/<sensor>/camera_config.html`
- **Add back-channel app:** see `docs/ARCHITECTURE.md` "Robot wrapper pattern"
- **Production hardening:** see `docs/DEPLOYMENT.md` "Hardening checklist"

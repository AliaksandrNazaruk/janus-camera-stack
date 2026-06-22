# Installation guide

Three install paths by complexity:

1. **One-line bootstrap** — fresh Ubuntu/Debian machine, automated
2. **Manual install** — step-by-step, for troubleshooting OR unsupported OS
3. **Docker / Kubernetes** — see [docs/DEPLOYMENT_CLOUD.md](DEPLOYMENT_CLOUD.md)

---

## 1. One-line bootstrap (recommended)

Supported targets:
- Raspberry Pi 4 / 5 on Ubuntu 22.04+ or 24.04 LTS (arm64)
- Generic x86_64 Linux on Ubuntu 22.04+/24.04 LTS or Debian 12+

```bash
# Probe environment first (no changes)
./installer/probe.sh

# Full install
sudo ./install.sh
```

What happens (idempotent):
1. Detects OS, arch, Pi model, attached cameras (V4L2 + RealSense)
2. Installs apt deps: ffmpeg, coturn, python3-venv, v4l-utils, libusb, etc.
3. Installs Janus Gateway (`apt install janus` or build instructions)
4. Installs coturn (config left for operator to edit)
5. Handles **pyrealsense2** specially:
   - Tier 1 OS + arm64 → install vendored wheel from `installer/wheels/`
   - amd64 → `pip install pyrealsense2` from PyPI
   - Other → print build instructions and continue without depth support
6. Deploys encoder scripts + systemd units (`/usr/local/bin/rs-stream.sh`, `realsense-mux`, `/usr/local/bin/rtp-*.sh`)
7. Installs camera-page L4 (`/opt/janus-camera-page/` + venv + systemd unit)
8. Generates secrets (`/etc/robot/camera-secrets.env` with mode 0600)
9. Starts + enables services
10. Verifies `/livez` returns 200

### Options

```bash
sudo ./install.sh --dry-run             # preview, change nothing
sudo ./install.sh --skip-janus          # use existing Janus install
sudo ./install.sh --skip-coturn         # use existing coturn
sudo ./install.sh --skip-pyrealsense    # color-only (no depth)
sudo ./install.sh --probe-only          # report env, no install
sudo ./install.sh -y                    # non-interactive
```

### Verifying

```bash
# Services up?
systemctl status janus-camera-page janus

# Dashboard reachable?
curl http://localhost:8900/livez            # → {"ok": true}
curl http://localhost:8900/healthz          # → full status JSON
curl http://localhost:8900/api/v1/color_camera/sensor_types | jq

# Open dashboard
xdg-open http://localhost:8900/color_camera
```

---

## 2. Manual install

For OS where `install.sh` doesn't apply (other Linux distros, custom builds)
or when you want to understand every step.

### 2.1 System deps

**Ubuntu / Debian:**
```bash
sudo apt update
sudo apt install -y ffmpeg coturn janus \
  python3.12 python3.12-venv python3-pip python3-dev \
  v4l-utils usbutils libssl-dev libusb-1.0-0-dev libudev-dev \
  build-essential cmake pkg-config git curl
```

**Fedora / RHEL:**
```bash
sudo dnf install -y ffmpeg coturn \
  python3.12 python3-pip python3-devel \
  v4l-utils usbutils openssl-devel libusb1-devel \
  gcc gcc-c++ cmake make pkgconfig git curl
# Janus: build from source (no official RPM)
```

### 2.2 Janus Gateway

If `apt install janus` fails (older Debian/Ubuntu), build from source:

```bash
git clone https://github.com/meetecho/janus-gateway.git
cd janus-gateway
sh autogen.sh
./configure --prefix=/opt/janus
make -j$(nproc)
sudo make install
sudo make configs    # writes default jcfg to /opt/janus/etc/janus/
```

Then configure `nat_1_1_mapping` in `/opt/janus/etc/janus/janus.jcfg`
(set to public IP).

### 2.3 pyrealsense2 (optional — for depth cameras)

**amd64 Linux:**
```bash
pip install pyrealsense2 numpy
```

**arm64 (Pi 4/5) and other:**
```bash
# Use our build script
./installer/build-pyrealsense.sh 2.55.1

# Wheel ends up in installer/wheels/
# Install to the camera-page venv:
/opt/janus-camera-page/venv/bin/pip install installer/wheels/pyrealsense2-*.whl
```

OR manually:
```bash
sudo apt install -y cmake build-essential python3-dev \
  libssl-dev libusb-1.0-0-dev libudev-dev pkg-config
git clone --branch v2.55.1 https://github.com/IntelRealSense/librealsense.git
cd librealsense
mkdir build && cd build
cmake .. -DBUILD_PYTHON_BINDINGS=ON \
         -DPYTHON_EXECUTABLE=$(which python3.12) \
         -DBUILD_EXAMPLES=OFF
make -j$(nproc)
sudo make install
sudo cp ../config/99-realsense-libusb.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger

# Install Python module to the venv
cd ../wrappers/python
/opt/janus-camera-page/venv/bin/pip install .
```

### 2.4 Encoder scripts + systemd units

```bash
sudo install -m 0755 host_infra/roles/encoder/files/rs-stream.sh /usr/local/bin/
sudo install -m 0755 host_infra/roles/encoder/files/realsense-mux.py /usr/local/bin/realsense-mux
sudo install -m 0755 host_infra/roles/encoder/files/rtp-v4l2.sh /usr/local/bin/
sudo install -m 0755 host_infra/roles/encoder/files/rtp-rtsp.sh /usr/local/bin/
sudo install -m 0755 host_infra/roles/encoder/files/encoder-admin.py /usr/local/bin/encoder-admin
sudo install -m 0755 host_infra/roles/encoder/files/camera-admin.py /usr/local/bin/camera-admin

sudo install -m 0644 host_infra/roles/encoder/files/rs-stream@.service /etc/systemd/system/
sudo install -m 0644 host_infra/roles/encoder/files/realsense-mux.service /etc/systemd/system/
sudo install -m 0644 host_infra/roles/encoder/files/rtp-v4l2@.service /etc/systemd/system/
sudo install -m 0644 host_infra/roles/encoder/files/rtp-rtsp@.service /etc/systemd/system/

sudo systemctl daemon-reload
```

### 2.5 camera-page L4 (FastAPI dashboard)

```bash
sudo mkdir -p /opt/janus-camera-page /var/lib/robot /etc/robot/plugins.d
sudo rsync -a --exclude='tests/' --exclude='docs/' --exclude='deploy/' \
  --exclude='installer/' --exclude='__pycache__/' --exclude='.git*' \
  $(pwd)/ /opt/janus-camera-page/

sudo python3.12 -m venv /opt/janus-camera-page/venv
sudo /opt/janus-camera-page/venv/bin/pip install --upgrade pip wheel
sudo /opt/janus-camera-page/venv/bin/pip install -r /opt/janus-camera-page/requirements.txt
```

Create systemd unit (see `install.sh` for template):
```bash
sudo tee /etc/systemd/system/janus-camera-page.service > /dev/null <<'EOF'
[Unit]
Description=janus-camera-page L4 FastAPI service
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/janus-camera-page
EnvironmentFile=-/etc/robot/camera-secrets.env
EnvironmentFile=-/etc/robot/camera-page.env
ExecStart=/opt/janus-camera-page/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8900
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now janus-camera-page
```

### 2.6 Secrets

```bash
sudo install -m 0600 /dev/null /etc/robot/camera-secrets.env
sudo tee /etc/robot/camera-secrets.env > /dev/null <<EOF
TURN_SHARED_SECRET=$(openssl rand -hex 32)
INTERNAL_API_SECRET=$(openssl rand -hex 32)
JANUS_ADMIN_SECRET=$(openssl rand -hex 32)
TURN_HOST=$(hostname -I | awk '{print $1}')
TURN_REALM=$(hostname).local
EOF
```

### 2.7 Start everything

```bash
sudo systemctl enable --now janus janus-camera-page
# Encoder for your camera — see docs/TUTORIAL_USB_WEBCAM.md
```

---

## Troubleshooting

### probe.sh says "tier 3"
Your OS isn't tested by us. Install may still work, but expect manual steps
for pyrealsense2 and possibly Janus.

### "no module named pyrealsense2" after install
The wheel went to the wrong venv. Verify:
```bash
/opt/janus-camera-page/venv/bin/python -c 'import pyrealsense2; print(pyrealsense2.__file__)'
```
If empty, re-install to the correct venv:
```bash
/opt/janus-camera-page/venv/bin/pip install installer/wheels/pyrealsense2-*.whl
```

### "permission denied" reading /dev/video* OR RealSense
Udev rules not loaded. Re-run:
```bash
sudo cp host_infra/roles/encoder/files/99-realsense-libusb.rules /etc/udev/rules.d/ 2>/dev/null \
  || sudo cp librealsense/config/99-realsense-libusb.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
# Disconnect + reconnect USB camera
```

### Janus runs but stream does not connect
Check `nat_1_1_mapping` in `/opt/janus/etc/janus/janus.jcfg`. Must be the
**publicly-reachable** IP of the Janus node. See
[docs/DEPLOYMENT_CLOUD.md#troubleshooting](DEPLOYMENT_CLOUD.md#troubleshooting).

### camera-page returns 500
```bash
sudo journalctl -u janus-camera-page -n 100 --no-pager
```
Most common: missing `TURN_SHARED_SECRET` — regenerate secrets file.

For more, see [docs/OPERATOR_RUNBOOK.md](OPERATOR_RUNBOOK.md).

---

## Uninstall

```bash
sudo systemctl disable --now janus-camera-page janus
sudo rm -rf /opt/janus-camera-page
sudo rm -f /etc/systemd/system/janus-camera-page.service
sudo rm -f /etc/systemd/system/rtp-*.service
sudo rm -f /etc/systemd/system/realsense-mux.service
sudo rm -f /etc/systemd/system/rs-stream@.service
sudo rm -f /usr/local/bin/rtp-*.sh /usr/local/bin/rs-stream.sh
sudo rm -f /usr/local/bin/encoder-admin /usr/local/bin/camera-admin
sudo rm -f /usr/local/bin/realsense-mux
sudo rm -rf /var/lib/robot /etc/robot

# Optional (leave installed for other apps):
# sudo apt remove --purge janus coturn ffmpeg
```

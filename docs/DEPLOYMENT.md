# Deployment Guide

Clean-room setup of full stack on new Pi5 + D435i.

## Prerequisites

- Pi5 (8GB) running Raspberry Pi OS Bookworm 64-bit
- D435i USB camera attached
- Stable LAN with outbound internet (TURN cred refresh, TURN traversal)
- SSH access as `boris` user (sudo enabled)

## 1. Base system setup

```bash
# System packages
sudo apt-get update
sudo apt-get install -y \
    python3-pip python3-venv \
    docker.io docker-compose-plugin \
    ffmpeg v4l-utils tcpdump \
    libsrtp2-dev libusb-1.0-0-dev \
    git jq

# Create boris user (if still default)
# Add boris to the video group for V4L2 access:
sudo usermod -a -G video,docker boris

# Increase pipe buffer for realsense-mux FIFOs:
sudo cp host_infra/roles/encoder/files/sysctl-realsense-mux.conf /etc/sysctl.d/99-realsense-mux.conf
sudo sysctl -p /etc/sysctl.d/99-realsense-mux.conf
```

## 2. Clone repo + Python venv

```bash
cd /opt
git clone https://github.com/<org>/robot.git
cd robot

python3 -m venv .venv
source .venv/bin/activate
pip install -r janus_camera_page/requirements.txt
pip install pyrealsense2  # for mux on .10 (color_camera node)
```

## 3. Janus install

Janus Gateway must be installed separately — see https://janus.conf.meetecho.com/docs/install.html

After install, copy config templates:

```bash
sudo cp janus_camera_page/infrastructure/color_node/janus/*.jcfg /opt/janus/etc/janus/
sudo cp janus_camera_page/infrastructure/color_node/janus/janus-nat.json /etc/janus/

# Generate persistent secrets (for camera mountpoint admin)
python3 -c "import secrets; print(secrets.token_urlsafe(32))" > /tmp/secret.txt
# ... merge into janus-streaming.jcfg manually
```

## 4. Secrets file

```bash
sudo mkdir -p /etc/robot
sudo cp janus_camera_page/back-channel-topics.json.example /etc/robot/back-channel-topics.json
sudo $EDITOR /etc/robot/back-channel-topics.json  # edit your robot sinks

# Camera secrets — generate fresh values:
sudo tee /etc/robot/camera-secrets.env > /dev/null <<EOF
CAM_ADMIN_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
INTERNAL_API_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
TURN_SHARED_SECRET=<from-coturn-config>
TURN_PASS=<for-static-fallback>
JANUS_STREAMING_ADMIN_KEY=<read-from-jcfg>
EOF
sudo chmod 0640 /etc/robot/camera-secrets.env
sudo chown root:boris /etc/robot/camera-secrets.env
```

## 5. Sudoers (boundary CLIs)

```bash
sudo tee /etc/sudoers.d/encoder-admin > /dev/null <<EOF
boris ALL=(root) NOPASSWD: /usr/local/bin/encoder-admin
EOF

sudo tee /etc/sudoers.d/camera-admin > /dev/null <<EOF
boris ALL=(root) NOPASSWD: /usr/local/bin/camera-admin
EOF

sudo tee /etc/sudoers.d/janus-admin > /dev/null <<EOF
boris ALL=(root) NOPASSWD: /usr/local/bin/janus-admin
EOF

sudo chmod 0440 /etc/sudoers.d/encoder-admin /etc/sudoers.d/camera-admin /etc/sudoers.d/janus-admin
sudo visudo -c  # verify syntax
```

## 6. Install boundary CLIs + scripts

```bash
sudo cp host_infra/roles/encoder/files/encoder-admin.py /usr/local/bin/encoder-admin
sudo cp host_infra/roles/encoder/files/camera-admin.py /usr/local/bin/camera-admin
sudo cp host_infra/roles/janus/files/janus-admin.py /usr/local/bin/janus-admin
sudo cp host_infra/roles/encoder/files/realsense-mux.py /usr/local/bin/realsense-mux.py
sudo cp host_infra/roles/encoder/files/rs-stream.sh /usr/local/bin/rs-stream.sh

sudo chmod 0755 /usr/local/bin/{encoder-admin,camera-admin,janus-admin,realsense-mux.py,rs-stream.sh}
```

## 7. systemd units

```bash
sudo cp janus_camera_page/infrastructure/color_node/systemd/*.service /etc/systemd/system/
sudo cp -r janus_camera_page/infrastructure/color_node/systemd/janus-camera-page.service.d /etc/systemd/system/
sudo cp host_infra/roles/encoder/files/realsense-mux.service /etc/systemd/system/
sudo cp host_infra/roles/encoder/files/rs-stream@.service /etc/systemd/system/

sudo systemctl daemon-reload
# Color is a mux sensor: realsense-mux (producer, RS_ENABLE_COLOR=1) + rs-stream@color (encoder).
sudo systemctl enable realsense-mux rs-stream@color janus janus-camera-page janus_camera_page_hook
sudo systemctl start realsense-mux rs-stream@color janus janus-camera-page janus_camera_page_hook
```

## 8. Gateway (Docker)

```bash
cd /opt/janus-camera-page
sudo docker compose build api-gateway
sudo docker compose up -d api-gateway

# Verify:
curl http://localhost:8201/readyz
```

## 9. Smoke test

```bash
SERIAL=$(curl -s http://localhost:8201/api/v1/cameras/registry.json | jq -r '.devices[0].serial')
echo "D435i serial: $SERIAL"

# Color stream:
curl -o /tmp/snapshot.jpg http://localhost:8201/api/v1/color_camera/snapshot.jpg
file /tmp/snapshot.jpg  # should be JPEG image data, 640x480

# Init depth and click-to-depth:
TOKEN=$(sudo grep CAM_ADMIN_TOKEN /etc/robot/camera-secrets.env | cut -d= -f2)
curl -s -X POST -H "X-Admin-Token: $TOKEN" \
  "http://localhost:8201/api/v1/cameras/$SERIAL/depth/initialize" | jq .

sleep 3
curl "http://localhost:8000/depth?x=50&y=50" | jq .  # direct mux

# Cleanup:
curl -s -X POST -H "X-Admin-Token: $TOKEN" \
  "http://localhost:8201/api/v1/cameras/$SERIAL/depth/stop" > /dev/null
```

## 10. Browser test (manual)

Open `https://<host>/api/v1/cameras/dashboard.html` in Chrome.
Expected: dashboard shows D435i and 4 sensors. Color = "running". 
Click "Open viewer" on color — video stream within 10sec.
Init depth via "Initialize" → "Open viewer" → you see colorized depth.

## Verification checklist

- [ ] `systemctl is-active realsense-mux rs-stream@color janus janus-camera-page janus_camera_page_hook` → all active
- [ ] `docker ps | grep api-gateway` → Up
- [ ] `curl localhost:8201/readyz` → 200 OK
- [ ] `curl localhost:8201/api/v1/color_camera/healthz | jq '.system.mode'` → 0 (nominal)
- [ ] `ls /run/realsense/color-snapshot.jpg` → exists, recent mtime
- [ ] Browser viewer reaches PLAYING within 10sec
- [ ] Audit log entries appear on admin POST: `sudo tail /var/log/camera-audit/audit.jsonl`

## Rollback

```bash
# Stop everything:
sudo systemctl stop rs-stream@color rs-stream@depth rs-stream@ir1 rs-stream@ir2
sudo systemctl stop realsense-mux janus janus-camera-page janus_camera_page_hook
cd /opt/janus-camera-page && sudo docker compose down

# Git revert:
git revert HEAD --no-commit
git commit -m "rollback: <reason>"

# Redeploy:
# Re-run steps 6-8
```

## Hardening checklist (Phase 2+)

- [ ] TLS termination at gateway (currently HTTP)
- [ ] Firewall rules: only 8201 exposed externally (gateway)
- [ ] Coturn behind authentication, not open-relay
- [ ] CAM_ADMIN_TOKEN rotated quarterly
- [ ] Audit log shipped to remote SIEM (currently local only)
- [ ] Backup `/etc/robot/` config files
- [ ] Restore-test from backup on clean Pi5

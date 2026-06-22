# Cloud deployment guide

Three deploy targets, ordered by setup complexity:

1. **Single-host Docker Compose** (5 min) — single VPS / VM
2. **Kubernetes raw manifests** (15 min) — managed k8s clusters
3. **Helm chart** (10 min, parameterized) — production rollouts

All three deploy the same stack:
- L4 dashboard (FastAPI camera-page)
- Janus Gateway (WebRTC SFU)
- coturn (TURN/STUN relay)
- Prometheus (optional, observability)

**What's NOT in cloud:** encoders. Cameras live on edge nodes (the robot,
fixed cameras, IP camera networks). Edge encoders push RTP to cloud Janus
over WireGuard / Tailscale / IPsec overlay.

---

## 1. Single-host Docker Compose

For dev / single-customer deployments, a single VM with public IP suffices.

```bash
# 1. Clone repo
git clone https://github.com/YOUR_ORG/janus-camera-page.git
cd janus-camera-page

# 2. Generate secrets
cat > .env <<EOF
TURN_SECRET=$(openssl rand -hex 32)
INTERNAL_API_SECRET=$(openssl rand -hex 32)
JANUS_ADMIN_SECRET=$(openssl rand -hex 32)
GRAFANA_PASS=$(openssl rand -base64 18)
TURN_PUBLIC_HOST=turn.your-domain.com
CAMERA_TYPE=color_camera
EOF

# 3. Configure DNS
# Point cameras.your-domain.com → VM's public IP
# Point turn.your-domain.com    → VM's public IP

# 4. Edit deploy/janus/etc/janus.jcfg
# Set nat_1_1_mapping = "VM_PUBLIC_IP" (else ICE fails for remote clients)

# 5. Bring up stack
docker compose -f janus_camera_page/docker-compose.prod.yml up -d

# 6. Reverse proxy / TLS — add Caddy or nginx pointing to camera-page:8900
# For Caddy:
#   cameras.your-domain.com {
#     reverse_proxy localhost:8900
#   }
```

**Validation:**
```bash
curl https://cameras.your-domain.com/livez
# → {"ok": true}

curl https://cameras.your-domain.com/api/v1/color_camera/sensor_types
# → JSON listing built-in sensor types
```

---

## 2. Kubernetes raw manifests

For self-hosted k8s clusters (k3s, k0s, RKE) or managed clusters
(EKS, GKE, AKS).

**Prerequisites:**
- At least one node with public IP — label it:
  ```bash
  kubectl label node node-1 node-role.cloud.janus/sfu=true
  kubectl label node node-1 node-role.cloud.janus/turn=true
  ```
- nginx-ingress controller installed
- cert-manager (optional, for auto TLS)

**Deploy:**
```bash
cd janus_camera_page/deploy/k8s

# 1. Create namespace
kubectl apply -f 00-namespace.yaml

# 2. Create secrets (DO NOT use 10-config.yaml stub — replace those values)
kubectl create secret generic camera-page-secrets \
  --from-literal=TURN_SECRET=$(openssl rand -hex 32) \
  --from-literal=INTERNAL_API_SECRET=$(openssl rand -hex 32) \
  --from-literal=JANUS_ADMIN_SECRET=$(openssl rand -hex 32) \
  -n janus-camera-stack

# 3. Apply config (edit 10-config.yaml first — set TURN_HOST, TURN_REALM)
kubectl apply -f 10-config.yaml

# 4. Deploy services (edit 30-janus.yaml: set nat_1_1_mapping in ConfigMap)
kubectl apply -f 20-camera-page.yaml
kubectl apply -f 30-janus.yaml
kubectl apply -f 40-coturn.yaml
kubectl apply -f 50-ingress.yaml
kubectl apply -f 60-prometheus.yaml   # optional

# 5. Verify
kubectl get pods -n janus-camera-stack
kubectl logs -n janus-camera-stack -l app=camera-page --tail=30
```

---

## 3. Helm chart (recommended for production)

```bash
cd janus_camera_page/deploy/helm

# 1. Create values.production.yaml
cat > values.production.yaml <<EOF
cameraPage:
  image:
    repository: ghcr.io/your-org/janus-camera-page
    tag: v1.0.0
  ingress:
    host: cameras.your-domain.com
    tls:
      secretName: cameras-tls
janus:
  publicIp: "203.0.113.42"        # node's public IP
coturn:
  realm: cameras.your-domain.com
  publicHost: turn.your-domain.com
secrets:
  turnSecret: "$(openssl rand -hex 32)"
  internalApiSecret: "$(openssl rand -hex 32)"
  janusAdminSecret: "$(openssl rand -hex 32)"
EOF

# 2. Install
helm install camera-stack ./janus-camera-stack \
  -f values.production.yaml \
  --create-namespace -n janus-camera-stack

# 3. Upgrade later:
helm upgrade camera-stack ./janus-camera-stack \
  -f values.production.yaml \
  -n janus-camera-stack
```

---

## Edge encoder configuration

Once cloud is up, configure edge node to push RTP to cloud Janus.

**Edge node prereqs:**
- WireGuard / Tailscale tunnel to cloud node (Janus side)
- ffmpeg, systemd
- Sensor type plugin already in `/etc/robot/plugins.d/` if custom

**Encoder service** (e.g., USB webcam, see TUTORIAL_USB_WEBCAM.md):
```bash
# /etc/robot/rtp-v4l2-webcam-0.contract.env
PORT=5004
JANUS_HOST=10.0.0.1     # tunnel IP to cloud Janus

# /etc/robot/rtp-v4l2-webcam-0.tuning.env
DEVICE=/dev/video0
WIDTH=1280
HEIGHT=720
FPS=30
BITRATE_KBPS=2000

sudo systemctl enable --now rtp-v4l2@webcam-0
```

Cloud Janus receives RTP at `udp://JANUS_NODE:5004` and creates a
streaming mountpoint. L4 dashboard sees the mountpoint and exposes it
to browser clients over WebRTC.

---

## Production hardening checklist

Before opening to real users:

- [ ] TLS certificate valid (Let's Encrypt or commercial CA)
- [ ] Rotated TURN_SECRET, INTERNAL_API_SECRET, JANUS_ADMIN_SECRET
- [ ] `nat_1_1_mapping` set in janus.jcfg
- [ ] Firewall allows: 80, 443 (HTTPS), 3478 UDP/TCP (TURN), 5349 TCP (TURN TLS),
      49152-49999 UDP (TURN relay range)
- [ ] Prometheus retention configured (default 7d — increase for postmortem capacity)
- [ ] Backup strategy for grafana dashboards + Prometheus data
- [ ] Resource limits set (don't run unbounded in production)
- [ ] Network policies restrict camera-page → janus only on internal cluster network
- [ ] Audit log shipped to centralized log store (camera-page writes to /var/lib/robot)
- [ ] Smoke test after deploy: `playwright` or `curl /livez` from external IP

---

## Troubleshooting

**"WebRTC connection fails / ICE failed":**
- Check `nat_1_1_mapping` — must be node's PUBLIC IP, not Pod IP
- Verify TURN auth working: `turnutils_uclient -u test -w $(date +%s):test ... turn.your-domain.com`
- Browser dev tools → chrome://webrtc-internals shows ICE candidate exchange

**"camera-page can't reach Janus":**
- Verify Janus pod healthy: `kubectl get pods -n janus-camera-stack -l app=janus`
- Test from camera-page pod: `kubectl exec -n janus-camera-stack deploy/camera-page -- curl janus:8088/janus/info`

**"Plugin not loading":**
- Confirm plugin file mounted: `kubectl exec deploy/camera-page -- ls /etc/robot/plugins.d`
- Check startup log: `kubectl logs deploy/camera-page | grep "sensor plugin"`

**"No streams visible in dashboard":**
- Confirm edge encoder pushing RTP: `tcpdump -i any -n udp port 5004` on Janus node
- Check Janus admin: `curl -X POST http://janus:7088/admin -d '{"janus":"list_sessions","admin_secret":"..."}'`

For deeper issues, check [OPERATOR_RUNBOOK.md](OPERATOR_RUNBOOK.md).

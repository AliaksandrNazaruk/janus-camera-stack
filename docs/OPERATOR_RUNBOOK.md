# Operator Runbook

If X happens, do Y. Designed for operational response, not for architectural understanding (see ARCHITECTURE.md for that).

## Quick reference

- **Operator dashboard:** `https://<host>/api/v1/cameras/dashboard.html`
- **Camera config:** `https://<host>/api/v1/color_camera/camera_config.html` (legacy)
                   `https://<host>/api/v1/cameras/{serial}/{sensor}/camera_config.html`
- **Health probe:** `curl https://<host>/api/v1/color_camera/healthz`
- **Metrics:** `curl https://<host>/api/v1/color_camera/metrics`
- **Audit log:** `sudo tail -f /var/log/camera-audit/audit.jsonl`
- **FDIR log:** `sudo tail -f /var/log/camera-fdir/fdir.jsonl`

## Common scenarios

### Stream stuck — black screen in browser

1. Check `dashboard.html` — does it show color sensor "running"?
2. If running but stream black, check pipeline FPS:
   ```
   curl /api/v1/color_camera/metrics | grep -E "mux_input_fps|janus_output_fps|client_frames_decoded"
   ```
3. Diagnosis by pattern:
   - `mux_input_fps=0` + `janus_output_fps=0` → camera/encoder down
   - `mux_input_fps>0` + `janus_output_fps=0` → encoder/Janus issue
   - Both >0 + `client_frames_decoded` not growing → client network
4. Action:
   ```
   # Encoder restart (color):
   sudo /usr/local/bin/encoder-admin restart --family rs-stream --instance color
   # Janus restart (if encoder OK but no output):
   sudo /usr/local/bin/janus-admin restart
   ```

### Depth sensor won't start

1. Try via dashboard → "Initialize" → if a 500 error:
2. Check mux:
   ```
   sudo systemctl status realsense-mux
   sudo journalctl -u realsense-mux -n 50
   ```
3. If "no RealSense device found":
   ```
   lsusb | grep -i intel
   # If absent — USB reset:
   sudo /usr/local/bin/camera-admin reset-usb
   sleep 5
   sudo systemctl restart realsense-mux
   ```
4. If "VIDIOC_S_FMT errno=5":
   - librealsense hardware_reset retry is already automatic (3 attempts)
   - If still fails: physical USB hub power cycle

### Reconnect storm (browser console shows many reconnect_attempts)

Phase 2 fix should prevent this. If still happening:
1. Check ICE state in browser DevTools → telemetry `ice_state`
2. Check TURN reachability:
   ```
   curl /api/v1/color_camera/health/stream | jq '.turn_server'
   ```
3. Check metrics:
   ```
   curl /metrics | grep -E "ice_setup_failures|client_packet_loss|client_jitter"
   ```
4. Action in order:
   - High jitter (>50ms): network issue — investigate client connection
   - High packet loss (>5%): bandwidth issue — reduce bitrate via camera_config.html
   - ICE setup failures: TURN credential expiration — restart janus-turn-rotator

### Audit log review

```
# Last 24h failures
sudo jq 'select(.outcome != "success") | .' /var/log/camera-audit/audit.jsonl

# Source IP distribution
sudo cat /var/log/camera-audit/audit.jsonl | jq -r '.source_ip' | sort | uniq -c

# Specific user actions
sudo jq 'select(.user=="admin" and .action | contains("initialize"))' /var/log/camera-audit/audit.jsonl

# Specific time window
sudo jq 'select(.ts >= "2026-06-15T10:00:00Z" and .ts < "2026-06-15T11:00:00Z")' /var/log/camera-audit/audit.jsonl
```

### Stop everything safely

```
# In order (don't kill streams for viewers mid-flight):
sudo systemctl stop rs-stream@color   # only if color shutdown needed
sudo systemctl stop rs-stream@depth rs-stream@ir1 rs-stream@ir2
sudo systemctl stop realsense-mux     # shared producer — stops ALL sensors (color+depth+IR)
# DO NOT stop janus.service unless everything is gracefully done — viewer reconnect storm risk
```

### Full reset (after maintenance window)

```
# Stop:
sudo systemctl stop rs-stream@color rs-stream@depth rs-stream@ir1 rs-stream@ir2
sudo systemctl stop realsense-mux

# Reset allocations (clears stable URL guarantees):
sudo rm /var/lib/camera-fdir/sensor_allocations.json

# Start in order (color is always-on: mux producer + rs-stream@color encoder):
sudo systemctl start realsense-mux
sudo systemctl start rs-stream@color
sudo systemctl start janus  # if was stopped
# depth/IR rs-stream@ encoders auto-start when their sensor is initialized via L4.

# Verify:
curl /api/v1/color_camera/healthz
```

### Common metrics thresholds for alerting

```promql
# Page operator immediately:
rate(camstack_encoder_restarts_total[1h]) > 5
camstack_recovery_ladder_level >= 3
camstack_mux_input_fps{sensor="depth"} < 5 for 2m
camstack_client_rtt_ms > 500 for 5m

# Investigate next business day:
camstack_client_jitter_ms > 50 for 30m
rate(camstack_admin_auth_failures_total[5m]) > 1
rate(camstack_orphaned_janus_sessions_total[15m]) > 0
```

### Emergency: stream not recovering, operator unreachable

1. Try in order:
   - Janus restart (~3sec WebRTC outage acceptable):
     ```sudo systemctl restart janus```
   - Encoder full recycle:
     ```sudo systemctl restart rs-stream@color.service```
   - L4 service restart (stream stays up — only HTTP API blip):
     ```sudo systemctl restart janus-camera-page```
   - Gateway restart (~5sec ALL services outage):
     ```cd /opt/janus-camera-page && sudo docker compose restart api-gateway```

2. If still broken after all restarts:
   - **DO NOT reboot Pi** without operator approval — reboot disabled by design
     (`Environment=CAM_WATCHDOG_REBOOT_ENABLED=0` in the systemd override)
   - Capture diagnostic data:
     ```
     sudo journalctl --since "10 minutes ago" > /tmp/diag.log
     sudo dmesg | tail -100 >> /tmp/diag.log
     ps auxf > /tmp/processes.log
     ss -tunpl > /tmp/ports.log
     ```
   - Page operator with files attached.

## Runtime config control plane (inspect → validate → apply)

The operator-tunable L4 surface under `/api/v1/admin/runtime-config` (admin-gated + rate-limited + audit-logged; distinct from the legacy `/api/v1/{cam}/...` routes). `GET /effective` and `POST /validate` are read-only/dry-run. **`POST /apply` is live for the `NEW_SESSIONS_ONLY` class only** (`webrtc.ice_policy`, `webrtc.turn_credential_ttl_seconds`) — it writes `/etc/robot/rs-runtime.env`, refreshes the process settings, verifies, and rolls back on failure. It does **not** restart the encoder/Janus, recreate mountpoints, touch FDIR, or reboot. Other impact classes (`RESTART_ENCODER`, etc.) are still refused by apply.

```bash
# Admin token — must be a strong value. The placeholder 'change-me' makes ALL
# admin endpoints fail-closed with 503 (by design), not 403.
TOKEN=$(sudo grep -m1 '^CAM_ADMIN_TOKEN=' /etc/robot/camera-secrets.env | cut -d= -f2-)
H="X-Admin-Token: $TOKEN"
BASE=http://127.0.0.1:8900/api/v1/admin/runtime-config
```

### Effective view — what L4 believes is true right now

```bash
curl -s -H "$H" "$BASE/effective" | jq .
```

Assembled from live state: Settings, allocation desired-state, a per-sensor `is_running` probe (`runtime_active`), and color tuning. Secret-free by construction (no TURN password / shared secret / admin token ever appears). `runtime_active=null` means the probe was indeterminate. Note: deriving `runtime_active` runs one `encoder-admin status` probe per sensor.

### Validate a patch — dry-run diff + impact classification

```bash
# no-op (value already current) → empty diff, empty impact
curl -s -H "$H" -H 'content-type: application/json' -X POST "$BASE/validate" \
  -d '{"webrtc":{"ice_policy":"relay"}}' | jq '{valid,impact,diff}'
# → valid:true, impact:[], diff:[]      (when ice_policy is already relay)

# real change → classified impact + a diff entry
curl -s -H "$H" -H 'content-type: application/json' -X POST "$BASE/validate" \
  -d '{"webrtc":{"turn_credential_ttl_seconds":1234}}' | jq '{valid,impact}'
# → valid:true, impact:["NEW_SESSIONS_ONLY"]   (diff: ttl 3600→1234)

# secret field → rejected (R9); rejection lives in errors[], NEVER a top-level impact
curl -s -H "$H" -H 'content-type: application/json' -X POST "$BASE/validate" \
  -d '{"TURN_SHARED_SECRET":"x"}' | jq '{valid,impact,errors}'
# → valid:false, impact:[], errors:["… secret field — not settable via runtime-config (R9)"]
```

**ApplyImpact** — the cost class of a change:

| Impact | Meaning | Applyable? |
|---|---|---|
| `NEW_SESSIONS_ONLY` | new WebRTC sessions only (`ice_policy`, TURN credential TTL) | ✅ **live** (`POST /apply`) |
| `RESTART_ENCODER` | needs a color encoder restart (resolution / fps / bitrate / gop) | ⛔ blocked (needs the FDIR-quiesce-coupled apply class) |
| `DEPLOYMENT_ONLY` | not hot-applyable; deploy-time (diagnostics) | ⛔ refused by apply |
| `REJECTED` | read-only/derived, depth/IR tuning, secrets, deployment fields | ⛔ never |

Full rule set (R1–R11): `docs/design/B1_RUNTIME_CONFIG.md`. Apply engine: `docs/design/B2_APPLY_NEW_SESSIONS_ONLY.md`.

### Apply a NEW_SESSIONS_ONLY change (ice_policy / TURN credential TTL)

Two-step, confirm-bound. `/validate` returns a `revision_id` + `diff_hash`; `/apply` requires `confirm == "apply-<diff_hash>"` for that exact revision (so "validate A, apply B" is impossible). The apply re-checks everything under a lock (re-validate, full-file hash of `rs-runtime.env`, coherence) and **409s on any drift** since validate.

```bash
# 1. validate the change → capture revision_id + diff_hash
V=$(curl -s -H "$H" -H 'content-type: application/json' -X POST "$BASE/validate" \
      -d '{"webrtc":{"turn_credential_ttl_seconds":1800}}')
RID=$(echo "$V" | jq -r .revision_id);  DH=$(echo "$V" | jq -r .diff_hash)

# 2. apply that revision (changed:true / verified:true on success)
curl -s -H "$H" -H 'content-type: application/json' -X POST "$BASE/apply" \
  -d "{\"revision_id\":\"$RID\",\"confirm\":\"apply-$DH\"}" | jq '{status,changed,verified}'
```

- **Safest field to exercise: `turn_credential_ttl_seconds`** — it only affects the TTL stamped on *newly-minted* ephemeral TURN creds; it disrupts no existing session. A `3600→1800→3600` round-trip is fully reversible and net-zero. `ice_policy` (relay↔all) changes network posture — change it deliberately.
- Codes: `200` applied · `400` confirm mismatch · `409` drift/coherence · `422` not-applyable (old/mixed-impact/forbidden field) · `423` another apply in progress · `500` rolled_back / rollback_failed.
- A no-op patch (value already current) produces no diff → no `revision_id` → nothing to apply.
- `GET /capabilities` reports apply support per impact class; `GET /revisions/{id}` shows a journaled revision (secret-redacted).

### FDIR during a recovery restart (TB-C1)

When the recovery ladder restarts the encoder/Janus, the watchdog is **quiesced** for that domain for the restart window — so it no longer re-escalates the staleness the recovery itself caused (which previously climbed toward reboot). In the audit/FDIR log you'll see `suppressed_planned` (WARN) events during a recovery restart — **that is expected**, not a fault. It is time-bounded (hard 120s ceiling) and domain-scoped: a *genuine* Janus fault during an *encoder* restart still escalates; only the planned action's own disturbance is suppressed.

**Two more "expected suppression / isolation" events you may see (G5) — do NOT treat as faults:**
- `outcome="suppressed_local_alive"` (Domain JANUS, WARN) — the **shared-Janus reboot guard**. A Janus admin-probe failed but the local color stream is provably still producing frames (snapshot fresh), so the JANUS escalation (which could reach reboot) was suppressed. Log line: `"janus probe failed but local stream alive (snapshot fresh) — suppressing JANUS escalation"`. This protects `cam10` from being rebooted by a *remote* mountpoint stalling the shared Janus.
- `domain="producer"` events — a **remote producer binding** going stale is classified `Domain.PRODUCER` and handled by the isolated remote monitor. It can never restart Janus, reset USB, or reboot the gateway. A stalling remote stream is *not* a local fault; alert on `camstack_fdir_events_total{domain="producer"}` separately (SLO.md).

> **Reconcile ≠ apply.** `/validate` is read-only and changes no state. But restarting `janus-camera-page` (e.g. to deploy new L4 code) triggers the **L4 startup reconcile**, which brings sensors with `desired_active=true` *up* to match desired state — color is `desired_active=true` by default, so a restart can flip `rs-stream@color` from inactive to active. That is reconcile-on-restart, not a config apply, and it does not modify `sensor_allocations.json`.

## Gateway nodes & stream bindings (G6)

The gateway can front **remote producer nodes** (e.g. `.55`) in addition to the local camera.
All admin-gated (`X-Admin-Token`). Topology lives in `/var/lib/camera-fdir/stream_bindings.json`.

```bash
TOKEN=$(sudo cat /etc/robot/camera-secrets.env | grep CAM_ADMIN_TOKEN | cut -d= -f2)
H="-H X-Admin-Token:$TOKEN"; B=http://localhost:8900/api/v1/admin

# 1. register the remote node + probe its agent
curl -s $H -XPOST $B/nodes/register -d '{"node_id":"cam55","host":"192.168.1.55","role":"remote_producer"}'
curl -s $H -XPOST $B/nodes/check    -d '{"node_id":"cam55"}'
#   → {"reachable":false,"reason":"node_agent_unreachable","next_step":"bootstrap_required"}
#   bootstrap_required is EXPECTED today — the per-node agent on :8901 does not exist yet.
#   It is NOT a failure; it means the node has no agent to drive remote restarts.

# 2. create a binding (auto-allocates mp ≥ 2000 / port ≥ 5100; rtp_iface = THIS gateway's LAN IP)
curl -s $H -XPOST $B/stream-bindings -d '{"node_id":"cam55","sensor":"color","rtp_iface":"192.168.1.10"}'

# 3. prepare Janus to receive that RTP
curl -s $H -XPOST $B/stream-bindings/cam55:color/ensure-janus
#   → {"status":"created","mountpoint_id":2000,"iface":"192.168.1.10"}

curl -s $H $B/stream-bindings | jq    # list (local projections + remote)
curl -s $H -XPOST $B/stream-bindings/cam55:color/remove   # teardown
```

> ⚠️ **`ensure-janus` only PREPARES Janus — it opens NO firewall rule.** The host-scoped,
> fail-closed RTP firewall (GATEWAY_REMOTE_RTP §4) is **not yet built**, so a remote producer
> is currently safe only on a trusted bench/loopback. If you `ensure-janus` and the producer's
> RTP cannot reach the gateway (no firewall opening), the binding will sit `waiting_for_rtp` /
> `degraded` — that is the firewall gap, not a bug. Do not expose remote RTP on an untrusted LAN
> until §4 lands.

## Configuration files reference

| Path | What it controls |
|---|---|
| `/etc/robot/rs-color.tuning.env` | Color encoder runtime params (BITRATE, FPS) |
| `/etc/robot/rs-mux.env` | Mux producer params (RS_ENABLE_COLOR, RS_OUTPUT_ROTATION_DEG) |
| `/etc/robot/rs-{sensor}.tuning.env` | Color/depth/IR encoder runtime params |
| `/etc/robot/back-channel-topics.json` | Joystick + other app topic routing |
| `/etc/robot/camera-secrets.env` | TURN passwords, admin tokens, internal secret |
| `/etc/systemd/system/janus-camera-page.service.d/override.conf` | L4 service config + sandbox |

After editing, restart appropriate service:
- `rs-color.tuning.env` → `sudo systemctl restart rs-stream@color`
- `rs-mux.env` → `sudo systemctl restart realsense-mux` (restarts ALL sensors)
- `rs-{sensor}.tuning.env` → `sudo systemctl restart rs-stream@{sensor}`
- `back-channel-topics.json` → `sudo systemctl restart janus_camera_page_hook`
- `camera-secrets.env` → restart both services

## Adding a generic USB webcam (Sprint B2)

Stack supports any V4L2 device — not just D435i. To add USB webcam:

```bash
# 1. Identify your device:
v4l2-ctl --list-devices
# Note the /dev/videoN path

# 2. Check supported formats/resolutions:
v4l2-ctl --device /dev/video0 --list-formats-ext

# 3. Create config files (replace <name> with a meaningful instance name):
sudo cp /opt/janus-camera-page/host_infra/roles/encoder/files/rtp-v4l2-example.tuning.env \
        /etc/robot/rtp-v4l2-<name>.tuning.env
sudo cp /opt/janus-camera-page/host_infra/roles/encoder/files/rtp-v4l2-example.contract.env \
        /etc/robot/rtp-v4l2-<name>.contract.env

# 4. Edit configs:
sudo $EDITOR /etc/robot/rtp-v4l2-<name>.tuning.env       # DEVICE, WIDTH, HEIGHT, FPS, BITRATE
sudo $EDITOR /etc/robot/rtp-v4l2-<name>.contract.env     # PORT (unique, do not collide)

# 5. Install adapter script + systemd template (one-time, if not already deployed):
sudo cp /opt/janus-camera-page/host_infra/roles/encoder/files/rtp-v4l2.sh /usr/local/bin/
sudo chmod 0755 /usr/local/bin/rtp-v4l2.sh
sudo cp /opt/janus-camera-page/host_infra/roles/encoder/files/rtp-v4l2@.service \
        /etc/systemd/system/
sudo systemctl daemon-reload

# 6. Register Janus mountpoint matching your PORT (via janus.plugin.streaming.jcfg
#    OR dynamic via janus_admin API — see ADAPTERS.md):
# Example mountpoint block:
#   v4l2-webcam-<name> : {
#       type = "rtp"; id = <unique-id-1400-1999>; description = "USB webcam <name>";
#       media = ({ type = "video"; port = "<port>"; codec = "h264"; ...});
#   };
sudo systemctl restart janus  # to load new mountpoint

# 7. Start the adapter:
sudo /usr/local/bin/encoder-admin start --family rtp-v4l2 --instance <name>
# OR direct: sudo systemctl start rtp-v4l2@<name>

# 8. Verify:
sudo journalctl -u rtp-v4l2@<name> -n 20 -f
# Browser: open /api/v1/{cam_type}/color_view.html?stream_id=<mountpoint-id>
```

Auto-detected pixel format: if you leave `PIX_FMT=""`, the script probes the device
and picks the best supported format (YUYV → MJPG → NV12 → fallback).

See `docs/ADAPTERS.md` for the full adapter taxonomy + how to add custom adapter types.

## Don't do this

- **Don't** reboot Pi without operator approval (config:`CAM_WATCHDOG_REBOOT_ENABLED=0`)
- **Don't** run admin CLI without sudo (boundary contract violated, won't work)
- **Don't** `kill -9` mux process — leaves FIFO orphans, restart cleanly
- **Don't** edit `secrets.yml` outside of operator change window
- **Don't** push to main branch without passing tests (`pytest`)
- **Don't** ignore audit log warnings — security forensics depend on it

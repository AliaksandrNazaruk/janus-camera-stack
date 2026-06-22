# Architecture Overview

This stack is a generic WebRTC video pipeline for RealSense / V4L2 cameras. One robot
deployment is shown as a reference; designed for reuse under other applications.

> **Current state & in-flight refactors:** this overview describes the *intended* layering. For
> what is actually done vs. still-debt as of the latest refactor wave (route infra-purity is
> closed; `stream_bindings.py` / `stream_binding_store.py` are still fat), read
> **[ARCHITECTURE_CURRENT.md](ARCHITECTURE_CURRENT.md)** — the authoritative current-state anchor.
> It wins over any older doc.

## Layers (L0 → L4)

```
L0 — Hardware + Linux primitives
     /dev/video*, /dev/cam-rgb (udev symlink), systemd unit files,
     /etc/sudoers.d/*.conf, /etc/sysctl.d/*.conf, /run/realsense/*.fifo

L1 — Encoder pipeline
     realsense-mux.py (pyrealsense2 → FIFO)   [color+depth+IR producer]
     rs-stream.sh (FIFO → ffmpeg → RTP)       [color+depth+IR consumers]

L2 — Encoder admin
     encoder-admin (sudoers-scoped — start/stop/restart/status)
                   families: rs-stream, realsense-mux, rtp-v4l2, rtp-rtsp
     camera-admin (V4L2 controls — formats, ctrls, reset-usb)

L3 — Streaming media
     Janus WebRTC Gateway
     - janus.plugin.streaming.jcfg: static mountpoint 1305 (color)
     - dynamic mountpoints 1306+ via admin API (depth/IR); remote bindings ≥ 2000
     - per-mountpoint RTP `iface`: loopback for local, gateway LAN IP for remote
     - janus.plugin.textroom.jcfg: room 1000 (back-channel transport)
     - control plane (HTTP 8088 / admin 7088 / WS 8188) is loopback-only (G2-sec)
     janus-admin (sudoers-scoped — restart, nat-config, status)

L4 — Application logic
     janus_camera_page (FastAPI :8900)
       - device_registry.py: pyrealsense2 device enumeration + provisioning state
       - mountpoint_allocator.py: dynamic mp_id + RTP port pool (flock'ed JSON)
       - janus_admin.py: HTTP client for Janus streaming plugin CRUD
       - sensor_lifecycle.py: orchestrates initialize/stop chains
       - audit_log.py: structured admin action audit (Phase 2)
       - depth_events.py: SSE per-session depth_result distribution (P0-SEC-001)
       - stream_binding_store.py: gateway topology (nodes + remote bindings; G1)
       - binding_provision.py: Janus mountpoint from a binding w/ iface (G2)
       - node_client.py: per-node recovery indirection (local vs offline remote stub; G5)
       - remote_stream_monitor.py: isolated FDIR monitor for remote bindings (G5)
     textroom_relay (FastAPI :9000)
       - topic-based router (Sprint AB1+X3.4 generic)
       - /etc/robot/back-channel-topics.json — operator config
       - rate_limit_hz enforcement per topic (Phase 2)
       - X-Internal-Secret HMAC verification (Phase 2)

Gateway — api-gateway (Docker :8201)
     - reverse proxy with per-service circuit breakers
     - SSE-aware streaming (Phase 1 fix)
     - SERVICE_MAP_JSON env: routes /api/v1/cameras/* and /api/v1/depth_map/*
```

## Flow: color stream

```
USB D435i RGB sensor → pyrealsense2.pipeline (color, RS_ENABLE_COLOR=1) →
  realsense-mux.service → FIFO /run/realsense/color.fifo →
  rs-stream@color.service →
  ffmpeg rawvideo read → libx264 encode → RTP to 127.0.0.1:5004 →
  Janus mountpoint 1305 →
  WebRTC peer connection (browser via TURN relay) →
  <video> element
```

## Flow: depth stream

```
USB D435i Stereo Module → pyrealsense2.pipeline (Z16 + IR Y8) →
  realsense-mux.service:
    - z16 → colorizer → RGB → FIFO /run/realsense/depth.fifo
    - z16 → DepthSampler (RAM, for click-to-depth queries)
    - ir1/ir2 Y8 → FIFOs /run/realsense/ir{1,2}.fifo →
  rs-stream@<sensor>.service [per consumer]:
    ffmpeg rawvideo read → libx264 → RTP to dynamic port (e.g. 5006) →
  Janus dynamic mountpoint (e.g. 1306, allocated via janus_admin) →
  WebRTC →
  depth_view.html with depth_features.js click-to-depth overlay
```

## Flow: depth click query

```
Browser depth click:
  backChannel.publish('depth_query', {req_id, session_id, x, y}) →
    Janus textroom plugin (DataChannel) → 
    POST /textroom-hook to textroom_relay :9000 →
  textroom_relay routes by topic:
    POST mux:8000/depth_query →
    mux DepthSampler.sample(x, y) → {depth_m, age_ms, stale} →
    response →
    relay enriches with session_id from request →
    POST camera-page /internal/depth_broadcast (with X-Internal-Secret) →
  camera-page routes by session_id to correct SSE subscriber →
  Browser EventSource ondata → match by req_id → HUD update
```

## Universal gateway / StreamBinding model (G0–G6)

The stack is now a **gateway** that can front local *and* remote producer nodes,
not just one local camera. The central abstraction is the **StreamBinding** =
one `(node, sensor)` → one Janus mountpoint. Local and remote differ only in
values. Design docs: `docs/design/{STREAM_BINDING_MODEL,GATEWAY_REMOTE_RTP_MODE,
UNIFIED_FDIR_OVER_STREAM_BINDINGS}.md`.

```
node "cam10" (local, host 127.0.0.1)        node "cam55" (remote, host 192.168.1.55)
  cam10:color  → mp 1305 (loopback)           cam55:color → mp 2000 (LAN iface .10)
  cam10:depth  → mp 1306+ (loopback)            (authoritative stored binding)
   (read-only PROJECTION over the                rs-stream on .55 → RTP over LAN →
    serial-keyed mountpoint_allocator)            Janus mountpoint on the GATEWAY
```

- **`stream_binding_store.py`** — the topology store (`/var/lib/camera-fdir/stream_bindings.json`).
  Two maps: `nodes` (single source of truth for a node's host + allocation `ordinal`) and
  `bindings`. **Local bindings are read-only projections** computed from `mountpoint_allocator`
  (all local serials fold to node `cam10`); **remote bindings are authoritative stored rows**.
  ONE free-list: remote allocation is strictly **above** the legacy pool — mountpoint ≥ 2000,
  RTP port ≥ 5100, per-node 100-wide ordinal windows; uniqueness is checked against the union
  of both stores.
- **`binding_provision.py`** — `ensure_janus(binding)` threads `binding.janus.rtp_iface` into
  `janus_admin.create_mountpoint` (loopback for local, gateway LAN IP for remote); idempotency
  is a state contract (CREATED / EXISTS / CONFLICT / FAILED). It **does not** open any firewall
  rule — see the §"Security model" note.
- **`node_client.py`** — recovery indirection keyed by node. `LocalNodeClientAdapter` (cam10,
  may run real local recovery) vs **`RemoteNodeClientStub`** (cam55 — OFFLINE by construction:
  no process/network/shell; a future per-node agent on `NODE_AGENT_PORT` replaces it). This is
  the hard trust boundary: **no code path from a remote binding to a local-destructive action.**
- **`remote_stream_monitor.py`** — an *isolated* FDIR monitor for remote bindings, started in
  `app/core/events.py`. It reuses `janus_summary(mountpoint_id)["video_age_ms"]` per remote
  binding; its only terminal actions are `{mark binding degraded, emit Domain.PRODUCER alert,
  NodeClient.restart_stream}`. It is structurally forbidden (and unit-tested) from touching the
  recovery ladder / reboot counter / quiesce gate — so a stale/fake/hostile remote stream can
  **never** reboot or restart the gateway. See §"Failure modes".

**G6 operator API** (admin-gated, `/api/v1/admin`): `GET /nodes`, `POST /nodes/register`,
`POST /nodes/check` (→ `node_agent_unreachable`/`bootstrap_required`), `GET/POST /stream-bindings`,
`POST /stream-bindings/{id}/ensure-janus`, `POST /stream-bindings/{id}/remove`.

## Robot wrapper pattern (Sprint B1)

Stack is truly generic — joystick, gamepad, gripper overlays, custom HUDs
live in `templates/robot_overlay/`, NOT mixed with player core.

```
templates/
├── color_view.html         # GENERIC — pure stack player, NO joystick scripts
├── depth_view.html         # GENERIC — depth probe only, no gripper
├── camera_config.html      # GENERIC — sensor config UI
│
├── player/                 # GENERIC — back-channel SDK + state machine
│   ├── bootstrap.js
│   ├── adapters/back_channel.js
│   ├── core/state_machine_canonical.js
│   └── app/{recovery_map,reconnect_coordinator,...}.js
│
└── robot_overlay/          # ROBOT-SPECIFIC overlays
    ├── color_view.html     # extends generic + injects robot scripts
    ├── joystick_service.js # consumes BackChannel SDK
    ├── gamepaddriver.js    # browser Gamepad API → axes/buttons
    └── gripper_reticle.js  # robot-specific HUD overlay
```

### How dispatch works

Server route `_render_template_response()`:
1. Read `Settings.stack_default_joystick_mode` (env var `STACK_DEFAULT_JOYSTICK_MODE`)
2. If `'off'` → render `templates/color_view.html` (pure generic stack)
3. If `'always'` AND `templates/robot_overlay/color_view.html` exists → render that
4. Fall back to generic if robot wrapper missing

Browser URL unchanged — same `/color_view.html`. Server picks template
based on deployment intent.

### How robot wrapper integrates

1. Robot wrapper's `<head>` loads joystick_service.js + gamepaddriver.js
2. `bootstrap.js` instantiates `JoystickService` ONLY IF `cfg.joystickMode != 'off'`
   AND `AP.App.JoystickService` is registered (which it is only in robot wrapper)
3. Generic stack: JoystickService class never loaded → bootstrap doesn't try
   to instantiate, conditional fails fast
4. Robot overlays receive `autonomous-player-ready` CustomEvent:
   ```js
   window.addEventListener('autonomous-player-ready', (e) => {
     const { backChannel, textroom, controller, cfg, log } = e.detail;
     // attach your overlay here
   });
   ```

### Creating your robot wrapper

1. Copy `templates/robot_overlay/color_view.html` as starting point
2. Add `<script>` tags for your overlays
3. Either set `STACK_DEFAULT_JOYSTICK_MODE=always` to use existing dispatch,
   or create new route serving your wrapper directly

### Routes for robot overlay scripts

- `/api/v1/{cam_type}/robot_overlay/{filename}` — generic path serving anything
  under `templates/robot_overlay/` directory
- `/api/v1/{cam_type}/gamepaddriver.js` — backward compat alias
- `/api/v1/{cam_type}/gripper_reticle.js` — backward compat alias
- `/api/v1/{cam_type}/player/app/joystick_service.js` — backward compat alias

## Boundary contracts

L4 NEVER calls systemctl directly. ALL state changes through:
  - encoder-admin (--family, --instance, start/stop/restart/status)
  - camera-admin (V4L2 ops + reset-usb)
  - janus-admin (restart, nat-config, status)

These boundary CLIs run via `sudo` with NOPASSWD scoped to each binary.
Sudoers (/etc/sudoers.d/) is the trust boundary.

Architecture fitness tests enforce this — see test_architecture_fitness.py.

## Configuration ownership

| File | Owner | Mutability |
|---|---|---|
| `/etc/robot/rs-color.tuning.env` | L4 writes defaults, operator edits | Runtime (L4 can write via UI) |
| `/etc/robot/rs-color.contract.env` | L4 writes (dynamic PORT) | Runtime |
| `/etc/robot/rs-mux.env` | Operator | Restart realsense-mux to reload (RS_ENABLE_COLOR, rotation) |
| `/etc/robot/rs-{sensor}.tuning.env` | L4 writes defaults, operator edits | Runtime |
| `/etc/robot/rs-{sensor}.contract.env` | L4 writes (dynamic PORT) | Runtime |
| `/etc/robot/back-channel-topics.json` | Operator | Restart relay to reload |
| `/etc/robot/camera-secrets.env` | Operator | RO inside L4 sandbox (BindReadOnlyPaths) |
| `/var/lib/camera-fdir/sensor_allocations.json` | L4 (flock) | Persisted dynamic |
| `/var/lib/camera-fdir/stream_bindings.json` | L4 (flock) | `nodes` + remote `bindings` (G6); local bindings are projections, not stored |
| `/var/log/camera-audit/audit.jsonl` | L4 append-only | Rotated 10MB×5 |
| `/var/log/camera-fdir/fdir.jsonl` | L4 append-only | Rotated 5MB×10 |

## Observability

Prometheus metrics on /metrics (camera-page :8900). Key signals:
- `camstack_mux_input_fps{sensor}` — depth/ir1/ir2 capture rate
- `camstack_janus_output_fps{mountpoint_id}` — Janus → client throughput
- `camstack_client_jitter_ms` / `camstack_client_rtt_ms` — network quality
- `camstack_client_frames_decoded_total` — browser receive count
- `camstack_recovery_ladder_level` — FDIR state (0=NOMINAL, 4=SAFE)
- `camstack_watchdog_escalations_total{level}` — escalation counts
- `camstack_fdir_events_total{domain,severity}` — `domain="producer"` = remote-binding faults (isolated from the local ladder; alert on these separately, see SLO.md)
- `gateway_proxy_requests_total{service, status}` — gateway-level
- `gateway_circuit_breaker_state{service}` — CB observability

Health endpoints:
- `/healthz` — basic liveness
- `/health/stream` — end-to-end stream health
- `/readyz` — readiness probe (camera-page + gateway)
- `/api/v1/cameras/dashboard.html` — operator UI
- `/api/v1/cameras/registry.json` — **local-device** registry (serials/sensors on this node only)
- `/api/v1/admin/nodes` + `/api/v1/admin/stream-bindings` — cross-node **topology** (G6, admin-gated)

Audit log:
- `/var/log/camera-audit/audit.jsonl` — admin action trail (Phase 2)
- Query: `jq 'select(.outcome != "success")' /var/log/camera-audit/audit.jsonl`

## Security model

| Layer | Mechanism |
|---|---|
| Browser → Janus | DTLS/SRTP encrypted media; ICE with TURN relay |
| Browser → API | Admin endpoints require `X-Admin-Token` HMAC compare |
| textroom_relay ↔ camera-page | `X-Internal-Secret` HMAC + localhost-only IP allowlist |
| L4 → admin CLIs | sudoers NOPASSWD scoped per-binary |
| L4 sandbox | systemd ProtectSystem=strict, BindReadOnlyPaths=secrets |
| TURN credentials | Ephemeral HMAC-SHA1 rotated on schedule |
| Per-session SSE | session_id routing prevents cross-tab leak (P0-SEC-001) |
| Back-channel | rate_limit_hz enforcement per topic prevents flood (Phase 2) |
| Janus control plane | HTTP 8088 / admin 7088 / WS 8188 loopback-bound + A2 firewall DROP off-loopback (G2-sec) |
| Remote RTP ingress | mountpoint binds the gateway LAN IP (never 0.0.0.0). ⚠️ **The per-binding host-scoped fail-closed firewall + SRTP are NOT yet built** (GATEWAY_REMOTE_RTP §4) — remote ingest is loopback/bench-only until then; `ensure-janus` opens no firewall rule. |
| Remote node trust | a remote binding can never trigger a local-destructive action (offline `RemoteNodeClientStub`, no shell) |

## Failure modes (FDIR)

There are now **two** FDIR subsystems. The **local recovery ladder** drives the
single local camera (`cam10`); the **remote stream monitor** handles remote
producer bindings and is structurally barred from the ladder.

**Local recovery ladder** (5-level, persisted):

| Level | Trigger | Action |
|---|---|---|
| 0 | NOMINAL | none |
| 1 | Stream age > threshold | restart encoder pipeline |
| 2 | Persistent failure | recreate Janus mountpoint |
| 3 | Janus unreachable | restart janus.service |
| 4 | All recovery exhausted | reboot (bounded by `max_fdir_reboots`, default 2; `CAM_WATCHDOG_REBOOT_ENABLED=0` disables it entirely) → then SAFE mode |

- **Shared-Janus reboot guard (G5.1d):** a Janus admin-probe failure escalates toward JANUS
  recovery (→ possible reboot) **only when the local stream is not independently confirmed
  alive** (via the color snapshot's monotonic freshness). So a remote mountpoint stalling the
  shared Janus can't reboot `cam10`. A suppressed escalation logs `outcome="suppressed_local_alive"`
  — expected, not a fault.
- **TB-C1 quiesce** suppresses self-inflicted staleness during a planned restart (domain-scoped,
  120s ceiling; `outcome="suppressed_planned"`).

**Remote stream monitor (`Domain.PRODUCER`):** a remote producer going silent is classified
`Domain.PRODUCER`, **never** JANUS/PIPELINE/SENSOR. Its only actions are mark-degraded + alert +
an (offline) remote restart — it can never restart Janus, reset USB, or reboot the gateway.
Remote-binding faults are therefore invisible to the local ladder and to `/healthz`; alert on
`camstack_fdir_events_total{domain="producer"}` separately (SLO.md).

State persisted in `/var/lib/camera-fdir/fdir_ladder.json` (flock atomic).
Events streamed to `/var/log/camera-fdir/fdir.jsonl` (rotated).

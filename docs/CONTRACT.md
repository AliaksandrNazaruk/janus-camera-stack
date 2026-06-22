# janus_camera_page contract — L4 control plane

> **Stack position**: L4 (control plane) — sits between L3 (Janus gateway) and browser/operator.
> **Runtime**: FastAPI service on port 8900 (main app) + textroom_relay on 9000.
> **Code**: ~5700 LOC (app/), 12 routes, 20 service modules.
> **Status**: WIP — contract documented here partially formalizes existing behavior;
> code remains pre-refactor (see "Refactor pain points" below).

## Position in layer stack

```
   ┌─────────────────────────────────────────────────────┐
   │  Browser (WebRTC client, joystick HID, admin UI)    │  L6/L7
   └───────────────┬─────────────────────────────────────┘
                   │ HTTPS via cloudflared tunnel
   ┌───────────────▼─────────────────────────────────────┐
   │  L5 cloudflared (ingress)                            │
   └───────────────┬─────────────────────────────────────┘
                   │
   ┌───────────────▼─────────────────────────────────────┐
   │  L4 janus_camera_page (this package)                 │
   │  ─ Session bootstrap, NAT config, mode enforcement   │
   │  ─ Joystick textroom relay (port 9000, separate svc)│
   │  ─ Depth proxy, thumbnail proxy, V4L2 enumeration   │
   │  ─ FDIR recovery ladder (autonomy subsystem)         │
   └───────────────┬─────────────────────────────────────┘
                   │ Janus public API (port 8088/8188)
                   │ NAT config write under flock (/var/lock/janus-jcfg.lock)
                   │ sudo systemctl ... (cross-layer leak — see below)
   ┌───────────────▼─────────────────────────────────────┐
   │  L3 janus.service (WebRTC gateway)                   │
   │  L2 rs-stream@color.service (ffmpeg encoder)         │
   │  L1 host kernel / iptables / sysctl                  │
   │  L0 realsense-mux → /run/realsense/color.fifo        │
   └─────────────────────────────────────────────────────┘
```

## What L4 owns

### URL tree (Sprint X4 contract — enforced by fitness tests)

Three namespaces, NO `{cam_type}` prefix anywhere (except 3 grandfathered URLs).

**Generative per-stream** — `/api/v1/cameras/...` (`devices.py` + `device_camera.py`):
| Route | Purpose |
|---|---|
| `GET /api/v1/cameras/registry.json` | All devices+sensors discovered |
| `GET /api/v1/cameras/streams` | All allocations with desired/runtime state (Sprint X4) |
| `GET /api/v1/cameras/dashboard.html` | Devices dashboard HTML |
| `POST /api/v1/cameras/{serial}/{sensor}/initialize` | Start pipeline + persist `desired_active=True` |
| `POST /api/v1/cameras/{serial}/{sensor}/stop` | Stop pipeline + persist `desired_active=False` |
| `GET/POST /api/v1/cameras/{serial}/{sensor}/config` | Encoder tuning (admin) |
| `GET /api/v1/cameras/{serial}/{sensor}/{viewer,camera_config}.html` | Per-stream HTML pages |
| `GET /api/v1/cameras/{serial}/{sensor}/{modes,sensors}` | V4L2/RealSense catalogs |

**Cross-cutting admin** — `/api/v1/admin/...` (`admin_dashboard.py` + `admin_config.py` + `stream_bindings.py`):
| Route | Purpose |
|---|---|
| `GET /api/v1/admin/dashboard` | Aggregate snapshot |
| `GET /api/v1/admin/services` | systemd state of known services |
| `GET /api/v1/admin/mountpoints` | Janus streaming plugin list |
| `GET /api/v1/admin/audit-log` | Recent audit entries |
| `POST /api/v1/admin/services/{name}/restart` | Service restart (rate-limited) |
| `POST/DELETE /api/v1/admin/mountpoints[/{id}]` | Janus mountpoint CRUD (create accepts optional `iface`, default `127.0.0.1`; gateway LAN IP for remote RTP) |
| `POST /api/v1/admin/encoders/{family}[/{instance}]/{start,stop}` | Low-level encoder control |
| `GET/POST /api/v1/admin/config/*` | Config snapshot + secret rotation + jcfg re-render |
| `GET /api/v1/admin/nodes` · `POST /api/v1/admin/nodes/register` · `POST /api/v1/admin/nodes/check` | G6 gateway topology — list/register/probe nodes (check → `node_agent_unreachable`/`bootstrap_required`) |
| `GET/POST /api/v1/admin/stream-bindings` | G6 — list (local projections + remote) / create remote binding |
| `POST /api/v1/admin/stream-bindings/{id}/ensure-janus` · `.../remove` | G6 — provision/teardown the Janus mountpoint for a remote binding |

**System-wide** — no prefix (camera-type-independent):
| Route | Purpose |
|---|---|
| `GET /healthz`, `/livez`, `/metrics`, `/telemetry` | Probes + ingestion |
| `GET /status`, `/relay/time`, `/relay/pong` | System status + relay clock |
| `GET /janus`, `WS /janus-ws`, `WS /janus/ws` | Janus pass-through proxy |
| `GET /client-config` | WebRTC ICE config (viewer-gated, ephemeral TURN per session) |
| `GET /depth`, `/depth/frame`, `/depth/color_frame`, `/depth/frame_color_overlay` | RealSense depth queries (viewer-gated) |
| `GET /depth_events`, `POST /internal/depth_broadcast` | SSE depth query results + internal relay |
| `GET /color_view.html`, `/depth_view.html`, `/ir_view.html`, `/operator_dashboard.html`, `/admin_config.html`, `/camera_config.html` | HTML views |
| `GET /preview/{mp_id}` | Generic mountpoint preview (viewer-gated) |
| `GET /janus.js`, `/streamer.js`, `/depth_features.js`, `/viewer_auth_bootstrap.js`, `/gripper_reticle.js`, `/gamepaddriver.js`, `/gamepad_config.json` | Static JS assets |
| `GET /player/{path:path}`, `/robot_overlay/{path:path}` | Player + robot wrapper assets |
| `GET /static/*` | StaticFiles mount |
| `GET /favicon.ico`, `/api/v1`, `/api/v1/sensor_types` | Misc |

**Grandfathered legacy** (only 3 — fitness test enforces immutability):
- `/api/v1/color_camera/color_view.html` (color node serves directly)
- `/api/v1/depth_camera/color_view.html` (color node proxies to depth)
- `/api/v1/depth_camera/depth_view.html` (color node proxies to depth)

**Cross-node proxy boundary** (not subject to "no cam_type" rule):
- `/api/v1/depth_camera/*` — `depth_proxy.py` forwards to remote depth_camera node (whitelist-gated)

### Service modules (`app/services/`)

| Module | Concern |
|---|---|
| `recovery_ladder.py` | 5-level FDIR escalation (retry → restart pipeline → restart janus → USB reset → reboot) |
| `recovery_persistence.py` | Ladder state JSON + reboot_count flock (extracted Sprint D) |
| `mountpoint_allocator.py` | (serial, sensor) → (mp_id, rtp_port) allocation with `desired_active` flag (Sprint X4) |
| `sensor_lifecycle.py` | initialize/stop pipelines, persists desired_active (Sprint X3/X4) |
| `nat_config.py` | NAT/TURN config model + janus-admin CLI dispatch (flock-coordinated) |
| `watchdogs.py` | Stream-age + janus-health watchdogs, triggers recovery ladder |
| `system_mode.py` | Mode lattice + policy enforcement (max FPS, bitrate, TURN requirement) |
| `janus.py`, `janus_admin.py` | Janus REST + admin API clients |
| `ws_proxy.py` | WebSocket reverse proxy (depth node) |
| `mode_enforcer.py` | Applies mode policy: stops streams in SAFE, throttles in DEGRADED |
| `fdir_events.py` | Event emission to journald + persistence |
| `audit_log.py` | Operator action audit (admin endpoints emit to JSONL) |
| `stream_binding_store.py` | Gateway topology store — `nodes` + remote `bindings`; local bindings are projections over `mountpoint_allocator`; remote alloc above the legacy pool (mp ≥ 2000, port ≥ 5100) (G1) |
| `binding_provision.py` | `ensure_janus(binding)` — Janus mountpoint create from a binding with `iface`; idempotency state contract (G2) |
| `node_client.py` | Per-node recovery indirection — `LocalNodeClientAdapter` vs offline `RemoteNodeClientStub` (no shell); `probe_agent` (G5) |
| `remote_stream_monitor.py` | Isolated FDIR monitor for remote bindings — `Domain.PRODUCER` only, never the local ladder; started by the main service (G5) |
| `turn_probe.py` | turnutils_stunclient/uclient wrapper (live STUN+TURN allocation health) |
| `viewer_auth.py` | Token-bound viewer gate (P0-SEC-001), per-session ephemeral TURN (P1-SEC-002) |
| `v4l2.py` | V4L2 queries through camera-admin CLI (Sprint B boundary) |
| `system.py` | Atomic file write helper + subprocess runner |

### CLI tools (`app/tools/`)

| Module | Concern |
|---|---|
| `sensor_reconcile.py` | Boot oneshot — reads `sensor_allocations.json`, starts streams marked `desired_active=True` (Sprint X4) |

### State files L4 owns

| Path | Owner | Purpose |
|---|---|---|
| `/var/lib/camera-fdir/ladder_state.json` | recovery_persistence | Current FDIR level + history |
| `/var/lib/camera-fdir/events.log` | fdir_events | Audit log of recovery actions |
| `/var/lib/camera-fdir/reboot_count` | recovery_persistence | Circuit breaker for reboot loops |
| `/var/lib/camera-fdir/sensor_allocations.json` | mountpoint_allocator | (serial, sensor) → (mp_id, rtp_port, desired_active) — boot reconciler input |
| `/var/lib/camera-fdir/stream_bindings.json` | stream_binding_store | `nodes` + remote `bindings` (G6 topology); local bindings are projections, not stored |
| `/var/log/camera-fdir/fdir.jsonl` | fdir_events | Structured event log (operator audit) |
| `/etc/robot/janus-nat.json` | nat_config | Persisted NAT/TURN config (source of truth for admin endpoint) |
| `/run/camera/<runtime files>` | various | systemd RuntimeDirectory |

### Runtime services

- `janus-camera-page.service` — main FastAPI on port 8900 (uvicorn, single worker, systemd Type=notify + WatchdogSec=30s). On startup it spawns the janus + snapshot watchdogs **and** the `remote_stream_monitor` thread (G5; a no-op until a remote binding is provisioned).
- `janus_camera_page_hook.service` — textroom_relay on port 9000 (separate sidecar)
- `sensor-reconcile.service` — boot-time oneshot, brings up `desired_active=True` streams (Sprint X4 — replaces per-stream systemd `enable` flags)

### State model: desired vs actual (Cycle 12)

> **Node lifecycle (local + remote):** how a node is provisioned, started, stopped, and recovered —
> and the `desired_up` ⟂ `fdir.enabled` split — is the canonical **`docs/NODE_CONTRACT.md`**. Read it
> for anything about running streams on cam10 or a remote node.

L4 is a control plane: it keeps a **desired** intent, observes **actual** runtime, and runs per-domain
**reconcilers** that converge actual → desired. There is deliberately NO single unified state machine —
the domains are heterogeneous and each owns its own reconcile loop. The three roles, kept distinct:

- **DESIRED (intent, the source of truth):** what the operator/config wants.
  - streams: `mountpoint_allocator` `desired_active` (per (serial, sensor); `list_desired_active()` is the
    boot reconciler's input). Remote topology: `stream_binding_store` (`nodes` + `bindings`).
  - runtime config: `runtime_revision_store` (validated revisions). NAT/TURN: `/etc/robot/janus-nat.json`.
    fleet: the declarative manifest. firewall: `firewall_sync.desired_rules` (derived from bindings).
- **STORED status (last-known / intent projection, NOT live):** `StreamBinding.status`
  (`StreamStatus`) — last-known for remote (`set_status`), an intent projection for local. Display +
  cache only; can be stale. The NAT apply-status sidecar (`pending`/`applied`/`failed`) is the analogous
  last-known for NAT.
- **ACTUAL (probed on demand, never persisted as truth):** `janus.janus_summary` (RTP age / mountpoint
  liveness), node `reachability`, `encoder_admin` status (systemd), realsense/v4l2 probes, snapshot
  freshness.
- **RECONCILERS (desired → actual convergence):** `binding_provision.reconcile_janus` (boot + run-once),
  `remote_stream_monitor` (steady-state remote), `firewall_sync.reconcile`, `fleet.reconcile`,
  `runtime_config_apply` (Cycle 3), the NAT apply op (Cycle 7), watchdogs/FDIR (local stream liveness).
  The read-only desired-vs-actual **drift report** is `reconcile_drift` (remote bindings vs live Janus);
  `ui_viewmodel` fuses desired + actual for the operator dashboard.

**Rule:** act on `desired_active` for intent and on the probes / `reconcile_drift` for actual. Do NOT
treat `StreamBinding.status` as the live truth — it is the stored last-known/intent. Known asymmetries
(see `docs/design/CAMERA_SESSION_STATE_MODEL.md`): local cam10 has no read-only drift view paired with
remote's (FDIR reconciles it implicitly); orphaned Janus sessions are counted + opportunistically
destroyed, not systematically reaped.

## What L4 does NOT own (boundaries)

| Resource | Owner | L4 access |
|---|---|---|
| `/dev/cam-rgb` | L0 camera_bringup | Read-only (V4L2 queries) |
| L0 Python `camera_bringup.api` | L0 camera_bringup | **Allowed import** — typed public facade (L0().status() etc.). Currently L4 accesses L0 only via `camera-admin` CLI subprocess, not direct import. `camera_bringup.api` listed in test_architecture_fitness ALLOWED_IMPORTS as a future opt-in option without re-test churn. |
| `/opt/janus/etc/janus/*.transport.*.jcfg` | L3 / host_infra | None (immutable Ansible config) |
| `/opt/janus/etc/janus/janus.plugin.streaming.jcfg` | L3 / host_infra | None (template'd from cameras dict) |
| `/opt/janus/etc/janus/janus.jcfg` (NAT block) | **Shared, flock-coordinated** | **Write** under `_jcfg_lock()` |
| `/etc/systemd/system/*` | L1 / host_infra | None |
| `iptables`, `sysctl` | L1 / host_infra | None |
| Mountpoint admin secret (`STREAMING_ADMIN_KEY`) | L3 / host_infra secrets.yml | **Read** — used as the streaming-plugin `admin_key` for mountpoint create/destroy (`janus_admin`) |
| TURN credentials (rotation) | L3 host_infra TURN rotator | Reads `turn_user`/`turn_pwd` from janus-nat.json (L4-managed) — separate from Janus's runtime creds |

## Contract with L3 (Janus)

### L4 reads + mountpoint CRUD (via Janus REST API on 8088, streaming plugin):
- `GET /janus/info` — version, uptime
- `POST /janus → janus:create` — new session
- `POST /janus/{sid} → janus:attach` — attach streaming plugin
- `POST /janus/{sid}/{hid} → janus:message` — list/info, **and create/destroy** mountpoints
  (`janus_admin.create_mountpoint`/`destroy_mountpoint`, authorized by the streaming-plugin
  `admin_key`). Callers: `sensor_lifecycle` (dynamic depth/IR), the `/mountpoints` admin endpoint,
  and `binding_provision.ensure_janus` for remote bindings.

L4 does **not** call the Janus *core* admin API on port **7088** (server_info/handle admin) —
it performs all mountpoint operations through the streaming plugin on 8088.

### L4 writes (via flock-coordinated direct file write):
- `/opt/janus/etc/janus/janus.jcfg` — between `# BEGIN NAT AUTO` and `# END NAT AUTO` markers
- Pattern: acquire `/var/lock/janus-jcfg.lock` → read → render NAT block → write atomically → restart janus
- See [host_infra ADR 0001](../../host_infra/docs/adr/0001-flock-multi-writer-coordination.md)

### L4 triggers (via `sudo systemctl`):
- `restart janus.service` — after NAT block update
- `restart rs-stream@color.service` (=`settings.service_name`) — recovery, mode enforcement
- `start realsense-failsafe.service` — USB reset (FDIR level 4)
- `reboot` — FDIR level 5 (with circuit breaker)

## Cross-layer leaks (status: 5 RESOLVED via admin CLIs, 1 reboot remaining)

L4 used to bypass L3's would-be API via shell-out — **violation of layer
boundaries**. Today (2026-06-14) we introduced `janus-admin` CLI (L3-owned
binary, scoped sudoers entry) — L4 invokes via subprocess vs. knowing L3
implementation details. Contract is now CLI args + exit codes, not "we both
know systemctl works".

| Leak | Location | Status | Implementation |
|---|---|---|---|
| ~~`sudo systemctl restart janus.service`~~ | `recovery_ladder.py:475`, `nat_config.py:215` | ✅ RESOLVED | `sudo /usr/local/bin/janus-admin restart` (L3-owned) |
| ~~Direct write `/opt/janus/etc/janus/janus.jcfg`~~ | `nat_config.py:patch_janus_cfg_with_nat()` | ✅ RESOLVED | `sudo /usr/local/bin/janus-admin nat-config < <json>` (L3-owned, internal flock) |
| ~~`sudo systemctl restart rtp-rgb@cam-rgb.service`~~ | `recovery_ladder.py:468`, `mode_enforcer.py:76,119,149`, `system.py:51,56` | ✅ RESOLVED | `sudo /usr/local/bin/encoder-admin {restart,stop,start,status}` (L2-owned) |
| ~~`sudo systemctl start realsense-failsafe.service`~~ | `recovery_ladder.py:485` | ✅ RESOLVED | `sudo /usr/local/bin/camera-admin reset-usb` (L0-owned) |
| ~~Direct invoke `v4l2-ctl`~~ | `services/v4l2.py:23,52-53,65,101` | ✅ RESOLVED | `sudo /usr/local/bin/camera-admin {v4l2-formats,v4l2-info,v4l2-ctrls,v4l2-set-ctrl}` (L0-owned, input validation) |
| `sudo systemctl reboot` | `recovery_ladder.py:535` | ⚠️ REMAINING | Code path enforced unreachable via CAM_WATCHDOG_REBOOT_ENABLED=0 |

**Pattern established**: L3 exposes `/usr/local/bin/janus-admin <command>`
with explicit contract:
- Caller doesn't know underlying systemctl unit names or file paths
- L3 owns: flock coordination, atomic write, backup, restart sequencing
- Sudoers entry scoped to a specific binary (NOT full systemctl access)
- Same pattern repeatable for L2 + L0 (when needed)

See `host_infra/roles/janus/files/janus-admin.py` for implementation.

L4 still runs with a sudoers entry for invoking janus-admin (and the remaining 3
leaks). Full removal of the sudo dependency requires either L2/L0 CLIs or
moving L4 into a container with D-Bus-based service control.

## Configuration

| Source | Loaded by | Contents |
|---|---|---|
| Env vars (`JANUS_URL`, `CAM_ADMIN_TOKEN`, `TURN_*`, `WATCHDOG_*`, etc.) | `app/core/settings.py` Pydantic Settings | ~40 settings, see `Settings` class |
| `/etc/robot/janus-nat.json` | `nat_config.py:load_nat_config()` | Persisted NAT/STUN/TURN config (operator-edited via API) |
| `/etc/robot/camera-secrets.env` | systemd EnvironmentFile | TURN_PASS, TURN_SHARED_SECRET (host secrets) |
| `/var/lib/camera-fdir/*` | recovery_ladder/fdir_events | Runtime state (not config) |
| `shared_config/network.py` | imported | Static DEVICES + PORTS dict (single source for IPs/ports) |

**Service-level env constants** (module-level `os.getenv` in `services/`, *not* in `Settings` — the
architecture-fitness test permits these and points here for the rationale):

| Env key | Default | Read by | Purpose |
|---|---|---|---|
| `CAM_STREAM_BINDINGS_PATH` | `/var/lib/camera-fdir/stream_bindings.json` | `stream_binding_store.py` | Node + remote-binding store path |
| `NODE_AGENT_PORT` | `8901` | `node_client.py` | Port probed for the (future) per-node agent `/healthz` |
| `REMOTE_MONITOR_HEARTBEAT_SEC` | `300` | `remote_stream_monitor.py` | Re-alert cadence for a degraded remote binding |

`RTP_TARGET_HOST` is **not** read by L4 — L4 *writes* it (default `127.0.0.1`; gateway LAN IP for
remote) into `/etc/robot/rs-{sensor}.contract.env`, which the external `host_infra` `rs-stream.sh`
consumes. It is a contract *output*, not an input.

## Health / observability

| Endpoint | Purpose |
|---|---|
| `GET /system/health` or `/healthz` | Liveness + janus_reachable + stream_active + mode |
| `GET /metrics` | Prometheus textfile (including FDIR counters, watchdog timers) |
| `GET /fdir/state` | Current ladder level + last action |
| `GET /fdir/events` | Recent recovery events |
| Logs | systemd journal (`journalctl -u janus-camera-page`) |
| sd_notify | systemd Type=notify, WatchdogSec=30s (watchdogs.py heartbeats) |

## Known issues / refactor pain points

(See also the survey in [host_infra/docs/adr/](../../host_infra/docs/adr/) — this contract captures the **existing state**, not the ideal one.)

1. **Fat recovery_ladder.py (593 LOC)** — single file owns: ladder state, escalation policy, action execution, persistence, circuit breaker. Should split: `Ladder` (state machine) + `Actions` (executors) + `Persistence` (state file).

2. **Cross-layer shell-outs** — see the "Known cross-layer leaks" table above. Each `sudo systemctl ...` violates a layer boundary.

3. **Mixed-concern routes** — `routes/janus.py` couples WebRTC bootstrap (public) with NAT CRUD (admin) with janus restart (admin). Should split into `public_webrtc.py` + `admin_nat.py`.

4. **Monolithic Settings** (~40 env vars one class) — should be per-subsystem `JanusSettings`, `TurnSettings`, `FdirSettings`, `WatchdogSettings`.

5. **No formal contract with L3 admin API** — see "Cross-layer leaks". Eventually L3 (Janus role in host_infra) should expose an HTTP API for NAT updates / restart, and L4 calls it instead of file write + sudo.

6. **textroom_relay.py separate app sidecar** — forks the main service for a single hook endpoint. Should be either: (a) a handler plugin inside the main app, (b) a separate microservice if it is genuinely independent.

7. **Lazy metric imports for circular deps** (`recovery_ladder.py:64-79`) — fragile; suggests circular module structure that should be refactored.

8. **No unit tests for recovery_ladder** — biggest file, no isolated tests. Existing `tests/test_concurrent_races.py` covers some scenarios, but the ladder state machine itself is untested. Should add `test_recovery_ladder.py` per-action + per-state-transition.

## Coordination invariants

| Invariant | Mechanism |
|---|---|
| Only one mutator on janus.jcfg at any moment | `_jcfg_lock()` (POSIX flock on `/var/lock/janus-jcfg.lock`, shared with host_infra writers) |
| FDIR ladder state not corrupted during concurrent recovery attempts | File lock inside `RecoveryLadder.execute()` (see tests/test_concurrent_races.py) |
| Mode transitions atomic | `system_mode.transition()` holds in-memory lock + writes mode file atomically |
| Reboot circuit breaker | `/var/lib/camera-fdir/reboot_count` + threshold check |

## Tests

```bash
cd janus_camera_page
python3 -m pytest tests/ -v
```

~50 test files. Highlights (Sprint X4):

| Test | Concern |
|---|---|
| `test_url_contract_fitness.py` | Enforces "only 3 grandfathered `/api/v1/{cam_type}/` URLs" (backend + HTML + JS) |
| `test_url_audit.py` | 10-case audit suite (A–F): liveness smoke / cross-link integrity / auth coverage / OpenAPI completeness / method strictness / path param validation |
| `test_allocator_desired_state.py` | 16 cases — `desired_active` round-trip, backward-compat load of legacy records, `set_desired`/`ensure`/`list_desired_active` |
| `test_sensor_reconcile.py` | 17 cases — boot reconciler (seed-on-empty, plan ordering, idempotency, dry-run, partial-failure exit code) |
| `test_streams_dashboard.py` | 3 cases — `GET /cameras/streams` list endpoint |
| `test_turn_probe.py` | 9 cases — STUN/TURN allocation probe (subprocess mock) |
| `test_architecture_fitness.py` | AST-walk import boundary checks (L4 must not import camera_bringup internals etc) |
| `test_boundary_fitness.py` | Cross-layer leak checks (no raw `sudo systemctl`, no direct jcfg writes, no v4l2-ctl) |
| `test_nat_config_lock.py` | flock acquire/release/exception safety/cross-process |
| `test_concurrent_races.py` | Recovery + mode race conditions |
| `test_recovery_ladder.py` | FDIR ladder state machine + actions |
| `test_realsense_mux.py` | RealSense mux logic (rotation, depth/color CameraService with mocked pyrealsense2) |

## TODO (formal refactor target)

Eventually L4 should be extracted to the same level as L0 camera_bringup / host_infra:

```
janus_camera_page/                       (current)
  ├── app/                               (FastAPI app)
  └── tests/                             (pytest)

→

l4_control_plane/                        (proposed)
  ├── l4_control_plane/                  (package)
  │   ├── public/                        (WebRTC bootstrap, no admin)
  │   ├── admin/                         (NAT CRUD, restart, FDIR)
  │   ├── autonomy/                      (recovery_ladder split)
  │   ├── proxies/                       (depth, ws)
  │   └── adapters/                      (ports: JanusClient, EncoderClient, SystemctlAdapter)
  ├── tests/
  ├── CONTRACT.md                        (this file, refined)
  └── docs/adr/
```

Per-subsystem packages, hexagonal architecture (ports/adapters), per-route admin/public split.

**Estimate**: a multi-hour effort, requires a proper L3 admin API first (so that L4 does not shell out).

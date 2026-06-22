# Node contract — how a camera node works and how to run it (local + remote)

**Status:** LIVING CONTRACT (not a point-in-time design note). This is the canonical reference for
what a camera node is, who owns its stream lifecycle, and how to bring one up locally or remotely.
If code and this doc disagree, that is a bug in one of them — fix it. Enforced in part by fitness
guard **#28** + the `remote_stream_monitor` / `binding_provision` / store tests.

Background design + history: `docs/design/UNIFIED_NODE_LIFECYCLE.md`,
`docs/design/DYNAMIC_CAMERA_ONBOARDING.md`. Desired-vs-actual state model: `docs/CONTRACT.md`.

---

## 1. The model in one screen

**A node is a node. The only difference between the local node (`cam10`, the gateway host) and a
remote node (e.g. `cam55`) is the TRANSPORT** the gateway uses to drive it (in-process/local calls vs
SSH for provisioning + HTTP to the node-agent for control). Everything else — the desired-state
contract, the lifecycle, recovery — is identical.

**Two INDEPENDENT axes per stream (never re-conflate them):**

| Axis | Field | Meaning | Drives |
|---|---|---|---|
| **Start/Stop intent** | `desired_up` (remote) / `desired_active` (local allocator) | "should this stream be wanted up?" | the gateway maintains the Janus **mountpoint** (the listener) — survives a gateway restart |
| **Autonomous keep-alive (FDIR owns recovery)** | `fdir.enabled` | "auto-manage this stream — detect + **recover** + escalate?" | bring up / restart the producer when reachable **and** emit a PRODUCER alert |

So **"FDIR off" means "not auto-managed" — the mountpoint is kept (it's wanted), but the gateway
does NOT auto-recover it; the operator restarts it by hand (Restart).** FDIR is the
**F**ault-**D**etection-**I**solation-**R**ecovery switch and genuinely owns recovery — recovery
gates on `desired_up AND fdir.enabled` (see `docs/design/FDIR_RECOVERY_SEMANTICS.md`).

**The gateway is the single orchestrator.** Nodes do **not** autostart their encoders. The gateway
owns the Janus mountpoints (the streams are useless if the gateway is down anyway), so it also owns
when streams come up. On a node coming back online the gateway converges it to desired.

## 2. Node anatomy — what runs ON a node

Installed by `host_infra/node-bundle/bootstrap.sh` into `/usr/local/bin` + `/etc/systemd/system`:

| Unit | Autostart (`enable`) | Role |
|---|---|---|
| `camera-node-agent.service` | **ENABLED** | the gateway control plane (HTTP `:8901`, per-node `X-Node-Token`) |
| `realsense-mux.service` | **ENABLED** | RealSense SDK → per-sensor FIFOs (`/run/realsense/<sensor>.fifo`); color is USB-bandwidth-gated (`RS_ENABLE_COLOR`) |
| `rs-stream@<sensor>.service` | **DISABLED** | the encoder (FIFO → H264 → RTP to the gateway). Started/stopped by the GATEWAY, never `enable`d for autostart (`Restart=always` still handles a crash while running) |

Node-agent endpoints (all `X-Node-Token`-gated except `/healthz`):

| Method · path | Use |
|---|---|
| `GET /healthz` | unauthenticated reachability probe (`probe_agent`) — gates gateway convergence |
| `GET /tuning?sensor=` · `POST /tuning?sensor=` | read / write encoder tuning (resolution/fps/rotation/bitrate) + restart the encoder |
| `GET /modes?sensor=` | supported `{width,height,fps}` from the RealSense SDK (for the console dropdown) |
| `POST /restart_stream?sensor=` · `POST /stop_stream?sensor=` | start/stop the encoder (the gateway's lever) |
| `GET /probe_devices` | RealSense device inventory (serials) |

The node ships **no** stream-lifecycle logic of its own (no autostart, no node-side reconciler) — it
executes the gateway's commands.

## 3. Onboarding / launch — local vs remote (same contract)

### Remote node (e.g. `cam55`) — gateway drives it over SSH + HTTP
1. **Register** — `POST /api/v1/admin/nodes/register` (host IP). Node gets an opaque `node_id` +
   `provision_state`.
2. **Confirm host key** — TOFU: `GET …/nodes/{id}/host-key` then `POST …/host-key/confirm` (412 until
   confirmed).
3. **Provision** — `POST …/nodes/{id}/provision`: SSH-push the node bundle, run `bootstrap.sh deploy`
   (installs deps + units, enables agent + mux, mints the per-node agent token). Deploys the *pipe*,
   not streams.
4. **Activate sensors** — `POST …/nodes/{id}/streams` (or the per-binding bind): gateway allocates
   `(mp_id ≥ 2000, rtp_port ≥ 5100)`, `ensure_janus` creates the mountpoint, then `bootstrap.sh
   activate --sensor S` on the node **starts** `rs-stream@S` (and `disable`s its autostart). Binding
   is created `desired_up=True`.
5. **Steady state** — the node streams RTP → the gateway Janus mountpoint → `cam55:color` ONLINE.

### Local node (`cam10`, the gateway host) — same contract, in-process
- `mountpoint_allocator.desired_active` is the local equivalent of `desired_up`; `sensor-reconcile.service`
  (boot oneshot) reads it and starts exactly the desired streams (`rs-stream@` is **disabled**, started
  by the reconciler — same "no autostart" rule). Local bindings are **projections** over the allocator,
  not stored rows.
- `POST /api/v1/cameras/{serial}/{sensor}/initialize` ↔ desired_active=True; `…/stop` ↔ False.

## 4. Operating a node (runbook)

| Action | Remote (`cam55`) | Local (`cam10`) |
|---|---|---|
| **Start a stream** | console Restart, or `POST …/stream-bindings/{id}/restart` → `desired_up=True` + ensure mountpoint + node `restart_stream` | `…/initialize` → `desired_active=True` + reconcile/encoder start |
| **Stop a stream** | console Stop, or `POST …/stream-bindings/{id}/stop` → `desired_up=False` + node `stop_stream`. **Does NOT touch FDIR.** | `…/stop` → `desired_active=False` |
| **Change resolution/fps** | the Tune modal → `POST …/stream-bindings/{id}/tuning` (options from `…/modes`) | the local tuning endpoint |
| **Toggle FDIR (auto-manage / recovery)** | `POST …/stream-bindings/{id}/fdir {"enabled":bool}` — independent of Start/Stop; ON = gateway auto-recovers, OFF = manual restart only | (cam10 watchdog ladder, always-on) |
| **After a node reboot** | streams DON'T autostart → the gateway monitor sees the node reachable + `desired_up` **+ `fdir.enabled`** and **converges** (ensure + `restart_stream`) within ~20–30 s (`BRINGUP_THROTTLE_SEC=20`, env `REMOTE_MONITOR_BRINGUP_SEC`). A `desired_up` stream with FDIR **off** keeps its mountpoint but is NOT auto-restarted (manual Restart). | `sensor-reconcile` brings up the desired set at boot |

**What survives a reboot:** every `desired_up` stream keeps its **mountpoint** — the gateway
re-creates it (`reconcile_janus` on gateway start). The **encoder** is auto-restarted only when
`fdir.enabled` (FDIR owns recovery); a `desired_up` + FDIR-off stream waits for a manual Restart. A
`desired_up=False` (Stopped) stream stays down entirely.

## 5. Who owns what

| Concern | Owner |
|---|---|
| Desired state (`desired_up` / `desired_active`), mountpoint id/port allocation | **gateway** (`stream_binding_store` + `mountpoint_allocator`) |
| Janus mountpoint existence (per `desired_up` binding) | **gateway** (`binding_provision.reconcile_janus` at start; `remote_stream_monitor` re-ensures it as part of recovery) |
| Auto-recovery (bring the encoder up / restart it) + fault escalation | **gateway**, gated on `desired_up AND fdir.enabled` (FDIR owns recovery) → node-agent `restart_stream` (remote) |
| Stopping the encoder | **gateway** → node-agent `stop_stream` (remote) or `sensor_lifecycle` (local) |
| Producing RTP from the camera | **node** (`realsense-mux` + `rs-stream@`) |

## 6. Invariants (don't regress these)

1. **`desired_up` ⟂ `fdir.enabled` (two axes, correctly wired).** Stop sets `desired_up=False` and
   must NOT call `set_fdir_enabled`; **mountpoint** maintenance gates on `desired_up`; **recovery
   (convergence) AND escalation** gate on `desired_up AND fdir.enabled` — FDIR owns recovery
   (`docs/design/FDIR_RECOVERY_SEMANTICS.md`). — *guard #28 (`test_start_stop_decoupled_from_fdir`)*.
2. **Nodes never autostart encoders.** `bootstrap.sh` `start`s but never `enable`s `rs-stream@`; the
   gateway brings streams up. Re-adding `systemctl enable rs-stream@` re-creates the node-vs-gateway
   split this contract removes.
3. **Status is observed for ALL bindings**, regardless of `fdir.enabled` — the operator view reflects
   reality (`remote_stream_monitor` always `set_status`).
4. **Convergence is gateway-reachability-gated + short-throttled** (probe `_node_reachable`, retry
   `BRINGUP_THROTTLE_SEC`), separate from the 300 s fault `HEARTBEAT_SEC`.
5. **FDIR safety boundary intact**: a remote fault only ever drives a remote action (node
   `restart_stream`) — never a local-destructive one (`test_remote_tick_does_not...` / the
   no-local-destructive-references guard).

## 7. Red lines

- Do **not** re-enable `rs-stream@` autostart on a node.
- Do **not** re-couple Stop to FDIR (Stop = `desired_up=False`, FDIR untouched).
- Do **not** make the **mountpoint** depend on `fdir.enabled` (it depends on `desired_up`). Conversely,
  **auto-recovery DOES depend on `fdir.enabled`** — FDIR off means manual-restart-only, by design.
- Do **not** add node-side stream-lifecycle logic — the gateway is the single orchestrator.

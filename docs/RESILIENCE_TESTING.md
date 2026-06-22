# Camera Stack — Resilience Testing (Fault Injection, Drills & Soak)

> Version: 1.0 | Date: 2026-03-06
> Reference: `RELEASE_GATE.md` (Gate C), `recovery_ladder.py`, `system_mode.py`

This document is the single resilience-testing reference for the camera
stack. It combines the **fault-injection plan** (the F01–F11 scenario
catalog with expected FDIR behavior) with the **executable soak & drill
procedures** (8-hour passive soak and the active disruption drills). The
executable drills are folded directly into the scenarios they exercise,
so each scenario reads as "what fails → how to inject it → expected
behavior → how to recover".

Passive observation + active disruption tests validate stack stability
beyond what the unit/integration suite can prove. The active drills must
be run manually before any high-stakes promotion (new release, new node
bringup, post-major-refactor sign-off).

---

## 1. FDIR Ladder Reference

The Fault Detection, Isolation & Recovery (FDIR) recovery ladder
escalates through bounded levels. Each escalation also calls
`degrade()` on the system mode (see §2).

| Level | Name | Action | Max attempts | Cooldown |
|-------|------|--------|-------------|----------|
| 0 | retry_handle | Verify Janus + pipeline status | 1 | 10 s |
| 1 | restart_pipeline | `systemctl restart <service>` | 5 | 45 s |
| 2 | restart_janus | `systemctl restart janus.service` | 3 | 90 s |
| 3 | usb_reset | Hardware reset (depth only) | 2 | 90 s |
| 4 | reboot_node | `systemctl reboot` (bounded) | 1 | 300 s |

Circuit breaker: after `MAX_FDIR_REBOOTS` (default 2) FDIR-initiated
reboots → SAFE mode, no more reboots.

---

## 2. System Modes

| Mode | Streams | FPS cap | Require TURN | Require uplink |
|------|---------|---------|-------------|----------------|
| NOMINAL | ✅ | 30 | ✅ | ✅ |
| DEGRADED | ✅ | 15 | ✅ | ✅ |
| LOCAL_ONLY | ✅ | 15 | ❌ | ❌ |
| SAFE | ❌ | 0 | ❌ | ❌ |

Degradation is monotonic: each ladder escalation calls `degrade()`.
Promotion back to NOMINAL requires explicit `promote()` after a healthy
streak.

---

## 3. Fault Scenario Catalog (F01–F11)

Each scenario lists the injection method, detection, expected FDIR
response, expected mode transition, recovery, and MTTR target. Where an
executable drill exists (F01 ⇐ Drill A, F02 ⇐ Drill B, F03 ⇐ Drill C,
F05 ⇐ Drill D), the exact shell commands, expected observations, and
verification steps are folded into that scenario.

**Depth-node-specific scenarios** (these run on the `.55` depth node, not
the `.10` color node): **F05** (realsense_mux crash), **F07** (depth node
isolation), and **F10** (depth proxy failover).

### F01 — Janus process death

| Field | Value |
|-------|-------|
| Injection | `systemctl stop janus.service` or `kill -9 $(pidof janus)` |
| Detection | Watchdog: `video_age_ms > 10000` (stale stream) |
| Expected FDIR | Level 0 retry → Level 2 restart_janus |
| Expected mode | NOMINAL → DEGRADED |
| Recovery | Janus restarts, mount re-appears, stream resumes |
| MTTR target | ≤ 60 s |
| drill_harness | `TestDrill01_JanusRestart` |

**Executable drill — kill Janus mid-session** (≈ Drill A)

> ⚠ Run only when no real users are connected (test environment OR
> maintenance window). Keep an observation tab open — watch what the
> dashboard reports during the disruption. Recovery may take 30–120 s.

```bash
# Verify pre-state
curl -sf http://localhost:8900/healthz | jq

# Open viewer in browser → confirm stream playing

# Disruption
sudo systemctl kill janus.service

# Expected within ~30s:
#   /healthz → ok=false, janus_reachable=false
#   FDIR ladder → escalate to "restart_janus" level
#   Audit log entry: action="restart_janus", outcome="success"
#   Janus auto-restarted (or restart_pipeline level 1 first if stream still ingesting)
#   Browser tab shows reconnect attempt (or stays connected if ICE recovers)
#   /healthz returns ok=true within 60s of kill

# Verify post-state
sleep 90
curl -sf http://localhost:8900/healthz | jq
journalctl -u janus-camera-page --since "2 minutes ago" | grep -E "fdir|recovery"
```

**Pass**: stream recovers in < 90 s, ladder returns to level 0 within 5 min.

### F02 — ffmpeg pipeline crash

| Field | Value |
|-------|-------|
| Injection | `pkill -9 ffmpeg` |
| Detection | Watchdog: stale `video_age_ms` (no RTP packets) |
| Expected FDIR | Level 0 retry → Level 1 restart_pipeline |
| Expected mode | NOMINAL → DEGRADED |
| Recovery | Pipeline systemd unit restarts, RTP resumes |
| MTTR target | ≤ 45 s |
| drill_harness | `TestDrill02_PipelineRestart` |

**Executable drill — kill encoder (`rs-stream@color`)** (≈ Drill B)

```bash
sudo systemctl kill rs-stream@color.service

# Expected:
#   stream_active=false within watchdog_stale_ms (10s default)
#   FDIR → restart_pipeline (level 1)
#   encoder-admin restart called
#   stream resumes in < 30s
```

### F03 — TURN port block (network blip)

| Field | Value |
|-------|-------|
| Injection | `iptables -I OUTPUT -p udp --dport 3478 -j DROP` for 15 s |
| Detection | Client: ICE disconnected → reconnect attempt |
| Expected FDIR | No server-side FDIR (client reconnects) |
| Expected mode | Unchanged (stream still flows on LAN) |
| Recovery | After iptables rule removed, TURN allocations resume |
| MTTR target | ≤ 30 s after rule removed |
| drill_harness | `TestDrill03_NetworkBlip` |

**Executable drill — packet loss simulation** (≈ Drill C)

> Requires `tc` (Linux traffic control) on the Janus host. Affects ALL
> traffic on the interface — only run in a test environment.

```bash
# Add 5% packet loss on loopback (where RTP flows to Janus)
sudo tc qdisc add dev lo root netem loss 5%

# Observe in dashboard:
#   client_packet_loss_ratio metric should rise
#   FPS may drop slightly
#   FDIR should NOT escalate (5% loss tolerable)

# Increase to 30% — should escalate eventually
sudo tc qdisc change dev lo root netem loss 30%

# Cleanup ALWAYS
sudo tc qdisc del dev lo root
```

### F04 — Full service restart

| Field | Value |
|-------|-------|
| Injection | `systemctl restart janus-camera-page.service` |
| Detection | Health endpoint temporarily unreachable |
| Expected FDIR | None (graceful restart) |
| Expected mode | NOMINAL (after restart) |
| Recovery | sd_notify signals readiness, watchdog resets |
| MTTR target | ≤ 15 s |
| drill_harness | `TestDrill04_FullServiceRestart` |

### F05 — realsense_mux.py crash (depth node)

> Depth-node-specific (runs on `.55`).

| Field | Value |
|-------|-------|
| Injection | `pkill -9 -f realsense_mux.py` on .55 |
| Detection | FIFO broken pipe → systemd restart |
| Expected FDIR | systemd Restart=on-failure (not ladder — separate unit) |
| Expected mode | .55 briefly DEGRADED, recovers to NOMINAL |
| Recovery | Process restarts, pipeline re-opens FIFOs |
| MTTR target | ≤ 30 s |
| drill_harness | `TestDrill02` (on depth node) |

**Executable drill — USB unplug (RealSense only)** (≈ Drill D)

Physical drill — D435 USB cable disconnect. This exercises the same
depth-node recovery path as the software `realsense_mux.py` kill above,
but via a real device-gone event:

- realsense-mux should detect device-gone within ~3 s
- `rs-stream@` encoders backed by FIFO ingest will stall (FIFOs empty)
- FDIR level 4 (USB reset) triggered if level 1–3 don't resolve
- After replug — automatic recovery via udev rules

**Failed drill checklist** (when recovery doesn't happen):

- [ ] Check `journalctl -u janus-camera-page --since "5 min ago"`
- [ ] `cat /var/lib/camera-fdir/ladder_state.json` — is level stuck high?
- [ ] `cat /var/lib/camera-fdir/reboot_count` — circuit breaker tripped?
- [ ] `curl http://localhost:8900/api/v1/admin/audit-log` — any failed actions?

### F06 — Cold boot E2E

| Field | Value |
|-------|-------|
| Injection | Power cycle both nodes |
| Detection | Operator observes boot sequence |
| Expected FDIR | None (normal startup) |
| Expected mode | NOMINAL within 120 s of power-on |
| Recovery | N/A (initial boot) |
| gate | Gate A (all checks) |
| drill_harness | `TestDrill06_ColdBootE2E` |

### F07 — Depth node isolation (.55 unreachable from .10)

> Depth-node-specific (runs on `.55` / the `.10`↔`.55` link).

| Field | Value |
|-------|-------|
| Injection | `iptables -I INPUT -s 192.168.1.55 -j DROP` on .10 |
| Detection | Depth proxy timeouts, `/healthz` reports depth_camera: unreachable |
| Expected FDIR | .10 system enters DEGRADED |
| Expected mode | DEGRADED (color survives, depth routes → 502) |
| Recovery | Remove iptables rule → depth proxy recovers |
| MTTR target | ≤ 30 s after link restore |
| drill_harness | `TestDrill07_DepthNodeIsolation` |

### F08 — WAN uplink flap

| Field | Value |
|-------|-------|
| Injection | `ip link set wlan0 down` on .10 for 30/60/120 s |
| Detection | Cloudflare tunnel down, TURN unreachable |
| Expected FDIR | System transitions to LOCAL_ONLY |
| Expected mode | LOCAL_ONLY while uplink is down |
| Recovery | `ip link set wlan0 up` → tunnel reconnects, promote to NOMINAL |
| MTTR target | ≤ 60 s after uplink returns |
| drill_harness | `TestDrill08_UplinkFlap` |

### F09 — Dual fault (Janus + pipeline simultaneously)

| Field | Value |
|-------|-------|
| Injection | `kill -9 $(pidof janus) && pkill -9 ffmpeg` |
| Detection | Watchdog: both stale video and Janus unreachable |
| Expected FDIR | Level 0 retry (fail) → Level 1 restart_pipeline → Level 2 restart_janus |
| Expected mode | NOMINAL → DEGRADED |
| Recovery | Both services restart via ladder |
| MTTR target | ≤ 90 s |
| drill_harness | `TestDrill09_DualFault` |

### F10 — Depth proxy failover

> Depth-node-specific (runs on `.55`).

| Field | Value |
|-------|-------|
| Injection | Stop FastAPI on .55 (`systemctl stop janus-camera-page` on depth) |
| Detection | Proxy connect error → HTTP 502 |
| Expected FDIR | No .10 FDIR escalation (proxy returns clean error) |
| Expected mode | DEGRADED on .10 (depth subsystem down) |
| Recovery | Restart service on .55 → proxy resumes |
| MTTR target | ≤ 15 s after .55 service restarts |
| drill_harness | `TestDrill10_DepthProxyFailover` |

### F11 — Reboot circuit breaker

| Field | Value |
|-------|-------|
| Injection | Artificially set reboot count ≥ `MAX_FDIR_REBOOTS` |
| Detection | Ladder reaches level 4, reads reboot count |
| Expected FDIR | Circuit breaker trips → SAFE mode (no reboot) |
| Expected mode | SAFE |
| Recovery | Manual reset required (`/fdir/ladder/reset`) |
| test | `test_fdir_integration.py::TestCircuitBreaker` |

---

## 4. 8-hour Passive Soak

**Goal**: prove the stream survives 8 h continuous play without mode
degradation, memory leak, or recovery escalation.

**Setup**:

```bash
# On observer host (laptop with Chrome):
open http://<color_node>:8900/operator_dashboard.html
# Open player in second tab — viewer URL for the color stream
open http://<color_node>:8900/color_view.html
```

**During soak — passive observation**:

Dashboard auto-refreshes every 5 s. Watch:

- **Live metrics widgets** — `video age`, `output FPS`, `client jitter`, `client RTT` should stay flat
- **Services panel** — janus, encoder, L4 must remain `active`
- **Streams panel** — `desired` ↔ `runtime` must match for full 8 h (no drift)
- **Mountpoints** — `age_ms` < 100 for the active stream throughout
- **Audit log** — must be empty except for periodic restart attempts (NONE expected if healthy)

**Capture metrics**:

```bash
# Snapshot every 10 min into a CSV
while true; do
  curl -sf http://<color_node>:8900/metrics | \
    grep -E "camstack_(video_age_ms|client_jitter_ms|client_rtt_ms|fdir_level|recovery_attempts_total)" | \
    awk -v ts=$(date +%s) '{print ts","$0}'
  sleep 600
done > soak_metrics_$(date +%Y%m%d_%H%M).csv
```

**Pass criteria**:

- `camstack_fdir_level` stays at 0 for the entire run
- `camstack_recovery_attempts_total` stays at startup value (no escalations)
- `client_jitter_ms` p99 < 50 ms
- `client_rtt_ms` p99 < 200 ms (depends on network)
- L4 process RSS growth < 20 % over 8 h (memory leak check)
- Janus process RSS growth < 30 %
- No 5xx in L4 access log

**Fail signals → investigate**:

- `fdir_level > 0` at any point → ladder escalated, check audit log
- `video_age_ms > 10000` for > 30 s → stream stalled, FDIR should have caught
- Client browser disconnect → WebRTC failure, check Chrome's `chrome://webrtc-internals`
- L4 OOM → mode_enforcer might have leak, profile with `tracemalloc`

---

## 4b. Gateway / remote-producer safety drills (G5)

These cover the new FDIR invariant: **a stale/fake/hostile remote producer binding can never
restart Janus, reset USB, or reboot the gateway.** (Design: `docs/design/UNIFIED_FDIR_OVER_STREAM_BINDINGS.md`.)
Note: `.55` in the legacy F05/F07/F10 scenarios is the old depth-proxy model; a *remote producer
binding* is a distinct, `Domain.PRODUCER` concern handled by the isolated `remote_stream_monitor`.

| ID | Inject | Expect |
|---|---|---|
| F12 — remote stall can't reboot | register a node + create a remote binding, then let its mountpoint go silent (no producer sends RTP) | `Domain.PRODUCER` WARN events, binding status → `degraded`; **local ladder stays level 0**, no `restart_janus` / `reboot`. Assert via `camstack_fdir_events_total{domain="producer"}` rising while `camstack_recovery_ladder_level == 0`. |
| F13 — shared-Janus reboot guard | stall the Janus admin API while the local color snapshot keeps updating | `outcome="suppressed_local_alive"` in `fdir.jsonl`; **no** climb toward `restart_janus`/reboot. |

Both are unit-covered (`tests/test_remote_stream_monitor.py`, `tests/test_watchdog_reboot_guard.py`);
the table is the manual/integration form.

---

## 5. Recovery from Drill

If a drill leaves the stack in a degraded state:

```bash
# Reset FDIR ladder manually (correct path is /fdir/ladder/reset)
curl -X POST -H "X-Admin-Token: $TOKEN" http://localhost:8900/fdir/ladder/reset

# Restart all stream services
sudo systemctl restart janus.service
sudo systemctl restart rs-stream@color.service
sudo systemctl restart janus-camera-page.service

# Verify clean state
curl -sf http://localhost:8900/healthz | jq
```

If the stack does NOT recover after manual reset — escalate to operator
review, likely a real bug in recovery logic or external dependency
(network, hardware).

---

## 6. Fault Injection Safety Rules

1. **Never inject faults in production without a maintenance window.**
2. **Always have SSH access to both nodes before starting drills.**
3. **Record the exact injection command and timestamp.**
4. **Set a timer for max duration of any iptables/link-down injection.**
5. **Verify /healthz returns 200 before AND after every drill.**
6. **If a drill leaves the system in SAFE mode, manually reset via
   `POST /fdir/ladder/reset`.** (There is no `/fdir/mode/nominal` endpoint — the
   system mode auto-promotes to NOMINAL after a sustained healthy-frame streak.)

---

## 7. Automation Status

| Scenario | drill_harness.py | Unit test | Status |
|----------|-----------------|-----------|--------|
| F01 Janus death | `TestDrill01` | `test_watchdogs.py` | ✅ |
| F02 Pipeline crash | `TestDrill02` | `test_watchdogs.py` | ✅ |
| F03 TURN block | `TestDrill03` | — | ✅ |
| F04 Service restart | `TestDrill04` | — | ✅ |
| F05 realsense_mux crash | `TestDrill02` (depth) | — | ✅ |
| F06 Cold boot | `TestDrill06` | — | ✅ (new) |
| F07 Depth isolation | `TestDrill07` | `test_layer_isolation.py` | ✅ (new) |
| F08 Uplink flap | `TestDrill08` | — | ✅ (new) |
| F09 Dual fault | `TestDrill09` | — | ✅ (new) |
| F10 Proxy failover | `TestDrill10` | `test_depth_proxy_routes.py` | ✅ (new) |
| F11 Circuit breaker | — | `test_fdir_integration.py` | ✅ (new) |

---

## 8. Related Documents

| Document | Purpose |
|----------|---------|
| [`TESTING.md`](TESTING.md) | Test strategy + full L1–L7 + cross-layer case inventory |
| `RELEASE_GATE.md` | Gate A–D pass/fail criteria |
| [`SLO.md`](SLO.md) | Service level objectives (MTTR, availability targets) |

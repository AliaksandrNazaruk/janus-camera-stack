# Camera Stack — Testing

> Version: 1.0 | Date: 2026-03-06
> Scope: L1 (Sensors) → L7 (Client) + cross-layer drills X1–X6

This document is the single testing reference for the camera stack. It
combines the **test strategy** (coverage model, layer responsibilities,
conflict-prevention policy, naming, automation policy, evidence rules)
with the **test matrix** (the concrete L1–L7 + cross-layer case
inventory, mapping of existing tests, and coverage summary).

Status legend (used in the case inventory): ✅ Automated | 🔧 Semi-auto | 📋 Manual | ❌ Not implemented

---

## 1. Coverage Model

Tests are organized in five escalating tiers. Each tier builds on the
one below and requires different infrastructure.

| Tier | Scope | Infra needed | Runner | Cadence |
|------|-------|-------------|--------|---------|
| **Unit** | Single function / class in isolation | None (mocks) | `pytest -m "not drill and not soak"` | Every commit |
| **Integration** | Module interactions (FastAPI routing, proxy chain, FDIR state machine) | None (ASGI test client) | `pytest -m integration` | Every commit |
| **Contract** | Data-format invariants against `DEPTH_SEMANTIC_CONTRACT.md` | None (synthetic numpy) | `pytest -m contract` | Every commit |
| **Drill** | Live fault-injection on real Pi nodes via SSH | Two Pi nodes on LAN | `pytest tests/drill_harness.py --node=<ip>` | Pre-release + weekly |
| **Soak** | 8 h / 24 h continuous operation with metric collection | Live deployment | `python tests/soak_runner.py --hours=8` | Nightly (8 h) / release (24 h) |

### Test markers (pytest.ini)

```
unit, integration, slow, hardware, contract, security, drill, soak, cross_layer
```

---

## 2. Layer Responsibilities and Case Inventory

Each layer owns a specific failure domain. Tests for that layer must
validate **only** its own contracts; cross-layer coupling is tested
separately in the X1–X6 drills (see §3).

The table below states each layer's responsibility once. The
subsections that follow give the concrete test-case inventory for that
layer.

| Layer | Owner | Tests must verify | Tests must NOT touch |
|-------|-------|-------------------|----------------------|
| **L1 Sensors** | Hardware (USB, power, thermal) | Camera detect, serial match, USB topology, no undervoltage | Encoding params, Janus mounts, API routes |
| **L2 Capture** | `realsense_mux.py`, V4L2 | Frame shape/dtype/rotation, timestamp monotonicity, FIFO recovery | ffmpeg args, RTP ports, network |
| **L3 Encoding** | ffmpeg / systemd units | RTP packet flow, encoder args match profile, snapshot isolation | Janus internals, API behavior, client JS |
| **L4 Media Broker** | Janus Streaming Plugin | Mount IDs exist, attach/watch/start cycle, session cleanup, admin API | Camera capture, API auth, CORS |
| **L5 Control/API** | FastAPI (.10 + .55) | Route contracts (2xx/4xx/5xx), admin auth, CSP, CORS, proxy correctness | Janus protocol details, USB reset, ffmpeg |
| **L6 Network** | Cloudflare Tunnel + TURN + LAN | External reachability, TURN relay candidates, uplink recovery | Camera frames, Janus mount config |
| **L7 Client** | Browser player + iframe | TTFF, ICE timing, getStats() metrics, reconnect, hidden-tab resume | Server-side FDIR, systemd services |

### L1. Sensors

| ID | Type | Description | Automation | Status | File / Tool |
|----|------|-------------|-----------|--------|-------------|
| L1-SMOKE-01 | smoke | Camera detect after cold boot (model + serial) | 🔧 | ✅ | `scripts/audit_camera_stack.sh` §1 |
| L1-STRESS-02 | stress | 2 h streaming: temp, USB errors, restart count | 🔧 | ❌ | `soak_runner.py` (planned) |
| L1-FAULT-03 | fault | USB disconnect → camera re-enumeration → pipeline recovery | 🔧 | ❌ | `drill_harness.py` (planned) |
| L1-POWER-04 | fault | Voltage sag under full load → no kernel faults | 📋 | ❌ | Manual + `dmesg` |

### L2. Capture

| ID | Type | Description | Automation | Status | File / Tool |
|----|------|-------------|-----------|--------|-------------|
| L2-SMOKE-01 | smoke | 100 consecutive frames: no gaps, no stale timestamps | ✅ | ✅ | `test_depth_contract.py::TestTimestampMonotonicity` |
| L2-CONTRACT-02 | contract | shape, dtype, rotation, timestamp freshness per contract | ✅ | ✅ | `test_depth_contract.py::TestDepthFrameContract` / `TestColorFrameContract` |
| L2-FAULT-03 | fault | Kill FIFO consumer → reopen logic → escalation if limit | ✅ | ❌ | (planned: mock FIFO test) |
| L2-RECOVERY-04 | fault | pyrealsense2 pipeline crash → process restart | 🔧 | ❌ | `drill_harness.py::TestDrill02` (partial) |

### L3. Encoding / Pipelines

| ID | Type | Description | Automation | Status | File / Tool |
|----|------|-------------|-----------|--------|-------------|
| L3-SMOKE-01 | smoke | Encoder processes alive + RTP on localhost | 🔧 | ✅ | `scripts/audit_camera_stack.sh` |
| L3-CONTRACT-02 | contract | Runtime ffmpeg args match reference profile | ✅ | ❌ | (planned) |
| L3-LOAD-03 | stress | All streams + depth poll + snapshot simultaneously | 🔧 | ❌ | `soak_runner.py` (planned) |
| L3-FAULT-04 | fault | Restart one pipeline → neighbors survive | ✅ | ✅ | `test_layer_isolation.py::TestPipelineIsolation` |

### L4. Media Broker (Janus)

| ID | Type | Description | Automation | Status | File / Tool |
|----|------|-------------|-----------|--------|-------------|
| L4-SMOKE-01 | smoke | For each mount: session → attach → watch → start | ✅ | ✅ | `test_janus_service.py::TestCreateSession` / `TestAttachStreaming` |
| L4-OBS-02 | obs | Admin snapshot: ICE/DTLS/media state correct | 🔧 | ❌ | (planned) |
| L4-FAULT-03 | fault | `systemctl restart janus` during active watch | 🔧 | ✅ | `drill_harness.py::TestDrill01_JanusRestart` |
| L4-LEAK-04 | stress | 100 attach/detach cycles → no session/handle accumulation | ✅ | ❌ | (planned: soak_runner session count) |

### L5. Control / API Layer

| ID | Type | Description | Automation | Status | File / Tool |
|----|------|-------------|-----------|--------|-------------|
| L5-API-01 | contract | All GET/POST routes → 2xx/4xx/5xx match spec | ✅ | ✅ | `test_system_routes.py` / `test_camera_routes.py` / `test_janus_routes.py` |
| L5-SEC-02 | security | Admin routes without token → 401/403 | ✅ | ✅ | `test_security.py::TestAdminAuth` |
| L5-IFRAME-03 | security | Embedding on allowed origin OK, disallowed blocked by CSP | ✅ | ✅ | `test_security.py::TestCSPFrameAncestors` |
| L5-PROXY-04 | fault | .55 offline → depth proxy returns 502, .10 stays healthy | ✅ | ✅ | `test_depth_proxy_routes.py::TestUpstreamFailure` |
| L5-CORS-05 | security | CORS allows exact origins, rejects wildcards | ✅ | ✅ | `test_security.py::TestCORS` |

### L6. Network Access

| ID | Type | Description | Automation | Status | File / Tool |
|----|------|-------------|-----------|--------|-------------|
| L6-SMOKE-01 | smoke | Player page reachable via public hostname | 🔧 | ✅ | `browser_canary.py --http-only` |
| L6-TURN-02 | integration | Hostile NAT → client uses relay candidates | 🔧 | ❌ | (planned: P2.8 trickle ICE script) |
| L6-FAULT-03 | fault | Uplink loss 30/60/120 s → controlled degradation → recovery | 🔧 | ✅ | `drill_harness.py::TestDrill08_UplinkFlap` |
| L6-LINK-04 | fault | Controlled loss/jitter .10 ↔ .55 → observable degradation | 🔧 | ❌ | (planned: tc netem drill) |

### L7. Client

| ID | Type | Description | Automation | Status | File / Tool |
|----|------|-------------|-----------|--------|-------------|
| L7-SMOKE-01 | smoke | Player page loads, TTFF within SLO | ✅ | ✅ | `browser_canary.py` / `test_canary_contract.py` |
| L7-METRICS-02 | obs | getStats() after 30 s: inbound-rtp, bytes/frames growing | ✅ | ✅ | `browser_canary.py` metrics extraction |
| L7-RESUME-03 | fault | Hidden tab 1/5/15 min → recovery on return | 🔧 | ❌ | (planned: Playwright hidden-tab drill) |
| L7-EXHAUST-04 | stress | 20 reconnect cycles → no handler/session leaks | ✅ | ❌ | (planned: canary loop script) |

---

## 3. Cross-Layer Tests

| ID | Layers | Description | Automation | Status | File / Tool |
|----|--------|-------------|-----------|--------|-------------|
| X1 | L1–L7 | Cold boot E2E: power → player page → first frame → depth API | 🔧 | ✅ | `drill_harness.py::TestDrill06_ColdBootE2E` |
| X2 | L5–L7 | Hostile NAT E2E: iframe host → CF control-plane → TURN media | 🔧 | ❌ | (planned) |
| X3 | L4–L7 | Janus restart during watch → MTTR within target | 🔧 | ✅ | `drill_harness.py::TestDrill01_JanusRestart` |
| X4 | L1–L5 | Depth node isolation: .55 down → .10 color survives, depth 502 | 🔧 | ✅ | `drill_harness.py::TestDrill07_DepthNodeIsolation` |
| X5 | L5–L6 | Uplink flap: WAN down → local mode → WAN up → auto-recovery | 🔧 | ✅ | `drill_harness.py::TestDrill08_UplinkFlap` |
| X6 | All | Soak 24 h: no restart storm, leak, silent freeze | 🔧 | ❌ | `soak_runner.py` (planned) |

---

## 4. Conflict Prevention Rules

1. **Single owner per failure mode.** If Janus dies, L4 tests verify
   mount recovery. L5 tests verify `/healthz` reports it. L3 tests
   verify ffmpeg is NOT killed by the Janus restart.

2. **No transitive assertions.** L5 proxy tests assert HTTP status
   codes, not depth frame content. L2 contract tests assert frame
   dtype, not HTTP response codes.

3. **Mocks at layer boundary.** Each unit / integration test mocks the
   layer below. For example L5 proxy tests mock
   `depth_camera_proxy.forward_request` — they never call the real .55
   node.

4. **Shared state isolation.** Tests that modify `system_mode._state`
   or `fdir_events._ring` must reset them in teardown. FDIR
   integration tests get their own `RecoveryLadder()` instance.

5. **No CI dependency on hardware.** All CI-runnable tests (`unit`,
   `integration`, `contract`, `security`) use only mocks and synthetic
   data. The `drill` and `soak` markers are excluded from CI.

---

## 5. Test Naming Convention

```
test_{layer}_{type}_{sequence}.py
```

| Pattern | Example | Purpose |
|---------|---------|---------|
| `test_depth_contract.py` | L2 contract | Depth semantic invariants |
| `test_security.py` | L5 security | Auth, CSP, CORS boundary |
| `test_depth_proxy_routes.py` | L5 integration | Proxy route correctness |
| `test_fdir_integration.py` | L4/L5 integration | FDIR ladder + mode transitions |
| `test_layer_isolation.py` | Cross-layer | Verify no cascading failures |
| `test_canary_contract.py` | L7 contract | Browser canary output schema |
| `drill_harness.py` | L1–L7 drill | Live fault-injection on Pi nodes |
| `soak_runner.py` | X6 soak | Long-running metric collection |

---

## 6. What to Automate vs Manual

### Automate (CI or nightly)

- API contract tests (all L5 routes)
- Watchdog / FDIR state-machine tests
- Depth semantic contract (L2)
- Player canary schema validation (L7)
- Proxy route coverage (L5)
- Security boundary (CSP, CORS, admin auth)
- Soak smoke with periodic health snapshots (X6)

### Semi-automatic (operator + script)

- Hostile NAT / TURN relay verification (L6)
- Wi-Fi degradation / packet loss injection (L6)
- Brownout / USB fault (L1)
- Thermal stress (L1)
- Full drill harness with SSH (X1–X5)

### Manual

- UX iframe embedding on third-party site (L7)
- Visual depth overlay alignment check (L2)
- Operator assessment after rare hardware faults (L1)

---

## 7. Mapping: Existing Tests → Matrix IDs

| Existing test file | Covers IDs |
|----|------------|
| `test_camera.py` | L5-API-01 (partial) |
| `test_janus_service.py` | L4-SMOKE-01 |
| `test_janus_routes.py` | L5-API-01 (Janus routes) |
| `test_system_routes.py` | L5-API-01 (system routes) |
| `test_camera_routes.py` | L5-API-01 (camera routes) |
| `test_proxies.py` | L5-PROXY-04 (partial) |
| `test_watchdogs.py` | L4-FAULT-03 (unit-level) |
| `test_v4l2_service.py` | L2-CONTRACT-02 (partial — V4L2 modes only) |
| `test_system_service.py` | L5-API-01 (systemd wrappers) |
| `test_env_store.py` | L5-API-01 (env file CRUD) |
| `drill_harness.py` | X3, L4-FAULT-03, L3-FAULT-04 (via drill 02) |
| `scripts/audit_camera_stack.sh` | L1-SMOKE-01, L3-SMOKE-01 |
| `scripts/browser_canary.py` | L6-SMOKE-01, L7-SMOKE-01, L7-METRICS-02 |

---

## 8. Coverage Summary

| Layer | Total cases | Automated | Semi-auto | Manual | Gap |
|-------|------------|-----------|-----------|--------|-----|
| L1 | 4 | 0 | 1 | 1 | 2 |
| L2 | 4 | 2 | 0 | 0 | 2 |
| L3 | 4 | 1 | 1 | 0 | 2 |
| L4 | 4 | 1 | 1 | 0 | 2 |
| L5 | 5 | 5 | 0 | 0 | 0 |
| L6 | 4 | 0 | 2 | 0 | 2 |
| L7 | 4 | 2 | 0 | 0 | 2 |
| X1–X6 | 6 | 0 | 4 | 0 | 2 |
| **Total** | **35** | **11** | **9** | **1** | **14** |

---

## 9. SLO Reference

Test pass/fail thresholds are derived from the service level objectives.
The authoritative source is [`SLO.md`](SLO.md); the key targets are
summarized here for convenience.

| Metric | Target | Alert |
|--------|--------|-------|
| ICE connect (p95) | ≤ 5 s | > 8 s / 5 min |
| TTFF (p95) | ≤ 8 s | > 12 s / 5 min |
| MTTR (p95) | ≤ 60 s | > 120 s / 10 min |
| Stream availability | ≥ 99.0 % | < 97 % / 1 h |
| Packet loss | ≤ 1 % | > 3 % / 5 min |

---

## 10. Evidence Requirements

Every test run (automated or manual) must capture the artifacts listed
in `RUNBOOK_EVIDENCE.md`. A test without evidence is not a test.

---

## 11. Related Documents

| Document | Purpose |
|----------|---------|
| `RELEASE_GATE.md` | Gate A–D pass/fail criteria |
| [`RESILIENCE_TESTING.md`](RESILIENCE_TESTING.md) | Fault scenarios, drills & soak + expected FDIR behavior |
| `RUNBOOK_EVIDENCE.md` | Artifact collection protocol |
| [`SLO.md`](SLO.md) | Service level objectives |
| [`DEPTH_SEMANTIC_CONTRACT.md`](DEPTH_SEMANTIC_CONTRACT.md) | Breaking change barrier |
| [`BACKLOG.md`](BACKLOG.md) | Live backlog + known gaps |

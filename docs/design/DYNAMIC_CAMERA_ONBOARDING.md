# Dynamic Camera Onboarding — add-by-IP, host-agnostic (SSH-push + node-agent)

Status: **DESIGN v2 + CORE IMPLEMENTED & LIVE-PROVEN (see §0)** — post 4-lens adversarial review · 2026-06-19
Builds on [STREAM_BINDING_MODEL](STREAM_BINDING_MODEL.md) ·
[GATEWAY_REMOTE_RTP_MODE](GATEWAY_REMOTE_RTP_MODE.md) ·
[UNIFIED_FDIR_OVER_STREAM_BINDINGS](UNIFIED_FDIR_OVER_STREAM_BINDINGS.md)

### Changelog v1 → v2 (what the review corrected)
- **Dropped the false "unification" premise.** v1 claimed local `cam10` is "the degenerate case" of one uniform pipeline. The code **rejects** local at every write boundary; local is a read-only projection and a **deliberate FDIR safety boundary**. §2 rewritten.
- **Per-node RTP firewall is greenfield + a P3 prerequisite, not "reuse"/P4.** The live `.55/32`+`/24` RTP rules are **drift (not in IaC)**; `-s <ip>/32` is spoofable. §6/§9/§10.
- **Added load-bearing sections that v1 lacked:** identity model (§4), persisted lifecycle state + reconciler (§5), observability (§8), bundle versioning/skew (§7), full teardown (§11), testability (§12), Day-2 (§13).
- **Flagged a real bug:** admin/provisioning audit is a silent no-op today (`stream_bindings.py:24-28` imports `audit`; `audit_log.py` exports `emit`). Fix is a **P1 prerequisite**.
- Corrected component paths to `janus_camera_page/app/services/…`.

## 0. IMPLEMENTATION STATUS — built + live-proven (2026-06-19, branch `feat/dynamic-camera-onboarding`)

The uniform onboarding pipeline is implemented and proven end-to-end on a real node (`.55`,
RealSense D435): **add by IP → provision (deploy pipe) → activate(color) → `cam55:color` ONLINE**
(RTP `192.168.1.55 → 192.168.1.10:5102`, 0 drops, Janus mp 2001). ~14 commits, 82 tests.

**Built:**
- **Identity (§4):** `add_node_by_host(ip)` mints an opaque `node-<uuid>` (no typed label),
  lookup-or-create per host; `serial` attached after probe; `provision_state` persisted write-ahead.
  `POST /api/v1/admin/nodes` (add-by-IP). `cam10` stays the local sentinel.
- **Uniform two-phase flow (operator feedback — no stream is special):** `provision(node)` deploys
  the *pipe* (mux); `activate_streams(node, sensors[])` activates a chosen subset, uniformly per
  `(node,sensor)`: gateway bind (allocate + ensure-janus) → node `bootstrap activate --sensor`.
  Endpoints `POST /nodes/{id}/provision` + `POST /nodes/{id}/streams`.
- **Node bundle (§7):** `host_infra/node-bundle/` — sensor-agnostic `bootstrap.sh`
  (probe/deploy/activate/deactivate), self-contained probe, versioned+checksummed `build-bundle.sh`,
  default-deny by construction (no Janus/coturn/secret), structural test.
- **SSH transport (§5):** pluggable subprocess ssh/scp + `FakeTransport` for CI; key_path +
  accept-new; sudo via `-S` stdin. **Audit fix:** the silently-dead admin audit trail restored.

**Key implementation learnings (the live run surfaced these — all fixed in the bundle):**
- Node portability: shipped `realsense-mux.service` hardcoded the gateway venv python; `RS_ENABLE_COLOR`
  wasn't shipped; runtime-deps assumed apt.
- **`fs.pipe-max-size`:** the node needs the same 8 MB sysctl the gateway has (`sysctl-realsense-mux.conf`);
  the 1 MB default caps `F_SETPIPE_SZ` (EPERM) below a 900 KB RGB frame → mux drops 100% → encoder
  starves. The bundle now ships+applies it before the mux opens FIFOs. **This — not anything
  color-specific — is why color "looked special."**

**Still open (review P3/P4 — NOT built):** per-node fail-closed RTP firewall (S1/S2) + SSH host-key
pinning (S3) before non-bench LAN exposure · node-agent on :8901 for steady-state FDIR recovery (P2;
`RemoteNodeClientStub` is inert today) · serial-keyed `binding_id` for multi-camera hosts (I4) ·
declarative desired-state (O7) + per-node firewall reconciler (L2/O5).

The rest of this document is the design (v2); §0 records what is actually built.

## 1. Problem

Operator **adds a camera by IP**; the gateway SSH-pushes an offline bundle to that host, **probes for a
RealSense**, and **only if a camera is found** deploys the full producer stack (mux + encoder + node-agent)
and binds it into Janus. We do not know the host in advance.

## 2. Principle (corrected) — remote pipeline; local is separate by design

A **node** is an identity with a `mode ∈ {local, remote}`.
- **Remote nodes** are host-reachable producers onboarded by this pipeline (§5).
- The single **local node `cam10`** is a **read-only projection** of the serial-keyed `mountpoint_allocator`
  and is provisioned by the camera lifecycle (`sensor_lifecycle` + local watchdog), **NOT** this pipeline.
  This is enforced — `create/ensure-janus/remove` 400 on `cam10`; `upsert_node` rejects it — and it is a
  **load-bearing safety boundary**: `node_client.get_node_client` routes local→`LocalNodeClientAdapter`
  (may act locally) and remote→inert `RemoteNodeClientStub` (cannot reboot the gateway). Do **not** collapse
  them. True local/remote unification (folding the serial allocator under the binding store) is a *separate
  future sprint*, already deferred in STREAM_BINDING_MODEL; it is **not** assumed here.

## 3. Onboarding state machine (per node, persisted)

```
added → reachable → probe_deployed → probing ─┬─ no_camera (teardown → terminal)
                                              └─ camera_found
   → stack_deployed → bound → waiting_for_rtp → online            (terminal: online)
                                                      ↘ degraded   (FDIR; auto-recover)
   any non-terminal state exceeding its deadline → *_failed (terminal-until-operator)
```
Every state is **persisted before** the side-effect that leaves it (write-ahead), so a gateway restart
**resumes/reconciles** (§5). Each remote step has a bounded timeout + retry/backoff; a watchdog flips
stuck nodes to `*_failed` with the failing step + stderr tail recorded (§8).

## 4. Identity model (NEW — load-bearing)

IP is a **mutable locator, never the key** (DHCP churns it). Three orthogonal identities:

| Concept | Key | Mutable | Role |
|---|---|---|---|
| **Node** | `node_id` = gateway-minted `node-<uuid4>` | no | durable PK |
| **Host** | `host` (IPv4) | **yes** | where to reach agent + firewall `-s <host>/32`; re-resolved by serial |
| **Device** | camera `serial` (from probe) | no | "same camera"; reconciliation anchor; survives replug/host-move |
| **Display name** | `display_name` ("cam55") | yes | operator UX only |

- `binding_id = f"{node_id}:{serial}:{sensor}"` — stable across IP change **and** rename, and **unique per
  device** so N RealSense on one host coexist (v1's `node:sensor` collided on two `color` cams).
- **"Add by IP" = lookup-or-create:** probe host → read serial via agent → if (serial|host) already mapped,
  **resume** that node; else mint a new `node_id`, store `host`+`serial`, operator sets `display_name`.
- On reachability loss, re-resolve `host` by serial (agent-announce / subnet sweep) and **auto-update** it;
  re-derive the `/32` firewall rule + contract-env target from the **current** `host`. Recommend DHCP
  reservations for camera hosts as a deployment requirement.

## 5. Provisioner = reconcile loop, not one-shot

Desired-state (declarative, §14) → reconcile → converge. **Persisted lifecycle state** lives in a new
`provision_state`/`provision_step`/`updated_at` on the node row (today `NodeEntry` carries only
host/role/reachability/ordinal — insufficient). On startup and on a periodic tick the reconciler:
1. resumes any node in a non-terminal state from its recorded step;
2. re-creates **absent** Janus mountpoints for remote bindings — mountpoints are `permanent:False`
   (Janus RAM only); a Janus restart drops them and **nothing today recreates them** → remote cameras go
   dark permanently. **Decision: a remote-binding boot/periodic reconciler that re-`ensure_janus`es any
   binding whose mountpoint is absent** (declarative; preferred over `permanent:True`).
3. detects drift (unit dead, bundle hash mismatch, firewall rule missing) and converges.
A **per-node provisioning lock** (CAS `state="provisioning"`) spans the whole multi-step flow — the file
flock only guards single calls, so two operators adding the same IP today would race (bundle write +
double ordinal). Re-add **reuses the existing binding's mp/port** (allocation is one-time per
`node:serial:sensor`; never re-rolled) and reconciles a Janus `CONFLICT` by destroy+recreate, not error.

## 6. Components — reuse vs build (corrected)

### Reuse (verified at `janus_camera_page/app/services/…`)
| Capability | Location |
|---|---|
| RealSense discovery (standalone, non-exclusive, serial+sensors+profiles) | `realsense_probe.py:67-136` `probe()` |
| (node,serial,sensor)→mountpoint model + allocators | `stream_binding_store.py` |
| Janus mountpoint provisioning on chosen iface/port (idempotent CREATED/EXISTS/CONFLICT) | `binding_provision.py` `ensure_janus()` |
| Contract-env writer (emits `PORT`,`RTP_TARGET_HOST`) | `sensor_lifecycle.py:119-138` |
| Binding-aware FDIR (remote fault can't reboot gateway) | `remote_stream_monitor.py` (live) |
| Gateway client expecting an agent (`:8901`, `GET /healthz`, `RestartResult`) | `node_client.py:29,39-42,97-111` |

### Build (gaps — none of these exist today)
1. Offline **bundle** + role-gated `bootstrap.sh` + **signed** manifest (§7).
2. **Node-agent** with auth (§9).
3. **Provisioner** reconcile loop + SSH transport (§5, §12).
4. **Per-node firewall** automation + reconciler — **greenfield**; the live `.55/32`+`5002:5120/24` rules
   are drift, NOT in `host_infra/roles/network`. Must be codified, fail-closed, and is a **P3 prerequisite**.
5. **Persisted provision lifecycle state** + `remove_node` teardown (§11).
6. **Cross-repo edit:** `host_infra/roles/encoder/files/rs-stream.sh` deployed copy is stale (PORT-only);
   the `RTP_TARGET_HOST` support exists in repo but must be shipped (external dependency, not "reuse").

## 7. Bundle — versioned, signed, reconciled (not just an integrity-checked tarball)

`bootstrap.sh` runs **only** an explicit NODE allowlist of install steps and is **default-deny**: it must be
*unable* to reach `generate_secrets`/`install_janus`/`install_coturn`/`install_relay` (today `install.sh` is
role-blind: `--role` does not exist; it unconditionally mints `JANUS_ADMIN_SECRET`/`STREAMING_ADMIN_KEY`).
A **CI test asserts the node bundle produces no Janus/TURN secret on disk** — until that test exists the
separation is unproven.

- **Version:** `BUNDLE_VERSION` = semver + git SHA + build timestamp, baked into the tarball and the agent.
  Node-agent `/healthz` advertises `bundle_version` + per-unit content hashes. Gateway exposes
  `camstack_node_bundle_skew{node_id}` and **alerts on drift** (this is the exact failure that already bit
  the repo twice: stale L4 build; stale PORT-only `rs-stream.sh`).
- **Rollout:** desired bundle version **pinned per node** (config-as-code §14), canary→fleet staging,
  one-command rollback to the previous pin.
- **Integrity ≠ authenticity:** `SHA256SUMS` is not enough. The bundle is **signed** (detached signature over
  the manifest) with a gateway provisioning key whose public half is **pinned on the node at enrollment**;
  the node verifies before executing `bootstrap.sh`. **Prefer pushing the bundle over the authenticated SSH
  channel** rather than a separate HTTP pull (eliminates the TOFU pull entirely).
- **Reproducible build:** scripted, emits a manifest (input SHAs, wheel versions, source SHA). Offline-
  feasible via the vendored `installer/wheels/*aarch64.whl` (node arch == gateway).
- Contents: `realsense_probe`-based probe, `realsense-mux.py`+unit, `rs-stream.sh`+`rs-stream@.service`,
  `node-agent/`, `wheels/`, `deb/` (offline ffmpeg/libusb), env templates, `bootstrap.sh`, `MANIFEST` + sig.

## 8. Observability (P1 deliverable, not an afterthought)

- **Fix the dead audit wiring first** (`audit`→`emit`, `stream_bindings.py:24-28`) — a latent bug: today
  node register / bind / ensure-janus write **no** audit record.
- **Every §3 transition emits an event** (`Domain.PROVISIONING`) tagged `node_id`, `from_state`/`to_state`,
  `duration_ms`, and on failure the **remote command + exit code + stderr tail**. The push (root-RCE-grade)
  must be the **most** audited action: target IP, accepted host-key fingerprint, bundle hash/sig, SSH user.
- **Metrics:** `camstack_provision_state{node_id,state}`, `camstack_provision_transitions_total{from,to,outcome}`,
  `camstack_provision_step_duration_seconds{step}`, `camstack_node_bundle_skew`.
- **Stuck detection:** per-state deadline → `*_failed`; SLO alert `CamstackProvisionStuck` (mirrors
  `CamstackRemoteProducerDegraded`). `GET /nodes/{id}/provision-log` surfaces captured SSH output centrally.

## 9. Node-agent (`:8901`) — authenticated, gateway-only

| Endpoint | Purpose | Backed by |
|---|---|---|
| `GET /healthz` | reachability + `bundle_version` + reported `serial`(s) | trivial |
| `GET /probe_devices` | discovery → devices[{serial,sensors,profiles}] | reuse `realsense_probe.probe()` |
| `POST /restart_stream?sensor=` | FDIR recovery → `{ok,detail}` (makes `RemoteNodeClientStub` real) | node `encoder-admin restart` |

- **Auth = mTLS (gateway client cert) or per-node token over TLS with nonce/timestamp anti-replay** — never
  plain bearer over HTTP (replayable). **Bind to the node's specific LAN IP, never `0.0.0.0`** (repo default
  is `0.0.0.0` everywhere — must not be copied). Enforce gateway-only reach with a **node-side** source-IP
  firewall **and** the token (defense-in-depth; source-IP alone is spoofable). Rate-limit `/restart_stream`.
- Node holds a node-agent token distinct from the gateway admin token; **never** the Janus admin secret,
  **never** a Cloudflare tunnel.

## 10. Security model

- **SSH host-key pinning (mandatory).** Every repo precedent is blind TOFU (`paramiko.AutoAddPolicy`,
  `ansible.cfg host_key_checking=False`) — forbidden here. Capture the fingerprint out-of-band at
  enrollment (operator confirms a displayed fingerprint / golden image), store on the node record, pin on
  every subsequent connect. First-contact acceptance is an explicit **audited operator decision**.
- **Least-privilege provisioning identity.** `bootstrap.sh` needs root (apt, `/etc/systemd`, `/usr/local/bin`,
  `/etc/sudoers.d`, udev). Do **not** hand the gateway a shared key = root-on-all-nodes. Use a dedicated
  provisioning user + a **scoped `sudoers` allowlist** (the `encoder-admin` NOPASSWD pattern), or per-host
  keys / short-lived CA-signed certs.
- **Per-node RTP firewall (greenfield, fail-closed).** On `bound`, add `-s <host>/32 --dport <rtp> ACCEPT`
  **above** the catch-all DROP **and** the backstop range DROP, **before** `ensure_janus` exposure; roll back
  the mountpoint if the firewall step fails. A **single reconciler keyed off the binding store** is the sole
  writer (tagged iptables comments reconciled to exactly the active set → no leak on churn). Pair with
  `rp_filter=1` + anti-spoof DROP; commit to **SRTP per-binding key** as the only control that authenticates
  frames (until then: documented residual injection risk, bench-only). Generalize to `nft`/`ipset` for fleet
  scale (§14).
- **Target-IP guardrail.** Validate the IP at the API boundary against the camera-LAN CIDR allowlist (reject
  outside `camera_lan_subnet`, the gateway's own IP, network/broadcast); require explicit operator confirm
  (resolved host + pinned fingerprint) before the first push; pass host as an **argv element, never shell-
  interpolated**.
- **Credentials.** Prefer pre-shared gateway key / golden image. If operator-supplied-at-add-time: receive
  write-only, hold in memory for the single enrollment connection, **never persist/log** (extend the existing
  sensitive-key scrub), zeroize after; audit *that* a credential was used, never its value. (Memory: the dev
  sudo password leaked twice — same trap.)
- **Attestation.** Trust today = "answered at an IP" — forgeable. Key identity by **serial** + a per-node
  enrollment secret/cert; re-validate serial on reconnect; alert on (serial↔host) change.

## 11. Teardown — reverse of every resource (v1 reclaimed 2 of 11)

`remove_node(node_id)` orchestrates, in dependency order, each step idempotent + audited, node parked in
`removing` until all confirm:

| # | Resource | Reverse |
|---|---|---|
| 1 | per-node firewall ACCEPT | **close first** (reconciler) |
| 2 | node-side units (`rs-stream@`,`realsense-mux`,`node-agent`) | agent/SSH stop+disable |
| 3 | node-side bundle + wheels + apt deps | uninstall (see byte-clean below) |
| 4 | node-side `contract.env` | remove |
| 5 | Janus mountpoint(s) | `destroy_mountpoint` (idempotent) |
| 6 | StreamBinding row(s) | `remove_binding` |
| 7 | allocated mp/port | released on binding delete |
| 8 | NodeEntry + **ordinal** | **new `remove_node`** (today no such API → ordinal leaks forever, shrinking windows) |
| 9 | persisted provision state | cleared |

**`no_camera` byte-clean** is only honest if the probe is dependency-free. v1's claim is false — probing needs
pyrealsense2+libusb installed. **Decision: ship a static, dependency-free USB VID:PID probe** for the pre-commit
step (truly clean teardown), OR drop "byte-clean" and document the exact residue + remove that named set.

## 12. Testability (a system whose job is SSH-to-hardware must not be untestable)

- **SSH transport behind an interface** with an in-memory fake (records commands, scripted responses) → the
  whole §3 state machine is unit-testable under CI's existing `-m "not hardware"`.
- `bootstrap.sh --dry-run` (plan, no mutation). Probe works against a **fake device fixture**
  (`realsense_probe` already degrades cleanly without hardware).
- A `simulator`-marked integration test driving the full machine against localhost-SSH-to-self + faked probe,
  run nightly.

## 13. Day-2 runbook (must exist before "industrial")

Partial-deploy recovery (resume vs clean rollback) · fleet bundle rollback · **node decommission** (byte-clean
verified) · agent-token rotation + expiry · SSH-key rotation (leaked key = fleet compromise) · each `*_failed`
state → "if X do Y". Extend `OPERATOR_RUNBOOK.md`; add SLO rows.

## 14. Config-as-code + fleet scale

- **Declarative desired-state** for nodes (e.g. `host_infra/inventory/nodes.yml`: node_id, ip|serial, sensors,
  pinned `bundle_version`, rtp_ports) that the Provisioner **reconciles toward**; "add by IP" **writes through**
  to it (or proposes a diff) — not invisible imperative JSON. Survives `stream_bindings.json` loss.
- **Scale (target N≈10–50):** size allocator windows (today 100 mp / 50 even-ports per node from 2000/5100)
  or make dynamic; define ordinal **reuse** on remove; **bounded, parallel, failure-isolated** SSH fan-out;
  `nft set`/`ipset` per-binding instead of N×iptables rules; publish a **per-node RTP bitrate budget vs the
  Pi NIC ceiling** with a saturation alert (a flooding producer can drive cam10's local ladder toward
  `REBOOT_NODE` — `GATEWAY_REMOTE_RTP_MODE §4.5`).

## 15. Phasing (re-ordered per review)

- **P1 — Bundle + manual push (prove the data path), to the industrial minimum bar:**
  build versioned+signed bundle + role-gated `bootstrap.sh` (+ CI "no Janus secret" test); manually push to
  `.55`, probe → deploy stack → `RTP_TARGET_HOST=192.168.1.10` → gateway bind (live) → **cam55 online**.
  **P1 minimum bar (non-negotiable to avoid ops debt):** fix the audit bug · per-step telemetry+metrics ·
  stuck-state timeouts+alert · bundle version + skew metric · fake-SSH transport + `--dry-run` + faked-probe
  tests · declarative desired-state (minimal) · Day-2 stubs.
- **P2 — Node-agent** (`/healthz`+version+serial, `/probe_devices`, `/restart_stream`) with mTLS/token →
  makes `RemoteNodeClientStub` real → automatic remote FDIR recovery; add the remote-binding reconciler (§5).
- **P3 — Provisioner + add-by-IP** (state machine over SSH, persisted state, per-node lock, host-key pinning).
  **Prerequisite: per-node fail-closed firewall + reconciler (§10) — P3 must refuse non-loopback binds until
  it exists.** Identity model (§4) lands here.
- **P4 — Fleet hardening:** nft/ipset, staged rollout/rollback, attestation, SRTP, capacity budgets, full
  decommission automation.

## 16. Open decisions (for operator)
1. **SSH credential model** (§10): pre-shared gateway key (golden image) vs operator-supplied-at-add-time vs
   CA-signed short-lived certs. (Recommend: provisioning key on a golden image + scoped sudoers.)
2. **Identity refactor now or phased:** adopt UUID `node_id` + serial-keyed `binding_id` from P1 (recommended —
   cheap now, expensive after bindings exist), or ship P1 with the current label-key and migrate at P3.
3. **Mountpoint durability:** remote-binding reconciler (recommended, declarative) vs `permanent:True`.

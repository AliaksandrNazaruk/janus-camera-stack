# Adversarial Review Tracker — Dynamic Camera Onboarding

4-lens adversarial review of [DYNAMIC_CAMERA_ONBOARDING.md](DYNAMIC_CAMERA_ONBOARDING.md) v1 (2026-06-19).
Each finding is grounded in `file:line`. **Status:** `v2-design` = addressed in the v2 design doc ·
`fixing` = code fix in progress · `P1`/`P3` = scheduled to that phase · `open` = needs decision.

Severity: **BLOCKER** (must close before the relevant phase) · MAJOR · MINOR.

## Cross-cutting themes (same defect seen by multiple lenses)
- **Per-node RTP firewall is unbuilt + mislabeled "reuse" + spoofable + mis-phased** → S1, S2, S11, I6, O4.
- **Identity keyed by IP breaks on DHCP; should key by serial** → I2, I3, L10, O10.
- **No persisted lifecycle state / not a reconciler / mountpoints `permanent:False`** → L1, L2, O5.
- **Teardown leaks most resources** → L3, I5.
- **Admin audit trail is silently dead** → O1 (confirmed defect, fixing now).
- **Bundle: integrity but no version/skew/rollback** → O3, S8.

## Lens 1 — Security / trust
| ID | Sev | Finding | Evidence | Disposition |
|----|-----|---------|----------|-------------|
| S1 | BLOCKER | Per-node RTP firewall presented as routine/reuse but §4 of GATEWAY_REMOTE_RTP_MODE is NOT BUILT; `bind→online` exposes a mountpoint with only `/24`-wide ACCEPT | `binding_provision.py` opens no rule; live `/24` RTP | v2-design §6/§10; **P3 prerequisite** |
| S2 | BLOCKER | `-s <ip>/32 ACCEPT` is defeated by on-LAN UDP source spoofing; only real control is SRTP/anti-spoof | GATEWAY_REMOTE_RTP_MODE §4.1 | v2-design §10 (rp_filter+SRTP); P3/P4 |
| S3 | BLOCKER | SSH host-key trust unspecified; every repo precedent is blind TOFU → first push trusts whoever answers the IP | `tests/drill_harness.py:55,212` `AutoAddPolicy`; `host_infra/ansible.cfg:4` | v2-design §10 (pin); P3 |
| S4 | BLOCKER | `bootstrap.sh` needs root; one shared key = root-on-all-nodes, unscoped | `install.sh:276` root-required | v2-design §10 (least-priv user+sudoers); P3 |
| S5 | BLOCKER | Node-agent auth/bind/exposure asserted not designed; `/restart_stream` = unauth RCE if `0.0.0.0`+weak token | `node_client.py:103` plain `/healthz`; `install.sh:499,706` `0.0.0.0` | v2-design §9 (mTLS/token, bind LAN IP); P2 |
| S6 | MAJOR | Cred option 2 puts a live secret into an app that already leaked the sudo password twice | memory `feedback_sudo_password_handling` | v2-design §10 (write-only, scrub, prefer opt 1); P3 |
| S7 | MAJOR | `--role remote-producer` doesn't exist; `install.sh` unconditionally mints Janus/TURN secrets | `grep --role install.sh` → none; `install.sh:784-796` | v2-design §7; **fixing at P1** (role gate + CI test) |
| S8 | MAJOR | `SHA256SUMS` = integrity not authenticity; signing "optional"; bundle pull auth undefined | doc §5/§8 | v2-design §7 (sign + pin + push over SSH); P1 |
| S9 | MAJOR | The highest-privilege action (root push) is the least audited | `audit_log.py` (and O1: audit dead) | v2-design §8; P1 |
| S10 | MAJOR | Rogue/typo target IP no guardrail; `NodeRegisterRequest.host` no pattern; injection vector once SSH shells out | `stream_bindings.py:53` | v2-design §10 (CIDR allowlist, argv-only); P3 |
| S11 | MAJOR | iptables per-node rule leak/explosion + ordering vs catch-all DROP | `roles/network/tasks/main.yml:24-27` manual-dedup note | v2-design §10 (single reconciler, tagged rules); P3/P4 |
| S12 | MAJOR | No node attestation — trust = "answered at an IP" | — | v2-design §10 (serial + enrollment cert); P3/P4 |
| S13 | MINOR | Single shared static `CAM_ADMIN_TOKEN` gates onboarding; no per-operator identity | `admin.py:9` default `change-me` | v2-design §10; P3 |
| S14 | MINOR | Probe executes code on an unverified host before camera confirmation | doc §3 | v2-design §10/§11 (gate probe behind pinning); P3 |
| S15 | MINOR | Doc `app/services/...` paths off by `janus_camera_page/` prefix | doc §4 | **fixed in v2** |
| S16 | MINOR | `MP_DEFAULT_SECRET` shared across all remote mountpoints | `sensor_lifecycle.py:57` | v2-design §10 (per-binding SRTP key); P4 |

## Lens 2 — State machine / failure / teardown
| ID | Sev | Finding | Evidence | Disposition |
|----|-----|---------|----------|-------------|
| L1 | BLOCKER | No persisted provisioning state; mid-flow crash unrecoverable+invisible | `NodeEntry` has only host/role/reachability/ordinal (`stream_binding_store.py:140-164`) | v2-design §5 (WAL state + resume); P3 |
| L2 | BLOCKER | Mountpoints `permanent:False`; no remote reconciler → Janus restart darkens every remote cam forever | `janus_admin.py:149`; `sensor_reconcile.py:92` local-only | v2-design §5 (remote reconciler); P2 |
| L3 | BLOCKER | Teardown reclaims 2 of 11 resources; no `remove_node`, ordinal leaks | `stream_bindings.py:200-218`; no `remove_node` in store | v2-design §11 (full reverse); P3 |
| L4 | BLOCKER | "no_camera byte-clean" false — probe needs pyrealsense2+libusb installed | doc §3 vs §5 | v2-design §11 (static probe or documented residue); P1 |
| L5 | MAJOR | No cross-step lock; concurrent add of same IP races bundle write + double ordinal | flock is per-call (`stream_binding_store.py:235-253`) | v2-design §5 (per-node provisioning lock); P3 |
| L6 | MAJOR | No timeouts/retries/backoff; no stuck-in-`deploying` detection | only 2.0s on `probe_agent` `node_client.py:103` | v2-design §3/§8; P1 |
| L7 | MAJOR | Allocation re-run doesn't converge; re-add can re-roll mp/port vs node's contract.env | `stream_bindings.py:157-160` | v2-design §5 (reuse existing mp/port); P3 |
| L8 | MAJOR | `ensure-janus`↔firewall ordering unenforced; baseline `/24` RTP rule is live-only drift not IaC | grep `5002:5120` not in host_infra | v2-design §10; codify IaC P1, enforce order P3 |
| L9 | MINOR | `reachability` free-form string conflates network vs lifecycle | `stream_binding_store.py:331` | v2-design §4 (split enums); P3 |
| L10 | MINOR | IP-as-identity breaks on DHCP | — | v2-design §4; P1 decision |

## Lens 3 — Node identity / lifecycle
| ID | Sev | Finding | Evidence | Disposition |
|----|-----|---------|----------|-------------|
| I1 | BLOCKER | "Unification / local=degenerate" false — write-path rejects cam10 at every boundary; local/remote split is a safety boundary | `stream_bindings.py:151,183,206`; `stream_binding_store.py:306,396` | **fixed in v2 §2** |
| I2 | BLOCKER | Primary key undefined — keyed on operator label, not IP/serial | `NodeRegisterRequest.node_id` `stream_bindings.py:52`; serial absent from remote | v2-design §4 (UUID + serial); **P1 decision: adopt now** |
| I3 | BLOCKER | DHCP re-IP silently breaks reachability/FDIR/firewall; no reconciliation | `probe_agent(node.host)` `:131`; `/32` pinned to old IP | v2-design §4 (serial anchor, re-resolve host); P3 |
| I4 | MAJOR | Multi-RealSense per host structurally impossible — `node:sensor` collides; local projection drops dup-sensor | `_project_local` `stream_binding_store.py:343-369` | v2-design §4 (`node:serial:sensor`); P1 schema |
| I5 | MAJOR | "Same camera" undefined across replug/host-move → dangling bindings/mountpoints | serial not stored on remote binding | v2-design §4/§11; P3 |
| I6 | MAJOR | Per-node firewall (the keystone making IP load-bearing) doesn't exist | no RTP rule in `roles/network` | v2-design §6 (greenfield); P3 |
| I7 | MINOR | `binding_id` couples human label to durable key → rename = destroy/recreate | `binding_id=node:sensor` | v2-design §4 (display_name field); P3 |
| I8 | MINOR | Duplicate-add of same host/serial unguarded | `upsert_node` keys on node_id only | v2-design §4 (lookup-or-create); P3 |
| I9 | MINOR | Doc citations partly wrong/over-stated | doc §4 | **fixed in v2** |

## Lens 4 — Industrial operability
| ID | Sev | Finding | Evidence | Disposition |
|----|-----|---------|----------|-------------|
| O1 | BLOCKER | Provisioning a black box; audit channel **silently dead** | `stream_bindings.py:24-28` import `audit`; `audit_log.py` exports `emit` | **FIXING NOW** (4 routes) + v2-design §8 telemetry |
| O2 | BLOCKER | No stuck-state detection / per-step timeout | — | v2-design §8 (`CamstackProvisionStuck`); P1 |
| O3 | BLOCKER | Bundle not versioned / no skew / no rollout-rollback — the drift that bit the repo twice | stale L4 build; stale `rs-stream.sh` | v2-design §7; P1 (version+skew), P4 (rollout) |
| O4 | BLOCKER | §4 firewall scheduled P4 but is a blocking prerequisite of any non-loopback exposure (P3) | GATEWAY_REMOTE_RTP_MODE §4 | **fixed phasing in v2 §15** (P3 gate) |
| O5 | MAJOR | No continuous reconciliation; install-once-and-pray | — | v2-design §5; P2/P3 |
| O6 | MAJOR | Untestable-by-construction (SSH-to-hardware, no fake/dry-run) | `ci.yml` `-m "not hardware"` | v2-design §12; **P1** |
| O7 | MAJOR | Imperative JSON state, not config-as-code | `stream_bindings.json`; `hosts.ini` edge_nodes commented | v2-design §14; P1 (minimal) |
| O8 | MAJOR | Fleet scale unengineered (allocator windows, SSH fan-out, rule explosion, gateway SPOF) | windows `stream_binding_store.py:56-59` | v2-design §14; P4 |
| O9 | MAJOR | No Day-2 runbook for new failure modes | `OPERATOR_RUNBOOK.md` | v2-design §13; P1 stubs |
| O10 | MINOR | IP identity DHCP (dup of I3) | — | v2-design §4 |
| O11 | MINOR | `no_camera` teardown + cred handling touch already-burned areas | runbook FIFO-orphan note; memory | v2-design §10/§11 |

## Confirmed defect being fixed now
**O1 / S9 — admin audit trail is a silent no-op.** Four route modules (`stream_bindings`, `runtime_config`,
`admin_config`, `admin_dashboard`) `try: from app.services.audit_log import audit` with a no-op `except`
fallback, but `audit_log` exports only `emit` → every `audit(...)` call (incl. secret rotate/reveal, service
restart, mountpoint CRUD, node/binding CRUD) writes nothing. `device_camera.py` is the one correct caller
(`audit_log.emit(...)`). Fix: add an `audit(action, details, …)` convenience wrapper over `emit()` in
`audit_log.py`, repair the four imports, add a regression test asserting audit actually writes.

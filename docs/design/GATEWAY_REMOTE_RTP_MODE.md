# Gateway Remote-RTP Mode — Janus mountpoints for remote producers

- **Status:** ⚠️ **PARTIALLY IMPLEMENTED** — §2/§3 + control-port hardening shipped (G2 `2f6b32e` / G2-sec `6f5986a`); **all of §4 (firewall + SRTP + reconciler + hashlimit) is DEFERRED / NOT BUILT.** · 2026-06-18
- **Node:** `.10` gateway (192.168.1.10)
- **Sprint:** G0 spec for **G2** (Janus remote-ready mountpoints) + promoted **G4** rs-stream contract; firewall/network interplay (A2).
- **Prime directive:** local `cam10` stays loopback and unchanged; remote RTP is opt-in, host-scoped, **fail-closed**, never public.
- **v2 delta:** the security model is now explicit — raw RTP ingest is unauthenticated and UDP source IPs are spoofable, so the gateway (not the producer, not a source-IP rule alone) must carry the guarantee; fail-closed firewall ordering + default-DROP backstop + boot reconciler; Janus control-port hardening; rate-limit; the rs-stream contract is promoted from a forward-ref to a concrete G0 deliverable; OPEN-Q6 closed.

> **🚨 Implementation status — remote RTP is NOT yet LAN-safe.** **Shipped (G2):** `iface` threaded from the binding into `create_mountpoint` (`binding_provision.ensure_janus`); idempotency state contract (CREATED/EXISTS/CONFLICT/FAILED, port-divergence reconcile, `binding_provision.py`); `RTP_TARGET_HOST` app-side (`sensor_lifecycle._write_contract_env` + the `rs-stream.sh` half in the encoder repo); the new `POST /mountpoints` gained an `iface` field. **Shipped (G2-sec `6f5986a`):** static `janus.transport.http.jcfg` bound to loopback (`ip`+`admin_ip=127.0.0.1`), CORS `*` removed; the A2 firewall IaC (`host_infra/roles/network/tasks/main.yml`) extended to DROP **7088** alongside 8088/8188. **NOT BUILT — the whole of §4:** per-binding host-scoped fail-closed firewall (§4.2), boot reconciler (§4.3), SRTP (§4.1), rp_filter / anti-spoof (§4.1), `5100–5199` backstop DROP, hashlimit/DoS (§4.5), observed-source verification (§3). `ensure_janus` deliberately opens **no** firewall rule. ⇒ a remote binding provisioned today is reachable by the whole LAN at the socket level once exposed — **only loopback / bench use is covered.** Janus error reflection is also not yet sanitized to an allow-list (§6 pt 3 partial).

---

## 1. Current state

The lowest layer is **already parameterized**; everything above hardcodes/omits it:
- `janus_admin.create_mountpoint(…, iface: str = "127.0.0.1")` (`janus_admin.py:108-151`) injects `"iface": iface` into the Janus media object (`:146`). The bind interface is a parameter, defaulting to loopback (`:118`), not threaded from above: `POST /mountpoints` (`admin_dashboard.py:494-547`) and `/streams/provision` (`:1033-1065`) don't forward it; dynamic depth create (`sensor_lifecycle.py:206-225`) doesn't pass it; static color jcfg `iface="127.0.0.1"` (`deploy/janus/etc/janus.plugin.streaming.jcfg.template:40`).
- Janus type:`rtp` mountpoints have **no per-packet auth** (no SRTP/source validation in the plugin). `permanent:false` (`janus_admin.py:149`) → mountpoints vanish on Janus restart; firewall rules (if persisted) do not.
- Admin key `JANUS_STREAMING_ADMIN_KEY` from `/etc/robot/camera-secrets.env`, SENSITIVE, **not logged today** (verified: audit logs only id/port/codec; `janus_admin.py:31`, `secret_store.py:29-38`).
- ~~**Committed Janus transport binds `0.0.0.0`**, `cors allow_origin="*"`; A2 drops 8088/8188 only — 7088 has no DROP.~~ **Resolved (G2-sec `6f5986a`):** the live `.10` admin already bound 7088/7089 to `127.0.0.1` with empty CORS; the *repo* `deploy/janus/etc/janus.transport.http.jcfg` now matches (`ip`+`admin_ip=127.0.0.1`, no CORS `*`), and the A2 IaC drops 7088 too. **Still open:** live HTTP (8088) binds `0.0.0.0` (A2-firewall-DROP'd, not loopback-bound); the legacy `infrastructure/depth_node/firewall-depth.sh:41` (the now-archived `.55` node) still opens 7088 to `/24`.
- **rs-stream producer hardcodes the destination:** `-f rtp "rtp://127.0.0.1:${PORT}"` (`host_infra/roles/encoder/files/rs-stream.sh:116,126`); `_write_contract_env` writes only `PORT` (`sensor_lifecycle.py:119-129`). There is **no** `RTP_TARGET_HOST` anywhere. ⇒ a remote producer **cannot send to `.10` today**.

## 2. G2 — create the mountpoint from a binding, with `iface` (OPEN-Q6 closed) — ✅ DONE

`binding_provision.ensure_janus(binding)` calls `create_mountpoint(…, iface=binding.janus.rtp_iface, payload_type=…, codec=…, rtp_port=…)`:
- local → `iface="127.0.0.1"`. remote → `iface=` the `.10` **LAN IP**, **never `0.0.0.0`**.
- **OPEN-Q6 resolved (DONE):** `rtp_iface` is **explicit in the binding** and validated non-loopback for remote (`stream_binding_store._validate_remote`); the G6 create endpoint requires it. (Subnet-membership validation beyond "non-loopback" is not enforced — minor gap.)
- **Done:** `POST /mountpoints` (`admin_dashboard.py`) gained an optional `iface` field (default `127.0.0.1`). **Not done:** `/streams/provision` and the dynamic-depth `sensor_lifecycle` create path still default loopback — only the new binding route + `/mountpoints` thread `iface`. That's fine: only remote bindings need a non-loopback iface, and they go through the binding path.
- **Idempotency as a state contract (R4-M7) — DONE:** `binding_provision.ProvisionStatus.{CREATED,EXISTS,CONFLICT,FAILED}` with robust `is_already_exists()` + port-divergence reconcile, replacing the brittle string-match.

## 3. rs-stream contract — promoted G4 (two-sided, cross-repo) — closes B-rsstream

Remote ingest is **inert** until the producer can target `.10`. Minimal contract, landed alongside G2:
- `_write_contract_env` emits `RTP_TARGET_HOST="{binding.janus.rtp_iface}"` (default `127.0.0.1`) in addition to `PORT`.
- `host_infra/roles/encoder/files/rs-stream.sh` uses `rtp://${RTP_TARGET_HOST}:${PORT}` (default preserves loopback). **This is an external-repo change** — call it out in the PR; `.10` alone cannot make `.55` send anywhere.
- **The producer's target host is UNTRUSTED.** A misconfigured/malicious `.55` can blast H.264 to an arbitrary LAN IP (disclosure of the robot's video). The security guarantee must come from the **gateway** (iface-bind + firewall + SRTP), never from trusting the producer to send to the right place. `.10` should additionally verify the observed RTP source matches `nodes[node_id].host` and alert on mismatch.

## 4. Security model for remote ingest (the core of v2)

> **🚨 STATUS: NONE OF §4 IS IMPLEMENTED (deferred).** There is currently **no** per-binding firewall, **no** boot reconciler, **no** SRTP, **no** rp_filter/anti-spoof, **no** RTP-range backstop DROP, **no** hashlimit, and **no** observed-source verification. `ensure_janus` opens no firewall rule. Remote RTP ingest therefore has **zero network-layer protection** today — it is safe for **loopback / trusted-bench** use only, and these guards are **blocking prerequisites** before any real LAN exposure of a remote producer. Each subsection below is the *intended* design, not the built state.

### 4.1 Threat: unauthenticated, spoofable ingest (R3-B1) — DEFERRED (not built)
Janus RTP ingest authenticates nothing; UDP source IPs are spoofable on the LAN. A `source={node_host}` iptables rule stops only **non-spoofing** hosts. Any LAN host can emit `src=.55,dst=.10:5100` and **inject H.264 into mountpoint 2000** — attacker-controlled video on a robot camera feed, or SSRC/seq desync to corrupt the real stream. The v1 threat model only considered a *silent* remote, never a *hostile injector*.
- **Mitigation (recommended):** SRTP on remote mountpoints (Janus `srtp_suite`/`srtp_crypto`), per-binding key delivered out-of-band to the producer; carry it in `StreamTransport.srtp`. This is the only control that actually authenticates frames.
- **If SRTP deferred:** document the residual risk as *accepted for the trusted LAN*, and add reverse-path filtering (`rp_filter=1` on the camera-LAN iface) + an anti-spoof DROP for LAN packets claiming an off-segment source. Add a per-mountpoint ingest `secret` field so the model can carry it later.

### 4.2 Fail-closed firewall, ordered, with a default-DROP backstop (R3-B2)
Binding to the LAN IP opens the socket to the whole subnet the instant the mountpoint exists; the firewall is the **only** control, and host_infra has **no default-DROP INPUT policy** (a missing rule = ACCEPT). Therefore:
- **Order:** add the host-scoped `ACCEPT udp dport={rtp_port},{rtp_port+1} in_interface={lan} source={node_host}` **and** a port-specific DROP-from-non-source **before** creating the mountpoint; **abort + roll back** mountpoint creation if the firewall step fails. Close firewall **after** destroying the mountpoint.
- **Backstop:** commit a default-DROP (or a catch-all DROP on the remote RTP ingest range, e.g. `5100–5199`) in host_infra, so the *absence* of a per-binding ACCEPT is closed, not open. Mirror the per-port deny pattern A2 used.
- **Do NOT** model the helper on `firewall-depth.sh` — that script is **subnet-permissive** (`/24` ACCEPTs, blanket-trusts `.10`), the opposite of deny-by-default (R3-m5). The helper must be single-source-host + deny-by-default.

### 4.3 Boot/periodic reconciler (R3-M2)
A crash between create and rule-add, or a failed delete on `remove`, leaves orphaned open ports. Define a reconciler that makes the iptables ingress set a **pure function of** `StreamBindingStore.list()` filtered to `remote_producer`: add missing rules, **delete rules with no live binding**. Rule identity carries `binding_id` in the iptables `--comment` for deterministic prune. Reuse the `desired_active` pattern (model OPEN-Q4).

### 4.4 Janus control-port hardening (R3-M1) — ✅ MOSTLY DONE (G2-sec)
Opening the LAN interface for RTP must not widen any TCP control port.
- **DONE:** `deploy/janus/etc/janus.transport.http.jcfg` now sets `ip="127.0.0.1"` + `admin_ip="127.0.0.1"` and drops CORS `*` (the live `.10` admin was already loopback-bound; this codifies it).
- **DONE:** the A2 IaC loop now DROPs **7088** from non-loopback alongside 8088/8188 (`host_infra/roles/network/tasks/main.yml`).
- **Still open:** live HTTP `8088` binds `0.0.0.0` (A2-firewall-protected, not loopback-bound — optional live re-bind in a maintenance window); the legacy `firewall-depth.sh:41` 7088→`/24` on the archived `.55` node.

### 4.5 DoS / blast-radius (R3-M3)
A flooding (or merely misconfigured) producer saturates the shared Pi NIC/CPU/conntrack, degrading **loopback `cam10`** → the **local** ladder then escalates that local staleness up to `REBOOT_NODE`. So a remote flood induces a local reboot **indirectly**, bypassing the FDIR safety property by an unmodeled path.
- Add an iptables `hashlimit` per remote ingest port sized to ~a few× the encoder `BITRATE_KBPS`, dropping overflow before Janus.
- UNIFIED_FDIR must treat ingress-rate anomalies as a producer signal, not let them masquerade as a local fault.

## 5. Non-goals
No Janus on `.55` · no `0.0.0.0` bind · no public Janus HTTP/Admin · no subnet-wide RTP opening (host-scoped only) · no trusting the producer for any security property.

## 6. Acceptance (G2) — observable
1. ✅ local binding → mountpoint `iface=127.0.0.1`; remote → `iface=.10` LAN IP. *(tests/test_binding_provision.py)*
2. ✅ `payload_type`/`codec`/`port` flow from the binding.
3. ⚠️ **Secret negative test — partial:** the admin_key is not logged (verified), but Janus `error` reflection is **not** yet sanitized to an allow-list (`_plugin_message` re-raises `pdata['error']` verbatim).
4. ✅ idempotency state contract: matching → no-op; divergent iface/port → reconcile-or-reject.
5. ⛔ **Firewall fail-closed — DEFERRED:** no firewall step exists yet (§4.2), so this is untestable/not-applicable until §4 is built.
6. ✅ opening remote RTP widens no TCP control port — 7088 loopback-bound + A2-DROP'd, admin already loopback (G2-sec).

## 7. Remaining open — now BLOCKING prerequisites before any LAN exposure of a remote producer
- **All of §4** (per-binding fail-closed firewall, boot reconciler, SRTP, rp_filter, backstop DROP, hashlimit, observed-source check) — none built.
- **OPEN-Q5** firewall ownership: helper + IaC with one reconciler as the single writer (avoid A2-style drift).
- **OPEN-Q7** RTCP for remote: ingest is **RTP-only unless the producer declares RTCP**; the per-binding rule opens exactly the even/odd pair `{rtp_port, rtp_port+1}`, since `create_mountpoint` always sets `rtcpport=rtp_port+1`.
- **SRTP rollout** (4.1): suite/key-exchange mechanism vs accepted-residual-risk — decide before remote ingest leaves a trusted bench LAN.

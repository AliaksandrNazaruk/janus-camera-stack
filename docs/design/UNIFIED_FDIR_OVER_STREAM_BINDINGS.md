# Unified FDIR over StreamBindings

- **Status:** ✅ **IMPLEMENTED** (G5.1 `399f885` + G5.1d `6a270c9`) — was DESIGN v2 · 2026-06-18
- **Node:** `.10` gateway (192.168.1.10)
- **Sprint:** G0 spec → built in **G5** (FDIR over binding_id).
- **PRIME SAFETY PROPERTY:** a stale/fake/**hostile** remote binding (`cam55`) must **never** cause a local destructive action on `.10` — no global restart, no Janus restart, no USB reset, **no reboot** — *including indirect paths*.
- **v2 delta:** v1's "structurally unreachable" was **intent, not mechanism** — the review found three reachable reboot paths. v2 replaces intent with mechanism: a fully-specified isolated `RemoteStreamMonitor`, detection-time mode resolution, a shared-Janus reboot guard, a `mountpoint_id==janus_mount_id` invariant, the full `FdirEvent` change surface, TB-C1 separation, `Domain.PRODUCER`, and failure-injection acceptance. OPEN-Q9/Q10/Q11 closed.

> **Implementation status — built.** G5.1 (`399f885`): `Domain.PRODUCER`; `FdirEvent`/`emit` binding identity (`fdir_events.py`); `NodeClient` (`node_client.py` — `LocalNodeClientAdapter` + offline `RemoteNodeClientStub`, `probe_agent`); isolated `RemoteStreamMonitor` (`remote_stream_monitor.py` — pure `evaluate()`, edge-triggered+heartbeat, terminal set capped) **with the invariant test** `test_monitor_module_has_no_local_destructive_references`. G5.1d (`6a270c9`): shared-Janus reboot guard (`watchdogs._janus_exception_escalation_allowed`/`_local_stream_recently_alive`), the `mountpoint_id==janus_mount_id` skip, monitor wired into `events.py`. OPEN-Q8 resolved as **"remote monitor takes no quiesce arm"** (global gate untouched); Q9/Q10/Q11 resolved as built. **Two nuances vs this doc:** the §4.6 invariant is enforced at **upsert + every monitor tick** (fail-closed skip), *not* a one-shot startup sweep; and the ladder/executor signatures were **intentionally left binding-agnostic** (remote events never enter them — see §2).

---

## 1. Current state — single-camera, all-global (the risk, confirmed)

- **Domains** (`fdir_events.py:38-45`): `SENSOR, PIPELINE, JANUS, NETWORK, TURN, CLIENT, SYSTEM` — no stream identity. `FdirEvent.emit()` (`fdir_events.py:80`) takes `domain` positionally; **no** binding param.
- **Watchdog** monitors **one** mountpoint: `janus.janus_summary(settings.janus_mount_id)` (single int, default 1305 — `settings.py:147`; `watchdogs.py:116,175`). `janus_summary→streaming_info` is wrapped by `@with_streaming_handle` (`janus.py:186`) → it acquires a session+handle on the **shared** Janus instance per call. `video_age_ms` vs `watchdog_stale_ms=10000`; interval default 8s (`settings.py:198`). `_try_escalate`: `is_quiesced(domain)` → global 5s dedup (single `_last_escalation_ts`) → `ladder.escalate(signal, domain)` (`watchdogs.py:187-209`). Exception path escalated `Domain.JANUS` (`watchdogs.py:180`) — *this section describes the pre-G5 risk; post-G5.1d this path is now guarded (§4.5): it downgrades to a non-destructive WARN when the local snapshot is fresh.*
- **Recovery** (`recovery_executor.py`): `RESTART_PIPELINE` (`encoder-admin restart`; quiesce `{PIPELINE,SENSOR}`), `RESTART_JANUS` (`encoder-admin stop`+`janus-admin restart`; quiesce `{PIPELINE,SENSOR,JANUS}`), `USB_RESET`, **`REBOOT_NODE` (`sudo systemctl reboot`**, circuit breaker `max_fdir_reboots`). Single global `RecoveryLadder` singleton + global reboot counter.
- **Quiesce** (`fdir_quiesce.py:37-87`): **global** module state (`_arms/_until/_domains`, single `_lock`), TTL ceiling 120s; TB-C1 = domain-scoped suppression with first-arm deadline anchoring (TB-C6) and domain-widening on nested arms.

**Three reachable reboot paths v1 ignored:**
1. **Shared-Janus coupling** — a remote mountpoint that wedges the shared Janus makes `janus_summary(1305)` raise → `watchdog_exception → Domain.JANUS` → global ladder → reboot. `binding_id` never enters.
2. **Undecidable classification** — `video_age_ms` on a mountpoint is symptom-identical for producer-died / LAN-dropped / Janus-stopped; "remote → not JANUS" needs a binding lookup that doesn't exist.
3. **Unspecified monitor** — the safety boundary was one sentence; an implementer reuses the global `get_ladder()` whose terminal action is reboot.

## 2. Target — resource identity on every event

Every signal carries `{ binding_id, node_id, sensor, mountpoint_id, domain, signal }`. Staleness is evaluated **per binding** (per `mountpoint_id`).

### `FdirEvent` change surface (R1-m2) — DONE, narrower than designed
Optional `binding_id`/`node_id`/`sensor` were added to `FdirEvent` + `emit` (keyword-only, `None` default — `fdir_events.py`). **As built, the binding identity rides on `FdirEvent`/`emit` only; the ladder/executor signatures were intentionally left binding-agnostic** (`recovery_ladder.escalate(signal, domain)` and `RecoveryExecutor.execute(level, signal, domain)` are unchanged). That is correct *because* remote-origin events never enter the local ladder — only the local watchdog feeds it, and the remote monitor emits its own `Domain.PRODUCER` events directly. So the threading is one consumer (the remote monitor), not the "~8 call sites through the ladder" v2 anticipated.

## 3. NodeClient — recovery indirection

`recover(binding_id)` routes through a `NodeClient` keyed by `node_id`:
- interface: `status(node_id)`, `stream_status(node_id, sensor)`, `restart_stream(node_id, sensor)`.
- **`LocalNodeClientAdapter`** (`cam10`): `restart_stream` → existing local path (`encoder-admin restart`), unchanged.
- **`RemoteNodeClientStub`** (`cam55`): offline — returns one of `{reachable, unreachable, bootstrap_required}`; **cannot run any local command**. `bootstrap_required` = node table has the node but no agent answered `/healthz` (maps to G6 `/nodes/check → node_agent_unreachable`); `unreachable` = no TCP. It never shells out.

## 4. The safety mechanism (mechanism, not intent)

### 4.1 `mode` is the structural cap (from STREAM_BINDING_MODEL §4)
| `mode` | allowed actions |
|--------|-----------------|
| `local_producer` | full ladder incl. `REBOOT_NODE` (via `LocalNodeClientAdapter`) |
| `remote_producer` | `{mark_degraded, emit_alert, ensure_janus_mountpoint, NodeClient.restart_stream}` — **no** local-destructive action (`ensure_janus_mountpoint` justified in §4.7) |

The cap keys off the `mode` **enum** (can't be misconfigured), resolved **before** any action selection.

### 4.2 `Domain.PRODUCER` for remote staleness (OPEN-Q9 closed — R1-B2/M2)
Add `Domain.PRODUCER`. A `remote_producer` mountpoint going stale escalates `Domain.PRODUCER`, **never** `JANUS`/`PIPELINE`/`SENSOR`. Reusing `SENSOR` is wrong (it re-enters the local ladder's `USB_RESET`, which is node-local). `Domain.PRODUCER` has exactly one handler path: the remote monitor (§4.4).

### 4.3 Detection-time mode resolution (R1-B2)
The watchdog must know `mode` **before** the age check, not infer it from the symptom:
- An **in-memory `mountpoint_id → binding` map**, refreshed out-of-band (rebuilt on binding upsert/remove and once per loop from a `StreamBindingStore.list()` **snapshot** — OPEN-Q11 closed: poll-snapshot). **No store I/O in the failure path.**
- An **unmapped** mountpoint going stale → classified conservatively: **alert-only, never reboot** (an orphaned/removed binding can't trip the local ladder).

### 4.4 The isolated `RemoteStreamMonitor` — fully specified (R1-B3)
A distinct component, **separate from the global watchdog/ladder**:
- own loop (or async task), own interval/grace/**dedup clock** (disjoint from the global `_last_escalation_ts` — R1-M2).
- own bounded state; **must not import or reference** `recovery_ladder.get_ladder`, the global reboot counter, or the global `fdir_quiesce` arm. **Enforced by a unit invariant test** (assert the module's imports exclude those symbols).
- terminal action set = exactly `{mark_degraded (set binding.status), emit_alert (Domain.PRODUCER), ensure_janus_mountpoint (§4.7 — additive, self-targeted, idempotent), NodeClient.restart_stream}` — a hard cap; no fallback to `get_ladder()` on repeated failure.
- **offline-stub behavior:** a permanently-unreachable remote binding is stale forever → **edge-triggered** alerting with backoff (alert on `healthy→degraded` transition + a slow heartbeat, **not** every interval), so a dead remote binding cannot evict `cam10`'s events from the 500-entry ring (R1-m4).

### 4.5 Shared-Janus reboot guard (R1-B1 — the path v1 missed)
Because remote mountpoints share the one Janus the global watchdog probes, a remote-induced Janus stall can raise `watchdog_exception → Domain.JANUS`. Guard:
- a Janus-wide-unreachable signal escalates `Domain.JANUS` (→ possible reboot) **only if the local mountpoint (`janus_mount_id`) is *itself* confirmed not delivering** — i.e. the *local* stream is actually down. If local RTP is still arriving while the admin probe is flaky, **downgrade** to a non-destructive signal (retry/alert), not reboot.
- Records the option to **isolate remote mountpoints to a separate Janus instance/process** as the stronger (future) form; the guard is the G5 minimum.

**G5.4 — guard extended to the normal stale/`None` path (the gap v1's guard left, observed in the field 2026-06-19).** The G5.1d guard above sat only on the watchdog *exception* handler (`janus_summary` raising). But a **Janus restart** makes `janus_summary(janus_mount_id)` return `video_age_ms=None` (mountpoint momentarily absent / not-yet-fed) — a *successful* probe, not an exception — so it fell through to the normal stale branch and escalated `Domain.PIPELINE` up the ladder to `restart_janus`, which dropped the mountpoint again → **a self-sustaining restart burst** (4–6 `janus-admin restart` in seconds, recurring ~every 20–45 min), each burst collaterally wiping the remote (`cam55`) runtime mountpoints. Fix: the same `_local_stream_recently_alive()` check now also guards the **stale/None** escalation (`watchdogs._watchdog_loop`): when the local encoder is provably producing (snapshot fresh) but the Janus mountpoint reads stale, the fault is the shared Janus layer, not cam10's pipeline — **suppress** (WARN, `Domain.JANUS` `suppressed_local_alive`) instead of climbing. The encoder's connectionless UDP RTP resumes once Janus re-listens on its permanent mountpoint; a genuine local outage (snapshot *also* stale) is unaffected and still escalates. Pairs with §4.7: §4.5 stops the *local* watchdog from restarting Janus on a transient gap; §4.7 recreates the *remote* mountpoints if Janus restarts for any other reason.

  *Field-confirmed root cause + G5.4b fix (`195017f`, 2026-06-19):* G5.4 as first shipped still did NOT stop the bursts — `_local_stream_recently_alive()` depended on `_last_mtime_change_mono` being seeded by the snapshot watchdog, and that seed was effectively 0 (not seeding), so the guard silently returned False and BOTH guards (exception + stale/None) kept escalating to `restart_janus` (ladder `total_recoveries` hit 250; local color stream healthy throughout). Fix: `_local_stream_recently_alive()` now **stats `settings.snapshot_path` directly** (ground truth), with the monotonic seed as a wall-clock-skew-immune fallback. Logic-verified: in a fresh process (seed=0) it returns True via the direct stat, so both guards now suppress. The deeper trigger — *why the Janus admin probe goes stale while RTP flows* — is the shared-Janus contention §4.5 always anticipated; the guard's job is to make it non-destructive, which (post-`195017f`) it does.

  *Adversarial-review hardening (the suppression must not become a new failure mode):* (a) **bounded** — suppression is allowed at most `_MAX_SUPPRESS_TICKS` (default 5) consecutive ticks; a *sustained* (genuinely wedged, not transient) Janus then falls through to one quiesce-gated escalation, so a wedged Janus can still be recovered rather than suppressed forever. (b) **cross-sensor guard** — the liveness signal is the *color* snapshot, a valid proxy ONLY when probing the color mountpoint, so `_local_stream_recently_alive()` returns False (fail-safe → escalate) whenever `janus_mount_id != janus_color_stream_id`. (c) **observable** — each suppression bumps `camstack_watchdog_suppressions_total{reason}`; alert on a sustained rate so a suppressed-but-dead cam10 isn't invisible (the FDIR state otherwise reads NOMINAL with only `stream_active=0`). (d) **precondition** — the snapshot is a valid RTP proxy only because the snapshot and the RTP are two `split` outputs of one ffmpeg/one decode (rs-stream@color); documented at the guard.

### 4.6 `mountpoint_id == janus_mount_id` invariant (R1-M3) — built differently (stronger)
No `remote_producer` binding may hold `mountpoint_id == janus_mount_id`. **As built, this is enforced in two places, not by a startup sweep:** (1) at **upsert** in the store (`stream_binding_store._validate_remote` rejects it), and (2) **every monitor tick** the `RemoteStreamMonitor` refuses (CRITICAL log) any remote binding squatting `janus_mount_id` — a fail-closed skip against a hand-edited state file (`remote_stream_monitor.tick`). There is *no* one-shot startup validation pass; the per-tick re-check is arguably stronger.

**G5.3 widening (review M-finding):** cam10 owns the *whole* mountpoint pool below `REMOTE_MP_MIN` (2000) — `janus_mount_id` (1305) is just one id in it. So the two runtime guards (the monitor tick **and** `reconcile_janus`) were broadened from `== janus_mount_id` to `< REMOTE_MP_MIN`: a hand-edited remote binding at e.g. 1306 is now refused too, not just one at 1305. (`_validate_remote` already enforces `mp ≥ REMOTE_MP_MIN` on the API path; this closes the hand-edited-file gap across the full local range.)

### 4.7 Mountpoint recovery — surviving a Janus restart (G5.3, post-G5.1)
**Failure mode (observed in the field, 2026-06-19):** Janus restarted; its *runtime* (non-permanent) mountpoints — every `remote_producer` binding's — vanished, while cam10's permanent jcfg mountpoints survived. The remote encoders kept sending RTP into now-nonexistent mountpoints, so `janus_summary` returned `video_age_ms=None` and the bindings sat **degraded indefinitely**. The §4.4 terminal action (`NodeClient.restart_stream`) restarts the *node* encoder — which was never the problem; the missing piece was the *gateway* mountpoint. This is exactly the "recovery on reboots" the model promises, and pre-G5.3 it was unhandled (cam55 was dark ~21 min until a manual ensure).

**Fix — two complementary, idempotent reconcilers, both via `binding_provision.ensure_janus`:**
1. **Startup reconcile** (`binding_provision.reconcile_janus`, wired in `events.py` startup as a **background task** — `asyncio.create_task` over `asyncio.to_thread`, off the event loop): re-ensure every stored `remote_producer` mountpoint. Covers gateway reboot, L4 restart, and a Janus restart that occurred while L4 was *down* — cases where the monitor's in-memory `ever_healthy` is empty on the first tick, so the §4.4 edge gate would classify a mountpoint-absent binding as `WAITING_FOR_RTP` and **never** recover it. It is backgrounded (review HIGH-finding) precisely because the incident it targets — a Janus that is slow/unreachable at boot — would otherwise block the sync `create_mountpoint` round-trips inside the async startup and delay `READY=1` past the systemd start timeout into a restart loop. The monitor is the steady-state backstop, so the few seconds of overlap before the sweep finishes are harmless (both are idempotent creates).
2. **Monitor re-ensure on the degraded edge** (`remote_stream_monitor._apply`, on the alert path only): when a *previously-healthy* binding regresses, re-ensure its mountpoint **before** the node restart. If it had to be CREATED, the mountpoint was the fault and the node is fine → skip the disruptive node restart; if it already EXISTED but is still stale, the fault is upstream → restart the node. Covers a Janus restart while L4 stays up (the observed incident): online → stale → alert → mountpoint recreated → RTP lands → online, self-healed within the alert. **Both-down** (mountpoint absent AND node encoder dead) still recovers: a *crashed* encoder is auto-restarted by the node's own systemd (`rs-stream@ Restart=always, RestartSec=2`) while this path recreates the mountpoint; only a *hung* (running-but-silent) encoder coinciding with an absent mountpoint waits for the monitor's next heartbeat — and even that is strictly better than pre-G5.3, which never recreated the mountpoint at all.

**Why `ensure_janus_mountpoint` does NOT violate the PRIME SAFETY PROPERTY (it is additive, not a "Janus restart"):**
- It calls `janus_admin.create_mountpoint` for **one** mountpoint — the binding's **own pre-allocated `mountpoint_id`, read from the gateway-authoritative store**. It does **not** restart the Janus *process*; existing mountpoints, cam10's 1305-1308 included, are untouched.
- A hostile/fake RTP sender cannot influence it: id/port/iface come from the store, which a remote sender cannot write. The most a hostile binding can do is stay degraded, which idempotently re-ensures *its own* (already-existing → no-op) mountpoint at most **once per heartbeat (~300 s)** — bounded, additive, self-targeted.
- It cannot squat cam10: the §4.6 guards — widened in G5.3 to the whole local-owned range `< REMOTE_MP_MIN`, enforced at upsert **and** in both runtime sweeps (monitor tick + `reconcile_janus`) — refuse any binding holding a cam10 id, so `ensure_janus` is only ever called for ids ≥ `REMOTE_MP_MIN`. Even a tampered id that somehow reached `create_mountpoint` could not *destroy* a live cam10 mountpoint: Janus refuses create-over-existing (→ EXISTS/CONFLICT, never a mutation).
- It touches no encoder/USB/reboot/systemctl path, so the §6 invariant test (`get_ladder`/`recovery_ladder`/`fdir_quiesce`/`reboot`/`systemctl`) is unchanged and still passes.

The `remote_producer` terminal set (§4.1, §4.4) therefore widens to `{mark_degraded, emit_alert, ensure_janus_mountpoint, NodeClient.restart_stream}` — all non-local-destructive.

## 5. Decomposing global state (staged; TB-C1 + cam10 preserved)

- **G5.1:** thread `binding_id` (§2); build the mountpoint→binding map (§4.3); add `Domain.PRODUCER` (§4.2); ship the `RemoteStreamMonitor` (§4.4) and the Janus guard (§4.5). `cam10` keeps the **existing global ladder unchanged** (it is the only `local_producer`).
- **G5.2 — quiesce scoping (OPEN-Q8, and TB-C1 is NOT free — R1-M1):** the global gate (`_arms/_until/_domains`) stays **untouched** for `cam10`. Remote monitors get a **completely separate** quiesce structure (or **no** quiesce — their terminal action `restart_stream` doesn't disrupt local watchdogs, so a gate may be unnecessary). A remote arm/unquiesce **must not** touch the global `_arms` (a remote `unquiesce` dropping `_arms` to 0 would clear `cam10`'s live window). Add a **TB-C6 regression test** interleaving a remote arm with a `cam10` arm, asserting `cam10`'s deadline + refcount are unaffected.
- **Later:** multiple *local* producers would generalize the ladder per-binding — out of G5 scope (only `cam10` is local).

## 6. Acceptance (G5) — failure-injection, not happy-path (R1-B4)

1. `FdirEvent` carries `binding_id` (old call sites still pass with `None`).
2. stale `cam10:color` recovers exactly as before; **TB-C1 + TB-C6 regression pass**.
3–4. **Reachable-path reboot tests** (assert `REBOOT_NODE`/`RESTART_JANUS`/`RESTART_PIPELINE`/`USB_RESET` are **never** invoked for a `remote_producer`):
   - (a) shared-Janus admin probe stalls **while** a remote mountpoint is stale **and** local RTP still arrives → guard holds, no reboot;
   - (b) a remote binding misconfigured with `mountpoint_id==janus_mount_id` → rejected at startup (fail-closed), cannot drive the local ladder;
   - (c) a binding removed mid-escalation / an unmapped stale mountpoint → alert-only, no local action, no stale ladder state inherited;
   - (d) a permanently-offline `cam55` (stub `unreachable`) → edge-triggered alerts with backoff, **no** escalation, does not flood the event ring.
5. ✅ the remote monitor takes **no** quiesce arm (the chosen G5.2 design — *not* a binding-scoped quiesce); cam10's global gate is provably untouched (regression `test_remote_tick_does_not_perturb_global_quiesce`).
6. ✅ **Invariant test:** `test_monitor_module_has_no_local_destructive_references` asserts the `RemoteStreamMonitor` module references no `get_ladder` / `recovery_ladder` / `fdir_quiesce` / reboot / systemctl.
7. **(G5.3) Janus-restart recovery:** with a remote binding online, destroy its mountpoint (simulating a Janus restart) → the monitor's next alert re-ensures it and the binding returns to ONLINE; **and** a fresh startup re-ensures all remote mountpoints before the monitor starts. Asserts neither path touches cam10's mountpoint or any local-destructive action.

*(All §6 acceptance criteria are met — see `tests/test_remote_stream_monitor.py`, `tests/test_watchdog_reboot_guard.py`, `tests/test_fdir_events.py`.)*

## 7. Non-goals — split by enforcement (R1-m3)
- **Structurally enforced today:** no command execution on `.55` (`RemoteNodeClientStub` cannot shell out); no SSH/remote-reboot of `.55`.
- **Enforced by G5 (was violable in v1):** no global gateway restart/reboot from remote-origin staleness — backed by §4.4/§4.5/§4.6 + the §6 failure-injection tests, not prose.
- No remote agent now · no rewrite of all FDIR in one commit (staged G5.1/G5.2) · no change to `cam10`'s ladder semantics.

## 8. Remaining open (non-blocking for G5.1)
- **OPEN-Q8** quiesce-scoping representation (separate structure vs binding-tagged) — G5.2.
- Whether to escalate to **separate-Janus-instance** isolation (§4.5) — future, beyond the G5 guard.
- Status→registry wiring (the `status` field is in the model; G3 consumes it).

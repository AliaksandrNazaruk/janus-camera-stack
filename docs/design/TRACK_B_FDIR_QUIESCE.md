# Track B — FDIR Quiesce Around Planned Restarts (Design Spec v2)

**Status:** DESIGN ONLY — **v2, adversarial-review-corrected.** No code, no FDIR change, no deploy.
**Scope:** make a *planned* encoder restart not escalate the recovery ladder toward `reboot_node` — and (v2) make that guarantee actually hold against the paths the review found around the bare gate.
**Date:** 2026-06-18 · **Prerequisite for:** B2 color `RESTART_ENCODER` apply.

> v1 proposed a single time-bounded gate at `_try_escalate`. A 2-reviewer adversarial
> pass (7 angles) + grounding found the gate alone does **not** close the reboot path:
> the recovery executor self-amplifies (a pre-existing live bug), multi-worker silently
> defeats it, a systemd `WatchdogSec` restart bypasses it, and the observability call
> would crash. §0 is the corrections changelog. The core idea survives; the safety now
> comes from the gate **plus four supporting guarantees** (§6).

---

## 0. v2 corrections (adversarial review)

| # | v1 claim | Reality (grounded) | v2 resolution |
|---|---|---|---|
| **TB-C1** | "single chokepoint, total coverage" (R-B8) | The recovery executor's OWN `restart_pipeline`/`restart_janus` (`recovery_executor.py:156,164`) make the stream stale → the watchdog re-escalates after the 5 s dedup (`watchdogs.py:66`) lapses but the 60 s restart is still in flight → **self-amplifies toward `reboot_node` during ordinary unplanned recovery**, no apply involved. Grace is startup-only, never re-armed (`watchdogs.py:84-93`). | §6.3 — OQ-B3 **promoted to mandatory**: the executor wraps its own restarts in quiesce (re-armed per attempt). This was a **live latent bug**; the gate is the fix. |
| **TB-C2** | gate emits `emit_fdir_event(detection=…, action="suppressed_planned", …)` | That function **does not exist**; real API is `emit(domain, severity, detection_signal, recovery_action, outcome, details)` (`fdir_events.py:80`), and `suppressed_planned` is not a `RecoveryAction` (`:48-56`). The gate would raise inside `_try_escalate` → swallowed as a watchdog error. | §4 — use the real `emit(domain, Severity.WARN, detection_signal=signal, recovery_action=RecoveryAction.NONE, outcome="suppressed_planned", details=…)` at **WARN** (survives the 500-entry ring + alert filters). |
| **TB-C3** | module-global flag; "single L4 process" (OQ-B2 = document) | Multi-worker **silently** defeats quiesce: apply arms in worker A, the watchdog in worker B has its own `_quiesce_until=0` → escalates → reboot, **no error/log**. `--workers 1` is enforced only by convention and the launch defs disagree — `main.py:10` forces 1 (app object), but `INSTALL.md:190` ships `uvicorn main:app` with **no `--workers`**. | §6.1 — **hard boot-time guard**: refuse to start a second watchdog-bearing worker; fix `INSTALL.md`; assert `--workers 1` in unit/Dockerfile. (File-backed deadline as the only multi-worker-safe alternative.) |
| **TB-C4** | "all escalation routes through `_try_escalate`" | The unit is `Type=notify WatchdogSec=30s Restart=always` (`infrastructure/color_node/systemd/janus-camera-page.service:7-12`). The `WATCHDOG=1` keepalive is an asyncio task (`events.py:34-40,124`); the **snapshot watchdog is also async** (`watchdogs.py:216,219`) and calls `ladder.escalate` (`:201`) → blocking 60 s subprocess **on the event loop** → starves the keepalive → systemd restarts L4. A restart path the gate cannot see. | §6.2 — ladder shell-outs AND the apply's quiesced restart **must run off the event loop** (`asyncio.to_thread`), so the keepalive survives. Add as a hard requirement + acceptance check. |
| **TB-C5** | no TTL ceiling (OQ-B1 deferred) | `quiesce(ttl_sec: float)` with no clamp: a config typo (`2000` vs `20`, ms/s confusion) blinds FDIR for 33 min. The "fail-safe deadline" becomes a fail-*open* knob. | §3.2 — **mandatory** `QUIESCE_TTL_CEILING_SEC` (120 s) hard cap + reject `ttl<=0`. "deadline ≤ ceiling" is an acceptance criterion. |
| **TB-C6** | `max()` re-arm; `unquiesce()` "deadline backstop remains" | `max()` accumulation walks the deadline forward every rollback re-arm → effective blind window = (n re-arms)×ttl, not ttl; `unquiesce()` can't shorten it. | §3.3 — **refcount/token per arm**: last owner out sets `_quiesce_until=0`; the ceiling is computed from the **first** arm, not refreshed per re-arm. |
| **TB-C7** | gate at `_try_escalate` makes arm/gate safe; `max()` on a bare global | The watchdog is a separate thread; `_quiesce_until = max(...)` is a non-atomic read-modify-write → lost update under two writers (apply + executor self-quiesce). | §3.4 — serialize **all** `_quiesce_until` reads + the arm write under the **existing `_escalation_lock`** (`watchdogs.py:63`, already taken by the gate at `:195`). |
| **TB-C8** | "short TTL contains the blast radius" (R-B4) | An escalation already **past** the gate when quiesce arms cannot be un-rung; at ladder level 4 that leaked escalation is a **physical reboot**, not a cheap retry. TTL bounds *future* suppression, not the in-flight action. | §6.4 — the apply **pre-empts the ladder**: refuse to issue the restart while ladder level ≥ `restart_pipeline` / an escalation is in-flight. Bound the leak to **at most one** and never at the reboot rung. |
| **TB-C9** | "single chokepoint" implies total SAFE/reboot coverage | `thermal._thermal_loop` (`thermal.py:73,94`) drives `system_mode.transition(SAFE)` + pipeline stop independently of `_try_escalate`; a color restart's CPU spike can cross thermal CRIT mid-apply. | §7.1 — thermal is **explicitly out of scope**: thermal SAFE is a *genuine* fault and must NOT be quiesced; the apply should check thermal headroom before arming. Correct the "single chokepoint" claim to "for the stream-staleness ladder only." |
| **TB-C10** | §2 row "watchdog exception → JANUS" + keeping JANUS armed | `janus_summary` is exception-safe — every failure returns a dict (`video_age_ms=None`) (`janus.py:202-264`), so a color restart hits the **PIPELINE** branch, never JANUS. Scope `{PIPELINE,SENSOR}` is correct, but it **depends on `janus_summary` never raising**. | §7.2 — keep `{PIPELINE,SENSOR}`; state the **`janus_summary` is total** invariant and test-pin it (a future refactor that lets it raise would silently break the scope). |

---

## 1. Purpose & Non-Goals

### 1.1 Purpose
A planned color encoder restart must not be read as a fault and escalate the ladder toward `reboot_node`. v2: the gate is necessary but not sufficient — §6 lists the four supporting guarantees that make the property actually hold.

### 1.2 Non-Goals
```
No B2 apply engine (Track B provides the quiesce primitive + the supporting guarantees).
No change to detection thresholds, the ladder, or reboot policy.
No suppression of UNPLANNED faults — quiesce is armed only around an explicit planned action,
  is time-bounded, domain-scoped, and CANNOT be left on.
No suppression of thermal SAFE (a genuine fault — §7.1).
No multi-worker support without a file-backed deadline (§6.1).
No Pi reboot.
```

---

## 2. Grounded reality

| Fact | Evidence |
|---|---|
| single **ladder** escalation chokepoint (stream-staleness only) | `watchdogs.py:187` `_try_escalate` → `:201` `ladder.escalate` |
| reusable lock the gate already takes | `watchdogs.py:63` `_escalation_lock`, taken at `:90,126,195,207` |
| grace is startup-only, never re-armed | `watchdogs.py:84-93` |
| executor restarts are **blocking subprocess** | `recovery_executor.py:156,164` via injected `_run_cmd` (`system.py:36-43`, timeouts 45/60/90 s) |
| **systemd watchdog** restart path: `Type=notify WatchdogSec=30s` + async keepalive | base unit `:7-12`; `events.py:34-40,124` |
| **snapshot watchdog is async**, escalate is blocking on the loop | `watchdogs.py:216,219,201` |
| **thermal** is a 2nd SAFE driver outside `_try_escalate` | `thermal.py:73,94` |
| `janus_summary` is exception-safe (→ PIPELINE, never JANUS) | `janus.py:202-264` |
| real event API + actions | `fdir_events.py:80` `emit(...)`; `:48-56` `RecoveryAction` (no suppressed) |
| `--workers` disagreement | `main.py:10` (forces 1) vs `INSTALL.md:190` (none) vs `Dockerfile:55`/`install.sh:706` (1) |
| planned restart call | `sensor_lifecycle.py:76` `_encoder_action("restart","rs-stream","color")` |

---

## 3. The quiesce mechanism (corrected)

### 3.1 State (module-level, single-process — guarded by §6.1)
```
_quiesce_until: float = 0.0          # monotonic deadline; 0 = not quiesced
_quiesce_domains: set[Domain]        # suppressed domains (default {PIPELINE, SENSOR})
_quiesce_reason: str = ""
_quiesce_arms: int = 0               # refcount (TB-C6)
```
`_is_quiesced(domain)` = `monotonic() < _quiesce_until and domain in _quiesce_domains`.

### 3.2 TTL ceiling — the deadline is the fail-safe ONLY if it is bounded (TB-C5)
```
QUIESCE_TTL_CEILING_SEC = 120        # hard module constant
arm: reject ttl_sec <= 0; effective = min(ttl_sec, QUIESCE_TTL_CEILING_SEC)
```
No caller can blind FDIR longer than the ceiling, regardless of a typo or a misread setting.

### 3.3 Refcount, not max() (TB-C6)
- `quiesce()` increments `_quiesce_arms`, sets `_quiesce_until` from the **first** arm (`monotonic()+effective`, not refreshed per re-arm), capped at the ceiling-from-first-arm.
- `unquiesce()` decrements; when `_quiesce_arms==0`, sets `_quiesce_until=0` (authoritative early clear).
- The deadline backstops a missing `unquiesce` (crash); the refcount prevents a re-arm chain from walking the window forward. Operator-visible blind time = **min(ceiling, T+margin)**, never n×ttl.

### 3.4 Serialize under the existing lock (TB-C7)
All `_quiesce_until`/`_quiesce_arms` reads and writes (arm, unquiesce, and `_is_quiesced` inside the gate) happen under **`_escalation_lock`** — the same lock `_try_escalate` already takes (`watchdogs.py:195`). This makes arm↔gate ordering well-defined and kills the non-atomic read-modify-write race.

---

## 4. The gate (real API — TB-C2)

Inside `_try_escalate(ladder, signal, domain)`, under `_escalation_lock`, before claiming the dedup window:
```
if _is_quiesced(domain):
    log.warning("FDIR escalation suppressed (planned: %s): %s [%s]", _quiesce_reason, signal, domain)
    emit(domain, Severity.WARN, detection_signal=signal,
         recovery_action=RecoveryAction.NONE, outcome="suppressed_planned",
         details={"reason": _quiesce_reason, "remaining_sec": round(_quiesce_until - monotonic(), 1)})
    metrics.fdir_suppressed_total.labels(domain=domain.value).inc()
    return False
```
**WARN**, not INFO — so it survives the 500-entry ring (`fdir_events.py:27`) and any alert keyed on WARN+. One gate, all four sources.

---

## 5. Arm / disarm API
```
quiesce(ttl_sec, reason, domains={PIPELINE,SENSOR}) -> None     # refcount++, ceiling-capped, under lock
unquiesce() -> None                                            # refcount--, clears at 0, under lock
@contextmanager fdir_quiesced(ttl_sec, reason, domains): quiesce(...); try: yield; finally: unquiesce()
```

---

## 6. The four guarantees that make the gate actually safe

### 6.1 Multi-worker guard (TB-C3) — MANDATORY
At `startup`, refuse to run a second watchdog-bearing worker: a boot-time flock/PID-sentinel under `/run/camera` that **hard-fails** if a second watchdog starts (so quiesce can never silently no-op in worker B). Fix `INSTALL.md:190` to add `--workers 1` with the comment "FDIR quiesce is process-local — MUST be 1". If multi-worker is ever required, the deadline must move to a **file-backed** store (flock + atomic write, the `mountpoint_allocator` pattern) so all workers share one deadline. Documentation alone is insufficient for a failure mode whose blast radius is a reboot.

### 6.2 Off-loop execution (TB-C4) — MANDATORY
Every ladder shell-out (`recovery_executor` restarts) AND the B2 apply's quiesced `_encoder_action` must run via `asyncio.to_thread`/an executor, never inline on the event loop — otherwise a 60 s blocking restart starves the `WATCHDOG=1` keepalive past `WatchdogSec=30s` and systemd restarts L4 (a path the gate can't suppress). Acceptance: a quiesced restart never blocks the loop > the keepalive interval.

### 6.3 Executor self-quiesce (TB-C1 / OQ-B3) — MANDATORY, fixes a live bug
`recovery_executor` wraps its **own** `restart_pipeline`/`restart_janus` in `fdir_quiesced(ttl≈restart_timeout+settle, {PIPELINE,SENSOR})`, re-armed each attempt. Without this, ordinary unplanned recovery self-amplifies toward `reboot_node` (the very outcome Track B exists to prevent) with no apply involved. This is the highest-value fix — the design has the primitive; it must be applied to the executor, not just the apply.

### 6.4 Ladder pre-emption (TB-C8)
The apply (and executor self-quiesce) **check ladder level under `_escalation_lock` before restarting**: if level ≥ `restart_pipeline` or an escalation is in-flight, defer/abort the restart rather than race it. This bounds the in-flight leak to **at most one** escalation and guarantees the leaked one is never the reboot rung.

---

## 7. Scope boundaries

### 7.1 Thermal is out of scope (TB-C9)
`thermal._thermal_loop` → `transition(SAFE)` is a **genuine** fault and must NOT be quiesced. The apply should check thermal headroom before arming a restart (don't start a CPU-heavy encode near CRIT). The "single chokepoint" claim is corrected: `_try_escalate` is the chokepoint for the **stream-staleness ladder**, not for SAFE-mode entry.

### 7.2 `janus_summary` is total — invariant (TB-C10)
Scope `{PIPELINE,SENSOR}` is correct **because** `janus_summary` never raises (always returns a dict → PIPELINE branch). This invariant is load-bearing for keeping JANUS armed; it must be test-pinned so a future refactor can't silently route a color restart into the un-suppressed JANUS domain.

---

## 8. Observability (TB-C2) — mandatory deliverables
- `camstack_fdir_quiesced{domain}` **Gauge** (1 while armed, 0 after) — lets an alert say "FDIR is deliberately muted right now."
- `camstack_fdir_suppressed_total{domain}` **Counter**.
- `/status` (`system.py:242-290`) gains a `quiesce` block: `{active, remaining_sec, reason, domains}` read from the state.
- Suppression events at **WARN**. A muted FDIR must never look identical to a healthy stream.

---

## 9. Adversarial risks (post-review)
```
R-B1  Quiesce never expires → MANDATORY monotonic deadline + ceiling (§3.2). [TB-C5]
R-B2  Over-broad scope → domain-scoped; JANUS armed; thermal out (§7). [TB-C9/C10]
R-B3  Re-arm walks the window forward → refcount, ceiling-from-first-arm (§3.3). [TB-C6]
R-B4  In-flight escalation leaks a reboot → ladder pre-emption bounds it to ≤1, never reboot rung (§6.4). [TB-C8]
R-B5  Multi-worker silent defeat → hard boot guard / file-backed (§6.1). [TB-C3]
R-B6  systemd WatchdogSec restart via loop starvation → off-loop execution (§6.2). [TB-C4]
R-B7  Executor self-amplification (live bug) → executor self-quiesce (§6.3). [TB-C1]
R-B8  Crashing/lost observability → real emit() at WARN + gauge/counter + /status (§4/§8). [TB-C2]
R-B9  Non-atomic arm/gate → serialize under _escalation_lock (§3.4). [TB-C7]
```

---

## 10. Acceptance + test plan
```
1. A planned color restart wrapped in fdir_quiesced() → ZERO ladder.escalate() for PIPELINE/SENSOR;
   JANUS NOT suppressed (R-B2).
2. EXECUTOR restart_pipeline/restart_janus → ZERO self-re-escalation during the restart window (R-B7) —
   the live-bug regression.
3. deadline ≤ QUIESCE_TTL_CEILING_SEC for any ttl, incl. a huge/typo'd value; ttl<=0 rejected (R-B1).
4. n overlapping re-arms → effective window ≤ ceiling, not n×ttl; last unquiesce clears (R-B3).
5. arm/gate under one lock → no lost update with two concurrent writers (R-B9).
6. a quiesced restart never blocks the event loop > keepalive interval (R-B6).
7. ladder level ≥ restart_pipeline at arm → restart deferred; leak ≤ 1, never reboot rung (R-B4).
8. boot guard: a 2nd watchdog-bearing worker hard-fails to start (R-B5).
9. observability: suppression emits WARN event + increments counter; /status shows quiesce active+remaining (R-B8).
10. janus_summary never raises (total) — pinned; thermal SAFE is NOT suppressed (R-B2).
11. quiesce changes NOTHING when not armed (default deadline 0).
```

---

## 11. Open questions
| OQ | Question | Default |
|---|---|---|
| OQ-B1 | ✅ resolved — TTL ceiling is mandatory (120 s, §3.2). | — |
| OQ-B2 | ✅ resolved — multi-worker is a hard boot guard, not a doc note (§6.1). | — |
| OQ-B3 | ✅ resolved — executor self-quiesce is mandatory (§6.3). | — |
| OQ-B4 | Gauge granularity — per-domain or single? | per-domain gauge + counter (§8). |
| OQ-B5 | Should the apply ABORT (vs defer) when thermal is near CRIT? | abort + surface "deferred: thermal headroom" — don't add load near CRIT (§7.1). |
| OQ-B6 | File-backed deadline now, or single-worker guard only? | guard only now (single-process is real today); file-backed is the documented path if L4 scales out. |

---

## 12. ADR summary (v2)
- **The gate alone is not enough.** v1's "one chokepoint, total coverage" was false: the executor self-amplifies (a live bug), multi-worker silently defeats it, a systemd `WatchdogSec` restart bypasses it, thermal is a separate SAFE path, and the observability call would crash. Safety = the gate **+** four guarantees (§6) + correct scope (§7) + real observability (§8).
- **The deadline is the fail-safe only if bounded** (ceiling) and **refcounted** (no re-arm growth).
- **Serialize arm/gate under the existing `_escalation_lock`** — no new lock, no non-atomic global.
- **Executor self-quiesce (OQ-B3) is the highest-value item** — it fixes a pre-existing escalation-to-reboot bug that needs no apply at all.
- **Multi-worker is a hard guard, not a comment**; **shell-outs run off the event loop**; **thermal stays a genuine fault**.

> Design-only, v2, corrected against an empirical adversarial review. No code, no FDIR change, no deploy. Implementation, when approved, is bigger than "one gate": it includes the multi-worker guard, off-loop execution, executor self-quiesce, and the observability surfaces — each independently testable.

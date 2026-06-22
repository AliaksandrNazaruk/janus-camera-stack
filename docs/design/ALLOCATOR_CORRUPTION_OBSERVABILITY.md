# Allocator corruption observability — Cycle 14A recon + design note

**Status:** RECON COMPLETE — awaiting GO on a gate decision. No production code yet.
**Scope:** OBSERVABILITY only. This cycle does **not** change the allocator's deliberate
fail-SAFE-to-empty read behavior (Cycle 1, fitness test line 643). It asks: *can a corrupt
or unreadable `mountpoint_allocator` silently degrade to "empty" and is that degraded state
visible to an operator anywhere?* Answer from code: **yes it degrades silently, and no
surface shows it.**

---

## 1. Schema & ownership

- **File:** `/var/lib/camera-fdir/sensor_allocations.json` (`DEFAULT_STATE_PATH`).
- **Owner:** `app/services/mountpoint_allocator.py` (307 LOC) — sole reader/writer.
- **Shape:** `{"version": 1, "allocations": {"<serial>:<sensor>": {mp_id, rtp_port, desired_active}}}`.
  Key = `_key(serial, sensor)`; value = `Allocation` dataclass (line 70), `desired_active: bool = False`.
- **Contract (docstring line 24):** `desired_active` is **"the source of truth for boot-time
  stream lifecycle"** — i.e. NOT merely a cache. The boot reconciler reads it to decide which
  streams to bring up. Records without the field load `desired_active=False` (safe default).

## 2. Missing-file behavior (legitimate cold-start)

- `get_allocation` (line 186): `if not state_path.exists(): return None` — **silent, no log**.
- `list_allocations` (line 292): `if not state_path.exists(): return {}` — **silent, no log**.
- This is the legitimate cold-start state. **Problem: it is byte-for-byte indistinguishable
  from a corrupt allocator** (see §3) — both present as "no allocations / no desired streams."

## 3. Corrupt-file behavior — READ vs WRITE asymmetry (the core defect)

**Reads silently degrade to empty; writes crash; nothing is quarantined.**

- **READ path, invalid-JSON or IO error:**
  - `list_allocations` (line 297): `except (json.JSONDecodeError, OSError): return {}` —
    corrupt **OR** io_error → `{}`, **with no log at all** (worse than get_allocation).
  - `get_allocation` (line 191): same except → `None`, but **does** `log.warning(...)`.
  - `_alloc_map` (line 97): coerces any non-dict shape (`null` / `"garbage"` / non-dict
    state) → `{}`. Read-side fail-safe, never raises.
- **WRITE path, invalid JSON:** `_flock_state` (line 140) does
  `state = json.loads(raw) if raw else {}` **NOT wrapped in try/except**. A truncated/garbage
  file → `json.JSONDecodeError` **propagates** and **crashes** `allocate` / `migrate_color_key`
  / `set_desired_active` / `free` (all 5 writers funnel through `_flock_state`: lines 120, 207,
  234, 267, 281). Non-dict *shapes* (valid JSON, wrong type) are coerced & silently reset to
  `{}` (lines 143, 153) — **data loss with only a log.error, no quarantine**.
- **No quarantine.** The binding store quarantines corrupt files to `*.corrupt.*` and exposes
  `store_corruption_status()`. The allocator has **neither** — corruption is invisible on read
  and explosive on write.

| File state | `list_allocations` / `list_desired_active` | `get_allocation` | next write (`allocate`…) |
|---|---|---|---|
| missing | `{}` (silent) | `None` (silent) | creates fresh file |
| valid JSON, non-dict shape (`null`,`"garbage"`) | `{}` (silent) | `None` (silent) | resets→`{}`, `log.error`, **data loss** |
| invalid JSON (truncated/garbage bytes) | `{}` (**silent, no log**) | `None` (`log.warning`) | **CRASH — uncaught JSONDecodeError** |
| IO error (perms, disk) | `{}` (silent, no log) | `None` (`log.warning`) | crash/raise from open |

## 4. The `desired_active` disappearance risk (production impact)

A corrupt/unreadable allocator → `list_allocations() == {}` → `list_desired_active() == {}`
(line 306, it is just the `desired_active` subset of the possibly-empty dict). Consequences,
**all silent, all indistinguishable from a legitimately-empty allocator:**

- **Boot reconciler** `app/tools/sensor_reconcile.py:70` reads `list_desired_active` to bring
  up streams at boot → brings up **ZERO streams**. The documented source-of-truth for boot
  stream lifecycle reads as "nothing is desired."
- **Runtime-config `/effective`** `runtime_config_builder.py:111` → empty allocations view.
- **Local binding projection** `stream_binding_store/bindings.py:70,89` → local bindings vanish.
- **Devices listing** `routes/devices.py:98` → empty device/allocation list.
- **Dashboard** `ui_viewmodel.py` (imports allocator, line 21) → reads bindings (now empty),
  computes `Streams: degraded if live < total` (line 253). With corrupt→`0 total`, it renders
  **"0 streams, healthy"** — the degraded heuristic can't fire when total collapses to zero.

The in-code comment at `mountpoint_allocator.py:151` already names this exact hazard
("a corrupt allocations map must not be read as 'nothing is desired'") — but the guard it
describes only protects the **write** path from persisting `null`; the **read** path still
hands `{}` to every consumer with no signal that it was corruption rather than emptiness.

## 5. Operator visibility map — where corruption shows today

| Surface | What it checks | Surfaces allocator corruption? |
|---|---|---|
| `/readyz` (system.py:60) | prod-config, **binding-store corruption** (`store_corruption_status`→503), janus reachable | **NO** — binding store only |
| `/healthz` (system.py:99) | janus, stream, SAFE mode | **NO** |
| `/health/stream` (system.py:131) | media-level janus summary | **NO** |
| `/system/status` (system.py:251) | health, recovery ladder, service, settings | **NO** |
| `/api/v1/devices` (devices.py:98) | lists `list_allocations()` (empty on corrupt) | **NO** — shows empty |
| `ui_viewmodel` dashboard | bindings → `live<total` heuristic | **NO** — corrupt→0 total→"healthy" |

**Net: zero surfaces distinguish a corrupt allocator from an empty one.** The single place
corruption logs anything is `get_allocation` (a `warning`); the hot path `list_allocations`
is fully silent. `/readyz` already has the *exact precedent* (binding-store
`store_corruption_status`) — the allocator just has no equivalent.

## 6. Existing test coverage

- `tests/test_sensor_keying.py:83-110` **PINS the fail-safe-to-empty behavior** (must
  preserve): `list_allocations(corrupt)=={}`, `list_desired_active=={}`, `get_allocation=None`,
  non-dict resets, `null` doesn't persist, "empty doesn't erase via read."
- `tests/test_streams_dashboard.py:73` patches `list_allocations→{}` (empty-dashboard render).
- **Gaps:** no test asserts corruption is *visible* on any health/readyz/status surface; no
  test for the write-path `JSONDecodeError` crash (§3); no `tests/test_mountpoint_allocator.py`
  exists (allocator tests are scattered across `test_sensor_keying` / `test_allocator_desired_state`).

## 7. Minimal proposed fix — **B + minimal C**

Mirror the binding store's proven, non-invasive pattern. **No rewrite, no fail-closed.**

1. **B — add a non-raising probe** `allocator_corruption_status(state_path=DEFAULT_STATE_PATH)
   -> dict`, modeled on `stream_binding_store/state_file.py:121` `store_corruption_status`.
   Returns e.g. `{"allocator_state": "ok"|"missing"|"corrupt"|"io_error", "detail"?: str}`.
   Crucially it **distinguishes missing (cold-start, fine) from corrupt (degraded) from
   io_error** — the distinction the read path throws away. It does an explicit
   `json.load` + shape check in a try/except and classifies; it does **not** change
   `list_allocations`/`get_allocation`/`list_desired_active` at all.
2. **minimal C — surface it in ONE read-model.** Recommended: `/readyz`, beside the existing
   binding-store check, as a **non-fatal `allocator_state` field** (stays 200 / `ok:true`).
   See §9 for why non-fatal, not 503.
3. **Tests:** corrupt file → `allocator_corruption_status` reports `corrupt`; missing → `ok`/
   `missing` (NOT corrupt); the surface shows it; the §6 fail-safe pins still pass unchanged.

This is additive and behavior-preserving for every existing caller. ~1 helper + 1 surface
field + tests. No touch to the 5 hard-constrained read/write functions' semantics.

## 8. Alternatives considered & rejected

- **A — do nothing / just add a log to `list_allocations`.** Insufficient: a log line in a
  hot read path is noisy and still invisible to operators/dashboards. Doesn't answer "is it
  degraded right now?" Rejected.
- **D — make the allocator fail-CLOSED (reads raise / readiness 503 on corrupt).** Violates
  hard constraints #3 (no rewrite) and #5 (no `desired_active` semantics change), and undoes
  Cycle 1's deliberate fail-SAFE: a corrupt allocator failing closed would tear down or refuse
  to reconcile **live encoder streams** — the precise harm Cycle 1 avoided. Rejected.
- **Quarantine-on-corrupt (mirror binding store `*.corrupt.*`) + wrap write-path
  `json.loads`.** Tempting and fixes the §3 write-crash, but it is a **write-path behavior
  change** (currently crashes; would reset/quarantine instead) → belongs behind its own gate,
  not this observability cut. Deferred (see §10, D4).

## 9. Red lines (honored)

- No rewrite of `mountpoint_allocator` (constraint #3); helper is purely additive.
- No change to `desired_active` semantics (#5) or read fail-safe-to-empty (Cycle 1).
- **No fail-closed change to the allocator** — readiness must NOT 503 on allocator corruption,
  because the streams are still running (fail-safe). A corrupt allocator is a *degraded/observe*
  state, not a *stop-serving* state. (Contrast: binding store corrupt = topology untrustworthy
  = correctly 503.)
- No health-response shape change before characterization tests (#6) — add `allocator_state`
  only after a char test pins the current `/readyz` body.
- No broad import guard (#7); no decorative guard (#8); no touch to binding-store semantics (#4).

## 10. Gate decisions (need GO before any code)

- **D1 — scope.** (A) **B + minimal C** [recommended]: add `allocator_corruption_status`
  helper + surface in ONE read-model + tests. (B) B only: helper + tests, no surface yet.
  (C) B + broad C: surface in `/readyz` AND `/healthz` AND `ui_viewmodel`. (D) defer.
- **D2 — surface location & severity.** (a) **`/readyz` as a non-fatal `allocator_state`
  field, stays 200** [recommended — preserves fail-safe]. (b) `/readyz` → 503 like the
  binding store [rejected-lean: breaks fail-safe, pulls a healthy-streaming pod from rotation].
  (c) `/healthz` `HealthResponse` degraded field. (d) `/system/status` admin snapshot only.
- **D3 — fitness guard?** Only with teeth. (a) guard asserting the allocator read helpers
  (`list_allocations`/`get_allocation`/`list_desired_active`) **never raise** on corrupt input
  (locks the fail-safe invariant). (b) guard asserting `allocator_corruption_status` exists &
  is wired into the chosen surface. (c) no guard (avoid decorative). 
- **D4 — write-path `JSONDecodeError` crash (§3, line 140).** Fix in THIS cycle (wrap +
  reset/quarantine) or split to its own gated cycle? [lean: **split** — it's a behavior change;
  this cycle is observability]. Recon flags it either way.

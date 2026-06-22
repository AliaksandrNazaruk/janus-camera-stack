# OPERATION_LIFECYCLE_HARDENING — recon + design note (gated, no code yet)

Builds on **Phase 11 / D5** ([OPERATION_JOURNAL.md](OPERATION_JOURNAL.md)): the durable
`operation_journal` + `node_operation_runner` already exist and reap restart-orphaned ops. This
note closes the three *operator-visibility & durability* gaps the release review surfaced — it does
**not** change provision/rotate/activate behavior.

> Filename note: tracked loosely as "JANUS_RESTART_OPERATION" in the campaign backlog; renamed to
> `OPERATION_LIFECYCLE_HARDENING` because the scope is the operation lifecycle (id surfacing, read
> API, journal corruption), not a Janus restart. Sits beside `OPERATION_JOURNAL.md`.

Companions: [OPERATION_JOURNAL.md](OPERATION_JOURNAL.md) ·
[../KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md) ·
[../ARCHITECTURE_CURRENT.md](../ARCHITECTURE_CURRENT.md)

---

## Recon — what exists today (verified, with file:line)

The durable substrate is already in place; what's missing is **surfacing** it and **failing safe**
when its file is corrupt.

| Piece | Where | State |
|---|---|---|
| Journal (flock'd, atomic, pruned) | `app/services/operation_journal.py` | ✅ `begin/finish/running_for_node/all_running/list_recent` |
| Runner (daemon thread + journal) | `app/services/node_operation_runner.py` | ✅ `run()` returns `op_id`; `reap_orphans()` |
| Startup reaper | `app/core/events.py:145` | ✅ `node_operation_runner.reap_orphans()` |
| Route spawn wrapper | `app/routes/stream_bindings.py:141` | ⚠️ `_spawn_node_op(...) -> None` **discards** the op_id |
| 3 async ops | provision `:355` · rotate-token `:371` · activate `:482` | ⚠️ responses are `{started: True, …}` — **no operation_id** |
| Operations read API | — | ❌ none; `list_recent`/`all_running` are unexposed |
| Journal corruption handling | `operation_journal.py:44-47` (`_load`) | ❌ `except (OSError, JSONDecodeError): return {…empty…}` — **silent-empty reset** |

Journal record shape (actual, from `begin()` `:77-85`):
`{operation_id, op_type, node_id, status, started_at, finished_at, last_error}` with
`status ∈ {running, succeeded, failed, interrupted}`. (The old design doc mentioned a `detail`
field — it never shipped; the note here uses the real fields.)

### The three gaps

1. **op_id is generated but never reaches the client.** `node_operation_runner.run()` returns the
   uuid4 op_id (`:38`), but `_spawn_node_op` (`:141-148`) is typed `-> None` and drops it. So a
   client that POSTs `/provision` gets `{started: True}` and can only poll the *node* (`provision_state`)
   — it cannot correlate to *its* operation, see when it finished, or read its `last_error`.

2. **No way to read operations.** The journal records everything (running + recent history), but
   there is no `GET …/operations`. An operator can't answer "is node X busy?", "did op `abc123`
   succeed?", or "what interrupted ops did the last restart reap?" without reading the JSON on disk.

3. **A corrupt journal silently becomes empty.** `_load()` swallows `JSONDecodeError` and returns
   `{"operations": []}`. Consequences: (a) **the per-node running-guard evaporates** → two concurrent
   long ops on one node could both pass `begin()`; (b) all interrupted/in-flight history is lost
   silently; (c) the corruption is never surfaced. This is the same "silent empty-reset" anti-pattern
   the **store already fixed under H-02** (`StoreCorruptionError` + quarantine + app-level 503); the
   journal was the last remaining instance — and here it's worse because the guard is a safety mechanism.

---

## Design (minimal, additive — mirrors the journal's existing style)

### H1 — surface `operation_id` (additive, behavior-preserving)

- `_spawn_node_op(...) -> str`: return `node_operation_runner.run(...)`'s op_id (409 mapping unchanged).
- The 3 handlers capture it and **add** `"operation_id": op_id` to their existing response dict.
  Keep `started` and `poll` exactly as-is (additive → existing clients that ignore extra keys are
  unaffected).
- Optionally also add `"operation": "GET /api/v1/admin/operations/{operation_id}"` as a second poll
  hint (does **not** replace the node/binding `poll` string → no oracle break).

### H2 — `GET /api/v1/admin/operations[/{operation_id}]` (read-only)

Thin route → journal service (the `GET /nodes:278` pattern: admin-gated by the router, **no** `_RL`).

- `GET /api/v1/admin/operations?limit=N` → `{"operations": [record, …]}` via `journal.list_recent(N)`
  (already newest-first). MVP limit default 50, same as the journal.
- `GET /api/v1/admin/operations/{operation_id}` → the single record, **404** if unknown. Needs one
  tiny additive journal read `get(operation_id) -> dict | None` (no new state, no write path).
- Records are returned **as stored** (epoch-int `started_at`/`finished_at`); optionally add a derived
  `duration_s` for terminal ops. No new fields persisted.

### H3 — corrupt journal → **quarantine + fail-closed** (no silent empty)

Replace the blanket `except (OSError, JSONDecodeError)` in `_load` with corruption-aware handling.
The key insight: *fail-closed means different things per caller*, so `_load` should signal corruption
(raise a typed `JournalCorrupt`) and let each entry point decide:

- **Distinguish the two errors.** `JSONDecodeError` = definitive corruption → quarantine. `OSError`
  (transient unreadable) → propagate, do **not** quarantine (don't destroy a file over a transient
  read error).
- **Quarantine** = atomically rename the bad file to `operations.json.corrupt-<started_at-style ts>`
  (ts passed in, not `time.time()` inside a pure-load path) so the evidence is preserved and the next
  write starts clean.
- **Per-caller fail-closed policy:**
  - `begin()` (about to start a long SSH/apt op) → **refuse**: quarantine, then raise so the route
    returns **503** "operation journal unavailable (corrupt, quarantined) — retry". Safer to not
    start an untrackable long op than to start one with no guard.
  - reads (`list_recent`/`get`/`running_for_node`/`all_running`) → surface **503** from the
    `/operations` endpoint (and a clear log) rather than lie with `[]`.
  - `reap_orphans()` at **startup** → quarantine + log **CRITICAL** + continue with a fresh empty
    journal. (After quarantine there is genuinely nothing left to reap; blocking boot on a corrupt
    history file would be worse. The CRITICAL log + the quarantined file are the audit trail.)

---

## Decisions (resolved 2026-06-21, user-gated)

- **D-H1 — response shape: ADDITIVE + 2nd poll hint.** Add `operation_id`; keep `started` and the
  existing node/binding `poll` string byte-for-byte; also add
  `"operation": "GET /api/v1/admin/operations/{operation_id}"` as a *second* hint. Zero oracle risk.
- **D-H2 — read API scope: MVP.** `GET /operations?limit=N` (newest-first) + `GET /operations/{id}`
  (404 if unknown). Records returned **verbatim** (epoch ints). No `?node_id`/`?status` filters and
  no derived `duration_s` in the first cut — revisit if a concrete UI need appears.
- **D-H3 — corruption policy: PER-CALLER SPLIT.** `_load` raises a typed `JournalCorrupt` on
  `JSONDecodeError` (definitive corruption) and quarantines (`operations.json.corrupt-<ts>`, ts passed
  in); transient `OSError` propagates and does **not** quarantine. Callers: `begin()` → refuse, route
  returns **503**; reads (`get`/`list_recent`/`running_for_node`/`all_running`) → **503** at the
  `/operations` endpoint; `reap_orphans()` at startup → quarantine + **CRITICAL** log + continue with
  a fresh empty journal. Quarantine files are kept (no auto-prune in this cut).
- **D-H4 — store parallel (H-02): N/A — ALREADY DONE (recon correction 2026-06-21).** The initial
  assumption that the store shared the silent-empty pattern was **wrong**. Recon found
  `stream_binding_store` already fails closed: `StoreCorruptionError` + idempotent quarantine
  (`<path>.corrupt.<ts>`, original preserved) + an app-level 503 handler (`core/app.py`) + the
  reconciler refusing to fabricate an empty report (R5, so a corrupt store never tears down the
  fleet) + a `readyz`/`store_corruption_status` probe + `tests/test_store_corruption.py` (10 tests).
  Startup reads are all `try/except`-wrapped → a corrupt store boots degraded (503), never crashes
  or wipes. So H3 brought the **journal** up to the store's existing standard; no store work remains.

## Implementation order (three focused commits, suite green between)

1. **H1** — `_spawn_node_op -> str`; 3 handlers add `operation_id` + the 2nd poll hint. Route-only.
2. **H2** — `journal.get(operation_id)`; `GET /operations` + `GET /operations/{id}` (thin, admin-gated).
3. **H3** — corruption-aware `_load` + `JournalCorrupt` + per-caller fail-closed + the reads' 503 map.

---

## Acceptance (when implemented)

- POST provision/rotate/activate responses include `operation_id`; `started`/`poll` unchanged.
- `GET /operations` lists recent ops; `GET /operations/{id}` returns one or 404; both admin-gated.
- A corrupt `operations.json`: `begin` → 503 (op not started, file quarantined), reads → 503,
  startup → quarantined + CRITICAL log + clean continue; a `*.corrupt-*` file exists; **the
  per-node running-guard is never silently bypassed**.
- provision/rotate/activate behavior, durable status fields, and the existing poll model unchanged.

## Test plan

- Unit (tmp journal): `get()` hit/miss; `list_recent` shape; corrupt-file → `JournalCorrupt` +
  quarantine file created; OSError → propagates, **no** quarantine.
- Route: `/operations` 200 list + `/operations/{id}` 200/404; all require admin (401 without token).
- Hardening: write garbage to the journal → `begin` returns 503 and **does not** spawn a thread /
  start the op; reads return 503; a restart-with-corrupt-journal boots and logs CRITICAL.
- The 3 async ops still 200 (now with `operation_id`) / 409-on-concurrent, exactly as before.

## Red lines

No change to provision/rotate/activate *behavior* (node_provisioner, firewall_sync, SSH/apt
untouched); no change to the daemon-thread model or the 409 guard semantics (only made fail-safe);
no queue/asyncio rewrite; the store split / reconcile / firewall are out of scope. New surface is
additive: response key, two GET routes, one journal read fn, corruption-aware `_load`.

## Implemented (2026-06-21) ✅

All three landed behavior-preserving; full non-e2e suite green except the pre-existing
`TestColorView::test_template_render` (stale `get_settings` patch target; fails identically on HEAD).

- **H1** (`1985e24`) — `_spawn_node_op -> str`; provision/rotate/activate return `operation_id` + the
  `operation` poll hint. Additive — `started`/`poll` unchanged.
- **H2** (`8b7a9f5`) — `journal.get()`; `GET /operations[?limit]` + `GET /operations/{id}` (404),
  admin-gated; `_operations_path()` centralises the journal location.
- **H3** (this commit) — `_load` quarantines a corrupt journal (`operations.json.corrupt-<ts>`) and
  raises `JournalCorrupt`; **begin → 503** (op not started untracked), **reads → 503**, **startup
  reap → quarantine + CRITICAL log + continue empty** (self-heals to an empty journal). Transient
  `OSError` propagates un-quarantined; non-UTF-8 bytes count as corruption.

Refinements vs the plan above: `time.time()` is used directly for the quarantine timestamp (the
"ts passed in" caution was a workflow-script rule, irrelevant in production code); finish-time
resilience lives in the **runner** (separates running `fn` from recording the outcome) so the journal
primitives stay honest and simply raise on corruption.

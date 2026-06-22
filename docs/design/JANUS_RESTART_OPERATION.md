# JANUS_RESTART_OPERATION — Cycle 13A recon (GATED, no code yet)

Question: should `POST /janus/restart` become a bounded/tracked admin operation instead of a synchronous
blocking endpoint? NOT a request to build a generic `AdminOperationRunner` (Cycle 8 rejected that) — the
narrow question is whether a Janus restart can be modeled as a small, bounded, observable operation using
the EXISTING `operation_journal` primitives, without disturbing the stabilized NAT/TURN boundary. The
recon's headline: it's a **real-but-modest** observability gap, and the fix MUST be **additive/opt-in** —
`/janus/restart` has a machine client that requires the current synchronous `200 = done` semantics. No
code until GO.

## 1. Current behavior inventory (verified 2026-06-21)
`app/routes/janus.py:232`:
```python
@router.post("/janus/restart", ..., dependencies=[ADMIN_DEPENDENCY, ADMIN_RATE_LIMIT])
def _restart_janus() -> None:                 # SYNC def → FastAPI threadpool (NOT the event loop)
    try:
        restart_janus()                       # nat_config.restart_janus
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```
- **Sync** (a plain `def` — runs in the anyio threadpool, so it does NOT block the event loop; it holds
  one threadpool worker).
- **Auth/limit:** `require_admin` + `require_admin_rate_limit` (unchanged constraint).
- **Response:** success → `200` with an **empty body** (`None`). Failure → `500 {"detail": str}`.
- **Errors mapped:** only `RuntimeError` (which `JanusAdminError` subclasses). Nothing else is mapped.

## 2. Call graph + timeout/blocking analysis
```
POST /janus/restart  → _restart_janus()  → nat_config.restart_janus()
    → subprocess.run(["sudo","/usr/local/bin/janus-admin","restart"], timeout=120)
       rc != 0           → JanusAdminError(exit_code)         → route 500
       Timeout/NotFound  → JanusAdminError (no exit_code)     → route 500   (Cycle 7B G3 mapping)
```
- **Inherited timeout = 120 s** (the subprocess timeout). The admin HTTP request can therefore hold open
  up to ~120 s.
- **Blocking severity = MODEST.** It's a sync `def`, so the *event loop* stays responsive (other requests
  served by other threadpool workers) — this is NOT a worker-starvation outage. The real harm: the
  **client** waits up to 120 s, which commonly exceeds proxy/browser timeouts (30–60 s) → the client gets
  a 504 while the restart continues server-side → **the operator can't confirm the outcome.** A Janus
  restart is rare (manual, or `config_apply`), and recoverable (`GET /janus/healthz`), so this is an
  observability/UX gap, not a critical bug.

`restart_janus()` has **3 callers** — `/janus/restart` (this), `application/config_apply.apply()`
(the `/config/apply` endpoint), and `application/janus_nat/update_nat_config` (the Cycle-7 NAT op). Only
the `/janus/restart` route is in scope; the other two call `restart_janus()` synchronously inside their
own operations and MUST NOT change.

## 3. Existing operation primitives (the reuse inventory)
- **`operation_journal`** — `begin(node_id, op_type, operation_id)` → `running` (raises `OperationConflict`
  if a `running` op already exists *for that node_id*); `finish(id, status, last_error)` →
  `succeeded`/`failed`/`interrupted`; `running_for_node`, `all_running`, `list_recent(limit)`, `get(id)`;
  `JournalCorrupt` fail-closed (Cycle 1). Journal file = `operations.json` beside the binding store.
- **`node_operation_runner.run(node_id, op_type, fn, *args, ops_path, **kwargs) -> op_id`** — begins the
  journal, runs `fn` in a **daemon thread** (returns the uuid `op_id` immediately, non-blocking), records
  `succeeded`/`failed`. `reap_orphans()` (run at startup) marks any still-`running` op `interrupted`.
- **`OperationStatus`** (`app/application/operations.py`) — canonical `pending`/`running`/`succeeded`/
  `failed` + `canonical_status()` (the journal's `running`/`succeeded`/`failed`/`interrupted` all map).
- **Read endpoints** — `GET /api/v1/admin/operations` + `/operations/{id}` (in
  `routes/stream_bindings/operations.py`) read the journal. Titled "node operations" but list ALL records.

## 4. Can Janus restart reuse the journal without the full node model?
**Yes, cleanly.** `node_id` is just a string conflict-key — it is NOT validated against the node store. So:
- `op_type = "janus_restart"`, `node_id = "local_janus"` (synthetic scope), `operation_id = uuid4`.
- `begin(...)` raises `OperationConflict` if a `janus_restart` is already `running` → **one-at-a-time for
  free** (route maps `OperationConflict` → `409`, exactly as the node routes do via `_spawn_node_op`).
- worker `fn = restart_janus`; success → `succeeded`, exception → `failed` (+ `last_error`).
- startup `reap_orphans()` already marks a `running` janus op `interrupted` if L4 restarted mid-restart.
- the existing `/operations` + `/operations/{id}` + `OperationStatus` surface it with ZERO new read code.
**Reusing `node_operation_runner.run` is reuse, not replacement** (constraint #5 satisfied). The only
wrinkle: a `janus_restart` record then appears in the "node operations" list — a naming/scope nit, not a
correctness issue (it IS a recent admin operation). Decide at D3 whether to relabel or filter.

## 5. Compatibility risks (the decisive evidence)
- **`/janus/restart` has a MACHINE client.** `nat_config.restart_depth_camera_janus()` does
  `httpx.post(http://<depth>:<port>/janus/restart, timeout=10); if response.status_code != 200: raise`.
  It REQUIRES the synchronous `200 = restart done` semantics. **Changing `/janus/restart`'s default to
  `202 Accepted` would break the depth-node restart** — which is the Cycle-7 NAT update's depth stage —
  i.e. it would indirectly change NAT behavior (against the spirit of constraints #1–#3). (Note: that
  client already has a 10 s timeout vs the 120 s server restart, so it frequently times out today — but
  Cycle-7 made the depth stage best-effort → warning, so a timeout is non-fatal. A 202 would NOT be a
  timeout; it would be a wrong-status FAILURE.)
- **Tests assert specific codes:** `test_security.py` (×6) posts `/janus/restart` and asserts `403` /
  `!= 403` (auth/limit); `test_janus_routes.test_restart_ok` patches `app.routes.janus.restart_janus` and
  asserts `403` (no token). A response-shape change must be characterized first (constraint #8).
- **Auth + rate-limit** must remain on whatever path serves restarts (constraint unchanged).
- `restart_janus()`'s other 2 callers (config_apply, NAT op) must be untouched.

## 6. Proposed minimal design (if GO) — ADDITIVE, opt-in
Keep `/janus/restart` **synchronous by default** (the machine client + tests rely on it). Add a tracked
path as an opt-in:
- a worker that calls `node_operation_runner.run("local_janus", "janus_restart", restart_janus,
  ops_path=<operations.json>)`; one-at-a-time via `OperationConflict` → `409`.
- the tracked response is `202 {operation_id, status_url: "/api/v1/admin/operations/<id>",
  operation_status: "running"}`; the operator polls the EXISTING `/operations/{id}` (canonical
  `OperationStatus`) for `succeeded`/`failed`/`interrupted`.
- exposure (D2): EITHER a new `POST /janus/restart-tracked` (clean separation) OR a `tracked=true` body
  param on `/janus/restart` (most surgical — default `false` keeps the sync machine-client path byte-for-
  byte). The depth peer + existing clients never pass `tracked`, so they are unaffected.

## 7. Alternatives rejected
- **B-as-stated (change `/janus/restart` itself to 202 async).** REJECTED — breaks the
  `restart_depth_camera_janus` machine client (200-check) and the security/route tests; touches NAT
  behavior indirectly.
- **C (track internally but keep the API synchronous).** Weak — you still block the client up to 120 s
  (the core problem unsolved); you gain only a journal record. Not worth the code.
- **A generic `AdminOperationRunner` / command bus.** REJECTED — Cycle 8's conclusion stands; the journal
  + `node_operation_runner` already provide exactly the bounded primitive needed.
- **A candidate guard "the `/janus/restart` route must not call `restart_janus` directly."** REJECTED —
  the kept sync default legitimately calls it; such a guard would fail a correct endpoint. No guard.

## 8. Red lines
No change to `/janus/nat`, `NatUpdateResult`, or the NAT status-sidecar schema. No generic command
bus/workflow/BaseUseCase. Don't replace `node_operation_runner`. Don't weaken a guard. Don't touch
FDIR/recovery. No HTTP-behavior change before characterization tests. **`/janus/restart` stays
synchronous by default** (machine client). The tracked path is additive, reuses existing primitives, and
surfaces through the existing `/operations` read endpoints.

## 9. Gate decisions (GO before any code)
- **D1 — which option?** **(B-additive)** add an opt-in tracked Janus-restart operation [LEAN] /
  **(A)** keep sync, only enrich the response shape (structured body + `exit_code`, no async) /
  **(D)** document as acceptable (modest severity; recoverable via healthz). The recon supports a real
  but modest gap → (B-additive) if you want it solved properly, (A) for the smallest surface, (D) if not
  worth a cycle.
- **D2 — exposure (if B):** new endpoint `POST /janus/restart-tracked` vs `tracked=true` body param on
  `/janus/restart`. Lean: `tracked=true` (most surgical; sync default unchanged for the machine client).
- **D3 — operation scope/label:** reuse the shared `operations.json` + `/operations` (a `janus_restart`
  record appears in the "node operations" list) vs a janus-scoped view. Lean: reuse as-is; optionally
  retitle the `/operations` summary to "admin operations".
- **D4 — orchestration seam:** thin route → a small `application/janus_restart` use-case → 
  `node_operation_runner` (matches the layering) vs route → `node_operation_runner` directly. Lean: a tiny
  use-case (keeps the route thin, consistent with `application/janus_nat`).

## Status — DONE (2026-06-21), scope (B-additive)
Decisions: **D1=(B-additive)** opt-in tracked restart; **D2 = a NEW endpoint** `POST /janus/restart-tracked`
(NOT a `tracked=true` body param — the machine client + 6 security tests POST `/janus/restart` with NO
body, and adding a `Body` param risks a 422 on a body-less POST; a separate route leaves `/janus/restart`
byte-for-byte unchanged); **D3** reuse the shared `operations.json` + the existing `/operations` read
endpoints; **D4** a thin route → a small `application/janus_restart` use-case → `node_operation_runner`.
- `app/application/janus_restart.py` (NEW, FastAPI-free): `start_tracked_restart()` →
  `node_operation_runner.run("local_janus", "janus_restart", nat_config.restart_janus)`.
- `app/routes/janus.py`: NEW `POST /janus/restart-tracked` → `202 {operation_id, operation_status:
  "running", status_url}`; `OperationConflict` → `409`. The sync `POST /janus/restart` is UNCHANGED
  (a clarifying comment added). Reuses the existing `/operations/{id}` for polling.
- Tests: 2 char tests pin the sync `/janus/restart` (200 / 500{detail}) as a regression guard; a new
  `tests/test_janus_restart_operation.py` covers the route (202 / 409 / admin-gated), the use-case wiring
  (correct scope/op_type/fn), and the real journal integration (success→`succeeded`, failure→`failed`+
  last_error, one-at-a-time→`OperationConflict`).
- **NO new fitness guard** — the candidate ("`/janus/restart` must not call `restart_janus` directly")
  would fail the deliberately-kept sync endpoint. Full non-e2e suite green; zero new lint debt; 25 guards.

**Result:** operators get a non-blocking, observable Janus restart (`202` + poll `/operations/{id}`,
one-at-a-time, journaled, reaped→interrupted on boot) WITHOUT touching the sync `/janus/restart` that the
depth-peer machine client + the NAT op depend on. No generic runner, no NAT change.

## Recommendation
**This IS a real production-risk, but modest, and the next code-cycle candidate only if you want the
observability win.** If taken, the minimal safe implementation is **(B-additive): an opt-in tracked
restart reusing `node_operation_runner` + `operation_journal` (synthetic `local_janus` scope) returning
`202 + operation_id`, polled via the existing `/operations/{id}`, with `/janus/restart` left synchronous
for the depth-peer machine client.** No generic runner, no NAT changes, no guard. If the appetite is even
smaller, **(A)** (enrich the sync response shape) captures part of the value at near-zero risk.

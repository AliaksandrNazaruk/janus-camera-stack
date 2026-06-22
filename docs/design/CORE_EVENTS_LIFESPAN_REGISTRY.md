# CORE_EVENTS_LIFESPAN_REGISTRY — Phase 5 / audit-priority #5 recon + plan (GATED, no code yet)

Part of [STRICT_ARCHITECTURE_HARDENING.md](STRICT_ARCHITECTURE_HARDENING.md). Converts
`app/core/events.py` from the **deprecated `@app.on_event("startup"/"shutdown")`** decorators to a
modern **`lifespan` async context manager with a task registry**, so the long-lived background loops
are tracked and cancelled on shutdown instead of leaked. Behavior-preserving. No code until GO.

## Recon — what events.py does today (verified 2026-06-21)
`register_event_handlers(app)` (called once from `core/app.py:113`) registers two handlers via the
deprecated `@app.on_event` API:

- **`_startup()`** — a long, order-sensitive sequence: `mode_enforcer.register` → `recover_on_boot`
  (best-effort) → sensor-plugin load → `camera_info` metric → `start_janus_watchdog` +
  `await start_snapshot_watchdog` → `migrate_remote_binding_ids` → `node_operation_runner.reap_orphans`
  → spawn `_reconcile_janus_bg` (one-shot `create_task`, runs `reconcile_janus` on a worker thread so a
  slow Janus never blocks READY) → `start_remote_stream_monitor` → `start_thermal_monitor` →
  `janus_proxy.start_client` + `relay_proxy.start_client` → (color only) `depth_camera_proxy.start_client`
  → **`_sd_notify("READY=1")`** → spawn `_watchdog_loop`, `_memory_gauge_loop`, `_mux_fps_scraper`.
- **`_shutdown()`** — `watchdogs.stop_all` → `remote_stream_monitor.stop` → `stop_thermal_monitor` →
  `janus_proxy.stop_client` + `relay_proxy.stop_client` → (color) `depth_camera_proxy.stop_client` →
  `depth_mux_client.close` → `janus.close_client` + `_executor.shutdown(wait=False)`.

### The actual defect (why the audit flagged this)
The three long-lived loops are **fire-and-forget** `asyncio.create_task(...)` with **no references kept
and no cancellation** — `_watchdog_loop` (sd_notify WATCHDOG=1 keepalive), `_memory_gauge_loop` (RSS
gauge), `_mux_fps_scraper` (mux /stats → FPS gauge) all run `while True` forever and are **never
cancelled** by `_shutdown()`. That's a task leak + swallowed-exception risk (a raise in a fire-and-forget
task is lost). `_reconcile_janus_bg` is also fire-and-forget but is one-shot (completes on its own).

### House precedent to match
`api_gateway_service/app/core/lifecycle.py` is the established pattern: `@asynccontextmanager async def
lifespan(app)`, startup before `yield`, shutdown in `finally`, and a tracked task cancelled+awaited:
`t = asyncio.create_task(loop); ... finally: t.cancel(); try: await t except CancelledError: pass`.
Scaled to N loops, that is exactly the target here.

### Test coupling (the oracles that constrain the move)
- **`tests/test_events.py`** is the ONLY test coupled to the registration mechanism. `test_sd_notify_*`
  exercise `_sd_notify` directly (unaffected). `test_startup_starts_watchdogs` /
  `test_shutdown_stops_proxies` set `mock_app.on_event = capture_handler` to **capture** the startup/
  shutdown fns, then call them and assert: `start_janus_watchdog` called, `start_snapshot_watchdog`
  awaited, thermal started, `janus_proxy`/`relay_proxy` start+stop awaited, `_sd_notify("READY=1")`.
  These capture-by-`on_event` tests MUST be re-pointed to drive the lifespan ctx manager — with the
  SAME assertions.
- **`tests/conftest.py:97`** patches `app.core.events.register_event_handlers` → `lambda app: None`
  so the suite's app never wires startup. **This seam decides the blast radius** (see D3). Most tests use
  `AsyncClient(ASGITransport(app))`, which does NOT run the lifespan protocol, so they never trigger
  startup either way; only `tests/test_textroom_relay.py` uses `TestClient(...)` as a context manager.
- `tests/test_watchdog_loop_integration.py` tests `watchdogs._watchdog_loop` (the FDIR ladder), NOT
  `events._watchdog_loop` (sd_notify keepalive) — no coupling. No `@app.on_event` exists outside events.py.

## Target shape (sketch — for review, not final)
```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def _lifespan(app: FastAPI):
    # ... the EXACT current _startup() body, verbatim and in order ...
    _sd_notify("READY=1")
    tasks = [asyncio.create_task(c) for c in (_watchdog_loop(), _memory_gauge_loop(), _mux_fps_scraper())]
    try:
        yield
    finally:
        # ... the EXACT current _shutdown() body, verbatim and in order ...
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)   # the leak fix
```

## Plan — sub-commits (tests-first, suite green between)
1. **char** — re-point `test_events.py`'s two capture tests to assert startup/shutdown side effects via
   the new seam, keeping IDENTICAL assertions (watchdogs/proxies/sd_notify); add one NEW assertion that
   the three loops are cancelled on shutdown (locks the leak fix). Land it red-then-green WITH step 2.
2. **convert** — `events.py`: `@app.on_event` → `_lifespan` asynccontextmanager + the task registry
   (cancel+gather in `finally`). Startup/shutdown bodies move VERBATIM; order unchanged; READY=1 timing
   unchanged. Wire per D3.
3. **(optional) guard** — a fitness guard banning `@app.on_event(` in `app/**` (locks the deprecation
   fix; passes once step 2 lands).

## Open decisions to gate (GO before any code)
- **D1 — registry shape:** a plain `list[asyncio.Task]` cancelled+gathered in `finally` (matches the
  api_gateway single-task pattern, scaled; no new abstraction). vs a small `TaskRegistry` helper. **Lean:
  plain list** (no DDD-for-DDD).
- **D2 — what the registry tracks:** the three infinite loops only (the actual leak) — leave
  `_reconcile_janus_bg` a fire-and-forget one-shot (it completes; cancelling its `to_thread` wouldn't stop
  the thread anyway). **Lean: track the three loops; leave reconcile as-is.**
- **D3 — the registration seam (decides blast radius):**
  - **(A) Keep `register_event_handlers(app)`** as the public seam but have it set
    `app.router.lifespan_context = _lifespan` instead of `@app.on_event`. `core/app.py:113` AND the
    `conftest.py:97` no-op patch stay UNCHANGED → minimal blast radius; the name becomes a mild misnomer.
  - **(B) Canonical `FastAPI(lifespan=_lifespan)`** at `app.py:84`, remove `register_event_handlers` →
    matches api_gateway exactly, but `conftest.py` must change (its patch target disappears), touching the
    shared fixture every test uses.
  - **Lean: (A)** — behavior-preserving, smallest risk, keeps the conftest seam; note (B) is more
    canonical and can be a follow-up.
- **D4 — tests-first:** the mechanism changes, so the re-point lands WITH the conversion (can't
  characterize lifespan before it exists); the EXISTING assertions are carried over verbatim + the new
  cancellation assertion. Never weaken an assertion to make it pass.

## Red lines
Behavior-preserving: the EXACT startup order, the EXACT shutdown order, and **`READY=1` timing** (it must
fire after the proxies start and before/around the metric loops — a missed READY makes systemd kill the
unit in a start-timeout loop). All best-effort `try/except` blocks stay verbatim. Keep the module-singleton
style (do NOT refactor service singletons onto `app.state` — out of scope). The only intended behavior
DELTA is the leak fix: the three loops are now cancelled at shutdown. `realsense_mux.py` untouched.

## Status — DONE (2026-06-21)
Decisions taken: **D1** plain `list[asyncio.Task]`; **D2** track the three infinite loops only (the
one-shot `_reconcile_janus_bg` stays fire-and-forget); **D3 (A)** keep `register_event_handlers` as the
seam, setting `app.router.lifespan_context = _lifespan`; **D4** re-point with identical assertions.
- **`2a65bf1`** — `events.py`: `@app.on_event` → `_lifespan` asynccontextmanager. Startup body before
  `yield`, shutdown body in `finally`, both VERBATIM (order + READY=1 timing unchanged). The three loops
  (`_watchdog_loop`, `_memory_gauge_loop`, `_mux_fps_scraper`) are tracked in `_bg_tasks` and
  cancelled + `gather`ed in the finally — the leak fix. `register_event_handlers` now sets the lifespan
  context, so `core/app.py:113` and `conftest.py:97` are UNTOUCHED. Verified Starlette honors
  `router.lifespan_context` set post-construction (TestClient drives startup+shutdown). `test_events.py`
  re-pointed to drive the lifespan with the same assertions + a new "loops cancelled on shutdown / one-shot
  not cancelled" assertion. Full non-e2e suite green.
- **(close)** — fitness guard **#17** `test_no_on_event_use_lifespan_instead` bans `.on_event(` in
  app/** (unconditional, no allowlist) — locks the deprecation fix. **17 fitness guards.**

**Follow-up (separate, optional):** D3-(B) canonical `FastAPI(lifespan=)` + a conftest rework, if the
team later wants the construction-time form. Not needed for correctness.

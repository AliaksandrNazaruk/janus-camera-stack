# TRACKED_BACKGROUND_TASKS — Cycle 4 recon + plan (GATED, no code yet)

Closes the audit's "untracked background work" risk: a task/thread spawned but never registered,
never cancelled, whose failure is lost and whose presence makes shutdown/recovery non-obvious.
Recon shows the codebase is ALREADY most of the way there (prior lifespan + per-service stop hooks
+ a durable op journal) — there is essentially ONE real gap. Minimal fix + a guard. No code until GO.

## Recon — every spawn site in app/**, classified (verified 2026-06-21)

| # | site | kind | lifecycle owner | verdict |
|---|---|---|---|---|
| 1 | `core/events.py:189-191` `_watchdog_loop` / `_memory_gauge_loop` / `_mux_fps_scraper` | lifespan async loops | held in `_bg_tasks`; `cancel()` + `gather(return_exceptions=True)` in lifespan `finally` | ✅ tracked |
| 2 | **`core/events.py:173` `asyncio.create_task(_reconcile_janus_bg())`** | lifespan one-shot async | **return value DISCARDED; not in `_bg_tasks`; never cancelled** | ❌ **GAP** |
| 3 | `services/watchdogs.py:110` janus watchdog | daemon thread loop | `_stop_event` + `stop_all()` called in lifespan `finally` | ✅ tracked |
| 4 | `services/watchdogs.py:327` `_snapshot_task` | lifespan async loop | held in module `_snapshot_task`; `cancel()` in `stop_all()` | ✅ tracked |
| 5 | `services/thermal.py:137` thermal monitor | daemon thread loop | `_stop_event` + `stop_thermal_monitor()` in `finally` | ✅ tracked |
| 6 | `services/remote_stream_monitor.py:185` remote monitor | daemon thread loop | `_stop_event` + `stop()` in `finally` | ✅ tracked |
| 7 | `services/node_operation_runner.py:45` node op (provision/rotate/activate) | request-scoped daemon thread | INTENTIONAL (Bug-A: immune to response lifecycle) + DURABLE `operation_journal` + `reap_orphans()` on boot | ✅ by design |
| 8 | `services/ws_proxy.py:214-216` client/upstream pumps | request-scoped | `asyncio.TaskGroup` — structured concurrency, scoped + awaited | ✅ gold standard |

So 7 of 8 are already owned: the lifespan reaps its async loops, each daemon-thread service has a
`_stop_event` + a stop hook invoked in the lifespan `finally`, the node-op threads are an intentional
fire-and-forget with a DURABLE journal + boot reap (the journal IS the registry), and ws_proxy uses a
TaskGroup. The audit's fear ("untracked daemon threads, lost failures") does not hold against the tree.

## The one gap (#2) — `_reconcile_janus_bg`

```python
asyncio.create_task(_reconcile_janus_bg())   # events.py:173 — return value dropped
```

This is the bare-`asyncio.create_task`-with-discarded-reference footgun (ruff RUF006):

1. **Dropped reference → GC race.** CPython's event loop holds only a *weak* reference to a Task. With
   no strong ref kept, the boot reconcile can be garbage-collected mid-flight and silently cancelled
   ("Task was destroyed but it is pending!"). The other lifespan tasks avoid this — they're held in
   `_bg_tasks` / `_snapshot_task`.
2. **Not cancelled on shutdown.** If the app shuts down WHILE the boot reconcile is in flight — exactly
   the slow/unreachable-Janus incident this code targets — the task (and its `asyncio.to_thread` worker)
   is abandoned; `_executor.shutdown(wait=False)` won't reap it cleanly.

It already has an internal `try/except` (failure is logged), so this is a lifecycle-ownership gap, not a
lost-exception gap — but it's the one task in the tree that nobody owns.

## Decisions (GATED — user GO 2026-06-21)
- **D1 = (B) formal `TaskRegistry`.** Build `app/services/task_registry.py` as the single owner of the
  app's long-lived background work; route every long-lived ASYNC spawn through it.
- **D2 = (A) cancel-on-shutdown** for the one-shot reconcile (idempotent; remote monitor backstops).
- **D3 = (B) allowlist-by-location** guard: a bare `asyncio.create_task` may appear ONLY inside
  `task_registry.py`; everything else goes through `task_registry.spawn`. (`tg.create_task` on a
  `TaskGroup` is a different construct that owns + awaits its own children → never flagged.)
- **D4 — guard scope:** app/**, allowlist = `{app/services/task_registry.py}`.

### Scope boundary I'm holding (safety, not asked) — the registry owns ASYNC tasks + AGGREGATES stoppers
The daemon-thread services #3/#5/#6 are part of the **FDIR recovery ladder / thermal / remote-stream**
safety surface; rewriting their internal `_stop_event` loops to be *created* by the registry is risky
surgery for no lifecycle gain (they already stop correctly). So the registry has TWO surfaces:
1. `spawn(coro, *, name)` — owns long-lived async tasks (strong ref + RUF006-safe done-discard + cancel
   on shutdown). The 4 events.py tasks (incl. the #2 gap) and the watchdogs `_snapshot_task` migrate here
   → after migration `asyncio.create_task` exists ONLY in `task_registry.py`.
2. `register_stopper(fn, *, name)` — the daemon-thread services register their EXISTING stop hook
   (`watchdogs.stop_all`, `thermal.stop_thermal_monitor`, `remote_stream_monitor.stop`); `shutdown()`
   invokes them. Their thread + `_stop_event` internals are UNTOUCHED.
`shutdown()` (one call in the lifespan `finally`) runs the stoppers then cancels + gathers the async
tasks. The node-op daemon threads (#7) stay journal-owned; the ws_proxy TaskGroup (#8) stays as-is.
Raw `threading.Thread` is NOT guarded this cycle (D3 was about `create_task`); it stays in its service
wrappers, which now register their stopper. (A thread-location guard is a possible follow-up.)

## Plan — sub-commits (tests-first, suite green between)
1. **registry + char tests** — `app/services/task_registry.py` (`spawn` / `register_stopper` /
   `async shutdown` / `_reset_for_tests`) + `tests/test_task_registry.py`: spawn holds a strong ref and
   the done-callback discards it; `shutdown()` cancels live tasks (gather return_exceptions) and invokes
   every registered stopper in order; idempotent; a stopper that raises doesn't abort the rest.
2. **migrate the lifespan** — `core/events.py`: the 4 `asyncio.create_task` sites → `task_registry.spawn`
   (incl. the #2 reconcile gap); the three `stop_*()` calls → `register_stopper(...)` at startup; the
   `finally` `_bg_tasks` cancel + manual stop calls → one `await task_registry.shutdown()` (async client
   closers stay inline, ordered after). `watchdogs.start_snapshot_watchdog` → `task_registry.spawn`
   (its `_stop_event` still gates the loop; `stop_all` no longer cancels the task — the registry does).
   Behaviour-preserving: same tasks run, same boot reconcile off the event loop (no READY=1 delay).
3. **guard** — fitness guard **#21** `test_asyncio_create_task_only_in_task_registry`: AST — a Call to
   `asyncio.create_task` (or a directly-imported `create_task`) outside the allowlist fails. After step 2
   the only such call is in `task_registry.py`. **21 fitness guards.**

## Red lines
Behaviour-preserving: the boot reconcile still runs on a worker thread off the event loop (no READY=1
delay); same idempotent sweep; same log lines; same set of tasks/threads run. Don't rewrite the
FDIR/thermal/remote daemon-thread loop internals (#3/#5/#6) — register their existing stop hooks. Don't
convert the node-op daemon threads (#7) or the TaskGroup pumps (#8). Tests-first; never weaken an
assertion. Full non-e2e suite green per sub-commit.

Expected: one owner for all long-lived background work; every async task is held + cancelled on shutdown
(no dropped-ref GC race, no leaked boot reconcile); every daemon-thread service's stop hook is registered;
request-scoped work is TaskGroup- or journal-owned. Locked by guard #21. ~7.6–7.9 → ~7.8–8.1 on the
lifecycle axis.

## Status — DONE (2026-06-21)
Decisions as gated: **D1=(B)** formal `TaskRegistry`; **D2=(A)** cancel-on-shutdown; **D3=(B)**
allowlist-by-location guard; **D4** app/** allowlist = `{app/services/task_registry.py}`.
- **4.1** `39e2571` — `app/services/task_registry.py` (`spawn` strong-ref+done-discard / `register_stopper`
  / `async shutdown` stoppers-then-cancel / `_reset_for_tests`) + `tests/test_task_registry.py` (7 char
  tests: strong-ref-until-done, cancel-on-shutdown, idempotent, stopper order, fault isolation).
- **4.2** `ca7d4be` — lifespan migration: the 4 `asyncio.create_task` sites (3 obs loops + the #2 boot
  reconcile gap) → `task_registry.spawn`; the 3 per-service stop calls → `register_stopper`; the
  `finally` `_bg_tasks` cancel + manual stops collapse to one `await task_registry.shutdown()`. The
  watchdogs snapshot task → `task_registry.spawn`. Behaviour-preserving (same tasks, boot reconcile still
  off the event loop). `test_events` re-pointed (reconcile NOW owned + cancelled — was assert_not_called);
  `test_watchdogs` patches `task_registry.spawn`.
- **4.3** (this) — fitness guard **#21** `test_asyncio_create_task_only_in_task_registry`: AST — a bare
  `asyncio.create_task(...)` (or a directly-imported `create_task`) outside the allowlist fails;
  `task_registry.py` is now the SOLE producer in app/**. `TaskGroup.create_task` (structured concurrency)
  is exempt. **21 fitness guards.**

**Result:** one owner for all long-lived background work. Every async task is created via
`task_registry.spawn` (strong ref + cancelled on `shutdown()`); every daemon-thread service registers its
stop hook (`watchdogs.stop_all`, `thermal`, `remote_stream_monitor`); the node-op threads stay
journal-owned (#7), the ws_proxy pumps stay TaskGroup-owned (#8). No dropped-ref GC race, no leaked boot
reconcile across shutdown. Locked by guard #21.

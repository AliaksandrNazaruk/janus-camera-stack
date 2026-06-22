"""Single owner of the app's long-lived background work (Cycle 4, D1=formal TaskRegistry).

Two registration surfaces, drained by one `shutdown()` in the lifespan ``finally``:

1. ``spawn(coro, *, name)`` — owns a long-lived ASYNC task. A strong reference is held in
   ``_tasks`` until the task completes (a bare ``asyncio.create_task`` whose return value is
   dropped can be garbage-collected mid-flight — CPython's loop keeps only a weak ref — so the
   work silently vanishes; RUF006). On completion the done-callback discards the ref; on
   ``shutdown()`` every live task is cancelled + reaped.
2. ``register_stopper(fn, *, name)`` — a daemon-thread service registers its EXISTING stop hook
   (e.g. ``watchdogs.stop_all``); ``shutdown()`` invokes each stopper. The service keeps its own
   thread + ``_stop_event`` internals — the registry only aggregates the shutdown call so the
   lifespan no longer hand-calls each ``stop_*()``.

This is the ONE place ``asyncio.create_task`` may be called (enforced by architecture guard #21);
everything else routes through ``spawn``. ``TaskGroup`` (request-scoped structured concurrency) is a
separate construct that owns + awaits its own children and is unaffected.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Coroutine, List, Set, Tuple

_log = logging.getLogger("task_registry")

# Strong refs to live background tasks (the leak fix — without this the loop keeps only a weak ref).
_tasks: Set[asyncio.Task] = set()
# (name, stop_hook) for daemon-thread services; invoked on shutdown in registration order.
_stoppers: List[Tuple[str, Callable[[], None]]] = []


def spawn(coro: Coroutine, *, name: str) -> asyncio.Task:
    """Create + OWN a long-lived async task: hold a strong ref until it completes (no GC race),
    then auto-discard. The task is cancelled + reaped by ``shutdown()``. The only sanctioned
    ``asyncio.create_task`` call site (guard #21)."""
    task = asyncio.create_task(coro, name=name)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


def register_stopper(fn: Callable[[], None], *, name: str) -> None:
    """Register a daemon-thread service's existing stop hook; ``shutdown()`` calls it (in order)."""
    _stoppers.append((name, fn))


async def shutdown(*, gather_timeout: float = 10.0) -> None:
    """Drain the registry: invoke every registered stopper (sets each service's ``_stop_event``),
    then cancel + reap all live async tasks. Idempotent — a stopper that raises is logged and does
    NOT abort the rest, and tasks are always cancelled. Safe to call with nothing registered."""
    # 1. Stop the daemon-thread services first (their stop hooks signal the threads to wind down).
    for name, fn in _stoppers:
        try:
            fn()
        except Exception:  # noqa: BLE001 — one bad stopper must not strand the others / the tasks
            _log.warning("task_registry: stopper %s failed during shutdown", name, exc_info=True)
    _stoppers.clear()

    # 2. Cancel + reap the async tasks.
    live = list(_tasks)
    for task in live:
        task.cancel()
    if live:
        try:
            await asyncio.wait_for(
                asyncio.gather(*live, return_exceptions=True), timeout=gather_timeout)
        except asyncio.TimeoutError:
            _log.warning("task_registry: %d task(s) did not finish cancelling within %.0fs",
                         len(live), gather_timeout)
    _tasks.clear()


def _live_task_count() -> int:
    """Test/diagnostic helper: number of tasks the registry currently holds a strong ref to."""
    return len(_tasks)


def _registered_stopper_names() -> List[str]:
    """Test/diagnostic helper: names of the stoppers registered (in order)."""
    return [name for name, _ in _stoppers]


def _reset_for_tests() -> None:
    """Drop all registry state WITHOUT cancelling (tests own their own tasks)."""
    _tasks.clear()
    _stoppers.clear()

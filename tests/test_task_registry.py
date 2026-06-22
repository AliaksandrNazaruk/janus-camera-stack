"""Cycle 4 — task_registry: the single owner of long-lived background work.

Pins the lifecycle contract: spawn() holds a STRONG ref until the task completes (the dropped-ref
GC race the bare asyncio.create_task at events.py:173 had), shutdown() cancels live tasks + invokes
every registered stopper in order, and one bad stopper never strands the rest or the task cancel.
"""
import asyncio

import pytest

from app.services import task_registry as reg


@pytest.fixture(autouse=True)
def _clean_registry():
    reg._reset_for_tests()
    yield
    reg._reset_for_tests()


# ── spawn: strong ref held until done, then auto-discarded ────────────────────

@pytest.mark.asyncio
async def test_spawn_holds_strong_ref_until_done_then_discards():
    started = asyncio.Event()
    release = asyncio.Event()

    async def _work():
        started.set()
        await release.wait()

    task = reg.spawn(_work(), name="work")
    await started.wait()
    assert reg._live_task_count() == 1          # strong ref held while running (no GC race)
    release.set()
    await task
    # the done-callback discards the ref (callbacks run on the next loop cycle)
    await asyncio.sleep(0)
    assert reg._live_task_count() == 0


@pytest.mark.asyncio
async def test_spawn_returns_named_task():
    async def _noop():
        return 42

    task = reg.spawn(_noop(), name="answer")
    assert task.get_name() == "answer"
    assert await task == 42


# ── shutdown: cancels live tasks + reaps ─────────────────────────────────────

@pytest.mark.asyncio
async def test_shutdown_cancels_live_tasks():
    running = asyncio.Event()
    cancelled = {"v": False}

    async def _loop():
        running.set()
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled["v"] = True
            raise

    task = reg.spawn(_loop(), name="loop")
    await running.wait()
    await reg.shutdown()
    assert task.cancelled() or task.done()
    assert cancelled["v"] is True
    assert reg._live_task_count() == 0


@pytest.mark.asyncio
async def test_shutdown_is_idempotent_and_safe_when_empty():
    await reg.shutdown()        # nothing registered — must not raise
    await reg.shutdown()        # second call still safe
    assert reg._live_task_count() == 0


# ── register_stopper: invoked on shutdown, in order, fault-isolated ──────────

@pytest.mark.asyncio
async def test_shutdown_invokes_stoppers_in_order():
    calls = []
    reg.register_stopper(lambda: calls.append("a"), name="a")
    reg.register_stopper(lambda: calls.append("b"), name="b")
    assert reg._registered_stopper_names() == ["a", "b"]
    await reg.shutdown()
    assert calls == ["a", "b"]
    assert reg._registered_stopper_names() == []   # cleared after shutdown


@pytest.mark.asyncio
async def test_one_failing_stopper_does_not_strand_the_rest_or_task_cancel():
    calls = []
    cancelled = {"v": False}

    def _boom():
        calls.append("boom")
        raise RuntimeError("stopper failed")

    async def _loop():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled["v"] = True
            raise

    reg.register_stopper(_boom, name="boom")
    reg.register_stopper(lambda: calls.append("after"), name="after")
    task = reg.spawn(_loop(), name="loop")
    await asyncio.sleep(0)

    await reg.shutdown()        # must NOT raise despite the failing stopper
    assert calls == ["boom", "after"]               # the later stopper still ran
    assert cancelled["v"] is True                   # the task was still cancelled
    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_stopper_runs_before_task_cancel():
    """Stoppers (daemon-thread stop hooks) fire BEFORE async tasks are cancelled, so a thread that
    feeds an async task is told to stop first."""
    order = []

    async def _loop():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            order.append("task_cancelled")
            raise

    reg.register_stopper(lambda: order.append("stopper"), name="s")
    reg.spawn(_loop(), name="loop")
    await asyncio.sleep(0)
    await reg.shutdown()
    assert order == ["stopper", "task_cancelled"]

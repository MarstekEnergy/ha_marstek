"""Concurrency/interleaving tests for the debounced write queue.

``tests/test_write_queue.py`` drives the queue deterministically (one write at
a time, handshakes via events). Those tests cannot catch problems that only
appear when several callers race the drain: the cancel path in
``DebouncedWriteQueue.async_write`` inspects the queue state after the fact, so
"who won the race" changes which branch runs. The invariants pinned here:

* a caller is only told "success" if *its* write (or the write that superseded
  it) really reached the device;
* a cancellation never leaves another caller hanging;
* the queue always ends quiescent -- no pending entry, no drain task, no
  unsettled future.
"""

from __future__ import annotations

import asyncio
import importlib.util
import random
from pathlib import Path

import pytest
from aiomarstek.udp_client import MarstekSetError

COMPONENT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "marstek"
DEBOUNCE = 0.01


def _load_queue_cls():
    """Import write_queue.py without triggering the marstek package __init__."""
    spec = importlib.util.spec_from_file_location(
        "marstek_write_queue_concurrency", COMPONENT_DIR / "write_queue.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.DebouncedWriteQueue


_QueueClass = _load_queue_cls()
_CREATED: list = []


class DebouncedWriteQueue(_QueueClass):  # type: ignore[misc, valid-type]
    """The real queue, additionally registered for teardown cleanup."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        _CREATED.append(self)


@pytest.fixture(autouse=True)
async def _shutdown_queues():
    """Shut every queue down so no drain task outlives its test."""
    yield
    while _CREATED:
        await _CREATED.pop().async_shutdown()


async def _noop_refresh() -> None:
    """A refresh callback that does nothing."""


def _new_queue(debounce: float = DEBOUNCE) -> DebouncedWriteQueue:
    return DebouncedWriteQueue(debounce, _noop_refresh)


async def _drain_settled(queue: DebouncedWriteQueue) -> None:
    """Wait until the queue has no pending writes and no drain task."""
    for _ in range(200):
        if queue._task is None and not queue._pending:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("the write queue did not settle")


def _action(log: list[str], name: str):
    async def run() -> None:
        log.append(name)

    return run


def _holding_action(started: asyncio.Event, release: asyncio.Event):
    """An action that signals it started and then waits to be released."""

    async def run() -> None:
        started.set()
        await release.wait()

    return run


def _failing_action(log: list[str], name: str):
    async def run() -> None:
        log.append(name)
        raise MarstekSetError("192.168.1.50", "Set", "rejected")

    return run


async def test_concurrent_same_key_writes_only_send_the_last() -> None:
    """Ten callers race one control: one command reaches the device, all agree."""
    queue = _new_queue(0.05)
    sent: list[str] = []

    results = await asyncio.gather(
        *(queue.async_write("device_mode", _action(sent, str(n))) for n in range(10))
    )

    assert sent == ["9"], "only the surviving (latest) action may run"
    assert results[-1] is True, "the surviving caller ran its own action"
    assert results[:-1] == [False] * 9, (
        "superseded callers learn that the write took effect, but not that "
        "their own value was sent"
    )
    await _drain_settled(queue)


async def test_concurrent_distinct_keys_run_with_isolated_failures() -> None:
    """One control failing must not swallow or resolve the others."""
    queue = _new_queue()
    sent: list[str] = []

    results = await asyncio.gather(
        queue.async_write("dod", _action(sent, "dod")),
        queue.async_write("led", _failing_action(sent, "led")),
        queue.async_write("bluetooth", _action(sent, "bluetooth")),
        return_exceptions=True,
    )

    assert sent == ["dod", "led", "bluetooth"], "arrival order, nothing coalesced"
    assert results[0] is True
    assert isinstance(results[1], MarstekSetError)
    assert results[2] is True
    await _drain_settled(queue)


async def test_superseded_chain_shares_the_surviving_outcome() -> None:
    """A/B/C on one key: the two superseded callers see C's real result."""
    queue = _new_queue(0.05)
    sent: list[str] = []

    first = asyncio.create_task(queue.async_write("dod", _action(sent, "a")))
    second = asyncio.create_task(queue.async_write("dod", _action(sent, "b")))
    third = asyncio.create_task(queue.async_write("dod", _failing_action(sent, "c")))

    outcomes = await asyncio.gather(first, second, third, return_exceptions=True)

    assert sent == ["c"]
    for outcome in outcomes:
        assert isinstance(outcome, MarstekSetError), (
            "a superseded caller must not be told success when the surviving "
            "write failed"
        )
    await _drain_settled(queue)


async def test_cancel_racing_the_drain_never_hangs_or_lies() -> None:
    """Cancel at a random moment: either it never ran, or it ran and is reported.

    The cancel handler looks at the queue state *after* the cancellation, so
    the two branches (still pending vs already in flight) are decided by
    scheduling. Whatever wins, the caller must not hang and must not report
    success for a write that never reached the device.
    """
    rng = random.Random(20261105)
    for _ in range(25):
        queue = _new_queue(0.002)
        started = asyncio.Event()
        release = asyncio.Event()

        waiter = asyncio.create_task(
            queue.async_write("dod", _holding_action(started, release))
        )
        await asyncio.sleep(rng.random() * 0.004)
        waiter.cancel()
        # Let the action finish whichever branch we land in, so the test can
        # never hang on an action that starts just after the cancel.
        release.set()

        cancelled = False
        try:
            await waiter
        except asyncio.CancelledError:
            cancelled = True

        if started.is_set():
            assert not cancelled, (
                "the write reached the device, so the caller must get its real "
                "outcome instead of a bare cancellation"
            )
        else:
            assert cancelled, "a write that never ran must report cancellation"
            assert queue._pending == {}
        await queue.async_shutdown()


async def test_cancel_after_the_drain_picked_the_write_reports_the_outcome() -> None:
    """A cancel that loses the race still gets the real outcome, not a lie."""
    queue = _new_queue()
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def action() -> None:
        started.set()
        await release.wait()
        finished.set()

    waiter = asyncio.create_task(queue.async_write("dod", action))
    await started.wait()
    waiter.cancel()
    release.set()

    # The write already left the queue, so the caller is told it succeeded.
    await waiter
    assert finished.is_set()
    await _drain_settled(queue)


async def test_shutdown_settles_every_waiting_caller() -> None:
    """Unloading must not leave a caller awaiting a future forever."""
    queue = _new_queue(0.05)
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow() -> None:
        started.set()
        await release.wait()

    callers = [
        asyncio.create_task(queue.async_write("dod", slow)),
        asyncio.create_task(queue.async_write("led", _action([], "led"))),
        asyncio.create_task(queue.async_write("device_mode", _action([], "mode"))),
    ]
    await started.wait()

    await queue.async_shutdown()
    release.set()

    outcomes = await asyncio.gather(*callers, return_exceptions=True)
    assert all(isinstance(o, asyncio.CancelledError) for o in outcomes), outcomes
    assert queue._pending == {}
    assert queue._task is None

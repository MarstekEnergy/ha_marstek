"""Behavioural tests for the debounced, per-control write queue.

Loaded directly from ``custom_components/marstek/write_queue.py`` by file path
so the tests run under the CI environment, which does not install
``homeassistant``. They exercise the real queue runtime: same-key coalescing,
distinct-key queuing, move-to-back ordering, shared mode-key behaviour and
failure isolation.
"""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

COMPONENT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "marstek"


def _load_queue_cls():
    """Import write_queue.py without triggering the marstek package __init__."""
    spec = importlib.util.spec_from_file_location(
        "marstek_write_queue", COMPONENT_DIR / "write_queue.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.DebouncedWriteQueue


_QueueClass = _load_queue_cls()

# Every queue a test creates, so the autouse fixture below can shut it down.
# A queue that is never shut down leaves its drain task sleeping when the test
# ends, which the Home Assistant test fixture reports as a failure.
_CREATED: list[DebouncedWriteQueue] = []


class DebouncedWriteQueue(_QueueClass):  # type: ignore[misc, valid-type]
    """The real queue, additionally registered for teardown cleanup."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        _CREATED.append(self)


@pytest.fixture(autouse=True)
async def _shutdown_queues() -> AsyncGenerator[None]:
    """Stop every queue's drain task before the event loop of the test closes."""
    yield
    while _CREATED:
        await _CREATED.pop().async_shutdown()


# A debounce window large enough that several writes can be queued before the
# drain fires, keeping the tests deterministic without real-time races.
_DEBOUNCE = 0.05


async def _noop_refresh() -> None:
    """A refresh callback that does nothing."""


async def test_same_key_coalesces_to_latest() -> None:
    """Two rapid writes of the same control send only the latest value."""
    sent: list[int] = []
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)

    async def write_a() -> None:
        sent.append(1)

    async def write_b() -> None:
        sent.append(2)

    task_a = asyncio.create_task(queue.async_write("dod", write_a))
    task_b = asyncio.create_task(queue.async_write("dod", write_b))
    await task_a
    await task_b

    # The second same-key write supersedes the first; only value 2 is sent.
    assert sent == [2]


async def test_distinct_keys_queue_and_run_in_order() -> None:
    """Writes to different controls both execute, oldest first."""
    sent: list[str] = []
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)

    async def led() -> None:
        sent.append("led")

    async def bluetooth() -> None:
        sent.append("bluetooth")

    task_led = asyncio.create_task(queue.async_write("led", led))
    task_bt = asyncio.create_task(queue.async_write("bluetooth", bluetooth))
    await task_led
    await task_bt

    assert sent == ["led", "bluetooth"]


async def test_coalesce_moves_entry_to_back() -> None:
    """A superseded same-key write reorders behind intervening distinct writes.

    Request order: mode(UPS), LED, mode(Auto). The second mode write supersedes
    the first and moves to the back, so the device receives LED then mode(Auto)
    -- never mode before the LED that was requested in between.
    """
    sent: list[str] = []
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)

    async def mode_ups() -> None:
        sent.append("mode:ups")

    async def led() -> None:
        sent.append("led")

    async def mode_auto() -> None:
        sent.append("mode:auto")

    await asyncio.gather(
        queue.async_write("device_mode", mode_ups),
        queue.async_write("led", led),
        queue.async_write("device_mode", mode_auto),
    )

    assert sent == ["led", "mode:auto"]


async def test_mode_writes_share_key_so_latest_wins() -> None:
    """Select UPS, then UPS on, then select Auto: only Auto reaches the device.

    The mode select and the ``set_mode`` service both set the device operating
    mode and share the "device_mode" coalesce key, so the latest mode request
    wins instead of an earlier one executing after a later one.
    """
    sent: list[str] = []
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)

    async def mode_ups() -> None:
        sent.append("mode:ups")

    async def ups_on() -> None:
        sent.append("ups:on")

    async def mode_auto() -> None:
        sent.append("mode:auto")

    await asyncio.gather(
        queue.async_write("device_mode", mode_ups),
        queue.async_write("device_mode", ups_on),
        queue.async_write("device_mode", mode_auto),
    )

    assert sent == ["mode:auto"]


async def test_dod_write_not_coalesced_by_mode_switch() -> None:
    """A DOD write and a mode switch use different keys and queue in order.

    They change different device state, so a later mode switch must not
    coalesce away a queued DOD write (which would report it as done without it
    ever reaching the device).
    """
    sent: list[str] = []
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)

    async def dod() -> None:
        sent.append("dod")

    async def auto() -> None:
        sent.append("auto")

    await asyncio.gather(
        queue.async_write("dod", dod),
        queue.async_write("device_mode", auto),
    )
    assert sent == ["dod", "auto"]

    sent.clear()
    await asyncio.gather(
        queue.async_write("device_mode", auto),
        queue.async_write("dod", dod),
    )
    assert sent == ["auto", "dod"]


async def test_failed_write_does_not_block_queue() -> None:
    """A failing write surfaces its error but does not stall later commands."""
    sent: list[str] = []
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)

    async def boom() -> None:
        raise RuntimeError("device said no")

    async def ok() -> None:
        sent.append("ok")

    task_fail = asyncio.create_task(queue.async_write("led", boom))
    task_ok = asyncio.create_task(queue.async_write("bluetooth", ok))

    with pytest.raises(RuntimeError, match="device said no"):
        await task_fail
    await task_ok

    assert sent == ["ok"]


async def test_superseded_write_shares_surviving_write_failure() -> None:
    """A coalesced-away write reports the surviving write's failure.

    The superseded caller must not be told success before the write that
    actually runs; when that write fails the earlier caller sees the failure
    too, because the device never reached the requested state.
    """
    sent: list[str] = []
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)

    async def write_first() -> None:
        sent.append("first")  # never runs

    async def write_second() -> None:
        raise RuntimeError("device said no")

    task_first = asyncio.create_task(queue.async_write("dod", write_first))
    task_second = asyncio.create_task(queue.async_write("dod", write_second))

    with pytest.raises(RuntimeError, match="device said no"):
        await task_second
    with pytest.raises(RuntimeError, match="device said no"):
        await task_first

    assert sent == []


async def test_shutdown_cancels_pending_writes_and_drain() -> None:
    """Shutting down cancels queued writes and stops the drain task.

    A write still inside the debounce window must not fire after the owner
    has gone; its caller receives CancelledError and no command is sent.
    """
    sent: list[str] = []
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)

    async def write() -> None:
        sent.append("write")

    task = asyncio.create_task(queue.async_write("led", write))
    # Let the write enqueue but stay inside the debounce window.
    await asyncio.sleep(_DEBOUNCE / 2)
    await queue.async_shutdown()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert sent == []


async def test_cancelled_waiter_does_not_send_queued_write() -> None:
    """A caller cancelled while waiting must not have its command sent.

    The write is still inside the debounce window (not yet started), so
    cancelling the caller removes it and the device never sees it.
    """
    sent: list[str] = []
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)

    async def write() -> None:
        sent.append("write")

    task = asyncio.create_task(queue.async_write("led", write))
    await asyncio.sleep(_DEBOUNCE / 2)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    # Let the drain wake, find the queue empty and exit.
    await asyncio.sleep(_DEBOUNCE)

    assert sent == []


async def test_shutdown_rejects_new_writes() -> None:
    """After shutdown the queue is closed and refuses new submissions."""
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)
    await queue.async_shutdown()

    async def write() -> None:
        pass

    with pytest.raises(RuntimeError, match="shut down"):
        await queue.async_write("led", write)


async def test_shutdown_cancels_running_action() -> None:
    """Shutting down cancels a write that has already started executing."""
    started = asyncio.Event()
    cancelled = asyncio.Event()
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)

    async def action() -> None:
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(queue.async_write("led", action))
    await started.wait()
    await queue.async_shutdown()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert cancelled.is_set()


async def test_cancel_after_start_reports_success_not_cancelled() -> None:
    """A caller cancelled after the write started gets the real outcome.

    The device write is in flight and cannot be unsent, so the caller is told
    the actual result (success here) instead of CancelledError -- otherwise a
    number entity would roll its field back to a value the device no longer
    uses, and a later sibling write would re-send that stale value.
    """
    started = asyncio.Event()
    release = asyncio.Event()
    sent: list[str] = []
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)

    async def action() -> None:
        sent.append("write")
        started.set()
        await release.wait()  # keep the write in flight until we release it

    task = asyncio.create_task(queue.async_write("led", action))
    await started.wait()  # the write has left the queue and is running
    task.cancel()  # cancel the caller while the write is in flight
    release.set()  # let the write succeed

    # The caller is told success, not CancelledError.
    await task

    assert sent == ["write"]


async def test_cancel_after_start_reports_real_failure() -> None:
    """A caller cancelled after the write started gets the real failure.

    The write failed on the device, so the caller must see the error to roll
    its field back -- not CancelledError, which would be indistinguishable
    from a write that never sent.
    """
    started = asyncio.Event()
    release = asyncio.Event()
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)

    async def action() -> None:
        started.set()
        await release.wait()
        raise RuntimeError("device said no")

    task = asyncio.create_task(queue.async_write("led", action))
    await started.wait()
    task.cancel()
    release.set()

    with pytest.raises(RuntimeError, match="device said no"):
        await task


async def test_cancel_superseded_does_not_await_unrelated_inflight() -> None:
    """A superseded write cancelled while an *earlier* same-key write is in
    flight must not adopt that earlier write's outcome.

    Sequence: A starts executing; B queues; C (same key) supersedes B; B is
    cancelled. B's command never ran (C replaced it), so B must see
    CancelledError -- not A's success. Awaiting A's outcome would let B
    confirm a value the device never received from B; if the surviving C write
    then failed, the cache would hold a phantom confirmed value.
    """
    a_started = asyncio.Event()
    a_release = asyncio.Event()
    sent: list[str] = []
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)

    async def action_a() -> None:
        a_started.set()
        await a_release.wait()
        sent.append("A")

    async def action_b() -> None:
        sent.append("B")

    async def action_c() -> None:
        sent.append("C")

    # A queues and starts executing (in flight on the device).
    task_a = asyncio.create_task(queue.async_write("dod", action_a))
    await a_started.wait()

    # B queues (same key, a fresh _pending entry since A already left it).
    task_b = asyncio.create_task(queue.async_write("dod", action_b))
    # C supersedes B (same key, not yet drained).
    task_c = asyncio.create_task(queue.async_write("dod", action_c))
    await asyncio.sleep(0)  # let B and C settle into the queue

    # Cancel B while A is still in flight and C is still queued.
    task_b.cancel()

    # Let A finish so the drain can pick up C and run it.
    a_release.set()
    await task_a
    await task_c

    # B was superseded by C and never sent; it must NOT have adopted A's
    # success.
    with pytest.raises(asyncio.CancelledError):
        await task_b

    assert sent == ["A", "C"]


async def test_cancel_at_action_completion_reports_success() -> None:
    """A caller cancelled exactly as the action completes gets success.

    The action finished but the drain has not yet set the future when the
    caller is cancelled, so the future is cancelled and the drain skips
    setting its result. The in-flight record must stay so the cancel handler
    can recover the real (successful) outcome instead of reporting
    CancelledError -- which would roll a field back to a value the device
    already has.
    """
    sent: list[str] = []
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)
    loop = asyncio.get_running_loop()

    async def action() -> None:
        sent.append("write")
        # Cancel the caller before the drain resumes to set the future, so
        # the future is cancelled and the drain skips set_result.
        loop.call_soon(task.cancel)

    task = asyncio.create_task(queue.async_write("led", action))
    await task

    assert sent == ["write"]


async def test_repeated_cancel_does_not_cancel_inflight_action() -> None:
    """A caller cancelled twice after the write started must not cancel the action.

    The action is owned by the drain; the caller's repeated cancellations are
    shielded so they cannot propagate into the device write. The caller still
    gets the real outcome (success here), not CancelledError, and the action is
    never cancelled.
    """
    started = asyncio.Event()
    release = asyncio.Event()
    action_was_cancelled = [0]
    sent: list[str] = []
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)

    async def action() -> None:
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            action_was_cancelled[0] += 1
            raise
        sent.append("write")

    task = asyncio.create_task(queue.async_write("led", action))
    await started.wait()  # the write is in flight on the device

    # First cancellation enters the cancel handler; it reaches the shielded
    # await for the action's real outcome.
    task.cancel()
    await asyncio.sleep(0)  # let the handler run to the shielded await

    # Second cancellation while the handler waits for the action -- this must
    # cancel the shield's wrapper, NOT the action itself.
    task.cancel()

    # Let the action complete successfully.
    release.set()
    await task  # told success, not CancelledError

    assert sent == ["write"]
    assert action_was_cancelled[0] == 0

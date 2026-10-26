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


DebouncedWriteQueue = _load_queue_cls()


def _load_field_cls():
    """Import passive_state.py without triggering the marstek package __init__."""
    spec = importlib.util.spec_from_file_location(
        "marstek_passive_state", COMPONENT_DIR / "passive_state.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.PassiveField


PassiveField = _load_field_cls()

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

    Request order: mode(AI), LED, mode(Auto). The second mode write supersedes
    the first and moves to the back, so the device receives LED then mode(Auto)
    -- never mode before the LED that was requested in between.
    """
    sent: list[str] = []
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)

    async def mode_ai() -> None:
        sent.append("mode:ai")

    async def led() -> None:
        sent.append("led")

    async def mode_auto() -> None:
        sent.append("mode:auto")

    await asyncio.gather(
        queue.async_write("device_mode", mode_ai),
        queue.async_write("led", led),
        queue.async_write("device_mode", mode_auto),
    )

    assert sent == ["led", "mode:auto"]


async def test_mode_writes_share_key_so_latest_wins() -> None:
    """Select AI, then UPS on, then select Auto: only Auto reaches the device.

    The mode select, UPS switch and Passive number entities all set the device
    operating mode and share the "device_mode" coalesce key, so the latest mode
    request wins instead of an earlier one executing after a later one.
    """
    sent: list[str] = []
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)

    async def mode_ai() -> None:
        sent.append("mode:ai")

    async def ups_on() -> None:
        sent.append("ups:on")

    async def mode_auto() -> None:
        sent.append("mode:auto")

    await asyncio.gather(
        queue.async_write("device_mode", mode_ai),
        queue.async_write("device_mode", ups_on),
        queue.async_write("device_mode", mode_auto),
    )

    assert sent == ["mode:auto"]


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


async def test_reopen_accepts_writes_again() -> None:
    """A queue reopened after a failed unload accepts new writes."""
    sent: list[str] = []
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)
    await queue.async_shutdown()

    async def write() -> None:
        sent.append("write")

    with pytest.raises(RuntimeError, match="shut down"):
        await queue.async_write("led", write)

    queue.reopen()
    await queue.async_write("led", write)

    assert sent == ["write"]


async def test_factory_reset_variants_queue_in_arrival_order() -> None:
    """The two factory-reset options keep distinct keys and run FIFO.

    A keep-data reset and a clearing reset are different device operations,
    so whichever is requested later runs later instead of an earlier queued
    reset pre-empting it. This covers both the entity-then-service and
    service-then-entity orderings.
    """
    sent: list[str] = []
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)

    async def clear() -> None:
        sent.append("clear")

    async def keep() -> None:
        sent.append("keep")

    # Entity press first (clear), then service call (keep): keep wins.
    await asyncio.gather(
        queue.async_write("factory_reset", clear),
        queue.async_write("factory_reset_keep_data", keep),
    )
    assert sent == ["clear", "keep"]

    # Reverse: service call (keep) first, then entity press (clear).
    sent.clear()
    await asyncio.gather(
        queue.async_write("factory_reset_keep_data", keep),
        queue.async_write("factory_reset", clear),
    )
    assert sent == ["keep", "clear"]


async def test_passive_write_not_coalesced_by_mode_switch() -> None:
    """A Passive write and a plain mode switch use different keys and queue.

    Setting Passive power then switching to Auto must send both (Passive
    first, then Auto); if they shared a key the Passive write would be
    coalesced away and reported as done without reaching the device.
    """
    sent: list[str] = []
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)

    async def passive() -> None:
        sent.append("passive")

    async def auto() -> None:
        sent.append("auto")

    await asyncio.gather(
        queue.async_write("passive", passive),
        queue.async_write("device_mode", auto),
    )
    assert sent == ["passive", "auto"]

    sent.clear()
    await asyncio.gather(
        queue.async_write("device_mode", auto),
        queue.async_write("passive", passive),
    )
    assert sent == ["auto", "passive"]


async def test_cancelled_passive_write_rolls_back_before_sibling_write() -> None:
    """A cancelled Passive power write must not leak into a later cooldown write.

    Mirrors MarstekPassivePowerNumber.async_set_native_value: the field's
    pending target is rolled back when the write is cancelled, so a later
    cooldown write re-sends Passive using the last *confirmed* power, not the
    phantom value that never reached the device.
    """
    power = PassiveField()
    cd_time = PassiveField()
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)
    sent: list[tuple[int, int]] = []

    async def send_passive() -> None:
        # The config is built from the fields' current values, like
        # number.py._send_passive.
        sent.append((power.value or 0, cd_time.value or 0))

    async def set_power(value: int) -> None:
        power.start_write(value)
        try:
            await queue.async_write("passive", send_passive)
        except asyncio.CancelledError:
            power.rollback(value)
            raise
        else:
            power.mark_written(value)

    # A power write is queued but cancelled before it drains.
    task = asyncio.create_task(set_power(1000))
    await asyncio.sleep(_DEBOUNCE / 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The cancelled write never ran; the field reverted to unconfirmed.
    assert power.value is None

    # A later cooldown write must not resurrect the phantom 1000 W.
    cd_time.start_write(300)
    await queue.async_write("passive", send_passive)

    assert sent == [(0, 300)]


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


async def test_cancel_after_start_does_not_roll_back_field() -> None:
    """A number entity cancelled after the write started must not roll back.

    The write is in flight and may succeed; rolling back would leave the field
    pointing at a stale value, so a later sibling write re-sends it. The queue
    reports the real outcome, so the entity's CancelledError branch (rollback)
    only runs for a write that was still queued and never sent.
    """
    power = PassiveField()
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)
    started = asyncio.Event()
    release = asyncio.Event()
    sent: list[int] = []

    async def send_power() -> None:
        started.set()
        await release.wait()
        sent.append(power.value or 0)

    async def set_power(value: int) -> None:
        power.start_write(value)
        try:
            await queue.async_write("passive", send_power)
        except asyncio.CancelledError:
            power.rollback(value)
            raise
        else:
            power.mark_written(value)

    # A confirmed power of 800 exists from a prior successful write.
    power.start_write(800)
    power.mark_written(800)

    # Change power to 500; the write starts on the device.
    task = asyncio.create_task(set_power(500))
    await started.wait()
    task.cancel()  # cancel the caller while the write is in flight
    release.set()  # let the write succeed

    await task  # told success, not CancelledError

    # The field was confirmed (not rolled back to 800).
    assert power.value == 500
    assert sent == [500]


async def test_passive_service_updates_fields_so_number_preserves_them() -> None:
    """A Passive service write updates both fields so a number write preserves them.

    The service sets power=800, cd_time=300. Before the write drains, a number
    entity changes power to 500. The number's surviving write must send 500 W
    with the service's 300 s cooldown, not 0 s -- the service's start_write on
    cd_time is what makes the number's _send_passive pick up 300. Without it
    (the bug) the number would send 500 W / 0 s and the service's 300 s would
    be lost.
    """
    power = PassiveField()
    cd_time = PassiveField()
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)
    sent: list[tuple[int, int]] = []

    async def send_passive() -> None:
        sent.append((power.value or 0, cd_time.value or 0))

    # The service writes both fields (mirrors _async_apply_passive).
    power.start_write(800)
    cd_time.start_write(300)

    async def service_write() -> None:
        try:
            await queue.async_write("passive", send_passive)
        except asyncio.CancelledError:
            power.rollback(800)
            cd_time.rollback(300)
            raise
        else:
            power.mark_written(800)
            cd_time.mark_written(300)

    service_task = asyncio.create_task(service_write())

    # A number entity changes power right after, superseding the service write.
    power.start_write(500)

    async def number_write() -> None:
        try:
            await queue.async_write("passive", send_passive)
        except asyncio.CancelledError:
            power.rollback(500)
            raise
        else:
            power.mark_written(500)

    await number_write()
    await service_task  # coalesced; reports the surviving write's success

    # Only the number's write ran; it preserved the service's 300 s cooldown.
    assert sent == [(500, 300)]
    # The service's mark_written confirmed the cd_time it carried.
    assert power.value == 500
    assert cd_time.value == 300


async def test_passive_service_failure_rolls_back_both_fields() -> None:
    """A failed Passive service write rolls back both fields.

    The surviving write fails, so neither the service's power nor its cd_time
    reached the device. Both fields revert to their last confirmed values, and
    the service surfaces the combined failure.
    """
    power = PassiveField()
    cd_time = PassiveField()
    queue = DebouncedWriteQueue(_DEBOUNCE, _noop_refresh)
    sent: list[tuple[int, int]] = []

    async def send_passive() -> None:
        sent.append((power.value or 0, cd_time.value or 0))
        raise RuntimeError("device said no")

    power.start_write(800)
    cd_time.start_write(300)

    try:
        await queue.async_write("passive", send_passive)
    except RuntimeError:
        power.rollback(800)
        cd_time.rollback(300)
    else:
        power.mark_written(800)
        cd_time.mark_written(300)

    # The write ran but failed; both fields reverted to unconfirmed.
    assert sent == [(800, 300)]
    assert power.value is None
    assert cd_time.value is None


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
    task_a = asyncio.create_task(queue.async_write("passive", action_a))
    await a_started.wait()

    # B queues (same key, a fresh _pending entry since A already left it).
    task_b = asyncio.create_task(queue.async_write("passive", action_b))
    # C supersedes B (same key, not yet drained).
    task_c = asyncio.create_task(queue.async_write("passive", action_c))
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

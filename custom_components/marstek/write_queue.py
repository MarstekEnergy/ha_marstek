"""Debounced, per-control write queue for Marstek device commands.

Everything here follows from two rules:

* A write is identified by the *device state* it changes, not by the entity
  that asked for it. Repeats of one key collapse into a single command within
  the debounce window, so only the latest value is sent; writes to different
  keys queue and run one per window, so changing one control never drops
  another. The key choice matters: the mode select and ``set_mode`` share
  ``device_mode`` so the latest mode request wins, while DOD, Bluetooth, LED and
  the power-version select keep their own keys.
* A caller learns what actually happened *on the device*. A superseded caller
  resolves with the surviving write's outcome and ``False`` on success, because
  its own action never ran; a caller cancelled before its write started is
  removed from the queue and its cancellation propagates.

The module deliberately depends only on the standard library, so this
behaviour can be unit-tested with plain asyncio and without Home Assistant.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Coroutine
from typing import Any


def _mirror_result(source: asyncio.Future[bool], target: asyncio.Future[bool]) -> None:
    """Make ``target`` settle the way ``source`` does, reporting ``False``.

    ``False`` tells the superseded caller that its own action never ran, even
    when the surviving write succeeds; a failure is forwarded verbatim so that
    caller reports the real error.
    """

    def _forward(done: asyncio.Future[bool]) -> None:
        if target.done():
            return
        if done.cancelled():
            target.cancel()
        elif (exception := done.exception()) is not None:
            target.set_exception(exception)
        else:
            target.set_result(False)

    source.add_done_callback(_forward)


class DebouncedWriteQueue:
    """Coalesces same-control writes; queues distinct controls.

    The coalescing and reporting rules live in the module docstring; the
    cancellation contract is on :meth:`async_write`.
    """

    def __init__(
        self,
        debounce_delay: float,
        refresh: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        """Store the debounce window and the post-write refresh callback."""
        self._debounce_delay = debounce_delay
        self._refresh = refresh
        self._pending: dict[
            str,
            tuple[Callable[[], Coroutine[Any, Any, None]], asyncio.Future[bool]],
        ] = {}
        # Actions the drain has already picked up and is running, paired with
        # the future they belong to. A caller cancelled *after* its write
        # started cannot unsent it, so it must be able to await the real
        # outcome -- but only its *own* action, not an unrelated same-key write
        # that happens to be in flight, so the future is checked for identity.
        self._inflight: dict[str, tuple[asyncio.Task[None], asyncio.Future[bool]]] = {}
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def async_write(
        self, key: str, action: Callable[[], Coroutine[Any, Any, None]]
    ) -> bool:
        """Queue a write, coalescing repeats of the same control key.

        Returns ``True`` when this call's action ran on the device and the
        device accepted it, ``False`` when a later same-key write superseded it
        before it could run. A superseded caller still learns whether the
        surviving command took effect (it raises when that write fails) but
        must not record the value it asked for.

        Cancellation: a caller cancelled while still awaiting its write has
        a command that has not started removed, so it never reaches the
        device, and the cancellation propagates. A write that already started
        cannot be unsent, so its real outcome is reported instead: success lets
        the caller confirm the result, failure lets it roll back knowing the
        device rejected the value -- never pretending the write never happened
        and rewinding to a value the device may already have left behind.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        async with self._lock:
            if self._closed:
                raise RuntimeError("the write queue has been shut down")
            if key in self._pending:
                # Same-key repeat: the new write supersedes the queued one and
                # moves to the back, so execution order tracks the latest
                # request. The replaced caller is tied to the surviving outcome.
                previous_future = self._pending[key][1]
                del self._pending[key]
                self._pending[key] = (action, future)
                _mirror_result(future, previous_future)
            else:
                self._pending[key] = (action, future)
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._drain())
        try:
            return await future
        except asyncio.CancelledError:
            # The caller gave up; which state it was in decides what that means.
            async with self._lock:
                entry = self._pending.get(key)
                if entry is not None and entry[1] is future:
                    # Still queued: drop it so the drain never sends it, then
                    # propagate the cancellation.
                    del self._pending[key]
                    future.cancel()
                    raise
                inflight = self._inflight.get(key)
            if inflight is not None and inflight[1] is future:
                # Its own write already left the queue, so cancellation cannot
                # unsend it: wait for the real outcome instead. The action
                # belongs to the drain, hence the shield -- a repeated
                # cancellation must not abort a command already on the wire.
                action_task = inflight[0]
                future.cancel()
                while not action_task.done():
                    try:
                        await asyncio.shield(action_task)
                    except asyncio.CancelledError:
                        continue
                # Returns None on success, raises on failure or (shutdown)
                # cancellation -- never a caller cancellation.
                action_task.result()
                return True
            # Superseded, or never queued: this command did not reach the device
            # as its own write, so the cancellation is not dressed up as one.
            future.cancel()
            raise

    async def _drain(self) -> None:
        """Send queued writes one per debounce window, oldest first.

        The leading sleep lets rapid same-control repeats fold into a single
        pending entry before it is sent; the same sleep between writes keeps
        distinct commands apart so they do not pile up behind the device's
        request quiet gap.
        """
        try:
            while True:
                await asyncio.sleep(self._debounce_delay)
                async with self._lock:
                    if not self._pending:
                        return
                    key, (action, future) = next(iter(self._pending.items()))
                    del self._pending[key]
                    action_task: asyncio.Task[None] = asyncio.create_task(action())
                    # Track the in-flight action *and* the future it belongs
                    # to, so a caller cancelled after its write started can
                    # confirm it is really awaiting its own action -- not an
                    # unrelated same-key write that happens to be in flight.
                    self._inflight[key] = (action_task, future)
                try:
                    await action_task
                except asyncio.CancelledError:
                    if not future.done():
                        future.cancel()
                    # Shutting down cancels the drain, which also cancels the
                    # in-flight action instead of orphaning its coroutine.
                    if not action_task.done():
                        action_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await action_task
                    raise
                # Deliberately catches Exception: any write failure must be
                # forwarded verbatim to the caller awaiting this write, rather
                # than letting the drain task die with the exception.
                except Exception as err:  # noqa: BLE001
                    if not future.done():
                        future.set_exception(err)
                else:
                    if not future.done():
                        future.set_result(True)
                finally:
                    # The in-flight record is deliberately left in place: a
                    # caller cancelled in the window between the action
                    # completing and its cancel handler running would otherwise
                    # find nothing here and be told CancelledError even though
                    # the write already succeeded (rolling a field back to a
                    # value the device already has). The record is overwritten
                    # when the next same-key write starts and cleared on
                    # shutdown, so it never grows beyond one entry per key.
                    await self._refresh()
        finally:
            async with self._lock:
                pending = {} if self._closed else self._pending
            if pending:
                self._task = asyncio.create_task(self._drain())
            else:
                self._task = None

    async def async_shutdown(self) -> None:
        """Cancel queued writes and stop the background drain task.

        Called when the owning config entry unloads so a lingering drain
        cannot keep sending commands (and refreshing) after the integration
        is gone. Callers still awaiting a queued write receive
        ``CancelledError``. After this the queue stays closed and
        ``async_write`` rejects new writes: a failed unload is handled by not
        shutting the coordinator down at all, so nothing needs reopening.
        """
        async with self._lock:
            self._closed = True
            task = self._task
            pending = list(self._pending.values())
            self._pending = {}
            self._inflight.clear()
            self._task = None
        for _, future in pending:
            if not future.done():
                future.cancel()
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

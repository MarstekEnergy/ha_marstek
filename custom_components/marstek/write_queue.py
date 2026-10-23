"""Debounced, per-control write queue for Marstek device commands.

This module deliberately depends only on the standard library so the queue's
runtime behaviour can be unit-tested without the Home Assistant test stack
(the CI test job does not install ``homeassistant``).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable


def _mirror_result(source: asyncio.Future[None], target: asyncio.Future[None]) -> None:
    """Make ``target`` settle the same way ``source`` eventually does.

    Used when a pending write is superseded: the superseded caller should
    learn the outcome of the write that actually runs (success *or* failure)
    rather than being told success up front.
    """

    def _forward(done: asyncio.Future[None]) -> None:
        if target.done():
            return
        if done.cancelled():
            target.cancel()
        elif done.exception() is not None:
            target.set_exception(done.exception())
        else:
            target.set_result(done.result())

    source.add_done_callback(_forward)


class DebouncedWriteQueue:
    """Coalesces same-control writes; queues distinct controls.

    Rapid repeats of the same control key collapse to the latest pending
    action within the debounce window; a superseded repeat resolves with the
    surviving write's outcome (success *or* failure), because only that write
    reaches the device. Writes to *different* controls queue and execute one
    per debounce window, so a mode change, LED toggle or factory reset is
    never silently dropped behind another control's write.

    Plain operating-mode switches (the mode select and the UPS switch) share
    one key so only the latest mode request survives. Writes that carry extra
    parameters (the Passive number entities) use a separate key, so a later
    plain mode switch never coalesces them away. When a same-key write
    supersedes a pending one the entry moves to the back of the queue, so
    execution order tracks the most recent request rather than the first one.
    """

    def __init__(
        self,
        debounce_delay: float,
        refresh: Callable[[], Awaitable[None]],
    ) -> None:
        """Store the debounce window and the post-write refresh callback."""
        self._debounce_delay = debounce_delay
        self._refresh = refresh
        self._pending: dict[
            str, tuple[Callable[[], Awaitable[None]], asyncio.Future[None]]
        ] = {}
        # Actions the drain has already picked up and is running, paired with
        # the future they belong to. A caller cancelled *after* its write
        # started cannot unsent it, so it must be able to await the real
        # outcome -- but only its *own* action, not an unrelated same-key write
        # that happens to be in flight, so the future is checked for identity.
        self._inflight: dict[str, tuple[asyncio.Task[None], asyncio.Future[None]]] = {}
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def async_write(
        self, key: str, action: Callable[[], Awaitable[None]]
    ) -> None:
        """Queue a write, coalescing repeats of the same control key.

        Cancellation semantics: if the caller is cancelled while still
        awaiting its write, a command that has *not yet started* (still in the
        debounce/queue window, or superseded by a later same-key write that has
        not started either) is removed so it never reaches the device, and the
        cancellation propagates. A write that has already started cannot be
        unsent, so its real outcome is reported instead: success lets the caller
        confirm the result, failure lets it roll back knowing the device
        rejected the value. This keeps a number entity from rolling a field back
        to a value the device may already have stopped using.
        """
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        async with self._lock:
            if self._closed:
                raise RuntimeError("the write queue has been shut down")
            if key in self._pending:
                # A same-control write is already waiting; the new one
                # supersedes it. Move the entry to the back so distinct
                # controls queued in between run first, matching the latest
                # request order. The replaced write never reaches the device,
                # so its caller is tied to the surviving write's outcome: it
                # is told success only once that write actually succeeds, and
                # it is told failure if the surviving write fails.
                previous_future = self._pending[key][1]
                del self._pending[key]
                self._pending[key] = (action, future)
                _mirror_result(future, previous_future)
            else:
                self._pending[key] = (action, future)
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._drain())
        try:
            await future
        except asyncio.CancelledError:
            # The caller gave up. Distinguish three states so a write that
            # already left the queue is not falsely reported as "never sent"
            # (which would roll a field back to a value the device may already
            # have stopped using, and make a later sibling write re-send it).
            async with self._lock:
                entry = self._pending.get(key)
                if entry is not None and entry[1] is future:
                    # Still queued with this caller's future: the write never
                    # reached the device. Drop it so the drain does not send
                    # it, then propagate the cancellation.
                    del self._pending[key]
                    future.cancel()
                    raise
                inflight = self._inflight.get(key)
            if inflight is not None and inflight[1] is future:
                # This caller's *own* write already left the queue and is
                # running on (or just finished on) the device. Cancellation
                # can no longer unsent it, so report the real outcome instead
                # of pretending it never happened: success lets the caller
                # confirm its field, failure lets it roll back knowing the
                # device rejected the value.
                future.cancel()
                await inflight[0]
                return
            # Superseded by a later same-key write (still queued or already
            # running as a different write), or never queued: this caller's
            # command never reached the device as its own write, so a
            # cancellation here must not be dressed up as its outcome.
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
                    action_task = asyncio.create_task(action())
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
                except Exception as err:
                    if not future.done():
                        future.set_exception(err)
                else:
                    if not future.done():
                        future.set_result(None)
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
        ``CancelledError``. After this the queue is closed: ``async_write``
        rejects new writes until :meth:`reopen` is called (used when an unload
        fails and the platforms stay loaded).
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

    def reopen(self) -> None:
        """Reopen the queue after a failed unload.

        Resets the closed flag so new writes are accepted again. Writes that
        were queued at shutdown time were already cancelled and are not
        restored; the closed flag only gates *new* submissions.
        """
        self._closed = False

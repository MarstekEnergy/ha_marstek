"""Last-write state tracking for the write-only Passive-mode fields.

The Open API does not report Passive-mode parameters, so the Passive power and
cooldown number entities are write-only and the integration remembers the most
recent value written from Home Assistant. Setting one field must re-send
Passive mode preserving the other, so both fields live on the coordinator as
:class:`PassiveField` instances.

This module deliberately depends only on the standard library so its
concurrency behaviour can be unit-tested without the Home Assistant test stack
(the CI test job does not install ``homeassistant``), matching
``write_queue.py``.
"""

from __future__ import annotations


class PassiveField:
    """Track a write-only field's pending target and last confirmed value.

    ``pending`` is the value a write is trying to reach; ``confirmed`` is the
    last value the device actually acknowledged. They diverge only while a
    write is in flight. The debounced write queue can coalesce rapid repeats
    of the shared "passive" key, so several concurrent ``set_native_value``
    calls race on one field; the value checks below make the rollback/confirm
    steps idempotent across that race.
    """

    __slots__ = ("_confirmed", "_pending")

    def __init__(self) -> None:
        self._pending: int | None = None
        self._confirmed: int | None = None

    @property
    def value(self) -> int | None:
        """The value to send now (the pending target)."""
        return self._pending

    def start_write(self, value: int) -> None:
        """Record a new target value for a write that is about to start."""
        self._pending = value

    def mark_written(self, value: int) -> None:
        """Promote ``value`` to confirmed after a successful write.

        No-ops when a newer target has already replaced ``value``: a write that
        was coalesced away but still reports success through the queue must not
        clobber the confirmation of a newer target.
        """
        if self._pending == value:
            self._confirmed = value

    def rollback(self, value: int) -> None:
        """Revert a failed write back to the last confirmed value.

        No-ops when a newer target has already replaced ``value``: the older,
        coalesced-away write reporting failure must not clobber the newer
        target with an intermediate value that never reached the device.
        """
        if self._pending == value:
            self._pending = self._confirmed

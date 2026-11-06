"""Base entity for Marstek devices."""

from abc import abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, override

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MarstekDataUpdateCoordinator


class MarstekEntity(CoordinatorEntity[MarstekDataUpdateCoordinator]):
    """Base class for Marstek entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        entity_description: EntityDescription,
    ) -> None:
        """Initialize the Marstek entity."""
        super().__init__(coordinator)
        self.entity_description = entity_description

        device_info = coordinator.device_info
        self._attr_unique_id = f"{device_info.stable_id}_{entity_description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_info.stable_id)},
            name=f"Marstek {device_info.device_type} v{device_info.version}",
            manufacturer="Marstek",
            model=device_info.device_type,
            sw_version=str(device_info.version),
        )


class MarstekAssumedStateEntity(RestoreEntity):
    """Write-only control that shows, and remembers, what Home Assistant wrote.

    The device never reports these settings back, so the state shown is
    *assumed*: it reflects the last write Home Assistant sent and the device
    accepted, not a reading. A change made in the Marstek app is invisible
    here, and a restored value is only as current as the last write Home
    Assistant itself sent.

    The control moves only after the device accepts the write, so it never
    shows a value the device did not take -- and a rejected or cancelled write
    leaves the screen untouched, because nothing was drawn ahead of it. The
    cost is that the UI does not move until the debounce window and the round
    trip are over.

    Writes arrive on two paths: a click calls :meth:`async_assume_value`, a
    service call writes through the coordinator and reports back through
    :meth:`async_note_confirmed_value`. Both claim a generation from
    :meth:`async_reserve_write` *before* queueing and adopt their value only
    when their write actually ran, which keeps the shown value equal to the
    newest write the device accepted regardless of the order the confirmations
    come back in.

    The state is kept in Home Assistant's restore-state store, so a restart or
    reload does not blank the control. Subclasses adopt a restored value only
    if the entity could still use it (range check, known option), so a stale
    value from an older firmware or device model is dropped instead of being
    shown as if it were valid.
    """

    # Supplied by the CoordinatorEntity base mixed in by every concrete entity;
    # declared here so the service-sync path can reach it without a cast.
    coordinator: MarstekDataUpdateCoordinator

    _attr_assumed_state = True

    # The last value the device confirmed; also what the control shows.
    _confirmed_value: Any = None

    # Generation of that value: confirmations can resolve out of order, so only
    # a newer write may replace what is shown.
    _confirmed_generation: int = 0

    # Numbers write requests in arrival order (see the class docstring).
    _assume_generation: int = 0

    @override
    async def async_added_to_hass(self) -> None:
        """Adopt the state the entity had before the restart."""
        await super().async_added_to_hass()
        # Register before reading the restore cache: the read awaits, and a
        # service call confirmed during that await has to reach this control
        # instead of being dropped because it is not tracked yet.
        self.coordinator.async_register_assumed_entity(
            self.entity_description.key, self
        )
        state = await self.async_get_last_state()
        if state is None or self._confirmed_generation:
            # A write was confirmed while the cache was loading, so the cached
            # value is older than what the device now holds.
            return
        if (restored := self._async_restore_assumed_state(state.state)) is not None:
            # A restored value is by definition the last one that was written,
            # so it is also the one a later write has to outrank.
            self._confirmed_value = restored

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Stop accepting service-triggered updates for this control."""
        self.coordinator.async_unregister_assumed_entity(
            self.entity_description.key, self
        )
        await super().async_will_remove_from_hass()

    @callback
    def async_reserve_write(self) -> int:
        """Claim the next generation for a write that is not queued yet.

        A service call claims its turn here *before* queueing; stamping the
        token on confirmation instead would rank an old write as newer than the
        click that superseded it.
        """
        self._assume_generation += 1
        return self._assume_generation

    @callback
    def async_note_confirmed_value(self, value: Any, generation: int) -> None:
        """Adopt a value a write sent and the device confirmed.

        Both paths end here: a click (through :meth:`async_assume_value`) and a
        service call (through the coordinator). ``generation`` is the token from
        :meth:`async_reserve_write`; only a write newer than the value on screen
        is adopted, so a slow confirmation cannot replace a later value.
        """
        if generation <= self._confirmed_generation:
            return
        self._confirmed_generation = generation
        self._confirmed_value = value
        self._apply_assumed_value(value)
        self.async_write_ha_state()

    async def async_assume_value(
        self, value: Any, action: Callable[[], Awaitable[bool]]
    ) -> None:
        """Write ``value`` and show it once the device confirms it.

        ``action`` reports whether its write actually ran on the device;
        ``False`` means the queue coalesced it away, so the value never reached
        the device and is not shown. An error or cancellation propagates
        untouched: the control still shows the last confirmed value, so there
        is nothing to undo.
        """
        generation = self.async_reserve_write()
        if await action():
            self.async_note_confirmed_value(value, generation)

    @abstractmethod
    def _apply_assumed_value(self, value: Any) -> None:
        """Put a value into the entity's own state field."""

    @abstractmethod
    def _async_restore_assumed_state(self, value: str) -> Any | None:
        """Adopt a restored state string, returning it, or None if unusable."""

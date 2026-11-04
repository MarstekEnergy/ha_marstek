"""Switch platform for Marstek devices."""

import time
from typing import override

from aiomarstek import MarstekModeConfig, MarstekSetError
from homeassistant.components.switch import (
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MarstekConfigEntry
from .entity import MarstekEntity

# Sys.GetInfo (which reports the Bluetooth lock and the LED) belongs to the
# slow extras group, so its values only show up every few polls. A fresh write
# is therefore shown optimistically for this long, otherwise the UI would snap
# the toggle back to the stale polled value for a couple of minutes.
OPTIMISTIC_HOLD_SECONDS = 300.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MarstekConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Marstek switch entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            MarstekBluetoothSwitch(coordinator),
            MarstekLedSwitch(coordinator),
            # The UPS switch is hidden: UPS is already an option in the
            # operating-mode select, and the switch's "off" action
            # (ups_enabled=False) has no documented use case.
            # MarstekUpsSwitch(coordinator),
        ]
    )


class MarstekSwitch(MarstekEntity, SwitchEntity):
    """Representation of a Marstek switch entity."""

    entity_description: SwitchEntityDescription


class MarstekReportedSwitch(MarstekSwitch):
    """Switch whose state the device reports in Sys.GetInfo.

    ``ble_adv_en`` and ``led_state`` mirror what ``Ble.Adv`` and ``Led.Ctrl``
    write, so these switches are no longer write-only. The readback is polled
    with the slow extras group, so a value written from Home Assistant is held
    optimistically until the device confirms it (or the hold expires), which
    keeps the toggle from flipping back for one or two polls.
    """

    def __init__(
        self,
        coordinator: MarstekConfigEntry.runtime_data,
        description: SwitchEntityDescription,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, description)
        self._optimistic_state: bool | None = None
        self._optimistic_until = 0.0

    def _reported_state(self) -> bool | None:
        """Return the state the device last reported."""
        raise NotImplementedError

    def _hold_optimistic(self, value: bool) -> None:
        """Show ``value`` until the device reports it or the hold expires."""
        self._optimistic_state = value
        self._optimistic_until = time.monotonic() + OPTIMISTIC_HOLD_SECONDS
        self.async_write_ha_state()

    @property
    @override
    def is_on(self) -> bool | None:
        """Return the written value while it is pending, else the device's."""
        if (
            self._optimistic_state is not None
            and time.monotonic() < self._optimistic_until
        ):
            return self._optimistic_state
        return self._reported_state()

    @override
    async def _handle_coordinator_update(self) -> None:
        """Drop the optimistic value once the device agrees or the hold ends."""
        if self._optimistic_state is not None and (
            self._reported_state() == self._optimistic_state
            or time.monotonic() >= self._optimistic_until
        ):
            self._optimistic_state = None
        await super()._handle_coordinator_update()


class MarstekBluetoothSwitch(MarstekReportedSwitch):
    """Bluetooth lock switch.

    ``Ble.Adv`` writes the lock and ``Sys.GetInfo``'s ``ble_adv_en`` reports
    it back, so the switch follows the device. ``ble_adv_en`` is read as
    "Bluetooth advertising is enabled" (1: on), the same polarity the write
    sends; a device that answers the opposite way would show it immediately by
    flipping the toggle back after a poll.
    """

    def __init__(self, coordinator: MarstekConfigEntry.runtime_data) -> None:
        """Initialize the Bluetooth switch."""
        super().__init__(
            coordinator,
            SwitchEntityDescription(
                key="bluetooth_advertising",
                translation_key="bluetooth_advertising",
                name="Bluetooth advertising",
                icon="mdi:bluetooth",
            ),
        )

    @override
    def _reported_state(self) -> bool | None:
        """Return the Bluetooth lock state the device reports."""
        data = self.coordinator.data
        return None if data is None else data.bluetooth_advertising

    @override
    async def async_turn_on(self) -> None:
        """Turn Bluetooth advertising on."""
        await self._set_advertising(True)

    @override
    async def async_turn_off(self) -> None:
        """Turn Bluetooth advertising off."""
        await self._set_advertising(False)

    async def _set_advertising(self, enabled: bool) -> None:
        coordinator = self.coordinator
        try:
            await coordinator.async_write(
                "bluetooth",
                lambda: coordinator.udp_client.set_bluetooth_advertising(
                    coordinator.device_ip, enabled, port=coordinator.device_port
                ),
            )
        except MarstekSetError as err:
            raise HomeAssistantError(
                f"Toggling Bluetooth advertising failed: {err.reason}"
            ) from err
        self._hold_optimistic(enabled)
        self._notify_success(
            f"Bluetooth advertising {'enabled' if enabled else 'disabled'}"
        )


class MarstekLedSwitch(MarstekReportedSwitch):
    """Front-panel LED switch.

    ``Led.Ctrl`` writes the LED and ``Sys.GetInfo``'s ``led_state`` reports the
    same 1: on / 0: off flag back, so the switch follows the device.
    """

    def __init__(self, coordinator: MarstekConfigEntry.runtime_data) -> None:
        """Initialize the LED switch."""
        super().__init__(
            coordinator,
            SwitchEntityDescription(
                key="led",
                translation_key="led",
                name="Front-panel LED",
                icon="mdi:led-on",
            ),
        )

    @override
    def _reported_state(self) -> bool | None:
        """Return the front-panel LED state the device reports."""
        data = self.coordinator.data
        return None if data is None else data.led

    @override
    async def async_turn_on(self) -> None:
        """Turn the LED on."""
        await self._set_led(True)

    @override
    async def async_turn_off(self) -> None:
        """Turn the LED off."""
        await self._set_led(False)

    async def _set_led(self, on: bool) -> None:
        coordinator = self.coordinator
        try:
            await coordinator.async_write(
                "led",
                lambda: coordinator.udp_client.set_led(
                    coordinator.device_ip, on, port=coordinator.device_port
                ),
            )
        except MarstekSetError as err:
            raise HomeAssistantError(
                f"Toggling the front-panel LED failed: {err.reason}"
            ) from err
        self._hold_optimistic(on)
        self._notify_success(f"Front-panel LED {'enabled' if on else 'disabled'}")


class MarstekUpsSwitch(MarstekSwitch):
    """UPS-mode enable switch.

    The Open API does not report the current UPS-enable flag, so this
    switch is write-only: toggling it engages UPS mode with the flag set,
    and its state stays unknown otherwise.
    """

    def __init__(self, coordinator: MarstekConfigEntry.runtime_data) -> None:
        """Initialize the UPS switch."""
        super().__init__(
            coordinator,
            SwitchEntityDescription(
                key="ups",
                translation_key="ups",
                name="UPS mode",
                icon="mdi:ups",
            ),
        )

    @property
    @override
    def is_on(self) -> bool | None:
        """UPS-enable state is write-only until firmware reports it."""
        return None

    @override
    async def async_turn_on(self) -> None:
        """Engage UPS mode with the UPS flag enabled."""
        await self._set_ups(True)

    @override
    async def async_turn_off(self) -> None:
        """Engage UPS mode with the UPS flag disabled."""
        await self._set_ups(False)

    async def _set_ups(self, enabled: bool) -> None:
        coordinator = self.coordinator
        config = MarstekModeConfig(mode="ups", ups_enabled=enabled)
        try:
            await coordinator.async_write(
                "device_mode",
                lambda: coordinator.udp_client.set_device_mode(
                    coordinator.device_ip, config, port=coordinator.device_port
                ),
            )
        except MarstekSetError as err:
            raise HomeAssistantError(f"Toggling UPS mode failed: {err.reason}") from err
        self._notify_success(f"UPS mode {'enabled' if enabled else 'disabled'}")

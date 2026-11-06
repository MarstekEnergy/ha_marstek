"""Switch platform for Marstek devices."""

from typing import Any, override

from aiomarstek import MarstekSetError
from homeassistant.components.switch import (
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MarstekConfigEntry, MarstekDataUpdateCoordinator
from .entity import MarstekAssumedStateEntity, MarstekEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MarstekConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Marstek switch entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        [MarstekBluetoothSwitch(coordinator), MarstekLedSwitch(coordinator)]
    )


class MarstekSwitch(MarstekEntity, SwitchEntity):
    """Representation of a Marstek switch entity."""

    entity_description: SwitchEntityDescription


class MarstekBluetoothSwitch(MarstekSwitch, MarstekAssumedStateEntity):
    """Bluetooth lock switch.

    On locks Bluetooth, which stops advertising; off unlocks it and resumes
    advertising. Ble.Adv's ``enable`` parameter describes advertising, so the
    lock is its inverse: locked sends ``enable=0`` and unlocked sends
    ``enable=1``. BLE.GetStatus only reports the connection state, not the
    lock flag, so the position shown is the last one Home Assistant wrote.
    """

    def __init__(self, coordinator: MarstekDataUpdateCoordinator) -> None:
        """Initialize the Bluetooth switch."""
        super().__init__(
            coordinator,
            SwitchEntityDescription(
                key="bluetooth_advertising",
                translation_key="bluetooth_advertising",
                name="Bluetooth lock",
                icon="mdi:bluetooth",
            ),
        )

    @override
    def _apply_assumed_value(self, value: Any) -> None:
        """Show a toggle position."""
        self._attr_is_on = value

    @override
    def _async_restore_assumed_state(self, value: str) -> bool | None:
        """Adopt a restored toggle state, ignoring anything else."""
        if value not in (STATE_ON, STATE_OFF):
            return None
        enabled = value == STATE_ON
        self._attr_is_on = enabled
        return enabled

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Lock Bluetooth, stopping advertising."""
        await self._set_lock(True)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Unlock Bluetooth, resuming advertising."""
        await self._set_lock(False)

    async def _set_lock(self, locked: bool) -> None:
        coordinator = self.coordinator
        try:
            await self.async_assume_value(
                locked,
                lambda: coordinator.async_write(
                    "bluetooth",
                    lambda: coordinator.udp_client.set_bluetooth_advertising(
                        # Ble.Adv's ``enable`` describes advertising, so the
                        # lock is its inverse: locked sends 0.
                        coordinator.device_ip,
                        not locked,
                        port=coordinator.device_port,
                    ),
                ),
            )
        except MarstekSetError as err:
            raise HomeAssistantError(
                f"Toggling the Bluetooth lock failed: {err.reason}"
            ) from err


class MarstekLedSwitch(MarstekSwitch, MarstekAssumedStateEntity):
    """Front-panel LED switch.

    The local API does not report the current LED state, so the position shown
    is the last one Home Assistant wrote.
    """

    def __init__(self, coordinator: MarstekDataUpdateCoordinator) -> None:
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
    def _apply_assumed_value(self, value: Any) -> None:
        """Show a toggle position."""
        self._attr_is_on = value

    @override
    def _async_restore_assumed_state(self, value: str) -> bool | None:
        """Adopt a restored toggle state, ignoring anything else."""
        if value not in (STATE_ON, STATE_OFF):
            return None
        enabled = value == STATE_ON
        self._attr_is_on = enabled
        return enabled

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the LED on."""
        await self._set_led(True)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the LED off."""
        await self._set_led(False)

    async def _set_led(self, on: bool) -> None:
        coordinator = self.coordinator
        try:
            await self.async_assume_value(
                on,
                lambda: coordinator.async_write(
                    "led",
                    lambda: coordinator.udp_client.set_led(
                        coordinator.device_ip, on, port=coordinator.device_port
                    ),
                ),
            )
        except MarstekSetError as err:
            raise HomeAssistantError(
                f"Toggling the front-panel LED failed: {err.reason}"
            ) from err

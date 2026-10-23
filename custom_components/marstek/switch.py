"""Switch platform for Marstek devices."""

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
            MarstekUpsSwitch(coordinator),
        ]
    )


class MarstekSwitch(MarstekEntity, SwitchEntity):
    """Representation of a Marstek switch entity."""

    entity_description: SwitchEntityDescription


class MarstekBluetoothSwitch(MarstekSwitch):
    """Bluetooth advertising lock switch.

    The Open API reports only the Bluetooth connection state, not the
    advertising lock flag, so this switch is write-only: its state stays
    unknown until it is toggled.
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

    @property
    @override
    def is_on(self) -> bool | None:
        """Bluetooth advertising state is write-only until firmware reports it."""
        return None

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
        self._notify_success(f"蓝牙广播已{'开启' if enabled else '关闭'}")


class MarstekLedSwitch(MarstekSwitch):
    """Front-panel LED switch.

    The Open API does not report the current LED state, so this switch
    is write-only: its state stays unknown until it is toggled.
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

    @property
    @override
    def is_on(self) -> bool | None:
        """LED state is write-only until firmware reports it."""
        return None

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
        self._notify_success(f"面板 LED 已{'开启' if on else '关闭'}")


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
        self._notify_success(f"UPS 模式已{'开启' if enabled else '关闭'}")

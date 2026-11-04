"""Button platform for Marstek devices."""

from typing import override

from aiomarstek import MarstekSetError
from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
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
    """Set up Marstek button entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            MarstekRestartButton(coordinator),
            MarstekFactoryResetButton(coordinator),
            MarstekFactoryResetKeepDataButton(coordinator),
        ]
    )


class MarstekButton(MarstekEntity, ButtonEntity):
    """Representation of a Marstek button entity."""

    entity_description: ButtonEntityDescription


class MarstekRestartButton(MarstekButton):
    """Restart the device through Sys.Reboot (type 1, the EMS).

    The EMS is the controller the local API talks to, so restarting it is the
    device restart users expect. The write is queued like any other control,
    and the client treats a dropped reply as "restart sent" -- a subsystem
    that is going down usually cannot answer, and the entities simply keep
    their last reading until the device is back.
    """

    def __init__(self, coordinator: MarstekConfigEntry.runtime_data) -> None:
        """Initialize the restart button."""
        super().__init__(
            coordinator,
            ButtonEntityDescription(
                key="restart",
                translation_key="restart",
                name="Restart device",
                icon="mdi:restart",
                device_class=ButtonDeviceClass.RESTART,
            ),
        )

    @override
    async def async_press(self) -> None:
        """Restart the device's EMS."""
        coordinator = self.coordinator
        try:
            await coordinator.async_write(
                "restart",
                lambda: coordinator.udp_client.reboot(
                    coordinator.device_ip, port=coordinator.device_port
                ),
            )
        except MarstekSetError as err:
            raise HomeAssistantError(
                f"Restarting the device failed: {err.reason}"
            ) from err
        except OSError as err:
            # The library treats a missing *reply* as "restart sent", but a
            # socket/send failure means the datagram may never have left, so it
            # surfaces as OSError (TimeoutError included) instead.
            raise HomeAssistantError(
                f"Could not send the restart command: {err}"
            ) from err


class MarstekFactoryResetButton(MarstekButton):
    """Factory reset button (clears accumulated data).

    This performs a destructive reset that clears the lifetime energy
    counters and other accumulated data. Use ``Factory reset (keep
    data)`` to preserve them.
    """

    def __init__(self, coordinator: MarstekConfigEntry.runtime_data) -> None:
        """Initialize the factory reset button."""
        super().__init__(
            coordinator,
            ButtonEntityDescription(
                key="factory_reset",
                translation_key="factory_reset",
                name="Factory reset",
                icon="mdi:restart-alert",
            ),
        )

    @override
    async def async_press(self) -> None:
        """Trigger a factory reset that clears accumulated data."""
        coordinator = self.coordinator
        try:
            await coordinator.async_write(
                "factory_reset",
                lambda: coordinator.udp_client.factory_reset(
                    coordinator.device_ip, port=coordinator.device_port
                ),
            )
        except MarstekSetError as err:
            raise HomeAssistantError(f"Factory reset failed: {err.reason}") from err


class MarstekFactoryResetKeepDataButton(MarstekButton):
    """Factory reset button (keeps accumulated data)."""

    def __init__(self, coordinator: MarstekConfigEntry.runtime_data) -> None:
        """Initialize the factory reset button."""
        super().__init__(
            coordinator,
            ButtonEntityDescription(
                key="factory_reset_keep_data",
                translation_key="factory_reset_keep_data",
                name="Factory reset (keep data)",
                icon="mdi:restart",
            ),
        )

    @override
    async def async_press(self) -> None:
        """Trigger a factory reset that keeps accumulated data."""
        coordinator = self.coordinator
        try:
            await coordinator.async_write(
                "factory_reset_keep_data",
                lambda: coordinator.udp_client.factory_reset(
                    coordinator.device_ip,
                    keep_accumulated_data=True,
                    port=coordinator.device_port,
                ),
            )
        except MarstekSetError as err:
            raise HomeAssistantError(f"Factory reset failed: {err.reason}") from err

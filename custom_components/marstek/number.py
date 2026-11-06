"""Number platform for Marstek devices."""

from typing import Any, override

from aiomarstek import MarstekSetError
from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOD_MAX, DOD_MIN
from .coordinator import MarstekConfigEntry, MarstekDataUpdateCoordinator
from .entity import MarstekAssumedStateEntity, MarstekEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MarstekConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Marstek number entities."""
    coordinator = entry.runtime_data
    async_add_entities([MarstekDODNumber(coordinator)])


class MarstekNumber(MarstekEntity, NumberEntity):
    """Representation of a Marstek number entity."""

    entity_description: NumberEntityDescription


class MarstekDODNumber(MarstekNumber, MarstekAssumedStateEntity):
    """Discharge depth (DOD) configuration entity.

    The local API does not report the current DOD, so this control cannot read
    its value back; the assumed-state base class shows what Home Assistant
    wrote.
    """

    def __init__(self, coordinator: MarstekDataUpdateCoordinator) -> None:
        """Initialize the DOD number entity."""
        super().__init__(
            coordinator,
            NumberEntityDescription(
                key="dod",
                translation_key="dod",
                name="Discharge depth (DOD)",
                native_min_value=DOD_MIN,
                native_max_value=DOD_MAX,
                native_step=1,
                mode=NumberMode.SLIDER,
            ),
        )
        # The unit lives on the entity so the card renders "50 %" even when the
        # number platform stops falling back to the description for it.
        self._attr_native_unit_of_measurement = PERCENTAGE

    @override
    def _apply_assumed_value(self, value: Any) -> None:
        """Show a depth."""
        self._attr_native_value = value

    @override
    def _async_restore_assumed_state(self, value: str) -> float | None:
        """Adopt a restored depth, dropping values outside the DOD range."""
        try:
            depth = float(value)
        except ValueError:
            return None
        if not DOD_MIN <= depth <= DOD_MAX:
            return None
        self._attr_native_value = depth
        return depth

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Set the discharge depth on the device."""
        depth = int(value)
        coordinator = self.coordinator
        try:
            await self.async_assume_value(
                depth,
                lambda: coordinator.async_write(
                    "dod",
                    lambda: coordinator.udp_client.set_dod(
                        coordinator.device_ip, depth, port=coordinator.device_port
                    ),
                ),
            )
        except MarstekSetError as err:
            raise HomeAssistantError(
                f"Setting the discharge depth failed: {err.reason}"
            ) from err

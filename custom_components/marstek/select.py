"""Select platform for Marstek devices."""

from typing import override

from aiomarstek import (
    CMD_ES_SET_MODE,
    MarstekModeConfig,
    MarstekSetError,
    build_command,
)

from homeassistant.components.select import (
    SelectEntity,
    SelectEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SELECTABLE_MODE_OPTIONS, power_versions_for
from .coordinator import MarstekConfigEntry
from .entity import MarstekEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MarstekConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Marstek select entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            MarstekModeSelect(coordinator),
            MarstekPowerVersionSelect(coordinator),
        ]
    )


class MarstekSelect(MarstekEntity, SelectEntity):
    """Representation of a Marstek select entity."""

    entity_description: SelectEntityDescription


class MarstekModeSelect(MarstekSelect):
    """Device operating mode selector.

    Auto, AI, UPS and Manual are selectable here. Manual switches the device
    into Manual mode without a time-slot schedule; the user configures the
    slots in the Marstek app (a notification reminds them). Passive needs
    power/cd_time parameters and is set through the ``set_mode`` service.
    The current mode is still shown even when it is Passive (a select may
    report a ``current_option`` that is not among its ``options``).
    """

    def __init__(self, coordinator: MarstekConfigEntry.runtime_data) -> None:
        """Initialize the mode select entity."""
        super().__init__(
            coordinator,
            SelectEntityDescription(
                key="device_mode_select",
                translation_key="device_mode",
                name="Operating mode",
                options=list(SELECTABLE_MODE_OPTIONS),
            ),
        )

    @override
    async def _handle_coordinator_update(self) -> None:
        """Sync the select with the device's real mode after each poll."""
        data = self.coordinator.data
        if data is not None:
            self._attr_current_option = data.device_mode
        await super()._handle_coordinator_update()

    @override
    async def async_select_option(self, option: str) -> None:
        """Set the operating mode on the device."""
        self._attr_current_option = option
        self.async_write_ha_state()
        coordinator = self.coordinator
        mode = option.lower()
        try:
            if mode == "manual":
                # Manual mode normally requires a full time-slot schedule, but
                # here we only switch the device into Manual mode and let the
                # user configure the slots in the Marstek app. The library's
                # MarstekModeConfig blocks Manual without slots, so the raw
                # ES.SetMode command is sent with just the mode field.
                await coordinator.async_write(
                    "device_mode",
                    lambda: coordinator.udp_client._async_send_set_command(
                        build_command(
                            CMD_ES_SET_MODE,
                            {"id": 0, "config": {"mode": "Manual"}},
                        ),
                        coordinator.device_ip,
                        CMD_ES_SET_MODE,
                        port=coordinator.device_port,
                    ),
                )
            else:
                await coordinator.async_write(
                    "device_mode",
                    lambda: coordinator.udp_client.set_device_mode(
                        coordinator.device_ip,
                        MarstekModeConfig(mode=mode),
                        port=coordinator.device_port,
                    ),
                )
        except MarstekSetError as err:
            raise HomeAssistantError(
                f"Setting the operating mode failed: {err.reason}"
            ) from err
        if mode == "manual":
            self._notify_success(
                "Switched to Manual mode. Configure the time slots in the Marstek app"
            )
        else:
            self._notify_success(f"Operating mode switched to {option}")


class MarstekPowerVersionSelect(MarstekSelect):
    """Charge/discharge power version selector.

    The documented power versions differ per model (1200/1500 W for
    Venus A, 2200 W for Venus D, 2500 W for Venus E); the options are
    narrowed to the versions documented for this device's type. Unknown
    models fall back to the full set and let the device reject a value
    it does not support.
    """

    def __init__(self, coordinator: MarstekConfigEntry.runtime_data) -> None:
        """Initialize the power version select entity."""
        super().__init__(
            coordinator,
            SelectEntityDescription(
                key="power_version",
                translation_key="power_version",
                name="Power version",
                # Populated in available_options / _attr_options below.
                options=[],
            ),
        )
        self._attr_options = [
            str(v) for v in power_versions_for(coordinator.device_info.device_type)
        ]

    @property
    @override
    def current_option(self) -> str | None:
        """Power version is write-only until firmware reports it."""
        return None

    @override
    async def async_select_option(self, option: str) -> None:
        """Set the power version on the device."""
        coordinator = self.coordinator
        try:
            await coordinator.async_write(
                "power_version",
                lambda: coordinator.udp_client.set_power_version(
                    coordinator.device_ip,
                    int(option),
                    port=coordinator.device_port,
                ),
            )
        except MarstekSetError as err:
            raise HomeAssistantError(
                f"Setting the power version failed: {err.reason}"
            ) from err
        self._notify_success(f"Power version set to {option} W")

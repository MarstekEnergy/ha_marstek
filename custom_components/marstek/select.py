"""Select platform for Marstek devices."""

from typing import Any, override

from aiomarstek import MarstekModeConfig, MarstekSetError
from homeassistant.components.select import (
    SelectEntity,
    SelectEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SELECTABLE_MODE_OPTIONS, power_versions_for
from .coordinator import MarstekConfigEntry, MarstekDataUpdateCoordinator
from .entity import MarstekAssumedStateEntity, MarstekEntity


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

    Auto and UPS are selectable here. AI, Manual and Passive are configured in
    the Marstek app and are not written from HA: Manual only works together
    with a time-slot schedule that is unconfirmed on real hardware, and Passive
    has no writable payload. Every poll still syncs the device's real mode into
    ``_attr_current_option``, but Home Assistant maps an option that is not in
    ``options`` to unknown, so a device running AI, Manual or Passive shows
    ``unknown`` here -- the device-mode sensor is what reports those modes. The
    options are deliberately not widened just to display them.
    """

    def __init__(self, coordinator: MarstekDataUpdateCoordinator) -> None:
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
        # The config entry refreshes the coordinator before the platforms are
        # set up, and subscribing to it does not replay that result, so seed
        # the option from the data already in hand; otherwise the select shows
        # unknown until the next poll.
        data = coordinator.data
        if data is not None:
            self._attr_current_option = data.device_mode

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Sync the select with the device's real mode after each poll."""
        self._sync_with_device()
        super()._handle_coordinator_update()

    def _sync_with_device(self) -> None:
        """Take the mode the device reports, or unknown if it has none."""
        data = self.coordinator.data
        self._attr_current_option = data.device_mode if data is not None else None

    @override
    async def async_select_option(self, option: str) -> None:
        """Set the operating mode on the device."""
        self._attr_current_option = option
        self.async_write_ha_state()
        coordinator = self.coordinator
        mode = option.lower()
        try:
            await coordinator.async_write(
                "device_mode",
                lambda: coordinator.udp_client.set_device_mode(
                    coordinator.device_ip,
                    MarstekModeConfig(mode=mode),
                    port=coordinator.device_port,
                ),
            )
        except MarstekSetError as err:
            # The device rejected the command, so the optimistically shown
            # option is wrong: go back to what the device actually reports
            # (never to a cached "previous" value, which may be stale too).
            self._sync_with_device()
            self.async_write_ha_state()
            raise HomeAssistantError(
                f"Setting the operating mode failed: {err.reason}"
            ) from err
        except BaseException:
            # A cancelled write never reaches the device, so the mode it shows
            # is one the device did not adopt. Snap back to the reported mode
            # instead of leaving the unadopted option on screen until the next
            # poll -- and only for this entity: the write is not ours to undo.
            self._sync_with_device()
            self.async_write_ha_state()
            raise


class MarstekPowerVersionSelect(MarstekSelect, MarstekAssumedStateEntity):
    """Charge/discharge power version selector.

    The documented power versions differ per model (1200/1500 W for Venus A,
    2200 W for Venus D, 2500 W for Venus E), so the options are narrowed to
    this device's type; unknown models fall back to the full set and let the
    device reject a value it does not support. The firmware does not report the
    active version back, so the selected option is the last one Home Assistant
    set.
    """

    def __init__(self, coordinator: MarstekDataUpdateCoordinator) -> None:
        """Initialize the power version select entity."""
        super().__init__(
            coordinator,
            SelectEntityDescription(
                key="power_version",
                translation_key="power_version",
                name="Maximum Grid power",
                # Model-dependent; assigned to _attr_options below.
                options=[],
            ),
        )
        self._attr_options = [
            str(v) for v in power_versions_for(coordinator.device_info.device_type)
        ]
        # ``_attr_current_option`` keeps its None default: the device does not
        # report the active version, so the option stays unknown until a write
        # is confirmed or a previous value is restored.

    @override
    def _apply_assumed_value(self, value: Any) -> None:
        """Show a power version."""
        self._attr_current_option = value

    @override
    def _async_restore_assumed_state(self, value: str) -> str | None:
        """Adopt a restored version, dropping options this model cannot take."""
        if value not in self._attr_options:
            return None
        self._attr_current_option = value
        return value

    @override
    async def async_select_option(self, option: str) -> None:
        """Set the power version on the device."""
        coordinator = self.coordinator
        try:
            await self.async_assume_value(
                option,
                lambda: coordinator.async_write(
                    "power_version",
                    lambda: coordinator.udp_client.set_power_version(
                        coordinator.device_ip,
                        int(option),
                        port=coordinator.device_port,
                    ),
                ),
            )
        except MarstekSetError as err:
            raise HomeAssistantError(
                f"Setting the power version failed: {err.reason}"
            ) from err

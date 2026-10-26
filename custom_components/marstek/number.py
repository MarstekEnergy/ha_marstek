"""Number platform for Marstek devices."""

import asyncio
from typing import override

from aiomarstek import (
    POWER_VERSIONS,
    MarstekModeConfig,
    MarstekPassiveConfig,
    MarstekSetError,
)
from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOD_MAX, DOD_MIN
from .coordinator import MarstekConfigEntry
from .entity import MarstekEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MarstekConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Marstek number entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            MarstekDODNumber(coordinator),
            # Passive power/cooldown controls are hidden for now: the Open
            # API does not reliably report the current Passive parameters, so
            # the write-only numbers would show "unknown" indefinitely.
            # Passive mode is still settable via the set_mode service.
            # MarstekPassivePowerNumber(coordinator),
            # MarstekPassiveCdTimeNumber(coordinator),
        ]
    )


class MarstekNumber(MarstekEntity, NumberEntity):
    """Representation of a Marstek number entity."""

    entity_description: NumberEntityDescription


class MarstekDODNumber(MarstekNumber):
    """Discharge depth (DOD) configuration entity.

    The Open API does not report the current DOD, so this entity is
    write-only: its state stays unknown until a new value is written.
    """

    def __init__(self, coordinator: MarstekConfigEntry.runtime_data) -> None:
        """Initialize the DOD number entity."""
        super().__init__(
            coordinator,
            NumberEntityDescription(
                key="dod",
                translation_key="dod",
                name="Discharge depth",
                native_min_value=DOD_MIN,
                native_max_value=DOD_MAX,
                native_step=1,
                native_unit_of_measurement=PERCENTAGE,
                mode=NumberMode.SLIDER,
            ),
        )

    @property
    @override
    def native_value(self) -> float | None:
        """Discharge depth is write-only until firmware reports it."""
        return None

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Set the discharge depth on the device."""
        value = int(value)
        coordinator = self.coordinator
        try:
            await coordinator.async_write(
                "dod",
                lambda: coordinator.udp_client.set_dod(
                    coordinator.device_ip, value, port=coordinator.device_port
                ),
            )
        except MarstekSetError as err:
            raise HomeAssistantError(
                f"Setting the discharge depth failed: {err.reason}"
            ) from err
        self._notify_success(f"放电深度已设置为 {value}%")


class MarstekPassivePowerNumber(MarstekNumber):
    """Passive-mode charge/discharge power (write-only).

    The Open API does not report Passive parameters, so this entity shows
    the most recent value written from HA. Writing it (re)sends Passive
    mode with this power and the cooldown last written via its sibling
    entity, so the two fields do not clobber each other.
    """

    def __init__(self, coordinator: MarstekConfigEntry.runtime_data) -> None:
        """Initialize the Passive power number entity."""
        super().__init__(
            coordinator,
            NumberEntityDescription(
                key="passive_power",
                translation_key="passive_power",
                name="Passive power",
                native_min_value=0,
                native_max_value=max(POWER_VERSIONS),
                native_step=1,
                native_unit_of_measurement=UnitOfPower.WATT,
                mode=NumberMode.SLIDER,
            ),
        )

    @property
    @override
    def native_value(self) -> float | None:
        """Passive power is write-only until firmware reports it."""
        return None

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Set the Passive power, preserving the cooldown.

        The value is tracked internally (not shown) so a later change to
        the cooldown re-sends Passive mode with this power instead of
        clobbering it to 0. The target is committed to the field's confirmed
        value only on success; on failure the field rolls back to the last
        confirmed value, never to an intermediate value that never reached the
        device.
        """
        coordinator = self.coordinator
        target = int(value)
        coordinator.passive_power.start_write(target)
        try:
            await self._send_passive(coordinator)
        except (HomeAssistantError, asyncio.CancelledError):
            # The write failed, or was cancelled while still queued and so
            # never reached the device. (A write that already started reports
            # its real outcome through the queue, so a genuine success does not
            # land here.) Revert to the last confirmed value so a later
            # sibling write does not re-send this phantom power.
            coordinator.passive_power.rollback(target)
            raise
        else:
            coordinator.passive_power.mark_written(target)

    async def _send_passive(self, coordinator: MarstekConfigEntry.runtime_data) -> None:
        """Re-send Passive mode with the current power and cooldown."""
        config = MarstekModeConfig(
            mode="passive",
            passive=MarstekPassiveConfig(
                power=coordinator.passive_power.value or 0,
                cd_time=coordinator.passive_cd_time.value or 0,
            ),
        )
        try:
            await coordinator.async_write(
                "passive",
                lambda: coordinator.udp_client.set_device_mode(
                    coordinator.device_ip, config, port=coordinator.device_port
                ),
            )
        except MarstekSetError as err:
            raise HomeAssistantError(
                f"Setting Passive power failed: {err.reason}"
            ) from err
        self._notify_success(
            f"Passive 功率已设置为 {coordinator.passive_power.value or 0} W"
        )


class MarstekPassiveCdTimeNumber(MarstekNumber):
    """Passive-mode cooldown time in seconds (write-only).

    See MarstekPassivePowerNumber: write-only, preserves the sibling power
    field on write so the two do not clobber each other.
    """

    def __init__(self, coordinator: MarstekConfigEntry.runtime_data) -> None:
        """Initialize the Passive cooldown number entity."""
        super().__init__(
            coordinator,
            NumberEntityDescription(
                key="passive_cd_time",
                translation_key="passive_cd_time",
                name="Passive cooldown",
                native_min_value=0,
                native_max_value=86400,
                native_step=1,
                native_unit_of_measurement=UnitOfTime.SECONDS,
                mode=NumberMode.SLIDER,
            ),
        )

    @property
    @override
    def native_value(self) -> float | None:
        """Passive cooldown is write-only until firmware reports it."""
        return None

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Set the Passive cooldown, preserving the power."""
        coordinator = self.coordinator
        target = int(value)
        coordinator.passive_cd_time.start_write(target)
        try:
            await self._send_passive(coordinator)
        except (HomeAssistantError, asyncio.CancelledError):
            # The write failed, or was cancelled while still queued and so
            # never reached the device. (A write that already started reports
            # its real outcome through the queue, so a genuine success does not
            # land here.) Revert to the last confirmed value so a later
            # sibling write does not re-send this phantom cooldown.
            coordinator.passive_cd_time.rollback(target)
            raise
        else:
            coordinator.passive_cd_time.mark_written(target)

    async def _send_passive(self, coordinator: MarstekConfigEntry.runtime_data) -> None:
        """Re-send Passive mode with the current power and cooldown."""
        config = MarstekModeConfig(
            mode="passive",
            passive=MarstekPassiveConfig(
                power=coordinator.passive_power.value or 0,
                cd_time=coordinator.passive_cd_time.value or 0,
            ),
        )
        try:
            await coordinator.async_write(
                "passive",
                lambda: coordinator.udp_client.set_device_mode(
                    coordinator.device_ip, config, port=coordinator.device_port
                ),
            )
        except MarstekSetError as err:
            raise HomeAssistantError(
                f"Setting Passive cooldown failed: {err.reason}"
            ) from err
        self._notify_success(
            f"Passive 冷却时间已设置为 {coordinator.passive_cd_time.value or 0} s"
        )

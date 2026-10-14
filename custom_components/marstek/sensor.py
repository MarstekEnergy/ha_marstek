"""Sensor platform for Marstek devices."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import override

from aiomarstek import MarstekDeviceStatus

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .const import (
    BATTERY_STATUS_OPTIONS,
    DEVICE_MODE_OPTIONS,
    ERROR_STATE_OPTIONS,
    PV_DEVICE_TYPES,
    PV_STATE_OPTIONS,
)
from .coordinator import MarstekConfigEntry
from .entity import MarstekEntity

_LOGGER = logging.getLogger(__name__)

# Polling gaps longer than this (e.g. Home Assistant downtime) are not counted
# towards grid energy, as the power during the gap is unknown.
MAX_GRID_ENERGY_INTERVAL = timedelta(minutes=5)


@dataclass(frozen=True, kw_only=True)
class MarstekSensorEntityDescription(SensorEntityDescription):
    """Describe a Marstek sensor entity."""

    requires_pv: bool = False
    # Derives the value from the status; reads the field named by key if unset.
    value_fn: Callable[[MarstekDeviceStatus], StateType] | None = None


@dataclass(frozen=True, kw_only=True)
class MarstekGridEnergySensorEntityDescription(SensorEntityDescription):
    """Describe a grid energy sensor integrated from CT meter grid power."""

    # 1 counts power drawn from the grid, -1 counts power fed into it.
    direction: int


def _stored_energy(status: MarstekDeviceStatus) -> StateType:
    """Return the energy currently stored, from capacity and battery level."""
    if status.battery_capacity is None or status.battery_soc is None:
        return None
    return status.battery_capacity * status.battery_soc / 100


def _battery_cycles(status: MarstekDeviceStatus) -> StateType:
    """Return equivalent full cycles: lifetime discharged energy over capacity."""
    if status.total_grid_output_energy is None or not status.battery_capacity:
        return None
    return round(status.total_grid_output_energy / status.battery_capacity, 2)


def _pv_input_sensor_descriptions() -> tuple[MarstekSensorEntityDescription, ...]:
    """Build sensors for each of the device's four PV inputs."""
    descriptions: list[MarstekSensorEntityDescription] = []
    for pv_input in range(1, 5):
        for metric, device_class, unit in (
            (
                "power",
                SensorDeviceClass.POWER,
                UnitOfPower.WATT,
            ),
            (
                "voltage",
                SensorDeviceClass.VOLTAGE,
                UnitOfElectricPotential.VOLT,
            ),
            (
                "current",
                SensorDeviceClass.CURRENT,
                UnitOfElectricCurrent.AMPERE,
            ),
            (
                "state",
                SensorDeviceClass.ENUM,
                None,
            ),
        ):
            key = f"pv{pv_input}_{metric}"
            descriptions.append(
                MarstekSensorEntityDescription(
                    key=key,
                    translation_key=f"pv_{metric}",
                    translation_placeholders={"input": str(pv_input)},
                    device_class=device_class,
                    native_unit_of_measurement=unit,
                    state_class=(
                        SensorStateClass.MEASUREMENT if metric != "state" else None
                    ),
                    options=list(PV_STATE_OPTIONS) if metric == "state" else None,
                    requires_pv=True,
                )
            )
    return tuple(descriptions)


SENSOR_DESCRIPTIONS: tuple[MarstekSensorEntityDescription, ...] = (
    MarstekSensorEntityDescription(
        key="battery_soc",
        translation_key="battery_soc",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    MarstekSensorEntityDescription(
        key="battery_power",
        translation_key="battery_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    MarstekSensorEntityDescription(
        key="total_pv_energy",
        translation_key="total_pv_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        requires_pv=True,
    ),
    MarstekSensorEntityDescription(
        key="total_grid_output_energy",
        translation_key="total_grid_output_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    MarstekSensorEntityDescription(
        key="total_grid_input_energy",
        translation_key="total_grid_input_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    MarstekSensorEntityDescription(
        key="grid_power",
        translation_key="grid_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    *(
        MarstekSensorEntityDescription(
            key=f"grid_phase_{phase}_power",
            translation_key="grid_phase_power",
            translation_placeholders={"phase": phase.upper()},
            device_class=SensorDeviceClass.POWER,
            native_unit_of_measurement=UnitOfPower.WATT,
            state_class=SensorStateClass.MEASUREMENT,
        )
        for phase in ("a", "b", "c")
    ),
    MarstekSensorEntityDescription(
        key="stored_energy",
        translation_key="stored_energy",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_stored_energy,
    ),
    MarstekSensorEntityDescription(
        key="battery_cycles",
        translation_key="battery_cycles",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_battery_cycles,
    ),
    MarstekSensorEntityDescription(
        key="device_mode",
        translation_key="device_mode",
        device_class=SensorDeviceClass.ENUM,
        options=list(DEVICE_MODE_OPTIONS),
    ),
    MarstekSensorEntityDescription(
        key="battery_status",
        translation_key="battery_status",
        device_class=SensorDeviceClass.ENUM,
        options=list(BATTERY_STATUS_OPTIONS),
    ),
    *_pv_input_sensor_descriptions(),
)


GRID_ENERGY_SENSOR_DESCRIPTIONS: tuple[
    MarstekGridEnergySensorEntityDescription, ...
] = (
    MarstekGridEnergySensorEntityDescription(
        key="grid_import_energy",
        translation_key="grid_import_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        direction=1,
    ),
    MarstekGridEnergySensorEntityDescription(
        key="grid_export_energy",
        translation_key="grid_export_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        direction=-1,
    ),
)


LAST_RESPONSE_SENSOR_DESCRIPTION = SensorEntityDescription(
    key="last_response",
    translation_key="last_response",
    device_class=SensorDeviceClass.TIMESTAMP,
    entity_category=EntityCategory.DIAGNOSTIC,
)

ERROR_STATE_SENSOR_DESCRIPTION = SensorEntityDescription(
    key="error_state",
    translation_key="error_state",
    device_class=SensorDeviceClass.ENUM,
    options=list(ERROR_STATE_OPTIONS),
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MarstekConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Marstek sensors based on a config entry."""
    coordinator = config_entry.runtime_data
    device_ip = coordinator.device_ip
    _LOGGER.debug("Setting up Marstek sensors: %s", device_ip)

    has_pv = coordinator.device_info.device_type in PV_DEVICE_TYPES
    sensors: list[SensorEntity] = [
        MarstekSensor(coordinator, description)
        for description in SENSOR_DESCRIPTIONS
        if has_pv or not description.requires_pv
    ]
    sensors.extend(
        MarstekGridEnergySensor(coordinator, description)
        for description in GRID_ENERGY_SENSOR_DESCRIPTIONS
    )
    sensors.append(
        MarstekLastResponseSensor(coordinator, LAST_RESPONSE_SENSOR_DESCRIPTION)
    )
    sensors.append(MarstekErrorStateSensor(coordinator, ERROR_STATE_SENSOR_DESCRIPTION))

    _LOGGER.debug("Device %s sensors set up, total %d", device_ip, len(sensors))
    async_add_entities(sensors)


class MarstekSensor(MarstekEntity, SensorEntity):
    """Representation of a Marstek sensor."""

    entity_description: MarstekSensorEntityDescription

    @property
    @override
    def native_value(self) -> StateType | None:
        """Return the state of the sensor."""
        if value_fn := self.entity_description.value_fn:
            return value_fn(self.coordinator.data)
        return self.coordinator.data.get_value(self.entity_description.key)


class MarstekGridEnergySensor(MarstekEntity, RestoreSensor):
    """Grid energy integrated from the CT meter's grid power readings."""

    entity_description: MarstekGridEnergySensorEntityDescription
    _energy: float | None = None
    _last_power: float | None = None
    _last_update: datetime | None = None

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the energy total accumulated before a restart."""
        await super().async_added_to_hass()
        if (last_data := await self.async_get_last_sensor_data()) is not None and (
            isinstance(last_data.native_value, int | float)
        ):
            self._energy = float(last_data.native_value)
        self._record_power()

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Add the energy flowed since the previous poll (left Riemann sum)."""
        if self.coordinator.failed_polls:
            # The device did not answer, so the power since the last reading is
            # unknown; resume counting from the next reading instead.
            self._last_power = None
            super()._handle_coordinator_update()
            return
        now = dt_util.utcnow()
        if self._last_power is not None and self._last_update is not None:
            elapsed = now - self._last_update
            if elapsed <= MAX_GRID_ENERGY_INTERVAL:
                power = max(self.entity_description.direction * self._last_power, 0)
                self._energy = (self._energy or 0.0) + (
                    power * elapsed.total_seconds() / 3600
                )
        self._record_power(now)
        super()._handle_coordinator_update()

    def _record_power(self, now: datetime | None = None) -> None:
        """Remember the current grid power as the start of the next interval."""
        power = self.coordinator.data.grid_power
        self._last_power = float(power) if power is not None else None
        self._last_update = now or dt_util.utcnow()
        if self._last_power is not None and self._energy is None:
            self._energy = 0.0

    @property
    @override
    def native_value(self) -> float | None:
        """Return the accumulated grid energy."""
        return self._energy


class MarstekLastResponseSensor(MarstekEntity, SensorEntity):
    """When the device last answered a status request."""

    @property
    @override
    def available(self) -> bool:
        """Stay available while the device is not answering, to show since when."""
        return True

    @property
    @override
    def native_value(self) -> datetime | None:
        """Return the time of the device's last response."""
        return self.coordinator.last_response


class MarstekErrorStateSensor(MarstekEntity, SensorEntity):
    """Outcome of the latest poll of the device."""

    @property
    @override
    def available(self) -> bool:
        """Stay available while the device is not answering, to show why."""
        return True

    @property
    @override
    def native_value(self) -> str:
        """Return the error state of the latest poll."""
        return self.coordinator.error_state

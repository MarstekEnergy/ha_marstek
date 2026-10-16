"""Sensor platform for Marstek devices."""

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

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


@dataclass(frozen=True, kw_only=True)
class MarstekSensorEntityDescription(SensorEntityDescription):
    """Describe a Marstek sensor entity."""

    requires_pv: bool = False


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
        return self.coordinator.data.get_value(self.entity_description.key)


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

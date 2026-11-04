"""Sensor platform for Marstek devices."""

import logging
from dataclasses import dataclass
from typing import override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import (
    BATTERY_STATUS_OPTIONS,
    BLE_STATE_OPTIONS,
    DEVICE_MODE_OPTIONS,
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
    # A subsystem error bitmask: state stays numeric, hex_code is added as an
    # attribute so the value reads the way the firmware reports it.
    is_error_code: bool = False


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
        key="battery_temperature",
        translation_key="battery_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    MarstekSensorEntityDescription(
        key="battery_remaining_capacity",
        translation_key="battery_remaining_capacity",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    MarstekSensorEntityDescription(
        key="battery_rated_capacity",
        translation_key="battery_rated_capacity",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
    ),
    MarstekSensorEntityDescription(
        key="battery_soh",
        translation_key="battery_soh",
        name="Battery state of health",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-heart-variant",
    ),
    MarstekSensorEntityDescription(
        key="battery_total_input_energy",
        translation_key="battery_total_input_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    MarstekSensorEntityDescription(
        key="battery_total_output_energy",
        translation_key="battery_total_output_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    MarstekSensorEntityDescription(
        key="backup_input_energy",
        translation_key="backup_input_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    MarstekSensorEntityDescription(
        key="backup_output_energy",
        translation_key="backup_output_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    MarstekSensorEntityDescription(
        key="battery_capacity",
        translation_key="battery_capacity",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
    ),
    # Energy.GetData: the device's own charge/discharge counters. These replace
    # integrating battery_power over recorder history for the day/month totals.
    MarstekSensorEntityDescription(
        key="energy_day_charge",
        translation_key="energy_day_charge",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    MarstekSensorEntityDescription(
        key="energy_day_discharge",
        translation_key="energy_day_discharge",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    MarstekSensorEntityDescription(
        key="energy_month_charge",
        translation_key="energy_month_charge",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    MarstekSensorEntityDescription(
        key="energy_month_discharge",
        translation_key="energy_month_discharge",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    MarstekSensorEntityDescription(
        key="energy_total_charge",
        translation_key="energy_total_charge",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    MarstekSensorEntityDescription(
        key="energy_total_discharge",
        translation_key="energy_total_discharge",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    # Err.Get: one bitmask per subsystem. The state is the numeric code (easy
    # to compare in automations); the hex spelling the firmware uses is
    # exposed as the hex_code attribute.
    MarstekSensorEntityDescription(
        key="ems_error",
        translation_key="ems_error",
        name="EMS error code",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_error_code=True,
    ),
    MarstekSensorEntityDescription(
        key="bms_error",
        translation_key="bms_error",
        name="BMS error code",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_error_code=True,
    ),
    MarstekSensorEntityDescription(
        key="inv_error",
        translation_key="inv_error",
        name="Inverter error code",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_error_code=True,
    ),
    MarstekSensorEntityDescription(
        key="mppt_error",
        translation_key="mppt_error",
        name="MPPT error code",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_error_code=True,
    ),
    # Sys.GetInfo: one firmware build number per subsystem.
    MarstekSensorEntityDescription(
        key="ems_version",
        translation_key="ems_version",
        name="EMS firmware",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MarstekSensorEntityDescription(
        key="boot_version",
        translation_key="boot_version",
        name="Boot firmware",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MarstekSensorEntityDescription(
        key="bms_version",
        translation_key="bms_version",
        name="BMS firmware",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MarstekSensorEntityDescription(
        key="inverter_version",
        translation_key="inverter_version",
        name="Inverter firmware",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
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
    MarstekSensorEntityDescription(
        key="ble_state",
        translation_key="ble_state",
        device_class=SensorDeviceClass.ENUM,
        options=list(BLE_STATE_OPTIONS),
    ),
    MarstekSensorEntityDescription(
        key="ongrid_power",
        translation_key="ongrid_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    MarstekSensorEntityDescription(
        key="offgrid_power",
        translation_key="offgrid_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    MarstekSensorEntityDescription(
        key="pv_power",
        translation_key="pv_total_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        requires_pv=True,
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
        key="total_load_energy",
        translation_key="total_load_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    MarstekSensorEntityDescription(
        key="a_power",
        translation_key="phase_a_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    MarstekSensorEntityDescription(
        key="b_power",
        translation_key="phase_b_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    MarstekSensorEntityDescription(
        key="c_power",
        translation_key="phase_c_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    MarstekSensorEntityDescription(
        key="total_power",
        translation_key="total_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    MarstekSensorEntityDescription(
        key="ct_input_energy",
        translation_key="ct_input_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    MarstekSensorEntityDescription(
        key="ct_output_energy",
        translation_key="ct_output_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    MarstekSensorEntityDescription(
        key="wifi_rssi",
        translation_key="wifi_rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    MarstekSensorEntityDescription(
        key="wifi_ssid",
        translation_key="wifi_ssid",
        icon="mdi:wifi",
    ),
    MarstekSensorEntityDescription(
        key="wifi_ip",
        translation_key="wifi_ip",
        icon="mdi:ip",
    ),
    MarstekSensorEntityDescription(
        key="wifi_gateway",
        translation_key="wifi_gateway",
        icon="mdi:router-network",
    ),
    MarstekSensorEntityDescription(
        key="wifi_netmask",
        translation_key="wifi_netmask",
        icon="mdi:network-outline",
    ),
    MarstekSensorEntityDescription(
        key="wifi_dns",
        translation_key="wifi_dns",
        icon="mdi:dns",
    ),
    *_pv_input_sensor_descriptions(),
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
    sensors: list[SensorEntity] = []
    for description in SENSOR_DESCRIPTIONS:
        if description.requires_pv and not has_pv:
            continue
        sensor_class = (
            MarstekErrorSensor if description.is_error_code else MarstekSensor
        )
        sensors.append(sensor_class(coordinator, description))

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


class MarstekErrorSensor(MarstekSensor):
    """Subsystem error bitmask from Err.Get.

    The state is the numeric code so automations can compare it directly; the
    hexadecimal spelling the firmware actually sends is added as an attribute.
    """

    @property
    @override
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Return the error code in the firmware's hexadecimal notation."""
        value = self.native_value
        if not isinstance(value, int):
            return None
        return {"hex_code": f"0x{value:X}"}

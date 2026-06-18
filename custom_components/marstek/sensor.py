"""Sensor platform for Marstek devices."""

from __future__ import annotations

import logging
from typing import Any, cast

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

try:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
except ImportError:
    # Fallback for older Home Assistant versions
    from collections.abc import Callable, Iterable
    from typing import TYPE_CHECKING, Protocol

    if TYPE_CHECKING:
        from homeassistant.helpers.entity import Entity
    else:
        Entity = object  # type: ignore[assignment, misc]

    class AddConfigEntryEntitiesCallback(Protocol):  # type: ignore[no-redef]
        """Protocol type for EntityPlatform.add_entities callback (fallback)."""

        def __call__(
            self,
            new_entities: Iterable[Entity],
            update_before_add: bool = False,
        ) -> None:
            """Define add_entities type."""

from . import MarstekConfigEntry
from .const import (
    DATA_CATEGORY_ENERGY,
    DATA_CATEGORY_ES,
    DATA_CATEGORY_PV,
    DATA_CATEGORY_STATIC,
    DOMAIN,
)
from .coordinator import MarstekDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


class MarstekSensor(CoordinatorEntity[MarstekDataUpdateCoordinator], SensorEntity):
    """Representation of a Marstek sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        sensor_type: str,
        config_entry: ConfigEntry | None = None,
        *,
        data_category: str | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device_info = device_info
        self._sensor_type = sensor_type
        self._config_entry = config_entry
        self._data_category = data_category
        # Use BLE-MAC as device identifier for stability (beardhatcode & mik-laj feedback)
        # BLE-MAC is more stable than IP and ensures device history continuity
        device_identifier = (
            device_info.get("ble_mac")
            or device_info.get("mac")
            or device_info.get("wifi_mac")
            or device_info["ip"]
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_identifier)},
            name=f"Marstek {device_info['device_type']}",
            manufacturer="Marstek",
            model=device_info["device_type"],
            sw_version=str(device_info["version"]),
            hw_version=device_info.get("wifi_mac", ""),
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        # Use BLE-MAC as device identifier for stability (beardhatcode & mik-laj feedback)
        device_id = (
            self._device_info.get("ble_mac")
            or self._device_info.get("mac")
            or self._device_info.get("wifi_mac")
            or self._device_info.get("ip", "unknown")
        )
        return f"{device_id}_{self._sensor_type}"

    def _get_current_ip(self) -> str:
        """Get current device IP from config_entry (supports dynamic IP updates)."""
        if self._config_entry:
            return self._config_entry.data.get(
                CONF_HOST, self._device_info.get("ip", "Unknown")
            )
        return self._device_info.get("ip", "Unknown")

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self._sensor_type.replace("_", " ").title()

    def _value_is_fresh(self) -> bool:
        """Return whether this sensor's data category is fresh enough to display."""
        if not self.coordinator.is_device_reachable():
            return False
        if self._data_category is None:
            return self.coordinator.data is not None
        return self.coordinator.is_category_fresh(self._data_category)

    def _read_value(self, key: str | None = None) -> Any:
        """Read a coordinator value when device and category are fresh."""
        if not self._value_is_fresh() or not self.coordinator.data:
            return None
        return self.coordinator.data.get(key or self._sensor_type)

    @property
    def available(self) -> bool:
        """Stay available during short outages; unavailable only after long silence."""
        return self.coordinator.is_device_reachable()

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        value = self._read_value()
        if isinstance(value, (int, float, str)):
            return cast(StateType, value)
        return None


class MarstekBatterySensor(MarstekSensor):
    """Representation of a Marstek battery sensor."""

    _attr_translation_key = "battery_level"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery"

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the battery sensor."""
        super().__init__(
            coordinator, device_info, "battery_soc", config_entry, data_category=DATA_CATEGORY_ES
        )

    @property
    def native_value(self) -> StateType:
        """Return the battery level."""
        value = self._read_value("battery_soc")
        if isinstance(value, (int, float)):
            return int(value)
        return None


class MarstekPowerSensor(MarstekSensor):
    """Representation of a Marstek power sensor."""

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:flash"

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the power sensor."""
        super().__init__(
            coordinator, device_info, "battery_power", config_entry, data_category=DATA_CATEGORY_ES
        )
        self._last_nonzero_power: int | None = None

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Grid Power"

    @property
    def native_value(self) -> StateType:
        """Return grid feed-in power (non-negative watts)."""
        if not self._value_is_fresh() or not self.coordinator.data:
            return None

        data = self.coordinator.data
        power = self.coordinator.grid_export_power_w(data)

        if power > 0:
            self._last_nonzero_power = power
            return power

        if (
            self._last_nonzero_power
            and self._last_nonzero_power >= 50
            and data.get("battery_status") in ("Selling", "Idle")
        ):
            return self._last_nonzero_power

        self._last_nonzero_power = None
        return 0


class MarstekDeviceInfoSensor(MarstekSensor):
    """Representation of a Marstek device info sensor."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        info_type: str,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the device info sensor."""
        super().__init__(
            coordinator, device_info, info_type, config_entry, data_category=DATA_CATEGORY_STATIC
        )
        self._info_type = info_type
        self._attr_icon = "mdi:information"
        self._attr_device_class = None
        self._attr_state_class = None

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        info_type_names = {
            "device_ip": "Device IP",
            "device_version": "Device version",
            "wifi_name": "Wi-Fi name",
            "ble_mac": "BLE MAC",
            "wifi_mac": "Wi-Fi MAC",
            "mac": "MAC address",
        }
        return info_type_names.get(self._info_type, self._info_type.replace("_", " "))

    @property
    def native_value(self) -> StateType:
        """Return the device info."""
        if self._info_type == "device_ip":
            # Get current IP from config_entry if available (supports dynamic IP updates)
            if self._config_entry:
                return self._config_entry.data.get(CONF_HOST, "")
            return self._device_info.get("ip", "")
        if self._info_type == "device_version":
            return str(self._device_info.get("version", ""))
        if self._info_type == "wifi_name":
            return self._device_info.get("wifi_name", "")
        if self._info_type == "ble_mac":
            return self._device_info.get("ble_mac", "")
        if self._info_type == "wifi_mac":
            return self._device_info.get("wifi_mac", "")
        if self._info_type == "mac":
            return self._device_info.get("mac", "")
        return None


class MarstekDeviceModeSensor(MarstekSensor):
    """Representation of a Marstek device mode sensor."""

    _attr_icon = "mdi:cog"
    _attr_device_class = None
    _attr_state_class = None

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the device mode sensor."""
        super().__init__(
            coordinator, device_info, "device_mode", config_entry, data_category=DATA_CATEGORY_ES
        )

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Device Mode"


class MarstekBatteryStatusSensor(MarstekSensor):
    """Representation of a Marstek battery status sensor."""

    _attr_icon = "mdi:battery"
    _attr_device_class = None
    _attr_state_class = None

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the battery status sensor."""
        super().__init__(
            coordinator, device_info, "battery_status", config_entry, data_category=DATA_CATEGORY_ES
        )

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Battery Status"


class MarstekPVSensor(MarstekSensor):
    """Representation of a Marstek PV sensor."""

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        pv_channel: int,
        metric_type: str,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the PV sensor."""
        sensor_key = f"pv{pv_channel}_{metric_type}"
        super().__init__(
            coordinator, device_info, sensor_key, config_entry, data_category=DATA_CATEGORY_PV
        )
        self._pv_channel = pv_channel
        self._metric_type = metric_type

        if metric_type == "power":
            self._attr_native_unit_of_measurement = UnitOfPower.WATT
            self._attr_icon = "mdi:solar-power"
        elif metric_type == "voltage":
            self._attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
            self._attr_icon = "mdi:flash"
        elif metric_type == "current":
            self._attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
            self._attr_icon = "mdi:current-ac"
        elif metric_type == "state":
            self._attr_icon = "mdi:state-machine"
            self._attr_device_class = None
            self._attr_state_class = None
        else:
            self._attr_icon = "mdi:solar-panel"

        if metric_type != "state":
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        metric_name = self._metric_type.replace("_", " ").title()
        return f"PV{self._pv_channel} {metric_name}"

    @property
    def native_value(self) -> StateType:
        """Return the PV metric value."""
        value = self._read_value()
        if isinstance(value, (int, float)):
            return cast(StateType, value)
        return None


class MarstekTotalPVPowerSensor(MarstekSensor):
    """Representation of total PV input power."""

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:solar-power"

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize total PV power sensor."""
        super().__init__(
            coordinator,
            device_info,
            "total_pv_input_power",
            config_entry,
            data_category=DATA_CATEGORY_ES,
        )

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Total PV Input Power"

    @property
    def native_value(self) -> StateType:
        """Return total PV power, preferring ES.GetStatus aggregate like jaapp.

        jaapp uses ``es.pv_power`` (ES.GetStatus) as authoritative solar power.
        Summing PV1..PV4 from PV.GetStatus can over-read when inactive channels
        report stale values; PV1 may be corrected against PV2–PV4 in the coordinator.
        """
        if not self._value_is_fresh() or not self.coordinator.data:
            return None

        data = self.coordinator.data
        aggregate = data.get("pv_power")
        if isinstance(aggregate, (int, float)) and float(aggregate) >= 0:
            return cast(StateType, round(float(aggregate), 1))

        if not self.coordinator.is_category_fresh(DATA_CATEGORY_PV):
            return None

        total = 0.0
        for channel in range(1, 5):
            power = data.get(f"pv{channel}_power")
            voltage = data.get(f"pv{channel}_voltage")
            if not isinstance(power, (int, float)):
                continue
            if isinstance(voltage, (int, float)) and float(voltage) <= 0:
                continue
            if float(power) <= 0:
                continue
            total += float(power)

        if total > 0:
            return cast(StateType, round(total, 1))
        return None


class MarstekPVAggregatePowerSensor(MarstekSensor):
    """Representation of PV aggregate power from ES.GetStatus."""

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:solar-power"

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize PV aggregate power sensor."""
        super().__init__(
            coordinator, device_info, "pv_power", config_entry, data_category=DATA_CATEGORY_ES
        )

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "PV Power (ES)"

    @property
    def native_value(self) -> StateType:
        """Return aggregate PV power from ES.GetStatus."""
        value = self._read_value("pv_power")
        if isinstance(value, (int, float)):
            return cast(StateType, float(value))
        return None


class MarstekEnergySensor(MarstekSensor):
    """Representation of a Marstek energy counter sensor."""

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:lightning-bolt"

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        sensor_type: str,
        display_name: str,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the energy sensor."""
        super().__init__(coordinator, device_info, sensor_type, config_entry)
        self._display_name = display_name
        self._data_category = (
            DATA_CATEGORY_ES
            if sensor_type in {"input_energy", "output_energy"}
            else DATA_CATEGORY_ENERGY
        )

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self._display_name

    @property
    def native_value(self) -> StateType:
        """Return energy in kWh."""
        value = self._read_value(self._sensor_type)
        if not isinstance(value, (int, float)):
            return None

        if self._sensor_type in {"input_energy", "output_energy"}:
            return cast(StateType, round(float(value) / 10000, 3))

        return cast(StateType, round(float(value) / 1000, 3))


class MarstekCapacitySensor(MarstekSensor):
    """Representation of a Marstek battery capacity sensor."""

    _attr_native_unit_of_measurement = "Wh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-high"

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize capacity sensor."""
        super().__init__(
            coordinator, device_info, "bat_cap", config_entry, data_category=DATA_CATEGORY_ENERGY
        )

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Battery Capacity"

    @property
    def native_value(self) -> StateType:
        """Return battery capacity in Wh."""
        value = self._read_value("bat_cap")
        if isinstance(value, (int, float)):
            return cast(StateType, int(value))
        return None


class MarstekBatteryStoredEnergySensor(MarstekSensor):
    """Representation of current battery stored energy (derived)."""

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:battery-medium"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize battery stored energy sensor."""
        super().__init__(
            coordinator,
            device_info,
            "battery_stored_energy",
            config_entry,
            data_category=DATA_CATEGORY_ES,
        )

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Battery Stored Energy"

    @property
    def native_value(self) -> StateType:
        """Return current battery stored energy in kWh."""
        if not self.coordinator.is_device_reachable() or not self.coordinator.data:
            return None
        if not self.coordinator.is_category_fresh(DATA_CATEGORY_ES):
            return None
        if not self.coordinator.is_category_fresh(DATA_CATEGORY_ENERGY):
            return None

        capacity_wh = self.coordinator.data.get("bat_cap")
        soc_percent = self.coordinator.data.get("battery_soc")
        if not isinstance(capacity_wh, (int, float)) or not isinstance(
            soc_percent, (int, float)
        ):
            return None

        if capacity_wh < 0:
            return None

        stored_kwh = (float(capacity_wh) * float(soc_percent) / 100.0) / 1000.0
        return cast(StateType, round(stored_kwh, 3))


class MarstekCTStateSensor(MarstekSensor):
    """Representation of CT state sensor."""

    _attr_icon = "mdi:counter"
    _attr_device_class = None
    _attr_state_class = None

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize CT state sensor."""
        super().__init__(
            coordinator, device_info, "ct_state", config_entry, data_category=DATA_CATEGORY_ES
        )

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "CT State"


class MarstekMeterPowerSensor(MarstekSensor):
    """Representation of meter channel power sensor."""

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:flash"

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        sensor_type: str,
        display_name: str,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize meter power sensor."""
        super().__init__(coordinator, device_info, sensor_type, config_entry)
        self._display_name = display_name
        self._data_category = DATA_CATEGORY_ES

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self._display_name

    @property
    def native_value(self) -> StateType:
        """Return meter power in watts."""
        value = self._read_value(self._sensor_type)
        if isinstance(value, (int, float)):
            return cast(StateType, float(value))
        return None


class MarstekLastMessageSensor(MarstekSensor):
    """Diagnostic sensor: seconds since the last successful device poll."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:timer-outline"

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize last-message diagnostic sensor."""
        super().__init__(
            coordinator,
            device_info,
            "last_message_seconds",
            config_entry,
            data_category=DATA_CATEGORY_STATIC,
        )

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Last message received"

    @property
    def native_value(self) -> StateType:
        """Return seconds since the last successful poll."""
        seconds = self.coordinator.get_seconds_since_last_message()
        if seconds is None:
            return None
        return cast(StateType, seconds)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MarstekConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Marstek sensors based on a config entry."""
    # Use shared coordinator and device_info from __init__.py (mik-laj feedback)
    coordinator = config_entry.runtime_data.coordinator
    device_info = config_entry.runtime_data.device_info
    device_ip = device_info["ip"]
    _LOGGER.info("Setting up Marstek sensors: %s", device_ip)

    sensors: list[MarstekSensor] = [
        MarstekBatterySensor(coordinator, device_info, config_entry),
        MarstekCapacitySensor(coordinator, device_info, config_entry),
        MarstekBatteryStoredEnergySensor(coordinator, device_info, config_entry),
        MarstekPowerSensor(coordinator, device_info, config_entry),
        MarstekDeviceModeSensor(coordinator, device_info, config_entry),
        MarstekCTStateSensor(coordinator, device_info, config_entry),
        MarstekBatteryStatusSensor(coordinator, device_info, config_entry),
        MarstekLastMessageSensor(coordinator, device_info, config_entry),
        MarstekDeviceInfoSensor(coordinator, device_info, "device_ip", config_entry),
        MarstekDeviceInfoSensor(
            coordinator, device_info, "device_version", config_entry
        ),
        MarstekDeviceInfoSensor(coordinator, device_info, "ble_mac", config_entry),
        MarstekDeviceInfoSensor(coordinator, device_info, "wifi_mac", config_entry),
        MarstekDeviceInfoSensor(coordinator, device_info, "mac", config_entry),
    ]

    sensors.extend(
        MarstekPVSensor(coordinator, device_info, pv_channel, metric_type, config_entry)
        for pv_channel in range(1, 5)
        for metric_type in ("power", "voltage", "current", "state")
    )
    sensors.append(MarstekTotalPVPowerSensor(coordinator, device_info, config_entry))
    sensors.append(MarstekPVAggregatePowerSensor(coordinator, device_info, config_entry))
    sensors.extend(
        MarstekMeterPowerSensor(
            coordinator, device_info, sensor_type, display_name, config_entry
        )
        for sensor_type, display_name in (
            ("a_power", "CT A Power"),
            ("b_power", "CT B Power"),
            ("c_power", "CT C Power"),
            ("total_power", "CT Total Power"),
        )
    )
    sensors.extend(
        MarstekEnergySensor(
            coordinator, device_info, sensor_type, display_name, config_entry
        )
        for sensor_type, display_name in (
            ("total_pv_energy", "Total PV Energy"),
            ("total_grid_output_energy", "Total Grid Output Energy"),
            ("total_grid_input_energy", "Total Grid Input Energy"),
            ("total_load_energy", "Total Load Energy"),
            ("input_energy", "Input Energy"),
            ("output_energy", "Output Energy"),
        )
    )

    _LOGGER.info("Device %s sensors set up, total %d", device_ip, len(sensors))
    async_add_entities(sensors)

"""Binary sensor platform for Marstek devices."""

import logging
from typing import override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MarstekConfigEntry
from .entity import MarstekEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MarstekConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Marstek binary sensor entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            MarstekChargePermissionBinarySensor(coordinator),
            MarstekDischargePermissionBinarySensor(coordinator),
            MarstekConnectedBinarySensor(coordinator),
        ]
    )


class MarstekPermissionBinarySensor(MarstekEntity, BinarySensorEntity):
    """Representation of a Marstek charge/discharge permission flag."""

    entity_description: BinarySensorEntityDescription

    @property
    @override
    def is_on(self) -> bool | None:
        """Return True if the permission flag is set."""
        return self.coordinator.data.get_value(self.entity_description.key)


class MarstekChargePermissionBinarySensor(MarstekPermissionBinarySensor):
    """Charging permission flag from Bat.GetStatus."""

    def __init__(self, coordinator: MarstekConfigEntry.runtime_data) -> None:
        """Initialize the charge-permission binary sensor."""
        super().__init__(
            coordinator,
            BinarySensorEntityDescription(
                key="charge_permission",
                translation_key="charge_permission",
                name="Charge permission",
                icon="mdi:battery-charging",
            ),
        )


class MarstekDischargePermissionBinarySensor(MarstekPermissionBinarySensor):
    """Discharge permission flag from Bat.GetStatus."""

    def __init__(self, coordinator: MarstekConfigEntry.runtime_data) -> None:
        """Initialize the discharge-permission binary sensor."""
        super().__init__(
            coordinator,
            BinarySensorEntityDescription(
                key="discharge_permission",
                translation_key="discharge_permission",
                name="Discharge permission",
                icon="mdi:battery-minus",
            ),
        )


class MarstekConnectedBinarySensor(MarstekEntity, BinarySensorEntity):
    """Device connectivity derived from coordinator update success.

    The coordinator keeps the last reading for a couple of silent polls
    and only reports failure once the device has been silent long enough
    that the data is no longer trustworthy (``MAX_SILENT_POLLS``), so this
    sensor turns off roughly that many poll intervals after the device
    stopped answering. ``available`` is overridden so the sensor can
    report "off" while the device's other entities are unavailable.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: MarstekConfigEntry.runtime_data) -> None:
        """Initialize the connectivity binary sensor."""
        super().__init__(
            coordinator,
            BinarySensorEntityDescription(
                key="connected",
                translation_key="connected",
                name="Connected",
            ),
        )

    @property
    @override
    def available(self) -> bool:
        """Stay available so the sensor itself can report the disconnect."""
        return True

    @property
    @override
    def is_on(self) -> bool | None:
        """Return True while the coordinator's last update succeeded."""
        return self.coordinator.last_update_success

"""Base entity for Marstek devices."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MarstekDataUpdateCoordinator


class MarstekEntity(CoordinatorEntity[MarstekDataUpdateCoordinator]):
    """Base class for Marstek entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        entity_description: EntityDescription,
    ) -> None:
        """Initialize the Marstek entity."""
        super().__init__(coordinator)
        self.entity_description = entity_description

        device_info = coordinator.device_info
        self._attr_unique_id = f"{device_info.stable_id}_{entity_description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_info.stable_id)},
            name=f"Marstek {device_info.device_type} v{device_info.version}",
            manufacturer="Marstek",
            model=device_info.device_type,
            sw_version=str(device_info.version),
        )

    def _notify_success(self, message: str) -> None:
        """Pop a confirmation notification for a successful device write.

        Home Assistant has no success-toast API for custom integrations
        (only errors surface as toasts), so write-only controls like DOD,
        Bluetooth advertising, LED and UPS would otherwise give no feedback
        that the command took effect. A per-entity notification_id replaces
        the previous one, so repeated writes update instead of stacking.
        """
        self.hass.components.persistent_notification.async_create(
            self.hass,
            message,
            title="Marstek",
            notification_id=f"marstek_{self.entity_description.key}_success",
        )

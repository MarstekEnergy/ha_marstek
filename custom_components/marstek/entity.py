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
        """Feedback hook for a successful device write.

        Historically this created a persistent notification so write-only
        controls (DOD, Bluetooth advertising, LED, UPS, power version,
        operating mode) had visible feedback. The sidebar panel now shows
        its own success/error toasts for these actions, so the persistent
        notification is suppressed to avoid double-messaging. The method is
        kept as the single feedback hook so callers do not need to change
        and a future transport (e.g. an event fired to the panel) can be
        added here without touching every entity.
        """
        return None

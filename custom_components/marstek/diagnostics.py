"""Diagnostics support for Marstek devices."""

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_BLE_MAC, CONF_WIFI_MAC, CONF_WIFI_NAME
from .coordinator import MarstekConfigEntry

TO_REDACT = {CONF_BLE_MAC, CONF_WIFI_MAC, CONF_WIFI_NAME, "mac", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MarstekConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    udp_client = coordinator.udp_client
    last_response = coordinator.last_response

    return {
        "entry": async_redact_data(
            {"data": dict(entry.data), "unique_id": entry.unique_id}, TO_REDACT
        ),
        "device_info": async_redact_data(asdict(coordinator.device_info), TO_REDACT),
        "poll": {
            "interval_seconds": coordinator.update_interval.total_seconds()
            if coordinator.update_interval
            else None,
            "last_update_success": coordinator.last_update_success,
            "error_state": coordinator.error_state,
            "failed_polls": coordinator.failed_polls,
            "last_response": last_response.isoformat() if last_response else None,
        },
        "status": asdict(coordinator.data) if coordinator.data else None,
        "raw_results": async_redact_data(
            udp_client.get_last_results(coordinator.device_ip), TO_REDACT
        ),
    }

"""Diagnostics support for the Marstek integration.

Lets a user download a snapshot of a configured device from the device page
(Settings -> Devices & Services -> Marstek device -> Download diagnostics).
The snapshot pairs the last status the coordinator received with the static
device info, a polling-health summary and the integration/library versions,
which is what is needed to reproduce the connection, polling and parsing
failures the coordinator already handles (silent polls, IP reassignment,
unsupported device types).

MarstekDeviceInfo and MarstekDeviceStatus are frozen, slotted dataclasses,
so they are serialized with dataclasses.asdict before redaction. The status
payload holds only measurements (battery, power, PV, temperatures) and is not
sensitive; only the network/hardware identifiers carried by the config
entry, device info and the wifi_* status fields are redacted.

The polling summary carries no raw exception text: it exposes only the
exception's class name and translation key, so a message the coordinator
templated with the device IP cannot leak. Versions are static metadata and
never sensitive.
"""

from __future__ import annotations

from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_MAC
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.loader import async_get_integration

from .const import CONF_BLE_MAC, CONF_WIFI_MAC, CONF_WIFI_NAME, DOMAIN
from .coordinator import MAX_SILENT_POLLS, MarstekConfigEntry

# Field names whose values identify the user's network or hardware and must
# not leave the device. The config-entry data dict and the MarstekDeviceInfo
# dataclass share several names (CONF_HOST == "host", CONF_MAC == "mac",
# CONF_WIFI_MAC == "wifi_mac", CONF_BLE_MAC == "ble_mac",
# CONF_WIFI_NAME == "wifi_name"), so a single set covers both, and
# async_redact_data recurses into every nested dict so the same set redacts
# them wherever they appear.
TO_REDACT: set[str] = {
    # Config-entry data: device IP and hardware MACs / SSID.
    CONF_HOST,
    CONF_MAC,
    CONF_WIFI_MAC,
    CONF_BLE_MAC,
    CONF_WIFI_NAME,
    # Config-entry bookkeeping derived from hardware identifiers.
    "title",
    "unique_id",
    # MarstekDeviceInfo fields not already covered above.
    "id",
    "ip",
    # MarstekDeviceStatus network fields.
    "device_ip",
    "wifi_ssid",
    "wifi_ip",
    "wifi_gateway",
    "wifi_netmask",
    "wifi_dns",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MarstekConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a Marstek config entry.

    Returns the config entry, the cached device info, the last status the
    coordinator received, a polling-health summary and the integration /
    aiomarstek versions. The entry is the source of truth for the static
    configuration; device_info, status and polling are only attached when
    the coordinator has successfully set up, so diagnostics still works (with
    just the entry) while a device is offline or mid-reload.

    The polling summary distinguishes a single dropped response (entities
    kept their last reading) from an outage that has crossed the
    ``MAX_SILENT_POLLS`` threshold (entities marked unavailable). It records
    the last failure's exception class name and translation key only, never
    the exception text (which may carry the device IP).
    """
    coordinator = entry.runtime_data

    device_info: dict[str, Any] | None = None
    status: dict[str, Any] | None = None
    polling: dict[str, Any] | None = None
    if coordinator is not None:
        if getattr(coordinator, "device_info", None) is not None:
            device_info = asdict(coordinator.device_info)
        if coordinator.data is not None:
            status = asdict(coordinator.data)
        last_exc = coordinator.last_exception
        polling = {
            "last_update_success": coordinator.last_update_success,
            "silent_polls": coordinator.silent_polls,
            "max_silent_polls": MAX_SILENT_POLLS,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "last_exception_type": type(last_exc).__name__ if last_exc else None,
            "last_exception_translation_key": getattr(
                last_exc, "translation_key", None
            ),
        }

    versions: dict[str, Any] = {}
    try:
        integration = await async_get_integration(hass, DOMAIN)
        versions["integration"] = integration.manifest.get("version")
        versions["declared_requirements"] = integration.manifest.get("requirements")
    except HomeAssistantError:
        versions["integration"] = None
        versions["declared_requirements"] = None
    try:
        versions["aiomarstek"] = version("aiomarstek")
    except PackageNotFoundError:
        versions["aiomarstek"] = None

    data: dict[str, Any] = {
        "entry": entry.as_dict(),
        "device_info": device_info,
        "status": status,
        "polling": polling,
        "versions": versions,
    }

    return async_redact_data(data, TO_REDACT)

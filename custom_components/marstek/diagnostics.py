"""Diagnostics support for the Marstek integration.

Lets a user download a snapshot of a configured device from the device page
(Settings -> Devices & Services -> Marstek device -> Download diagnostics).
The snapshot pairs the last status the coordinator received with the static
device info, which is what is needed to reproduce the connection, polling
and parsing failures the coordinator already handles (silent polls, IP
reassignment, unsupported device types).

MarstekDeviceInfo and MarstekDeviceStatus are frozen, slotted dataclasses,
so they are serialized with dataclasses.asdict before redaction. The status
payload holds only measurements (battery, power, PV, temperatures) and is not
sensitive; only the network/hardware identifiers carried by the config
entry, device info and the wifi_* status fields are redacted.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_MAC
from homeassistant.core import HomeAssistant

from .const import CONF_BLE_MAC, CONF_WIFI_MAC, CONF_WIFI_NAME
from .coordinator import MarstekConfigEntry

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

    Returns the config entry, the cached device info and the last status the
    coordinator received. The entry is the source of truth for the static
    configuration; device_info and status are only attached when the
    coordinator has successfully set up, so diagnostics still works (with
    just the entry) while a device is offline or mid-reload.
    """
    coordinator = entry.runtime_data

    device_info: dict[str, Any] | None = None
    status: dict[str, Any] | None = None
    if coordinator is not None:
        if getattr(coordinator, "device_info", None) is not None:
            device_info = asdict(coordinator.device_info)
        if coordinator.data is not None:
            status = asdict(coordinator.data)

    data: dict[str, Any] = {
        "entry": entry.as_dict(),
        "device_info": device_info,
        "status": status,
    }

    return async_redact_data(data, TO_REDACT)

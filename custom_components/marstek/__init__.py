"""The Marstek integration."""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
import logging
from typing import Any

from pymarstek import MarstekUDPClient, get_es_mode

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DEFAULT_UDP_PORT, DOMAIN, PLATFORMS, get_entry_options
from .scanner import MarstekScanner

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


# Runtime data and typed ConfigEntry defined early to prevent circular imports.
# Submodules (services.py, button.py, select.py, sensor.py, number.py) do
# "from . import MarstekConfigEntry" at their top level.
@dataclass
class MarstekRuntimeData:
    """Runtime data for Marstek integration."""

    udp_client: MarstekUDPClient
    coordinator: "MarstekDataUpdateCoordinator"
    device_info: dict[str, Any]


type MarstekConfigEntry = ConfigEntry[MarstekRuntimeData]

# Import runtime-dependent modules *after* publishing MarstekConfigEntry to break
# the import cycle with services.py (and platform files that import the alias).
from .coordinator import MarstekDataUpdateCoordinator
from .services import async_setup_services


def _normalize_mac(value: str | None) -> str:
    """Normalize MAC-like strings for loose matching."""
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value.lower() if ch.isalnum())


async def _refresh_device_metadata_from_discovery(
    hass: HomeAssistant,
    entry: MarstekConfigEntry,
    udp_client: MarstekUDPClient,
    base_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Refresh config entry metadata from current discovery data.

    This keeps firmware/version and related fields current even after OTA updates.
    """
    try:
        devices = await udp_client.discover_devices(use_cache=False)
    except (TimeoutError, OSError, ValueError) as err:
        _LOGGER.debug("Metadata refresh via discovery failed: %s", err)
        return base_data or entry.data

    if not devices:
        return base_data or entry.data

    current_data = base_data or entry.data
    entry_ble = _normalize_mac(current_data.get("ble_mac"))
    entry_wifi = _normalize_mac(current_data.get("wifi_mac"))
    entry_mac = _normalize_mac(current_data.get("mac"))
    entry_ip = current_data.get(CONF_HOST)

    matched: dict[str, Any] | None = None
    for device in devices:
        device_ble = _normalize_mac(device.get("ble_mac"))
        device_wifi = _normalize_mac(device.get("wifi_mac"))
        device_mac = _normalize_mac(device.get("mac"))
        device_ip = device.get("ip")

        if entry_ble and device_ble and entry_ble == device_ble:
            matched = device
            break
        if entry_wifi and device_wifi and entry_wifi == device_wifi:
            matched = device
            break
        if entry_mac and device_mac and entry_mac == device_mac:
            matched = device
            break
        if isinstance(entry_ip, str) and entry_ip and device_ip == entry_ip:
            matched = device
            break

    if not matched:
        return current_data

    updated_data = dict(current_data)
    changed = False

    # Discovery payloads can vary by source/library version.
    # Normalize to our config entry keys.
    normalized_updates: dict[str, Any] = {
        "version": matched.get("version", matched.get("ver", matched.get("firmware"))),
        "device_type": matched.get("device_type", matched.get("device")),
        "wifi_name": matched.get("wifi_name"),
        "wifi_mac": matched.get("wifi_mac"),
        "mac": matched.get("mac"),
        "ble_mac": matched.get("ble_mac"),
    }
    for key, new_value in normalized_updates.items():
        if new_value is None:
            continue
        if updated_data.get(key) != new_value:
            updated_data[key] = new_value
            changed = True

    if changed:
        hass.config_entries.async_update_entry(entry, data=updated_data)
        _LOGGER.info(
            "Updated Marstek metadata from discovery for %s (version=%s)",
            entry.title,
            updated_data.get("version"),
        )

    return updated_data


async def _refresh_device_metadata_direct(
    hass: HomeAssistant,
    entry: MarstekConfigEntry,
    udp_client: MarstekUDPClient,
    device_ip: str,
) -> dict[str, Any]:
    """Refresh metadata via direct Marstek.GetDevice call to current IP."""
    request = json.dumps(
        {"id": 75, "method": "Marstek.GetDevice", "params": {"ble_mac": "0"}},
        separators=(",", ":"),
    )
    try:
        response = await udp_client.send_request(
            request, device_ip, DEFAULT_UDP_PORT, timeout=4.0, quiet_on_timeout=True
        )
    except (TimeoutError, OSError, ValueError) as err:
        _LOGGER.debug("Direct metadata refresh failed for %s: %s", device_ip, err)
        return entry.data

    result = response.get("result", {}) if isinstance(response, dict) else {}
    if not isinstance(result, dict):
        return entry.data

    updated_data = dict(entry.data)
    changed = False
    normalized_updates: dict[str, Any] = {
        "version": result.get("version", result.get("ver", result.get("firmware"))),
        "device_type": result.get("device_type", result.get("device")),
        "wifi_name": result.get("wifi_name"),
        "wifi_mac": result.get("wifi_mac"),
        "mac": result.get("mac"),
        "ble_mac": result.get("ble_mac"),
    }
    for key, new_value in normalized_updates.items():
        if new_value is None:
            continue
        if updated_data.get(key) != new_value:
            updated_data[key] = new_value
            changed = True

    if changed:
        hass.config_entries.async_update_entry(entry, data=updated_data)
        _LOGGER.info(
            "Updated Marstek metadata from direct query for %s (version=%s)",
            entry.title,
            updated_data.get("version"),
        )
    return updated_data


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Marstek component."""
    # Initialize scanner (only once, regardless of number of config entries)
    # Scanner will detect IP changes and update config entries via config flow
    scanner = MarstekScanner.async_get(hass)
    await scanner.async_setup()
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MarstekConfigEntry) -> bool:
    """Set up Marstek from a config entry."""
    _LOGGER.info("Setting up Marstek config entry: %s", entry.title)

    udp_client = MarstekUDPClient()
    await udp_client.async_setup()

    stored_ip = entry.data[CONF_HOST]
    stored_ble_mac = entry.data.get("ble_mac")
    options = get_entry_options(entry)

    _LOGGER.info(
        "Setting up Marstek at %s (BLE-MAC: %s); first poll in %ss",
        stored_ip,
        stored_ble_mac or "unknown",
        options.startup_delay,
    )

    # Reuse config entry metadata on reload — skip extra GetDevice/discovery UDP burst.
    entry_data = dict(entry.data)

    device_info_dict = {
        "ip": stored_ip,
        "mac": entry_data.get("mac", ""),
        "device_type": entry_data.get("device_type", "Unknown"),
        "version": entry_data.get("version", 0),
        "wifi_name": entry_data.get("wifi_name", ""),
        "wifi_mac": entry_data.get("wifi_mac", ""),
        "ble_mac": entry_data.get("ble_mac", ""),
    }

    coordinator = MarstekDataUpdateCoordinator(
        hass, entry, udp_client, device_info_dict["ip"]
    )

    if options.startup_delay == 0:
        # Quick reachability probe (short timeout, single attempt) so that
        # async_setup_entry returns promptly and we can still raise
        # ConfigEntryNotReady for unreachable devices without long blocking.
        try:
            await asyncio.wait_for(
                udp_client.send_request(
                    get_es_mode(0),
                    stored_ip,
                    DEFAULT_UDP_PORT,
                    timeout=3.0,
                    quiet_on_timeout=True,
                ),
                timeout=4.0,
            )
        except Exception as ex:
            await udp_client.async_cleanup()
            _LOGGER.warning(
                "Unable to reach Marstek at %s during initial probe: %s",
                stored_ip,
                ex,
            )
            raise ConfigEntryNotReady(
                f"Unable to connect to device at {stored_ip}."
            ) from ex

        entry.runtime_data = MarstekRuntimeData(
            udp_client=udp_client,
            coordinator=coordinator,
            device_info=device_info_dict,
        )

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        # Schedule the full first data update (the one that performs the
        # spaced ES.GetMode / sleep / ES.GetStatus / sleep / PV.GetStatus).
        # We do not await it here so that async_setup_entry returns quickly.
        hass.async_create_task(coordinator.async_request_refresh())
    else:
        # Defer first UDP contact to avoid disturbing the device's export/MPPT
        # after HA (re)load. Load platforms immediately; data will arrive later.
        entry.runtime_data = MarstekRuntimeData(
            udp_client=udp_client,
            coordinator=coordinator,
            device_info=device_info_dict,
        )

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        async def _async_delayed_first_refresh() -> None:
            _LOGGER.info(
                "Deferring first Marstek poll for %ss so the device can keep exporting "
                "undisturbed after Home Assistant reload",
                options.startup_delay,
            )
            try:
                await asyncio.sleep(options.startup_delay)
                await coordinator.async_config_entry_first_refresh()
            except asyncio.CancelledError:
                _LOGGER.debug("Delayed first refresh cancelled for %s", stored_ip)
                raise
            except Exception as ex:
                _LOGGER.warning(
                    "Unable to reach Marstek at %s after startup delay: %s",
                    stored_ip,
                    ex,
                )
                # Coordinator will continue retrying on its normal schedule.

        task = hass.async_create_task(_async_delayed_first_refresh())
        entry.async_on_unload(task.cancel)

    # Register services only once for the integration
    if not hass.data.get(DOMAIN + "_services_registered"):
        await async_setup_services(hass)
        hass.data[DOMAIN + "_services_registered"] = True

    return True


async def async_unload_entry(hass: HomeAssistant, entry: MarstekConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Marstek config entry: %s", entry.title)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok and entry.runtime_data:
        await entry.runtime_data.udp_client.async_cleanup()

    # Services are integration-wide; we leave them registered until last unload
    # (HA will clean on integration remove)
    return unload_ok

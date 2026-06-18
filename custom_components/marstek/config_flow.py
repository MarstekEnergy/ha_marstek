"""Config flow for Marstek integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pymarstek import MarstekUDPClient
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_MAC
from homeassistant.core import callback
from homeassistant.helpers.device_registry import format_mac

try:
    from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
except ImportError:
    # Fallback for older Home Assistant versions (pre-2025.1)
    try:
        from homeassistant.components.dhcp import DhcpServiceInfo  # type: ignore[assignment,no-redef]
    except ImportError:
        # If DHCP service info is not available, create a minimal stub
        from dataclasses import dataclass

        @dataclass
        class DhcpServiceInfo:  # type: ignore[no-redef]
            """Fallback DHCP service info for older Home Assistant versions."""

            ip: str
            hostname: str
            macaddress: str

from .const import (
    CONF_OPTION_FAILURES_BEFORE_UNAVAILABLE,
    CONF_OPTION_MEDIUM_SCAN_INTERVAL,
    CONF_OPTION_REQUEST_DELAY,
    CONF_OPTION_REQUEST_TIMEOUT,
    CONF_OPTION_SCAN_INTERVAL,
    DOMAIN,
    MAX_FAILURES_BEFORE_UNAVAILABLE,
    MAX_MEDIUM_SCAN_INTERVAL,
    MAX_REQUEST_DELAY,
    MAX_REQUEST_TIMEOUT,
    MAX_SCAN_INTERVAL,
    MIN_FAILURES_BEFORE_UNAVAILABLE,
    MIN_MEDIUM_SCAN_INTERVAL,
    MIN_REQUEST_DELAY,
    MIN_REQUEST_TIMEOUT,
    MIN_SCAN_INTERVAL,
    get_entry_options,
)

_LOGGER = logging.getLogger(__name__)


class MarstekConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Marstek."""

    VERSION = 1
    domain = DOMAIN
    discovered_devices: list[dict[str, Any]]
    _discovered_ip: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step - broadcast device discovery."""
        if user_input is not None:
            # User has selected a device from the discovered list
            device_index = int(user_input["device"])
            device = self.discovered_devices[device_index]

            # Check if device is already configured using host/mac
            self._async_abort_entries_match({CONF_HOST: device["ip"]})
            # Use BLE-MAC as unique_id for stability (beardhatcode & mik-laj feedback)
            # BLE-MAC is more stable than WiFi MAC and ensures device history continuity
            unique_id_mac = (
                device.get("ble_mac") or device.get("mac") or device.get("wifi_mac")
            )
            if unique_id_mac:
                self._async_abort_entries_match({CONF_MAC: unique_id_mac})
                await self.async_set_unique_id(format_mac(unique_id_mac))
                self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=f"Marstek {device['device_type']} ({device['ip']})",
                data={
                    CONF_HOST: device["ip"],
                    CONF_MAC: device["mac"],
                    "device_type": device["device_type"],
                    "version": device["version"],
                    "wifi_name": device["wifi_name"],
                    "wifi_mac": device["wifi_mac"],
                    "ble_mac": device["ble_mac"],
                    "model": device["model"],  # Compatibility field
                    "firmware": device["firmware"],  # Compatibility field
                },
            )

        # Start broadcast device discovery
        try:
            _LOGGER.info("Starting device discovery")
            udp_client = MarstekUDPClient()
            await udp_client.async_setup()

            # Execute broadcast discovery with retry mechanism
            devices = await self._discover_devices_with_retry(udp_client)
            await udp_client.async_cleanup()

            if not devices:
                return self.async_show_form(
                    step_id="user",
                    data_schema=vol.Schema({}),
                    errors={"base": "no_devices_found"},
                )

            # Store discovered devices for selection
            self.discovered_devices = devices
            _LOGGER.info("Discovered %d devices", len(devices))

            # Show device selection form with detailed device information
            device_options = {}
            for i, device in enumerate(devices):
                # Build detailed device display name with all important info
                device_name = (
                    f"{device.get('device_type', 'Unknown')} "
                    f"v{device.get('version', 'Unknown')} "
                    f"({device.get('wifi_name', 'No WiFi')}) "
                    f"- {device.get('ip', 'Unknown')}"
                )
                device_options[str(i)] = device_name

            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {vol.Required("device"): vol.In(device_options)}
                ),
                description_placeholders={
                    "devices": "\n".join(
                        [f"- {name}" for name in device_options.values()]
                    )
                },
            )

        except (OSError, TimeoutError, ValueError) as err:
            _LOGGER.error("Device discovery failed: %s", err)
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({}),
                errors={"base": "discovery_failed"},
            )

    async def _discover_devices_with_retry(
        self, udp_client, max_retries=2, retry_delay=3000
    ):
        """Device discovery retry mechanism."""
        for attempt in range(1, max_retries + 1):
            try:
                if attempt > 1:
                    _LOGGER.info("Device discovery, attempt %d", attempt)
                    await asyncio.sleep(retry_delay / 1000)  # Convert to seconds
                    # Clear cache, force re-discovery
                    udp_client.clear_discovery_cache()

                # First attempt uses cache, retries force refresh
                use_cache = attempt == 1
                devices = await udp_client.discover_devices(use_cache=use_cache)

                if devices:
                    if attempt > 1:
                        _LOGGER.info("Device discovery retry successful")
                    return devices
                _LOGGER.warning("Attempt %d found no devices", attempt)

            except (OSError, TimeoutError, ValueError) as error:
                _LOGGER.error("Device discovery failed, attempt %d: %s", attempt, error)

                if attempt == max_retries:
                    _LOGGER.error(
                        "Device discovery failed after %d retries: %s",
                        max_retries,
                        error,
                    )
                    # Try using cached data as fallback
                    if udp_client._discovery_cache:  # noqa: SLF001 - internal access needed for fallback
                        _LOGGER.info("Using cached device data as fallback")
                        return udp_client._discovery_cache.copy()  # noqa: SLF001 - internal access needed for fallback
                    raise

        return []

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> config_entries.ConfigFlowResult:
        """Handle DHCP discovery to update IP address when it changes (mik-laj feedback)."""
        mac = format_mac(discovery_info.macaddress)
        _LOGGER.info(
            "DHCP discovery triggered: MAC=%s, IP=%s, Hostname=%s",
            mac,
            discovery_info.ip,
            discovery_info.hostname,
        )

        # Use BLE-MAC or MAC as unique_id (beardhatcode & mik-laj feedback)
        # Try to find existing entry by MAC address
        for entry in self._async_current_entries(include_ignore=False):
            entry_mac = (
                entry.data.get("ble_mac")
                or entry.data.get("mac")
                or entry.data.get("wifi_mac")
            )
            if entry_mac and format_mac(entry_mac) == mac:
                # Found existing entry, update IP if it changed
                if entry.data.get(CONF_HOST) != discovery_info.ip:
                    _LOGGER.info(
                        "DHCP discovery: Device %s IP changed from %s to %s, updating config entry",
                        mac,
                        entry.data.get(CONF_HOST),
                        discovery_info.ip,
                    )
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={**entry.data, CONF_HOST: discovery_info.ip},
                    )
                    # Reload the entry to use new IP
                    self.hass.async_create_task(
                        self.hass.config_entries.async_reload(entry.entry_id)
                    )
                else:
                    _LOGGER.debug(
                        "DHCP discovery: Device %s IP unchanged (%s)",
                        mac,
                        discovery_info.ip,
                    )
                return self.async_abort(reason="already_configured")

        # No existing entry found, continue with user flow
        _LOGGER.debug("DHCP discovery: No existing entry found for MAC %s", mac)
        return await self.async_step_user()

    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Handle discovery from Scanner (integration discovery)."""
        discovered_ip = discovery_info.get("ip")
        discovered_ble_mac = discovery_info.get("ble_mac")

        if not discovered_ble_mac:
            return self.async_abort(reason="invalid_discovery_info")

        # Set unique_id using BLE-MAC
        await self.async_set_unique_id(format_mac(discovered_ble_mac))
        self._discovered_ip = discovered_ip

        # Handle discovery with unique_id (updates existing entries or creates new)
        return await self._async_handle_discovery_with_unique_id()

    async def _async_handle_discovery_with_unique_id(
        self,
    ) -> config_entries.ConfigFlowResult:
        """Handle any discovery with a unique id (similar to Yeelight pattern)."""
        for entry in self._async_current_entries(include_ignore=False):
            # Check if unique_id matches
            if entry.unique_id != self.unique_id:
                continue

            reload = entry.state == ConfigEntryState.SETUP_RETRY
            if entry.data.get(CONF_HOST) != self._discovered_ip:
                _LOGGER.info(
                    "Discovery: Device %s IP changed from %s to %s, updating config entry",
                    entry.unique_id,
                    entry.data.get(CONF_HOST),
                    self._discovered_ip,
                )
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_HOST: self._discovered_ip}
                )
                reload = entry.state in (
                    ConfigEntryState.SETUP_RETRY,
                    ConfigEntryState.LOADED,
                )
            if reload:
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return self.async_abort(reason="already_configured")

        # No existing entry found, continue with user flow
        return await self.async_step_user()

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> MarstekOptionsFlowHandler:
        """Return the options flow handler."""
        return MarstekOptionsFlowHandler(config_entry)


class MarstekOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Marstek integration options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage Marstek polling and communication options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = get_entry_options(self._config_entry)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_OPTION_SCAN_INTERVAL,
                        default=options.scan_interval,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    ),
                    vol.Required(
                        CONF_OPTION_MEDIUM_SCAN_INTERVAL,
                        default=options.medium_scan_interval,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_MEDIUM_SCAN_INTERVAL,
                            max=MAX_MEDIUM_SCAN_INTERVAL,
                        ),
                    ),
                    vol.Required(
                        CONF_OPTION_REQUEST_DELAY,
                        default=options.request_delay,
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(min=MIN_REQUEST_DELAY, max=MAX_REQUEST_DELAY),
                    ),
                    vol.Required(
                        CONF_OPTION_REQUEST_TIMEOUT,
                        default=options.request_timeout,
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(min=MIN_REQUEST_TIMEOUT, max=MAX_REQUEST_TIMEOUT),
                    ),
                    vol.Required(
                        CONF_OPTION_FAILURES_BEFORE_UNAVAILABLE,
                        default=options.failures_before_unavailable,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_FAILURES_BEFORE_UNAVAILABLE,
                            max=MAX_FAILURES_BEFORE_UNAVAILABLE,
                        ),
                    ),
                }
            ),
        )

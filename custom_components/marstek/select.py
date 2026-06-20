"""Select platform for Marstek integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pymarstek import build_command, get_es_mode

from homeassistant.components.select import SelectEntity
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from typing import TYPE_CHECKING

from .button import _build_mode_button_config
from .const import DEFAULT_UDP_PORT, DOMAIN
from .coordinator import MarstekDataUpdateCoordinator

if TYPE_CHECKING:
    from . import MarstekConfigEntry

_LOGGER = logging.getLogger(__name__)

CMD_ES_SET_MODE = "ES.SetMode"
MODE_OPTIONS = ["Auto", "AI", "Manual", "Passive", "Ups"]
RETRY_TIMEOUTS = [2.4, 3.2, 4.0]
RETRY_BACKOFF_BASES = [0.4, 0.6, 0.8]
VERIFY_ATTEMPTS = 4
VERIFY_TIMEOUT = 2.4
VERIFY_DELAY = 0.6


def _build_mode_command(mode: str) -> str:
    """Build ES.SetMode command for target mode (uses shared builder for consistency)."""
    config = _build_mode_button_config(mode)
    # For select "Passive" and "Manual" we may want power, but services/number are preferred for control.
    # Keep simple delegation; for full power use services.
    if mode == "Passive":
        # Select Passive -> default neutral, use number or service for real power
        config = {"mode": "Passive", "passive_cfg": {"power": 0, "cd_time": 300}}
    elif mode == "Manual":
        # Neutral as in buttons
        config = _build_mode_button_config("Manual")
    return build_command(CMD_ES_SET_MODE, {"id": 0, "config": config})


def _normalize_mode_option(mode: str) -> str | None:
    """Normalize device mode strings to UI option values."""
    normalized = mode.strip().upper()
    if normalized == "AUTO":
        return "Auto"
    if normalized == "AI":
        return "AI"
    if normalized == "MANUAL":
        return "Manual"
    if normalized == "PASSIVE":
        return "Passive"
    if normalized in {"UPS", "UPS MODE"}:
        return "Ups"
    return None


class MarstekModeSelect(CoordinatorEntity[MarstekDataUpdateCoordinator], SelectEntity):
    """Select entity for Marstek mode."""

    _attr_has_entity_name = True
    _attr_name = "Operation Mode"
    _attr_options = MODE_OPTIONS
    _attr_icon = "mdi:cog"

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: MarstekConfigEntry,
    ) -> None:
        """Initialize mode select."""
        super().__init__(coordinator)
        self._device_info = device_info
        self._config_entry = config_entry
        self._optimistic_option: str | None = None
        self._apply_task: asyncio.Task | None = None
        self._last_confirmed_option: str | None = None

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
        """Return unique ID."""
        device_id = (
            self._device_info.get("ble_mac")
            or self._device_info.get("mac")
            or self._device_info.get("wifi_mac")
            or self._device_info.get("ip", "unknown")
        )
        return f"{device_id}_operation_mode"

    @property
    def available(self) -> bool:
        """Return whether the device is reachable."""
        return self.coordinator.is_device_reachable()

    @property
    def current_option(self) -> str | None:
        """Return selected option."""
        if self._optimistic_option is not None:
            return self._optimistic_option
        mode = self.coordinator.data.get("device_mode") if self.coordinator.data else None
        if not isinstance(mode, str):
            return self._last_confirmed_option
        normalized = _normalize_mode_option(mode)
        if normalized is not None:
            self._last_confirmed_option = normalized
            return normalized
        # Keep last stable mode during transient "unknown"/empty responses.
        return self._last_confirmed_option

    async def async_select_option(self, option: str) -> None:
        """Select mode option and send ES.SetMode in background."""
        if option not in MODE_OPTIONS:
            return

        host = self._config_entry.data.get(CONF_HOST, self._device_info.get("ip", ""))
        if not isinstance(host, str) or not host:
            return

        previous_option = self.current_option
        self._optimistic_option = option
        self.async_write_ha_state()

        _LOGGER.info(
            "Sending ES.SetMode (%s) to Marstek at %s — user/automation action",
            option,
            host,
        )

        if self.hass:
            if self._apply_task and not self._apply_task.done():
                self._apply_task.cancel()
            self._apply_task = self.hass.async_create_task(
                self._async_apply_mode(option, host, previous_option)
            )

    async def _async_apply_mode(
        self, option: str, host: str, previous_option: str | None
    ) -> None:
        """Apply mode change with retries and rollback on failure."""
        success = False
        await self.coordinator.udp_client.pause_polling(host)
        try:
            command = _build_mode_command(option)
            for attempt_idx, (timeout, backoff_base) in enumerate(
                zip(RETRY_TIMEOUTS, RETRY_BACKOFF_BASES, strict=False), start=1
            ):
                try:
                    response = await self.coordinator.udp_client.send_request(
                        command,
                        host,
                        DEFAULT_UDP_PORT,
                        timeout=timeout,
                        quiet_on_timeout=True,
                    )
                    result = response.get("result", {}) if isinstance(response, dict) else {}
                    set_result = (
                        result.get("set_result") if isinstance(result, dict) else None
                    )
                    if set_result is False:
                        raise ValueError("ES.SetMode returned set_result=false")
                    if await self._verify_mode(option, host):
                        success = True
                        break
                except (TimeoutError, OSError, ValueError) as err:
                    _LOGGER.debug(
                        "Mode change attempt %d/%d failed for %s: %s",
                        attempt_idx,
                        len(RETRY_TIMEOUTS),
                        host,
                        err,
                    )
                    if attempt_idx >= len(RETRY_TIMEOUTS):
                        break
                    await asyncio.sleep(backoff_base * attempt_idx + 0.2 * attempt_idx)
        finally:
            await self.coordinator.udp_client.resume_polling(host)

        if not success and previous_option in MODE_OPTIONS:
            self._optimistic_option = previous_option
            self._last_confirmed_option = previous_option
            self.async_write_ha_state()
        self._optimistic_option = None
        if not success:
            _LOGGER.warning("Failed to apply mode '%s' for device %s", option, host)
        else:
            self._last_confirmed_option = option
        await self.coordinator.async_request_refresh()

    async def _verify_mode(self, expected_option: str, host: str) -> bool:
        """Verify mode after ES.SetMode."""
        expected = "UPS" if expected_option == "Ups" else expected_option
        for _ in range(VERIFY_ATTEMPTS):
            try:
                response = await self.coordinator.udp_client.send_request(
                    get_es_mode(0),
                    host,
                    DEFAULT_UDP_PORT,
                    timeout=VERIFY_TIMEOUT,
                    quiet_on_timeout=True,
                )
            except (TimeoutError, OSError, ValueError):
                await asyncio.sleep(VERIFY_DELAY)
                continue

            result = response.get("result", {}) if isinstance(response, dict) else {}
            mode = result.get("mode") if isinstance(result, dict) else None
            if isinstance(mode, str) and mode.upper() == expected.upper():
                return True
            await asyncio.sleep(VERIFY_DELAY)
        return False


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MarstekConfigEntry,
    async_add_entities,
) -> None:
    """Set up select entities for Marstek config entry."""
    coordinator = config_entry.runtime_data.coordinator
    device_info = config_entry.runtime_data.device_info
    async_add_entities([MarstekModeSelect(coordinator, device_info, config_entry)])

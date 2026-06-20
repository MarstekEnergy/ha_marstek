"""Button platform for Marstek mode switching (inspired by jaapp/ha-marstek-local-api)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pymarstek import build_command

from homeassistant.components.button import ButtonEntity
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from typing import TYPE_CHECKING

from .const import DEFAULT_UDP_PORT, DOMAIN
from .coordinator import MarstekDataUpdateCoordinator

if TYPE_CHECKING:
    from . import MarstekConfigEntry

_LOGGER = logging.getLogger(__name__)

CMD_ES_SET_MODE = "ES.SetMode"
RETRY_TIMEOUTS = [2.4, 3.2, 4.0]
RETRY_BACKOFF_BASES = [0.4, 0.6, 0.8]
PAUSE_ON_WRITE = True


def _build_mode_button_config(mode: str) -> dict[str, Any]:
    """Build mode config for button presses (neutral for Manual to avoid forcing idle)."""
    if mode == "Auto":
        return {"mode": "Auto", "auto_cfg": {"enable": 1}}
    elif mode == "AI":
        return {"mode": "AI", "ai_cfg": {"enable": 1}}
    elif mode == "Manual":
        # Neutral Manual (no active schedule/power). Use services for real manual control.
        return {
            "mode": "Manual",
            "manual_cfg": {
                "time_num": 9,
                "start_time": "00:00",
                "end_time": "00:00",
                "week_set": 0,
                "power": 0,
                "enable": 0,
            },
        }
    else:
        # Fallback
        return {"mode": "Auto", "auto_cfg": {"enable": 1}}


class MarstekModeButton(CoordinatorEntity[MarstekDataUpdateCoordinator], ButtonEntity):
    """Button to switch to a specific operating mode."""

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: MarstekConfigEntry,
        mode: str,
        name: str,
        icon: str,
    ) -> None:
        """Initialize mode button."""
        super().__init__(coordinator)
        self._device_info = device_info
        self._config_entry = config_entry
        self._mode = mode
        self._attr_has_entity_name = True
        self._attr_name = name
        self._attr_icon = icon

        device_identifier = (
            device_info.get("ble_mac")
            or device_info.get("mac")
            or device_info.get("wifi_mac")
            or device_info["ip"]
        )
        self._attr_unique_id = f"{device_identifier}_{mode.lower()}_mode_button"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_identifier)},
            name=f"Marstek {device_info['device_type']}",
            manufacturer="Marstek",
            model=device_info["device_type"],
            sw_version=str(device_info["version"]),
            hw_version=device_info.get("wifi_mac", ""),
        )

    @property
    def available(self) -> bool:
        """Return whether the device is reachable."""
        return self.coordinator.is_device_reachable()

    async def async_press(self) -> None:
        """Press the button to switch mode."""
        host = self._config_entry.data.get(CONF_HOST, self._device_info.get("ip", ""))
        if not isinstance(host, str) or not host:
            return

        _LOGGER.info("Button: Setting mode to %s for %s", self._mode, host)

        config = _build_mode_button_config(self._mode)
        command = build_command(CMD_ES_SET_MODE, {"id": 0, "config": config})

        success = False

        if PAUSE_ON_WRITE:
            await self.coordinator.udp_client.pause_polling(host)

        try:
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
                    set_result = result.get("set_result") if isinstance(result, dict) else None
                    if set_result is False:
                        raise ValueError("ES.SetMode returned set_result=false")

                    # Optimistic update of device_mode
                    if self.coordinator.data:
                        updated = dict(self.coordinator.data)
                        updated["device_mode"] = self._mode
                        self.coordinator.async_set_updated_data(updated)

                    success = True
                    _LOGGER.info("Successfully set mode to %s for %s", self._mode, host)
                    break
                except (TimeoutError, OSError, ValueError) as err:
                    _LOGGER.debug(
                        "Mode button %s attempt %d/%d failed for %s: %s",
                        self._mode,
                        attempt_idx,
                        len(RETRY_TIMEOUTS),
                        host,
                        err,
                    )
                    if attempt_idx >= len(RETRY_TIMEOUTS):
                        break
                    await asyncio.sleep(backoff_base * attempt_idx + 0.2 * attempt_idx)
        finally:
            if PAUSE_ON_WRITE:
                await self.coordinator.udp_client.resume_polling(host)

        if not success:
            _LOGGER.warning("Failed to set mode to %s for device %s via button", self._mode, host)

        # Refresh to get real state
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MarstekConfigEntry,
    async_add_entities,
) -> None:
    """Set up mode button entities."""
    coordinator = config_entry.runtime_data.coordinator
    device_info = config_entry.runtime_data.device_info

    entities = [
        MarstekModeButton(coordinator, device_info, config_entry, "Auto", "Auto mode", "mdi:auto-mode"),
        MarstekModeButton(coordinator, device_info, config_entry, "AI", "AI mode", "mdi:brain"),
        MarstekModeButton(coordinator, device_info, config_entry, "Manual", "Manual mode", "mdi:calendar-clock"),
    ]

    async_add_entities(entities)

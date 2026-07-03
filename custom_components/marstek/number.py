"""Number platform for Marstek integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pymarstek import build_command

from homeassistant.components.number import NumberEntity
from homeassistant.const import CONF_HOST, UnitOfPower
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
RETRY_TIMEOUTS = [2.4, 3.2, 4.0, 5.0, 6.0]
RETRY_BACKOFF_BASES = [0.4, 0.6, 0.8, 1.0, 1.2]


def _build_passive_command(power: int) -> str:
    """Build ES.SetMode command for Passive mode."""
    payload = {
        "id": 0,
        "config": {
            "mode": "Passive",
            "passive_cfg": {
                "power": power,
                "cd_time": 300,
            },
        },
    }
    return build_command(CMD_ES_SET_MODE, payload)


class MarstekPassivePowerNumber(
    CoordinatorEntity[MarstekDataUpdateCoordinator], NumberEntity
):
    """Number entity for passive mode power."""

    _attr_has_entity_name = True
    _attr_name = "Passive Power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:flash"
    _attr_native_min_value = 0
    _attr_native_max_value = 3000
    _attr_native_step = 10
    _attr_mode = "box"

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: MarstekConfigEntry,
    ) -> None:
        """Initialize passive power number."""
        super().__init__(coordinator)
        self._device_info = device_info
        self._config_entry = config_entry
        self._value: float | None = None
        self._apply_task: asyncio.Task | None = None

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
        return f"{device_id}_passive_power"

    @property
    def available(self) -> bool:
        """Return whether the device is reachable."""
        return self.coordinator.is_device_reachable()

    @property
    def native_value(self) -> float | None:
        """Return configured passive power when known."""
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        """Set passive power and apply via ES.SetMode in background."""
        new_value = int(value)
        host = self._config_entry.data.get(CONF_HOST, self._device_info.get("ip", ""))
        if not isinstance(host, str) or not host:
            return

        old_value = self._value
        self._value = float(new_value)
        self.async_write_ha_state()

        _LOGGER.info(
            "Sending ES.SetMode (Passive %s W) to Marstek at %s — user/automation action",
            new_value,
            host,
        )

        if self.hass:
            if self._apply_task and not self._apply_task.done():
                self._apply_task.cancel()
            self._apply_task = self.hass.async_create_task(
                self._async_apply_value(new_value, old_value, host)
            )

    async def _async_apply_value(
        self, new_value: int, old_value: float | None, host: str
    ) -> None:
        """Apply passive power with retries and rollback on failure."""
        success = False
        await self.coordinator.udp_client.pause_polling(host)
        try:
            command = _build_passive_command(new_value)
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
                    success = True
                    break
                except (TimeoutError, OSError, ValueError) as err:
                    _LOGGER.debug(
                        "Passive power set attempt %d/%d failed for %s: %s",
                        attempt_idx,
                        len(RETRY_TIMEOUTS),
                        host,
                        err,
                    )
                    if attempt_idx >= len(RETRY_TIMEOUTS):
                        break
                    jitter = 0.25 * attempt_idx
                    await asyncio.sleep(backoff_base * attempt_idx + jitter)
        finally:
            await self.coordinator.udp_client.resume_polling(host)

        if not success:
            self._value = old_value
            self.async_write_ha_state()
            _LOGGER.warning(
                "Failed to set passive power to %s W for device %s", new_value, host
            )
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MarstekConfigEntry,
    async_add_entities,
) -> None:
    """Set up number entities for Marstek config entry."""
    coordinator = config_entry.runtime_data.coordinator
    device_info = config_entry.runtime_data.device_info
    async_add_entities([MarstekPassivePowerNumber(coordinator, device_info, config_entry)])

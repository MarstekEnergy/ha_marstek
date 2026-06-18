"""Data update coordinator for Marstek devices."""

from __future__ import annotations

from datetime import timedelta
import asyncio
import logging
import time
from typing import Any

from pymarstek import MarstekUDPClient, get_es_mode, get_es_status, get_pv_status
from pymarstek.data_parser import parse_es_mode_response, parse_pv_status_response

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DATA_CATEGORY_STATIC,
    DEFAULT_UDP_PORT,
    DOMAIN,
    MarstekOptions,
    get_entry_options,
)

_LOGGER = logging.getLogger(__name__)

_ES_MODE_EXTENDED_KEYS = (
    "input_energy",
    "output_energy",
    "ct_state",
    "a_power",
    "b_power",
    "c_power",
    "total_power",
    "offgrid_power",
)

_ES_STATUS_KEYS = (
    "bat_soc",
    "bat_power",
    "ongrid_power",
    "offgrid_power",
    "bat_cap",
    "pv_power",
    "total_pv_energy",
    "total_grid_output_energy",
    "total_grid_input_energy",
    "total_load_energy",
)


class MarstekDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Per-device data update coordinator."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        udp_client: MarstekUDPClient,
        device_ip: str,
    ) -> None:
        """Initialize the coordinator."""
        self.udp_client = udp_client
        self.config_entry = config_entry
        self._initial_device_ip = device_ip
        self._options = get_entry_options(config_entry)
        self.category_last_updated: dict[str, float] = {}
        self.last_message_timestamp: float | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=f"Marstek {device_ip}",
            update_interval=timedelta(seconds=self._options.scan_interval),
            config_entry=config_entry,
        )
        _LOGGER.debug(
            "Device %s polling coordinator started, interval: %ss, "
            "medium interval: %ss, request delay: %ss",
            device_ip,
            self._options.scan_interval,
            self._options.medium_scan_interval,
            self._options.request_delay,
        )

        config_entry.async_on_unload(
            config_entry.add_update_listener(self._async_config_entry_updated)
        )

    @property
    def device_ip(self) -> str:
        """Get current device IP from config entry (supports dynamic IP updates)."""
        if self.config_entry:
            return self.config_entry.data.get(CONF_HOST, self._initial_device_ip)
        return self._initial_device_ip

    def _apply_options(self) -> None:
        """Reload polling options from the config entry."""
        self._options = get_entry_options(self.config_entry)
        self.update_interval = timedelta(seconds=self._options.scan_interval)

    def _touch_category(self, category: str) -> None:
        """Record a successful refresh for a data category."""
        self.category_last_updated[category] = time.time()

    def is_category_fresh(self, category: str) -> bool:
        """Return whether a data category was updated recently enough to display."""
        if category == DATA_CATEGORY_STATIC:
            return True
        if category not in self.category_last_updated:
            return False
        max_age = self._options.scan_interval * self._options.staleness_threshold
        return (time.time() - self.category_last_updated[category]) < max_age

    def get_seconds_since_last_message(self) -> int | None:
        """Return seconds since any API call last succeeded."""
        if self.last_message_timestamp is None:
            return None
        return int(time.time() - self.last_message_timestamp)

    def is_device_reachable(self) -> bool:
        """Return whether the device had a successful poll within the unavailable window."""
        if self.last_message_timestamp is None:
            return self.data is not None
        elapsed = time.time() - self.last_message_timestamp
        return elapsed < self._options.unavailable_after_seconds

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch device data; preserve previous values on partial UDP failures."""
        current_ip = self.device_ip
        _LOGGER.debug("Start polling device: %s", current_ip)

        if self.udp_client.is_polling_paused(current_ip):
            _LOGGER.debug("Polling paused for device: %s, skipping update", current_ip)
            return self.data or {}

        is_first_update = self.data is None
        device_status: dict[str, Any] = dict(self.data) if self.data else {}
        options = self._options
        had_success = False

        es_mode_response = await self._send_with_retry(
            get_es_mode(0),
            "ES.GetMode",
            current_ip,
            options,
        )
        if isinstance(es_mode_response, dict):
            parsed = parse_es_mode_response(es_mode_response)
            if parsed.get("device_mode", "Unknown") != "Unknown":
                device_status.update(parsed)
                self._merge_es_mode_extended_fields(device_status, es_mode_response)
                self._touch_category("es")
                had_success = True
            else:
                _LOGGER.debug("ES.GetMode returned unknown mode for %s", current_ip)
        else:
            _LOGGER.debug("ES.GetMode missed for %s", current_ip)

        await asyncio.sleep(options.request_delay)

        # jaapp reads grid power from ES.GetStatus on every fast poll (not ES.GetMode).
        device_status["_es_status_ongrid_fresh"] = False
        es_status_response = await self._send_with_retry(
            get_es_status(0),
            "ES.GetStatus",
            current_ip,
            options,
        )
        if isinstance(es_status_response, dict):
            if self._merge_es_status_fields(device_status, es_status_response):
                device_status["_es_status_ongrid_fresh"] = True
            self._touch_category("es")
            self._touch_category("energy")
            had_success = True
        else:
            _LOGGER.debug("ES.GetStatus missed for %s", current_ip)

        await asyncio.sleep(options.request_delay)

        pv_response = await self._send_with_retry(
            get_pv_status(0),
            "PV.GetStatus",
            current_ip,
            options,
        )
        if isinstance(pv_response, dict):
            device_status.update(parse_pv_status_response(pv_response))
            self._touch_category("pv")
            had_success = True
        else:
            _LOGGER.debug("PV.GetStatus missed for %s", current_ip)

        self._restore_previous_pv_if_missing(device_status)

        if self._is_suspicious_zero_snapshot(device_status):
            _LOGGER.warning(
                "Transient zero/default snapshot from %s; keeping previous values",
                current_ip,
            )
            if self.data:
                device_status = dict(self.data)
            elif not had_success:
                device_status = {}
        else:
            self._normalize_pv_power_scaling(device_status)

        if had_success:
            self.last_message_timestamp = time.time()
            _LOGGER.debug(
                "Device %s poll done: SOC %s%%, ongrid %sW, Mode %s",
                current_ip,
                device_status.get("battery_soc"),
                device_status.get("ongrid_power"),
                device_status.get("device_mode"),
            )
        else:
            _LOGGER.debug(
                "No fresh data for %s this cycle; keeping previous snapshot",
                current_ip,
            )

        if is_first_update and not had_success:
            raise UpdateFailed(f"No response from Marstek device at {current_ip}")

        return device_status

    async def _send_with_retry(
        self,
        message: str,
        method: str,
        current_ip: str,
        options: MarstekOptions,
        *,
        retry_attempts: int = 2,
    ) -> dict[str, Any] | None:
        """Send a UDP request with bounded retries."""
        for attempt in range(1, retry_attempts + 1):
            try:
                return await self.udp_client.send_request(
                    message,
                    current_ip,
                    DEFAULT_UDP_PORT,
                    timeout=options.request_timeout,
                )
            except (TimeoutError, OSError, ValueError) as err:
                _LOGGER.debug(
                    "%s failed for %s on attempt %d/%d: %s",
                    method,
                    current_ip,
                    attempt,
                    retry_attempts,
                    err,
                )
                if attempt < retry_attempts:
                    await asyncio.sleep(1.0)
        return None

    @staticmethod
    def _merge_es_mode_extended_fields(
        device_status: dict[str, Any], response: dict[str, Any]
    ) -> None:
        """Extract additional ES.GetMode fields not covered by the library parser."""
        result = response.get("result", {})
        if not isinstance(result, dict):
            return
        for key in _ES_MODE_EXTENDED_KEYS:
            value = result.get(key)
            if isinstance(value, (int, float)):
                device_status[key] = value

    @staticmethod
    def _merge_es_status_fields(
        device_status: dict[str, Any], response: dict[str, Any]
    ) -> bool:
        """Extract ES.GetStatus fields; return True if ongrid_power was updated."""
        result = response.get("result", {})
        if not isinstance(result, dict):
            return False
        updated_ongrid = False
        for key in _ES_STATUS_KEYS:
            value = result.get(key)
            if isinstance(value, (int, float)):
                device_status[key] = value
                if key == "ongrid_power":
                    updated_ongrid = True
        return updated_ongrid

    def _normalize_pv_power_scaling(self, device_status: dict[str, Any]) -> None:
        """Normalize PV power units if payload appears to be deciwatts."""
        for channel in range(1, 5):
            power_key = f"pv{channel}_power"
            voltage_key = f"pv{channel}_voltage"
            current_key = f"pv{channel}_current"

            power = device_status.get(power_key)
            voltage = device_status.get(voltage_key)
            current = device_status.get(current_key)

            if not all(isinstance(v, (int, float)) for v in (power, voltage, current)):
                continue

            power_w = float(power)
            expected_w = float(voltage) * float(current)
            if expected_w <= 0:
                continue

            ratio = power_w / expected_w
            if ratio > 5 and abs((power_w / 10) - expected_w) / expected_w < 0.35:
                device_status[power_key] = round(power_w / 10, 1)

        aggregate_pv = device_status.get("pv_power")
        if isinstance(aggregate_pv, (int, float)):
            aggregate_w = float(aggregate_pv)
            if aggregate_w > 0:
                channel_powers: dict[int, float] = {}
                for channel in range(1, 5):
                    value = device_status.get(f"pv{channel}_power")
                    if isinstance(value, (int, float)):
                        channel_powers[channel] = float(value)

                if channel_powers:
                    raw_sum = sum(channel_powers.values())
                    raw_error = abs(raw_sum - aggregate_w)

                    if raw_sum > aggregate_w * 2.0:
                        best_channel: int | None = None
                        best_scaled_value = 0.0
                        best_error = raw_error

                        for channel, value in channel_powers.items():
                            candidate_sum = raw_sum - value + (value / 10.0)
                            candidate_error = abs(candidate_sum - aggregate_w)
                            if candidate_error < best_error:
                                best_error = candidate_error
                                best_channel = channel
                                best_scaled_value = round(value / 10.0, 1)

                        if best_channel is not None and best_error < raw_error * 0.5:
                            device_status[f"pv{best_channel}_power"] = best_scaled_value

        pv1_power = device_status.get("pv1_power")
        if not isinstance(pv1_power, (int, float)) or float(pv1_power) <= 0:
            return

        other_powers = [
            float(device_status.get(f"pv{ch}_power"))
            for ch in range(2, 5)
            if isinstance(device_status.get(f"pv{ch}_power"), (int, float))
            and float(device_status.get(f"pv{ch}_power")) > 0
        ]
        if len(other_powers) < 2:
            return
        baseline = sum(other_powers) / len(other_powers)
        if baseline <= 0:
            return

        ratio = float(pv1_power) / baseline
        if 6.5 <= ratio <= 13.5:
            device_status["pv1_power"] = round(float(pv1_power) / 10.0, 1)

    def _restore_previous_pv_if_missing(self, device_status: dict[str, Any]) -> None:
        """Keep previous PV snapshot when PV.GetStatus likely failed."""
        previous = self.data or {}
        if not previous:
            return

        pv_keys = [
            f"pv{ch}_{metric}"
            for ch in range(1, 5)
            for metric in ("power", "voltage", "current", "state")
        ]
        current_voltages = [
            device_status.get(f"pv{ch}_voltage", 0)
            for ch in range(1, 5)
            if isinstance(device_status.get(f"pv{ch}_voltage"), (int, float))
        ]
        if not current_voltages or not all(v == 0 for v in current_voltages):
            return

        previous_voltages = [
            previous.get(f"pv{ch}_voltage", 0)
            for ch in range(1, 5)
            if isinstance(previous.get(f"pv{ch}_voltage"), (int, float))
        ]
        if not previous_voltages or not any(v > 0 for v in previous_voltages):
            return

        for key in pv_keys:
            value = previous.get(key)
            if isinstance(value, (int, float)):
                device_status[key] = value

    def _is_suspicious_zero_snapshot(self, device_status: dict[str, Any]) -> bool:
        """Detect likely transient all-zero/default frame."""
        previous = self.data or {}
        if not previous:
            return False

        numeric_keys = [
            "battery_soc",
            "battery_power",
            "ongrid_power",
            "offgrid_power",
            "pv1_power",
            "pv1_voltage",
            "pv1_current",
            "pv2_power",
            "pv2_voltage",
            "pv2_current",
            "pv3_power",
            "pv3_voltage",
            "pv3_current",
            "pv4_power",
            "pv4_voltage",
            "pv4_current",
            "a_power",
            "b_power",
            "c_power",
            "total_power",
            "pv_power",
        ]

        current_values = [
            float(device_status[k])
            for k in numeric_keys
            if isinstance(device_status.get(k), (int, float))
        ]
        previous_values = [
            float(previous[k])
            for k in numeric_keys
            if isinstance(previous.get(k), (int, float))
        ]
        if len(current_values) < 8 or not previous_values:
            return False

        current_zero_ratio = sum(1 for v in current_values if v == 0) / len(
            current_values
        )
        previous_nonzero_ratio = sum(1 for v in previous_values if v != 0) / len(
            previous_values
        )

        return current_zero_ratio >= 0.85 and previous_nonzero_ratio >= 0.25

    async def _async_config_entry_updated(
        self, hass: HomeAssistant, entry: ConfigEntry
    ) -> None:
        """Handle config entry update - IP changes and option reloads."""
        if not self.config_entry:
            return

        self._apply_options()

        old_ip = self._initial_device_ip
        new_ip = entry.data.get(CONF_HOST, old_ip)

        if new_ip != old_ip:
            _LOGGER.info(
                "Config entry updated, IP changed from %s to %s, updating entity names",
                old_ip,
                new_ip,
            )
            await self._update_entity_names(new_ip, old_ip)
            self._initial_device_ip = new_ip

        await self.async_request_refresh()

    async def _update_entity_names(self, new_ip: str, old_ip: str) -> None:
        """Update device and entity names in registry when IP changes."""
        if not self.config_entry:
            return
        device_registry = dr.async_get(self.hass)
        device_identifier = (
            self.config_entry.data.get("ble_mac")
            or self.config_entry.data.get("mac")
            or self.config_entry.data.get("wifi_mac")
        )
        if device_identifier:
            device = device_registry.async_get_device(
                identifiers={(DOMAIN, device_identifier)}
            )
            if device and device.name and old_ip in device.name:
                device_registry.async_update_device(
                    device.id, name=device.name.replace(old_ip, new_ip)
                )

        entity_registry = er.async_get(self.hass)
        entities = er.async_entries_for_config_entry(
            entity_registry, self.config_entry.entry_id
        )

        for entity_entry in entities:
            if entity_entry.name and old_ip in entity_entry.name:
                entity_registry.async_update_entity(
                    entity_entry.entity_id,
                    name=entity_entry.name.replace(old_ip, new_ip),
                )

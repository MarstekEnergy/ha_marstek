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
        self._consecutive_failures = 0
        self._last_medium_fetch: float = 0.0

        super().__init__(
            hass,
            _LOGGER,
            name=f"Marstek {device_ip}",
            update_interval=timedelta(seconds=self._options.scan_interval),
            config_entry=config_entry,
        )
        _LOGGER.debug(
            "Device %s polling coordinator started, fast interval: %ss, "
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
        self._last_medium_fetch = 0.0

    def _should_fetch_medium(self) -> bool:
        """Return whether the medium-tier ES.GetStatus poll is due."""
        if self._last_medium_fetch == 0.0:
            return True
        elapsed = time.monotonic() - self._last_medium_fetch
        return elapsed >= self._options.medium_scan_interval

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch device data using tiered Open API polling."""
        current_ip = self.device_ip
        _LOGGER.debug("Start polling device: %s", current_ip)

        if self.udp_client.is_polling_paused(current_ip):
            _LOGGER.debug("Polling paused for device: %s, skipping update", current_ip)
            return self.data or {}

        options = self._options
        device_status: dict[str, Any] = {}

        try:
            es_mode_response = await self._send_with_retry(
                get_es_mode(0),
                "ES.GetMode",
                current_ip,
                options,
            )
            if not isinstance(es_mode_response, dict):
                raise TimeoutError(f"No ES.GetMode response from {current_ip}")

            device_status.update(parse_es_mode_response(es_mode_response))
            self._merge_es_mode_extended_fields(device_status, es_mode_response)

            if device_status.get("device_mode", "Unknown") == "Unknown":
                raise TimeoutError(
                    f"No valid ES.GetMode data received from device at {current_ip}"
                )

            await asyncio.sleep(options.request_delay)

            pv_response = await self._send_with_retry(
                get_pv_status(0),
                "PV.GetStatus",
                current_ip,
                options,
            )
            if isinstance(pv_response, dict):
                device_status.update(parse_pv_status_response(pv_response))

            if self._should_fetch_medium():
                await asyncio.sleep(options.request_delay)
                es_status_response = await self._send_with_retry(
                    get_es_status(0),
                    "ES.GetStatus",
                    current_ip,
                    options,
                )
                if isinstance(es_status_response, dict):
                    self._merge_es_status_fields(device_status, es_status_response)
                self._last_medium_fetch = time.monotonic()

            if self._is_suspicious_zero_snapshot(device_status):
                _LOGGER.warning(
                    "Detected transient zero/default snapshot from %s; keeping previous values",
                    current_ip,
                )
                raise TimeoutError("Transient zero/default snapshot detected")

            self._restore_previous_pv_if_missing(device_status)
            self._carry_forward_missing_snapshot_values(device_status)
            self._normalize_pv_power_scaling(device_status)

            _LOGGER.debug(
                "Device %s poll done: SOC %s%%, Power %sW, Mode %s, Status %s",
                current_ip,
                device_status.get("battery_soc"),
                device_status.get("battery_power"),
                device_status.get("device_mode"),
                device_status.get("battery_status"),
            )

            self._consecutive_failures = 0
            return device_status
        except (TimeoutError, OSError, ValueError) as err:
            return self._handle_poll_failure(current_ip, err)

    def _handle_poll_failure(
        self, current_ip: str, err: TimeoutError | OSError | ValueError
    ) -> dict[str, Any]:
        """Track consecutive failures and mark entities unavailable when threshold is hit."""
        self._consecutive_failures += 1
        threshold = self._options.failures_before_unavailable
        _LOGGER.warning(
            "Device %s status request failed (%d/%d): %s. "
            "Scanner will detect IP changes automatically",
            current_ip,
            self._consecutive_failures,
            threshold,
            err,
        )
        if self._consecutive_failures >= threshold:
            raise UpdateFailed(
                f"Device {current_ip} unreachable after {self._consecutive_failures} "
                f"consecutive failures"
            ) from err
        return self.data or {}

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
    ) -> None:
        """Extract energy counters and aggregate PV power from ES.GetStatus."""
        result = response.get("result", {})
        if not isinstance(result, dict):
            return
        for key in _ES_STATUS_KEYS:
            value = result.get(key)
            if isinstance(value, (int, float)):
                device_status[key] = value

    def _carry_forward_missing_snapshot_values(
        self, device_status: dict[str, Any]
    ) -> None:
        """Keep last known values when optional API calls miss fields.

        Medium-tier calls (`ES.GetStatus`) or intermittent UDP loss can omit
        fields for a single cycle. Without this, entities flip to `unknown`.
        """
        previous = self.data or {}
        if not previous:
            return

        sticky_keys = (
            *_ES_STATUS_KEYS,
            *_ES_MODE_EXTENDED_KEYS,
        )

        restored_keys: list[str] = []
        for key in sticky_keys:
            if key in device_status:
                continue
            previous_value = previous.get(key)
            if isinstance(previous_value, (int, float)):
                device_status[key] = previous_value
                restored_keys.append(key)

        if restored_keys:
            _LOGGER.debug(
                "Restored %d missing transient keys from previous snapshot: %s",
                len(restored_keys),
                ", ".join(restored_keys),
            )

    def _normalize_pv_power_scaling(self, device_status: dict[str, Any]) -> None:
        """Normalize PV power units if payload appears to be deciwatts.

        Some devices/firmwares appear to report PV power in deciwatts for
        individual channels while voltage/current are still in V/A.
        We detect this by comparing reported power against V * I.
        """
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
                normalized = round(power_w / 10, 1)
                _LOGGER.debug(
                    "Normalized %s from %s to %s W (V=%s, I=%s)",
                    power_key,
                    power_w,
                    normalized,
                    voltage,
                    current,
                )
                device_status[power_key] = normalized

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
                            key = f"pv{best_channel}_power"
                            _LOGGER.debug(
                                "Normalized %s from %s to %s W using aggregate pv_power=%s",
                                key,
                                device_status.get(key),
                                best_scaled_value,
                                aggregate_w,
                            )
                            device_status[key] = best_scaled_value

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
        if not (6.5 <= ratio <= 13.5):
            return

        normalized = round(float(pv1_power) / 10.0, 1)
        _LOGGER.debug(
            "Normalized pv1_power from %s to %s W using PV1 10x outlier rule "
            "(baseline=%.1f, ratio=%.2f)",
            pv1_power,
            normalized,
            baseline,
            ratio,
        )
        device_status["pv1_power"] = normalized

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
        _LOGGER.debug("Restored previous PV snapshot after transient PV.GetStatus failure")

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
                new_device_name = device.name.replace(old_ip, new_ip)
                _LOGGER.info(
                    "Updating device name from %s to %s",
                    device.name,
                    new_device_name,
                )
                device_registry.async_update_device(device.id, name=new_device_name)

        entity_registry = er.async_get(self.hass)
        entities = er.async_entries_for_config_entry(
            entity_registry, self.config_entry.entry_id
        )

        updated_count = 0
        for entity_entry in entities:
            if entity_entry.name and old_ip in entity_entry.name:
                new_name = entity_entry.name.replace(old_ip, new_ip)
                _LOGGER.debug(
                    "Updating entity %s name from %s to %s",
                    entity_entry.entity_id,
                    entity_entry.name,
                    new_name,
                )
                entity_registry.async_update_entity(
                    entity_entry.entity_id, name=new_name
                )
                updated_count += 1

        if updated_count > 0:
            _LOGGER.info(
                "Updated %d entity name(s) to reflect new IP: %s -> %s",
                updated_count,
                old_ip,
                new_ip,
            )

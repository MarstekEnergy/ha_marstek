"""Data update coordinator for Marstek devices."""

from dataclasses import replace
from datetime import datetime, timedelta
import logging
from typing import override

from aiomarstek import MarstekDeviceInfo, MarstekDeviceStatus, MarstekUDPClient

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.hass_dict import HassKey

from .const import (
    DOMAIN,
    ERROR_INVALID_DATA,
    ERROR_NETWORK,
    ERROR_NO_RESPONSE,
    ERROR_NONE,
    PV_DEVICE_TYPES,
    SUPPORTED_DEVICE_TYPES,
)

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=30)
# Missed polls in a row before entities become unavailable; the ones before
# keep showing the last values, so a single dropped packet goes unnoticed.
UNAVAILABLE_POLLS = 2
# Missed polls in a row before a repair issue is raised. The device's local
# API can hang until it is switched off and on again in the Marstek app.
UNREACHABLE_POLLS = 10

# Lifetime counters only ever grow; a lower reading is a device glitch and
# would show up in the Energy dashboard as a meter reset.
MONOTONIC_KEYS = (
    "total_pv_energy",
    "total_grid_output_energy",
    "total_grid_input_energy",
)
# A battery level this high cannot drop to 0 % within one poll.
SOC_GLITCH_FLOOR = 10
# Polls in a row reporting 0 % after which the reading is accepted as real,
# so a device that is really empty is not held at its last level forever.
ZERO_SOC_POLLS = 3


type MarstekConfigEntry = ConfigEntry[MarstekDataUpdateCoordinator]

MARSTEK_UDP_CLIENT: HassKey[MarstekUDPClient] = HassKey(DOMAIN)


class MarstekDataUpdateCoordinator(DataUpdateCoordinator[MarstekDeviceStatus]):
    """Per-device data update coordinator."""

    config_entry: MarstekConfigEntry
    device_info: MarstekDeviceInfo
    # When the device last answered a status request; data from a poll the
    # device did not answer is the previous data, so this shows staleness.
    last_response: datetime | None = None
    failed_polls: int = 0
    error_state: str = ERROR_NONE
    # Polls in a row the device answered with a battery level of exactly 0 %.
    zero_soc_polls: int = 0

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MarstekConfigEntry,
        udp_client: MarstekUDPClient,
    ) -> None:
        """Initialize the coordinator."""
        self.device_ip = config_entry.data[CONF_HOST]
        self.udp_client = udp_client
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"Marstek {self.device_ip}",
            update_interval=SCAN_INTERVAL,
        )
        _LOGGER.debug(
            "Device %s polling coordinator started, interval: %ss",
            self.device_ip,
            SCAN_INTERVAL.total_seconds(),
        )

    @override
    async def _async_setup(self) -> None:
        """Validate device availability and cache its device information."""
        try:
            device_info = await self.udp_client.get_device_info(self.device_ip)
        except (TimeoutError, OSError, TypeError) as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="device_connection_failed",
                translation_placeholders={"host": self.device_ip},
            ) from err

        if not device_info.stable_id:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="missing_stable_id",
                translation_placeholders={"host": self.device_ip},
            )

        if device_info.device_type not in SUPPORTED_DEVICE_TYPES:
            raise ConfigEntryError(
                translation_domain=DOMAIN,
                translation_key="unsupported_device",
                translation_placeholders={"device_type": device_info.device_type},
            )

        self.device_info = device_info

    @property
    def issue_id(self) -> str:
        """Return the repair issue id for an unreachable device."""
        return f"open_api_unreachable_{self.config_entry.entry_id}"

    @override
    async def _async_update_data(self) -> MarstekDeviceStatus:
        """Fetch device data from the Marstek client library."""
        _LOGGER.debug("Start polling device: %s", self.device_ip)
        previous = self.data
        answered_before = self.udp_client.get_last_response_time(self.device_ip)

        try:
            status = await self.udp_client.get_device_status(
                self.device_ip,
                previous_data=previous,
                include_pv=self.device_info.device_type in PV_DEVICE_TYPES,
            )
        except (TimeoutError, OSError, TypeError) as err:
            return self._missed_poll(previous, err)

        last_response = self.udp_client.get_last_response_time(self.device_ip)
        if last_response is None or last_response == answered_before:
            # The library returns the previous data when nothing answered.
            return self._missed_poll(
                previous, TimeoutError(f"No response from {self.device_ip}")
            )

        if self.failed_polls >= UNREACHABLE_POLLS:
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
        if self.failed_polls:
            _LOGGER.info(
                "Device %s answered again after %s missed polls",
                self.device_ip,
                self.failed_polls,
            )
            await self._async_refresh_firmware()
        self.failed_polls = 0
        self.error_state = ERROR_NONE
        self.last_response = last_response
        if status.battery_soc == 0:
            self.zero_soc_polls += 1
        else:
            self.zero_soc_polls = 0
        return hold_glitches(previous, status, self.zero_soc_polls)

    def _missed_poll(
        self, previous: MarstekDeviceStatus | None, err: Exception
    ) -> MarstekDeviceStatus:
        """Keep the last data through a missed poll, then fail the update."""
        self.failed_polls += 1
        self.error_state = (
            ERROR_NO_RESPONSE
            if isinstance(err, TimeoutError)
            else ERROR_NETWORK
            if isinstance(err, OSError)
            else ERROR_INVALID_DATA
        )
        if self.failed_polls == UNREACHABLE_POLLS:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self.issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="open_api_unreachable",
                translation_placeholders={"host": self.device_ip},
            )
        if previous is not None and self.failed_polls < UNAVAILABLE_POLLS:
            _LOGGER.debug(
                "Device %s missed poll %s: %s", self.device_ip, self.failed_polls, err
            )
            return previous
        raise UpdateFailed(
            translation_domain=DOMAIN,
            translation_key="device_update_failed",
            translation_placeholders={"host": self.device_ip},
        ) from err

    async def _async_refresh_firmware(self) -> None:
        """Re-read the firmware version, as a firmware update causes an outage."""
        try:
            info = await self.udp_client.get_device_info(self.device_ip)
        except (TimeoutError, OSError, TypeError):
            return
        if info.version == self.device_info.version:
            return
        _LOGGER.info(
            "Device %s firmware changed from %s to %s",
            self.device_ip,
            self.device_info.version,
            info.version,
        )
        self.device_info = replace(self.device_info, version=info.version)
        registry = dr.async_get(self.hass)
        if device := registry.async_get_device_by_identifier(
            (DOMAIN, self.device_info.stable_id), self.config_entry.entry_id
        ):
            registry.async_update_device(device.id, sw_version=str(info.version))


def hold_glitches(
    previous: MarstekDeviceStatus | None,
    current: MarstekDeviceStatus,
    zero_soc_polls: int = 0,
) -> MarstekDeviceStatus:
    """Keep previous values where the device reports implausible readings."""
    if previous is None:
        return current
    held: dict[str, object] = {}
    for key in MONOTONIC_KEYS:
        old, new = getattr(previous, key), getattr(current, key)
        if old is not None and (new is None or new < old):
            held[key] = old
    if (
        current.battery_soc == 0
        and (previous.battery_soc or 0) >= SOC_GLITCH_FLOOR
        and zero_soc_polls < ZERO_SOC_POLLS
    ):
        held["battery_soc"] = previous.battery_soc
    if held:
        _LOGGER.debug(
            "Device %s implausible readings held: %s", current.device_ip, held
        )
        return replace(current, **held)
    return current

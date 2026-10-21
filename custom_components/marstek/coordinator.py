"""Data update coordinator for Marstek devices."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import logging
from typing import NoReturn, override

from aiomarstek import (
    CMD_ES_MODE,
    CMD_ES_STATUS,
    CMD_PV_GET_STATUS,
    MONOTONIC_KEYS,
    MarstekDeviceInfo,
    MarstekDeviceStatus,
    MarstekNoResponse,
    MarstekUDPClient,
    hold_glitches,
)

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
    ERROR_PARTIAL_RESPONSE,
    PV_DEVICE_TYPES,
    SUPPORTED_DEVICE_TYPES,
)

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=30)
# Missed polls in a row before a repair issue is raised. The device's local
# API can hang until it is switched off and on again in the Marstek app.
UNREACHABLE_POLLS = 10


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
    # Polls in a row each lifetime counter stayed below its previous reading.
    falling_energy_polls: dict[str, int]

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MarstekConfigEntry,
        udp_client: MarstekUDPClient,
    ) -> None:
        """Initialize the coordinator."""
        self.device_ip = config_entry.data[CONF_HOST]
        self.udp_client = udp_client
        self.falling_energy_polls: dict[str, int] = {
            key: 0 for key in MONOTONIC_KEYS
        }
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

        try:
            status = await self.udp_client.get_device_status(
                self.device_ip,
                previous_data=previous,
                include_pv=self.device_info.device_type in PV_DEVICE_TYPES,
            )
        except (TimeoutError, OSError, TypeError, MarstekNoResponse) as err:
            self._missed_poll(err)

        # Clear the repair issue once the device answers again after a long
        # outage; the firmware version is re-read below in case it changed.
        if self.failed_polls >= UNREACHABLE_POLLS:
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
        if self.failed_polls:
            _LOGGER.info(
                "Device %s answered again after %s missed polls",
                self.device_ip,
                self.failed_polls,
            )
            # Run in the background so a slow or hanging device info request
            # does not delay the first status update after a recovery.
            self.hass.async_create_task(self._async_refresh_firmware())
        self.failed_polls = 0
        # A poll counts as partial when a status request the device should
        # answer stayed silent: the fields it feeds keep their previous
        # values while the update succeeds, so the error state is the only
        # hint that data is stale. PV is only expected once the device has
        # answered or refused PV.GetStatus, so a non-PV device never flags
        # it and a PV-capable one does not before its capability is known.
        answered = self.udp_client.get_last_results(self.device_ip)
        expected = {CMD_ES_STATUS, CMD_ES_MODE}
        if (
            self.device_info.device_type in PV_DEVICE_TYPES
            and self.udp_client.is_pv_status_supported(self.device_ip) is not False
        ):
            expected.add(CMD_PV_GET_STATUS)
        self.error_state = (
            ERROR_PARTIAL_RESPONSE if expected - answered.keys() else ERROR_NONE
        )
        self.last_response = datetime.now(UTC)
        if status.battery_soc == 0:
            self.zero_soc_polls += 1
        else:
            self.zero_soc_polls = 0
        # Track each counter's drop streak on its own, so one resetting
        # cannot let another's glitch through; only a real drop counts.
        if previous is None:
            self.falling_energy_polls = {key: 0 for key in MONOTONIC_KEYS}
        else:
            for key in MONOTONIC_KEYS:
                old = getattr(previous, key)
                new = getattr(status, key)
                if old is not None and new is not None and new < old:
                    self.falling_energy_polls[key] += 1
                else:
                    self.falling_energy_polls[key] = 0
        return hold_glitches(
            previous, status, self.zero_soc_polls, self.falling_energy_polls
        )

    def _missed_poll(
        self, err: Exception
    ) -> NoReturn:
        """Count an unreachable poll, raise the repair issue, then fail the update."""
        # Network errors are usually swallowed inside the library's status
        # requests and surface as MarstekNoResponse, so ERROR_NETWORK mainly
        # guards against OSError raised outside them (e.g. a closed socket).
        self.error_state = (
            ERROR_NO_RESPONSE
            if isinstance(err, (TimeoutError, MarstekNoResponse))
            else ERROR_NETWORK
            if isinstance(err, OSError)
            else ERROR_INVALID_DATA
        )
        # Invalid data means the device answered, so it breaks the streak of
        # unreachable polls: reset the counter and clear any repair issue.
        # The update still fails, but the unreachable advice does not apply.
        if isinstance(err, TypeError):
            self.failed_polls = 0
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
        else:
            self.failed_polls += 1
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
        raise UpdateFailed(
            translation_domain=DOMAIN,
            translation_key="device_update_failed",
            translation_placeholders={"host": self.device_ip},
        ) from err

    async def _async_refresh_firmware(self) -> None:
        """Re-read the firmware version, as a firmware update causes an outage."""
        try:
            info = await self.udp_client.get_device_info(self.device_ip)
        except (TimeoutError, OSError, TypeError) as err:
            _LOGGER.debug(
                "Device %s firmware re-read failed: %s", self.device_ip, err
            )
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

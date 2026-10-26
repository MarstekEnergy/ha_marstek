"""Data update coordinator for Marstek devices."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import TypeAlias, override

from aiomarstek import (
    DEFAULT_UDP_PORT,
    MarstekDeviceInfo,
    MarstekDeviceStatus,
    MarstekNoResponse,
    MarstekUDPClient,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.hass_dict import HassKey

from .const import (
    CONF_PORT,
    DOMAIN,
    PV_DEVICE_TYPES,
    SUPPORTED_DEVICE_TYPES,
    WRITE_DEBOUNCE_DELAY,
)
from .passive_state import PassiveField
from .write_queue import DebouncedWriteQueue

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=30)

# Polls a device may stay fully silent before its entities are marked
# unavailable. Below this threshold the last reading is kept so a single
# glitch does not flip the entities; above it a real outage surfaces.
MAX_SILENT_POLLS = 3

MARSTEK_UDP_CLIENT: HassKey[MarstekUDPClient] = HassKey(DOMAIN)


class MarstekDataUpdateCoordinator(DataUpdateCoordinator[MarstekDeviceStatus]):
    """Per-device data update coordinator."""

    config_entry: MarstekConfigEntry
    device_info: MarstekDeviceInfo

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MarstekConfigEntry,
        udp_client: MarstekUDPClient,
    ) -> None:
        """Initialize the coordinator."""
        self.device_ip = config_entry.data[CONF_HOST]
        # The device UDP port is configurable per device; the Open API
        # documents 49152-65535 as the recommended custom range. The shared
        # client listens on a fixed local port, so only the target port here
        # changes between devices.
        self.device_port = config_entry.data.get(CONF_PORT, DEFAULT_UDP_PORT)
        self.udp_client = udp_client
        self._silent_polls = 0
        # Last-written Passive-mode parameters. The Open API does not report
        # them, so the passive number entities are write-only; these hold the
        # most recent value written from HA so setting one field can re-send
        # Passive mode preserving the other instead of clobbering it to 0.
        # Each field tracks a pending target and the last device-confirmed
        # value separately so a failed/coalesced write rolls back to the last
        # confirmed value instead of an intermediate one (see passive_state).
        self.passive_power = PassiveField()
        self.passive_cd_time = PassiveField()
        # Writes go through the shared debounce queue, which coalesces
        # same-control repeats and keeps distinct controls queued; see
        # write_queue.DebouncedWriteQueue for the ordering rules.
        self._write_queue = DebouncedWriteQueue(
            WRITE_DEBOUNCE_DELAY, self.async_request_refresh
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"Marstek {self.device_ip}",
            update_interval=SCAN_INTERVAL,
        )
        _LOGGER.debug(
            "Device %s polling coordinator started (port %s), interval: %ss",
            self.device_ip,
            self.device_port,
            SCAN_INTERVAL.total_seconds(),
        )

    @property
    def silent_polls(self) -> int:
        """Number of consecutive polls the device did not answer.

        Reset to zero on the next poll that returns data. Exposed for
        diagnostics so a download can distinguish a single dropped response
        (``silent_polls < MAX_SILENT_POLLS``) from an outage that has crossed
        the availability threshold, without the diagnostics module reaching
        into a private counter.
        """
        return self._silent_polls

    @override
    async def _async_setup(self) -> None:
        """Validate device availability and cache its device information."""
        try:
            device_info = await self.udp_client.get_device_info(
                self.device_ip, port=self.device_port
            )
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

        # Guard against an IP reassigned to a different Marstek device:
        # the entry was created for a specific stable id, so the device
        # answering now must still match it. A mismatch means this entry
        # would otherwise silently read and control another device.
        configured_unique_id = self.config_entry.unique_id
        if configured_unique_id and device_info.stable_id != configured_unique_id:
            raise ConfigEntryError(
                translation_domain=DOMAIN,
                translation_key="device_identity_mismatch",
                translation_placeholders={
                    "host": self.device_ip,
                    "expected": configured_unique_id,
                    "actual": device_info.stable_id,
                },
            )

        if device_info.device_type not in SUPPORTED_DEVICE_TYPES:
            raise ConfigEntryError(
                translation_domain=DOMAIN,
                translation_key="unsupported_device",
                translation_placeholders={"device_type": device_info.device_type},
            )

        self.device_info = device_info

    @override
    async def _async_update_data(self) -> MarstekDeviceStatus:
        """Fetch device data from the Marstek client library.

        A fully silent poll (MarstekNoResponse) keeps the last successful
        reading rather than immediately marking the entities unavailable,
        matching the client library contract. The silence is counted, and
        after ``MAX_SILENT_POLLS`` consecutive silent polls the entities are
        marked unavailable via UpdateFailed, so a prolonged outage does not
        keep showing stale-but-plausible data forever. A single successful
        poll clears the counter.
        """
        _LOGGER.debug("Start polling device: %s", self.device_ip)
        previous = self.data

        try:
            data = await self.udp_client.get_device_status(
                self.device_ip,
                port=self.device_port,
                previous_data=previous,
                include_pv=self.device_info.device_type in PV_DEVICE_TYPES,
            )
        except MarstekNoResponse:
            self._silent_polls += 1
            if previous is None or self._silent_polls >= MAX_SILENT_POLLS:
                # No data yet, or the device has been silent long enough that
                # the last reading is no longer trustworthy.
                _LOGGER.warning(
                    "Device %s silent for %d polls; marking unavailable",
                    self.device_ip,
                    self._silent_polls,
                )
                raise UpdateFailed(
                    translation_domain=DOMAIN,
                    translation_key="device_update_failed",
                    translation_placeholders={"host": self.device_ip},
                ) from None
            _LOGGER.warning(
                "Device %s did not answer; keeping last data (silent %d/%d)",
                self.device_ip,
                self._silent_polls,
                MAX_SILENT_POLLS,
            )
            return previous
        except (TimeoutError, OSError, TypeError) as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="device_update_failed",
                translation_placeholders={"host": self.device_ip},
            ) from err

        self._silent_polls = 0
        return data

    async def async_write(
        self, key: str, action: Callable[[], Awaitable[None]]
    ) -> None:
        """Run a write command, coalescing only same-control repeats.

        Rapid writes to the *same* control item (e.g. toggling one switch
        back and forth) collapse within the debounce window so only the
        latest value is sent; a superseded repeat resolves with the
        surviving write's outcome (success *or* failure), because only that
        write reaches the device. Writes to *different* control items do not
        replace each other: they queue and execute one per debounce window,
        so a mode change, LED toggle or factory reset is never silently
        dropped behind another control's write. A single refresh follows
        each executed write.

        ``key`` identifies the *device state* being changed, not the
        entity: the mode select and the UPS switch share one
        ``"device_mode"`` key so the latest plain mode request wins, while
        the Passive number entities and the Passive ``set_mode`` service
        share a separate ``"passive"`` key so a plain mode switch never
        coalesces away their power/cooldown parameters.
        """
        await self._write_queue.async_write(key, action)

    async def async_shutdown(self) -> None:
        """Stop the debounced write queue before the entry unloads.

        Cancels any queued write and the background drain task so a pending
        command cannot fire (and refresh) after the integration is gone.
        """
        await self._write_queue.async_shutdown()

    def reopen_write_queue(self) -> None:
        """Reopen the write queue after a config entry unload failed.

        The platforms stay loaded when unloading fails, so writes must work
        again. Only the closed flag is reset; writes queued at shutdown time
        were already cancelled and are not restored.
        """
        self._write_queue.reopen()


MarstekConfigEntry: TypeAlias = ConfigEntry[MarstekDataUpdateCoordinator]

"""Data update coordinator for Marstek devices."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from datetime import timedelta
from typing import TYPE_CHECKING, Any, override

from aiomarstek import (
    DEFAULT_UDP_PORT,
    MarstekDeviceInfo,
    MarstekDeviceStatus,
    MarstekNoResponse,
    MarstekUDPClient,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
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
from .write_queue import DebouncedWriteQueue

if TYPE_CHECKING:
    from .entity import MarstekAssumedStateEntity

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=30)

# Consecutive fully silent polls tolerated before the entities are marked
# unavailable. The first MAX_SILENT_POLLS - 1 silent polls keep the last
# reading so a single glitch does not flip the entities; the MAX_SILENT_POLLS-th
# one raises UpdateFailed so a real outage surfaces.
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
        # The device UDP port is configurable per device; the local API
        # documents 49152-65535 as the recommended custom range. The shared
        # client listens on a fixed local port, so only the target port here
        # changes between devices.
        self.device_port = config_entry.data.get(CONF_PORT, DEFAULT_UDP_PORT)
        self.udp_client = udp_client
        self._silent_polls = 0
        # All writes go through one debounce queue; see write_queue for the
        # coalescing, ordering and cancellation rules.
        self._write_queue = DebouncedWriteQueue(
            WRITE_DEBOUNCE_DELAY, self.async_request_refresh
        )
        # Write-only controls keyed by their entity key. Service calls write
        # through the coordinator rather than through the entity, and the
        # device never reports these settings back, so the entity has to be
        # told which value a confirmed service write produced.
        self._assumed_entities: dict[str, MarstekAssumedStateEntity] = {}
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
        self, key: str, action: Callable[[], Coroutine[Any, Any, None]]
    ) -> bool:
        """Queue a write on this device's debounce queue.

        ``key`` identifies the *device state* being changed, not the entity:
        controls that write the same state share one key so the latest request
        wins, and unrelated controls keep separate keys so one is never
        coalesced away behind another. Ordering, coalescing and cancellation
        are the queue's (see :mod:`.write_queue`); one refresh follows each
        executed write.

        Returns ``True`` when this action ran on the device, ``False`` when a
        later same-key write superseded it; callers that record the written
        value must check it, or a coalesced call would claim a value that never
        left the queue.
        """
        return await self._write_queue.async_write(key, action)

    @callback
    def async_register_assumed_entity(
        self, key: str, entity: MarstekAssumedStateEntity
    ) -> None:
        """Track a write-only control so confirmed writes can reach it."""
        self._assumed_entities[key] = entity

    @callback
    def async_unregister_assumed_entity(
        self, key: str, entity: MarstekAssumedStateEntity
    ) -> None:
        """Stop tracking a control that is being removed."""
        if self._assumed_entities.get(key) is entity:
            del self._assumed_entities[key]

    @callback
    def async_reserve_assumed_write(self, key: str) -> int:
        """Claim the display generation for a service write about to be queued.

        Returns the token the caller hands back to
        :meth:`async_note_assumed_write` once the device confirms, or ``0`` when
        no write-only control shows this key (the mode select reads the mode
        back from the device instead). The token must be taken *before* the
        write is queued, so it orders the request against entity clicks and
        other service calls by arrival rather than by confirmation order.
        """
        entity = self._assumed_entities.get(key)
        return 0 if entity is None else entity.async_reserve_write()

    @callback
    def async_note_assumed_write(self, key: str, value: Any, generation: int) -> None:
        """Tell the matching write-only control that a service write was confirmed.

        ``generation`` is the token reserved before queueing; the control uses
        it to keep a newer click on screen (see
        :meth:`MarstekAssumedStateEntity.async_note_confirmed_value`).
        """
        if (entity := self._assumed_entities.get(key)) is not None:
            entity.async_note_confirmed_value(value, generation)

    @override
    async def async_shutdown(self) -> None:
        """Stop the write queue and the coordinator's own timers on unload.

        Cancels any queued write and the background drain task so a pending
        command cannot fire (and refresh) after the integration is gone, then
        lets the base class cancel the scheduled/debounced refresh -- otherwise
        a queued refresh could still poll the device after the entry unloaded.
        """
        await self._write_queue.async_shutdown()
        await super().async_shutdown()


type MarstekConfigEntry = ConfigEntry[MarstekDataUpdateCoordinator]

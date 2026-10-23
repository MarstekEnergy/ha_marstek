"""The Marstek integration."""

from __future__ import annotations

import asyncio
import logging

import voluptuous as vol
from aiomarstek import MarstekModeConfig, MarstekSetError
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, power_versions_for
from .coordinator import (
    MARSTEK_UDP_CLIENT,
    MarstekConfigEntry,
    MarstekDataUpdateCoordinator,
)
from .helpers import async_create_udp_client
from .service_schema import (
    ATTR_KEEP_ACCUMULATED_DATA,
    ATTR_POWER_VERSION,
    ATTR_SLOTS,
    ATTR_VALUE,
    FACTORY_RESET_FIELDS,
    SET_DOD_FIELDS,
    SET_MANUAL_SCHEDULE_FIELDS,
    SET_MODE_FIELDS,
    SET_POWER_VERSION_FIELDS,
    build_manual_schedule,
    build_mode_config,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Wrap each business-field set with the entity-service target keys so the
# target selector's entity_id/area_id/device_id survive schema validation.
SET_MODE_SERVICE_SCHEMA = cv.make_entity_service_schema(SET_MODE_FIELDS)
SET_DOD_SERVICE_SCHEMA = cv.make_entity_service_schema(SET_DOD_FIELDS)
SET_POWER_VERSION_SERVICE_SCHEMA = cv.make_entity_service_schema(
    SET_POWER_VERSION_FIELDS
)
SET_MANUAL_SCHEDULE_SERVICE_SCHEMA = cv.make_entity_service_schema(
    SET_MANUAL_SCHEDULE_FIELDS
)
FACTORY_RESET_SERVICE_SCHEMA = cv.make_entity_service_schema(FACTORY_RESET_FIELDS)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Marstek integration.

    The shared UDP client is created once and lives for the lifetime of
    the integration. If creation fails, integration setup fails and the
    config entries stay not loaded; reloading the integration retries.
    """
    udp_client = await async_create_udp_client(hass)
    hass.data[MARSTEK_UDP_CLIENT] = udp_client

    async def close_udp_client(_: Event) -> None:
        """Clean up the shared UDP client when Home Assistant stops."""
        await udp_client.async_cleanup()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, close_udp_client)

    _async_setup_services(hass)

    return True


@callback
def _async_setup_services(hass: HomeAssistant) -> None:
    """Register the Marstek configuration services."""
    if hass.services.has_service(DOMAIN, "set_mode"):
        return

    async def set_mode(call: ServiceCall) -> None:
        """Switch device(s) to a new operating mode."""
        try:
            config = build_mode_config(call.data)
        except vol.Invalid as err:
            raise ServiceValidationError(
                f"Invalid mode configuration: {err.msg}"
            ) from err
        if config.mode == "passive":
            # A Passive write carries the power/cooldown parameters and must
            # not be coalesced away by a plain mode switch, so it queues under
            # the same "passive" key the Passive number entities use. The
            # service also drives the passive_power / passive_cd_time fields
            # through the same write lifecycle the number entities use
            # (start_write before, mark_written on success, rollback on
            # failure/cancel), so a number entity changing one field right
            # after preserves the service's other field instead of sending a
            # stale 0, and a coalesced-away service write does not leave the
            # cache pointing at a value the device never received.
            await _async_apply_passive(hass, call, config)
            return
        # Plain mode switches (auto/ai/manual/ups) share the "device_mode"
        # key so they coalesce to the latest request.
        await _async_apply_to_targets(
            hass,
            call,
            "device_mode",
            lambda coord: (
                lambda: coord.udp_client.set_device_mode(
                    coord.device_ip, config, port=coord.device_port
                )
            ),
        )

    async def set_dod(call: ServiceCall) -> None:
        """Set the discharge depth."""
        value = int(call.data[ATTR_VALUE])
        await _async_apply_to_targets(
            hass,
            call,
            "dod",
            lambda coord: (
                lambda: coord.udp_client.set_dod(
                    coord.device_ip, value, port=coord.device_port
                )
            ),
        )

    async def set_power_version(call: ServiceCall) -> None:
        """Set the charge/discharge power version.

        The schema accepts the global documented set; the per-model
        restriction is enforced here against each target device, because a
        service call may target devices of different models. All targets are
        validated before any command is sent, so a multi-device call never
        leaves some devices changed and others untouched.
        """
        version = int(call.data[ATTR_POWER_VERSION])
        coordinators = await _async_resolve_coordinators(hass, call)

        # Pre-check: reject the whole call if any target's model does not
        # support the version, before touching any device.
        for coord in coordinators:
            allowed = power_versions_for(coord.device_info.device_type)
            if version not in allowed:
                raise ServiceValidationError(
                    f"Power version {version} W is not supported for "
                    f"{coord.device_info.device_type}; allowed: "
                    f"{', '.join(str(v) for v in allowed)}"
                )

        failures: list[str] = []
        for coord in coordinators:
            try:
                await coord.async_write(
                    "power_version",
                    lambda c=coord: c.udp_client.set_power_version(
                        c.device_ip, version, port=c.device_port
                    ),
                )
            except MarstekSetError as err:
                _LOGGER.error(
                    "Setting power version failed for %s: %s",
                    coord.device_ip,
                    err,
                )
                failures.append(f"{coord.device_ip}: {err.reason}")
        if failures:
            raise HomeAssistantError(
                "Marstek set_power_version failed for: " + "; ".join(failures)
            )

    async def factory_reset(call: ServiceCall) -> None:
        """Reset device(s) to factory settings."""
        keep = bool(call.data.get(ATTR_KEEP_ACCUMULATED_DATA, False))
        # Match the factory-reset buttons' keys so a service call and a
        # button press are ordered by arrival instead of racing. The two
        # variants stay distinct keys: they are different device operations,
        # so the later one runs after the earlier one rather than replacing it.
        await _async_apply_to_targets(
            hass,
            call,
            "factory_reset_keep_data" if keep else "factory_reset",
            lambda coord: (
                lambda: coord.udp_client.factory_reset(
                    coord.device_ip, keep_accumulated_data=keep, port=coord.device_port
                )
            ),
        )

    async def set_manual_schedule(call: ServiceCall) -> None:
        """Replace the device's Manual-mode time schedule.

        Each slot is sent as its own ES.SetMode with a single-object
        manual_cfg (the documented form); see aiomarstek.set_manual_schedule.
        The friendly ``days`` list and auto-assigned ``time_num`` are folded
        into the device's bitmask/index form by build_manual_schedule.
        """
        try:
            slots = build_manual_schedule(call.data[ATTR_SLOTS])
        except vol.Invalid as err:
            raise ServiceValidationError(f"Invalid manual schedule: {err.msg}") from err
        await _async_apply_to_targets(
            hass,
            call,
            "manual_schedule",
            lambda coord: (
                lambda: coord.udp_client.set_manual_schedule(
                    coord.device_ip, slots, port=coord.device_port
                )
            ),
        )

    hass.services.async_register(DOMAIN, "set_mode", set_mode, SET_MODE_SERVICE_SCHEMA)
    hass.services.async_register(DOMAIN, "set_dod", set_dod, SET_DOD_SERVICE_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        "set_power_version",
        set_power_version,
        SET_POWER_VERSION_SERVICE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "set_manual_schedule",
        set_manual_schedule,
        SET_MANUAL_SCHEDULE_SERVICE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, "factory_reset", factory_reset, FACTORY_RESET_SERVICE_SCHEMA
    )


async def _async_resolve_coordinators(
    hass: HomeAssistant, call: ServiceCall
) -> list[MarstekDataUpdateCoordinator]:
    """Resolve the deduplicated coordinators targeted by a service call.

    Services accept multiple entities, areas or device targets; each resolves
    to one or more Marstek entities, and each entity belongs to one device.
    Coordinators are deduplicated by device IP so a multi-target call acts on
    each device once.
    """
    entity_ids = await cv.async_extract_entity_ids(call)
    if not entity_ids:
        raise ServiceValidationError(
            "No Marstek entities were targeted by this service call; "
            "provide at least one entity_id, device_id, or area_id."
        )
    seen: set[str] = set()
    coordinators: list[MarstekDataUpdateCoordinator] = []
    for entity_id in entity_ids:
        coordinator = _coordinator_for_entity(hass, entity_id)
        if coordinator.device_ip in seen:
            continue
        seen.add(coordinator.device_ip)
        coordinators.append(coordinator)
    return coordinators


async def _async_apply_to_targets(
    hass: HomeAssistant, call: ServiceCall, key: str, make_action
) -> None:
    """Queue a write on every Marstek entity targeted by a service call.

    The write goes through the same per-device debounce queue the entities
    use, so a service call and an entity operation are ordered by arrival
    instead of the service racing ahead of a queued entity write. Runs every
    target even if one fails, then surfaces the combined failure so callers
    (automations, scripts, UI) actually see that the command did not take
    effect instead of a silently successful call.
    """
    coordinators = await _async_resolve_coordinators(hass, call)
    failures: list[str] = []
    for coordinator in coordinators:
        try:
            await coordinator.async_write(key, make_action(coordinator))
        except MarstekSetError as err:
            _LOGGER.error(
                "Marstek command failed for %s: %s", coordinator.device_ip, err
            )
            failures.append(f"{coordinator.device_ip}: {err.reason}")

    if failures:
        raise HomeAssistantError("Marstek command failed for: " + "; ".join(failures))


async def _async_apply_passive(
    hass: HomeAssistant, call: ServiceCall, config: MarstekModeConfig
) -> None:
    """Queue a Passive-mode write, keeping the passive fields in sync.

    Mirrors the field lifecycle the Passive number entities use, so the
    service and the entities never disagree about the last-written power or
    cooldown: both fields are ``start_write``'d before the write, confirmed
    on success and rolled back on failure or a cancel that left the write
    still queued. A write that already started reports its real outcome
    through the queue, so that path does not roll the fields back here.
    """
    coordinators = await _async_resolve_coordinators(hass, call)
    power = config.passive.power
    cd_time = config.passive.cd_time
    failures: list[str] = []
    for coordinator in coordinators:
        coordinator.passive_power.start_write(power)
        coordinator.passive_cd_time.start_write(cd_time)
        try:
            await coordinator.async_write(
                "passive",
                lambda c=coordinator: c.udp_client.set_device_mode(
                    c.device_ip, config, port=c.device_port
                ),
            )
        except MarstekSetError as err:
            coordinator.passive_power.rollback(power)
            coordinator.passive_cd_time.rollback(cd_time)
            _LOGGER.error(
                "Marstek Passive command failed for %s: %s",
                coordinator.device_ip,
                err,
            )
            failures.append(f"{coordinator.device_ip}: {err.reason}")
        except asyncio.CancelledError:
            # The write was still queued and never reached the device; a
            # write that already started reports its real outcome above.
            coordinator.passive_power.rollback(power)
            coordinator.passive_cd_time.rollback(cd_time)
            raise
        else:
            coordinator.passive_power.mark_written(power)
            coordinator.passive_cd_time.mark_written(cd_time)
    if failures:
        raise HomeAssistantError("Marstek command failed for: " + "; ".join(failures))


def _coordinator_for_entity(
    hass: HomeAssistant, entity_id: str
) -> MarstekDataUpdateCoordinator:
    """Return the coordinator owning an entity id."""
    ent_reg = er.async_get(hass)
    entity = ent_reg.async_get(entity_id)
    if entity is None:
        raise ServiceValidationError(f"Entity {entity_id} was not found")
    entry = hass.config_entries.async_get_entry(entity.config_entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            f"Entity {entity_id} does not belong to the Marstek integration"
        )
    return entry.runtime_data


async def async_setup_entry(hass: HomeAssistant, entry: MarstekConfigEntry) -> bool:
    """Set up Marstek from a config entry."""
    coordinator = MarstekDataUpdateCoordinator(
        hass, entry, hass.data[MARSTEK_UDP_CLIENT]
    )
    entry.runtime_data = coordinator
    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: MarstekConfigEntry) -> bool:
    """Unload a config entry."""
    await entry.runtime_data.async_shutdown()
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return True
    # The platforms failed to unload, so they (and their entities/services)
    # stay loaded; reopen the write queue so commands keep working.
    entry.runtime_data.reopen_write_queue()
    return False

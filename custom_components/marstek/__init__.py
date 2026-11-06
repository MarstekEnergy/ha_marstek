"""The Marstek integration."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from functools import partial
from typing import Any

import voluptuous as vol
from aiomarstek import MarstekSetError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.target import (
    SelectedEntities,
    TargetSelection,
    async_extract_referenced_entity_ids,
)
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, power_versions_for
from .coordinator import (
    MARSTEK_UDP_CLIENT,
    MarstekConfigEntry,
    MarstekDataUpdateCoordinator,
)
from .helpers import async_create_udp_client
from .service_schema import (
    ATTR_POWER_VERSION,
    ATTR_VALUE,
    SET_DOD_FIELDS,
    SET_MODE_FIELDS,
    SET_POWER_VERSION_FIELDS,
    build_mode_config,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# A queued device command, and a factory that builds one for a given device.
TargetAction = Callable[[], Coroutine[Any, Any, None]]
TargetActionFactory = Callable[[MarstekDataUpdateCoordinator], TargetAction]
# Optional pre-flight check run against every target before anything is queued.
TargetValidator = Callable[[MarstekDataUpdateCoordinator], None]
# Optional callback run after a target's write was confirmed, to sync the
# write-only entity showing the same setting. The int is the generation token
# reserved before queueing.
TargetWritten = Callable[[MarstekDataUpdateCoordinator, int], None]

# Wrap each business-field set with the entity-service target keys so the
# target selector's entity_id/area_id/device_id survive schema validation.
SET_MODE_SERVICE_SCHEMA = cv.make_entity_service_schema(SET_MODE_FIELDS)
SET_DOD_SERVICE_SCHEMA = cv.make_entity_service_schema(SET_DOD_FIELDS)
SET_POWER_VERSION_SERVICE_SCHEMA = cv.make_entity_service_schema(
    SET_POWER_VERSION_FIELDS
)


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
        # Mode switches (auto/ups) share the "device_mode" key so they
        # coalesce to the latest request.
        await _async_apply_to_targets(
            hass,
            call,
            "device_mode",
            _bind("set_device_mode", config),
        )

    async def set_dod(call: ServiceCall) -> None:
        """Set the discharge depth."""
        value = int(call.data[ATTR_VALUE])
        await _async_apply_to_targets(
            hass,
            call,
            "dod",
            _bind("set_dod", value),
            on_success=lambda coordinator, generation: (
                coordinator.async_note_assumed_write("dod", value, generation)
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

        def check_supported(coordinator: MarstekDataUpdateCoordinator) -> None:
            allowed = power_versions_for(coordinator.device_info.device_type)
            if version not in allowed:
                raise ServiceValidationError(
                    f"Power version {version} W is not supported for "
                    f"{coordinator.device_info.device_type}; allowed: "
                    f"{', '.join(str(v) for v in allowed)}"
                )

        await _async_apply_to_targets(
            hass,
            call,
            "power_version",
            _bind("set_power_version", version),
            validate=check_supported,
            on_success=lambda coordinator, generation: (
                coordinator.async_note_assumed_write(
                    "power_version", str(version), generation
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


def _async_selected_entities(
    hass: HomeAssistant, call: ServiceCall
) -> SelectedEntities:
    """Expand the service target into explicit and indirect entity ids.

    ``referenced`` holds the entity ids the caller named directly; device,
    area, floor and label targets land in ``indirectly_referenced``. The
    distinction matters: a directly named entity that is not a Marstek entity
    is a user error worth raising, while an area that also contains another
    integration's entities must not fail the whole call.
    """
    return async_extract_referenced_entity_ids(hass, TargetSelection(call.data), True)


async def _async_resolve_coordinators(
    hass: HomeAssistant, call: ServiceCall
) -> list[MarstekDataUpdateCoordinator]:
    """Resolve the deduplicated coordinators targeted by a service call.

    Services accept multiple entities, areas or device targets; each resolves
    to one or more entities, and each Marstek entity belongs to one device.
    Coordinators are deduplicated by device IP so a multi-target call acts on
    each device once. Only loaded Marstek entries are usable, so an entry that
    failed setup is reported (explicit target) or skipped (area expansion).
    """
    selected = _async_selected_entities(hass, call)
    explicit = selected.referenced
    candidates = set(explicit) | set(selected.indirectly_referenced)
    if not candidates:
        raise ServiceValidationError(
            "No Marstek entities were targeted by this service call; "
            "provide at least one entity_id, device_id, or area_id."
        )

    seen: set[str] = set()
    coordinators: list[MarstekDataUpdateCoordinator] = []
    for entity_id in sorted(candidates):
        coordinator = _coordinator_for_entity(
            hass, entity_id, strict=entity_id in explicit
        )
        if coordinator is None or coordinator.device_ip in seen:
            continue
        seen.add(coordinator.device_ip)
        coordinators.append(coordinator)

    if not coordinators:
        raise ServiceValidationError(
            "No loaded Marstek device matched the service target."
        )
    return coordinators


def _bind(client_method: str, *args: object, **kwargs: object) -> TargetActionFactory:
    """Return a factory binding a UDP client method to one target device.

    Every device command has the ``client_method(device_ip, *args, port=...)``
    shape, so only the device (known per target) has to be filled in late; the
    method and its arguments are bound once with :func:`functools.partial`
    instead of nesting one lambda per target inside another.
    """

    def factory(coordinator: MarstekDataUpdateCoordinator) -> TargetAction:
        return partial(
            getattr(coordinator.udp_client, client_method),
            coordinator.device_ip,
            *args,
            port=coordinator.device_port,
            **kwargs,
        )

    return factory


def _failure_reason(err: Exception) -> str:
    """Return the device's own reason for a failed write, when it has one."""
    reason = getattr(err, "reason", None)
    return str(reason) if reason is not None else str(err)


async def _async_apply_to_targets(
    hass: HomeAssistant,
    call: ServiceCall,
    key: str,
    action_factory: TargetActionFactory,
    validate: TargetValidator | None = None,
    on_success: TargetWritten | None = None,
) -> None:
    """Queue a write on every Marstek device targeted by a service call.

    The write goes through the same per-device queue the entities use, so a
    service call and an entity click are ordered by arrival. Every target is
    attempted even when one fails, then the combined failure is surfaced so
    callers (automations, scripts, UI) see that the command did not take effect.

    ``validate`` runs against every resolved coordinator *before* anything is
    queued, so a call that is invalid for one target leaves all devices
    untouched (``set_power_version`` depends on the model's allowed values).

    ``on_success`` syncs the write-only entity that shows the same setting; it
    receives the token reserved for this target before queueing, so the entity
    can tell this confirmation apart from a newer click.
    """
    coordinators = await _async_resolve_coordinators(hass, call)
    if validate is not None:
        for coordinator in coordinators:
            validate(coordinator)

    failures: list[str] = []
    for coordinator in coordinators:
        # Claimed before queueing so the token reflects request order: a click
        # that lands during the debounce window must outrank this write.
        generation = coordinator.async_reserve_assumed_write(key) if on_success else 0
        try:
            executed = await coordinator.async_write(key, action_factory(coordinator))
        except (MarstekSetError, RuntimeError, OSError) as err:
            # A shut-down queue (RuntimeError) or an unreachable device (OSError,
            # TimeoutError) must not stop the remaining targets. Cancellation is
            # deliberately not caught: an aborted call stops the whole loop.
            _LOGGER.error(
                "Marstek command failed for %s: %s", coordinator.device_ip, err
            )
            failures.append(f"{coordinator.device_ip}: {_failure_reason(err)}")
        else:
            # A coalesced call reports success without sending anything; it must
            # not sync its own value (the surviving call syncs the real one).
            if executed and on_success is not None:
                on_success(coordinator, generation)

    if failures:
        raise HomeAssistantError("Marstek command failed for: " + "; ".join(failures))


def _coordinator_for_entity(
    hass: HomeAssistant, entity_id: str, *, strict: bool
) -> MarstekDataUpdateCoordinator | None:
    """Return the coordinator owning a targeted entity, or skip it.

    ``strict`` is True for entities the caller named explicitly: those must be
    Marstek entities of a loaded entry, so a typo or a non-Marstek entity
    surfaces as an error. Entities expanded from a device/area/floor/label are
    filtered instead, so a mixed area still works.
    """
    ent_reg = er.async_get(hass)
    entity = ent_reg.async_get(entity_id)
    # ``platform`` is the integration that owns the entity ("marstek");
    # ``domain`` is the entity domain derived from the entity_id ("select",
    # "sensor", ...), so the integration check must use ``platform``.
    if entity is None or entity.platform != DOMAIN:
        _skip_target(strict, f"entity {entity_id} is not a Marstek entity")
        return None

    # config_entry_id is None for a registry entry that is not tied to a
    # config entry; async_get_entry expects a str, so check before calling.
    entry = (
        hass.config_entries.async_get_entry(entity.config_entry_id)
        if entity.config_entry_id
        else None
    )
    if entry is None or entry.domain != DOMAIN:
        _skip_target(
            strict, f"entity {entity_id} is not tied to a Marstek config entry"
        )
        return None
    if entry.state is not ConfigEntryState.LOADED:
        _skip_target(strict, f"the config entry for {entity_id} is not loaded")
        return None

    # runtime_data only exists once async_setup_entry stored a coordinator, and
    # HA deletes it again on unload, so it must never be read unguarded.
    coordinator: MarstekDataUpdateCoordinator | None = getattr(
        entry, "runtime_data", None
    )
    if coordinator is None:
        _skip_target(strict, f"the config entry for {entity_id} has no runtime data")
        return None
    return coordinator


def _skip_target(strict: bool, reason: str) -> None:
    """Raise for an explicitly named target, ignore an expanded one."""
    if strict:
        raise ServiceValidationError(f"Cannot use this target: {reason}")
    _LOGGER.debug("Skipping service target: %s", reason)


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
    """Unload a config entry.

    Platforms are unloaded *before* the coordinator is shut down: a failed
    platform unload leaves the entities (and the entry) loaded, so the
    coordinator must keep polling and stay fully usable. Shutting down first
    would cancel the scheduled and debounced refresh, and no public API can
    restart them -- the entities would then stay loaded but frozen.
    """
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_shutdown()
    return True

"""Service schemas and pure helpers for the Marstek integration.

This module deliberately depends only on voluptuous and the aiomarstek
library, never on the Home Assistant runtime, so its validation and
configuration-building logic can be unit-tested without a running HA
instance.

The schemas here validate only the *business* fields of a service call.
The integration wraps them with ``cv.make_entity_service_schema`` at
registration time so Home Assistant can inject the ``entity_id`` /
``area_id`` / ``device_id`` keys produced by the ``target:`` selector.
"""

from __future__ import annotations

import voluptuous as vol

from aiomarstek import (
    DOD_MAX,
    DOD_MIN,
    MANUAL_SET_AUTO,
    MANUAL_SET_CHARGE,
    MANUAL_SET_DISABLE,
    MANUAL_SET_DISCHARGE,
    MANUAL_SLOT_MAX,
    POWER_VERSIONS,
    MarstekManualSlot,
    MarstekModeConfig,
    MarstekPassiveConfig,
)

# Field names used in service call data.
ATTR_MODE = "mode"
ATTR_CONFIG = "config"
ATTR_VALUE = "value"
ATTR_KEEP_ACCUMULATED_DATA = "keep_accumulated_data"
ATTR_POWER_VERSION = "power_version"
ATTR_SLOTS = "slots"

# The Open API documents the mode field with these spellings.
DOCUMENTED_MODES = ["auto", "ai", "manual", "passive", "ups"]

# Weekday names accepted by the manual-schedule service, in the order the
# Marstek week_set bitmask lays them out (bit 0 = Monday .. bit 6 = Sunday,
# per the Open API document: 0b0000001 = Monday, 0b1111111 = all week).
MANUAL_SCHEDULE_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_WEEKDAY_BITS = {
    "mon": 1,
    "tue": 2,
    "wed": 4,
    "thu": 8,
    "fri": 16,
    "sat": 32,
    "sun": 64,
}
MANUAL_SET_VALUES = [
    MANUAL_SET_DISABLE,
    MANUAL_SET_CHARGE,
    MANUAL_SET_DISCHARGE,
    MANUAL_SET_AUTO,
]


def _strict_bool(value: object) -> bool:
    """Coerce a service value to bool, rejecting ambiguous truthiness.

    A non-empty string like "false" is truthy under ``bool()``, so it
    would silently enable UPS instead of disabling it; only actual
    booleans and the integers 0/1 are accepted.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool) and value in (0, 1):
        return bool(value)
    raise vol.Invalid(f"expected true/false or 0/1, got {value!r}")


def _strict_int(value: object) -> int:
    """Coerce a service value to int, rejecting non-integer values.

    ``int(1200.9)`` would silently truncate to 1200; a non-integer float
    or string is rejected instead, so a typo cannot pick a different
    power version or slot index.
    """
    if isinstance(value, bool):
        raise vol.Invalid(f"expected an integer, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise vol.Invalid(f"expected an integer, got {value!r}")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as err:
        raise vol.Invalid(f"expected an integer, got {value!r}") from err
    if not parsed.is_integer():
        raise vol.Invalid(f"expected an integer, got {value!r}")
    return int(parsed)


# Per-mode nested schemas. Validating the inner structure here means a
# missing field or wrong type raises a clear voluptuous Invalid instead
# of a KeyError/TypeError from the builder.
_MANUAL_SLOT_SCHEMA = vol.Schema(
    {
        vol.Required("time_num"): _strict_int,
        vol.Required("start_time"): str,
        vol.Required("end_time"): str,
        vol.Required("week_set"): _strict_int,
        vol.Required("power"): _strict_int,
        vol.Optional("enable", default=True): _strict_bool,
        vol.Optional("manual_set"): _strict_int,
    }
)
_PASSIVE_CFG_SCHEMA = vol.Schema(
    {
        vol.Required("power"): _strict_int,
        vol.Required("cd_time"): _strict_int,
    }
)
_UPS_CFG_SCHEMA = vol.Schema(
    {
        vol.Optional("enable", default=True): _strict_bool,
    }
)

# Business-field definitions. The integration combines each with the
# entity-service target keys via cv.make_entity_service_schema, so the
# target fields are not (and must not be) listed here.
SET_MODE_FIELDS = {
    vol.Required(ATTR_MODE): vol.In(DOCUMENTED_MODES),
    vol.Optional(ATTR_CONFIG, default={}): dict,
}
SET_DOD_FIELDS = {
    vol.Required(ATTR_VALUE): vol.All(_strict_int, vol.Range(min=DOD_MIN, max=DOD_MAX)),
}
SET_POWER_VERSION_FIELDS = {
    vol.Required(ATTR_POWER_VERSION): vol.All(
        _strict_int, vol.In(list(POWER_VERSIONS))
    ),
}
FACTORY_RESET_FIELDS = {
    vol.Optional(ATTR_KEEP_ACCUMULATED_DATA, default=False): bool,
}

# A single manual-schedule slot for the set_manual_schedule service. Unlike
# _MANUAL_SLOT_SCHEMA (which mirrors the Open API's raw week_set bitmask),
# this one takes a friendly ``days`` list and lets time_num default to the
# slot's position in the list, so callers (and the future schedule card) do
# not need to know the bitmask layout or assign slot indices themselves.
_SCHEDULE_SLOT_SCHEMA = vol.Schema(
    {
        vol.Required("start_time"): str,
        vol.Required("end_time"): str,
        vol.Required("days"): [vol.In(MANUAL_SCHEDULE_WEEKDAYS)],
        vol.Required("power"): _strict_int,
        vol.Optional("manual_set", default=MANUAL_SET_CHARGE): vol.All(
            _strict_int, vol.In(MANUAL_SET_VALUES)
        ),
        vol.Optional("enable", default=True): _strict_bool,
        vol.Optional("time_num"): _strict_int,
    }
)

SET_MANUAL_SCHEDULE_FIELDS = {
    vol.Required(ATTR_SLOTS): [_SCHEDULE_SLOT_SCHEMA],
}

# Standalone schemas (business fields only) used by the unit tests.
SET_MODE_SCHEMA = vol.Schema(SET_MODE_FIELDS)
SET_DOD_SCHEMA = vol.Schema(SET_DOD_FIELDS)
SET_POWER_VERSION_SCHEMA = vol.Schema(SET_POWER_VERSION_FIELDS)
FACTORY_RESET_SCHEMA = vol.Schema(FACTORY_RESET_FIELDS)
SET_MANUAL_SCHEDULE_SCHEMA = vol.Schema(SET_MANUAL_SCHEDULE_FIELDS)


def build_manual_schedule(slots_data: list[dict]) -> list[MarstekManualSlot]:
    """Build manual slots from the set_manual_schedule service payload.

    ``days`` (weekday names) is folded into the device's ``week_set`` bitmask
    (bit 0 = Monday, per the Open API), and ``time_num`` defaults to the
    slot's index in the list so callers do not have to assign it. The slot
    count is capped at the documented maximum (Venus A/C/D/E: 0-9).
    """
    max_slots = MANUAL_SLOT_MAX + 1
    if len(slots_data) > max_slots:
        raise vol.Invalid(
            f"manual schedule supports at most {max_slots} time slots, "
            f"got {len(slots_data)}"
        )
    slots: list[MarstekManualSlot] = []
    for index, slot_data in enumerate(slots_data):
        validated = _SCHEDULE_SLOT_SCHEMA(slot_data)
        week_set = sum(_WEEKDAY_BITS[day] for day in validated["days"])
        try:
            slots.append(
                MarstekManualSlot(
                    time_num=validated.get("time_num", index),
                    start_time=validated["start_time"],
                    end_time=validated["end_time"],
                    week_set=week_set,
                    power=validated["power"],
                    enable=validated["enable"],
                    manual_set=validated["manual_set"],
                )
            )
        except ValueError as err:
            raise vol.Invalid(str(err)) from err
    return slots


def build_mode_config(data: dict) -> MarstekModeConfig:
    """Build a MarstekModeConfig from a validated service call payload.

    The Open API takes the whole manual schedule in one ES.SetMode payload,
    so callers pass the complete list of time slots under ``config.slots``
    (or ``config.manual_cfg``). The API document shows a single slot as an
    *object* in its example, while the firmware's accepted shape (object vs
    array) has not been confirmed against a live device; this builder
    therefore accepts either form and normalizes to the array the client
    library serializes. Treat Manual scheduling as experimental until it is
    verified on real hardware.
    """
    mode = str(data[ATTR_MODE]).casefold()
    config_data = data.get(ATTR_CONFIG) or {}

    passive: MarstekPassiveConfig | None = None
    if mode == "passive":
        passive_cfg = config_data.get("passive_cfg", config_data)
        try:
            validated = _PASSIVE_CFG_SCHEMA(passive_cfg)
        except vol.Invalid as err:
            raise vol.Invalid(f"passive mode config is invalid: {err.msg}") from err
        passive = MarstekPassiveConfig(
            power=validated["power"], cd_time=validated["cd_time"]
        )

    manual_slots: tuple[MarstekManualSlot, ...] = ()
    if mode == "manual":
        # Manual scheduling is experimental: the API document shows a single
        # slot as an object while the client serializes an array, and the
        # firmware's accepted shape has not been confirmed against a live
        # device. Require at least one well-formed slot so a call never
        # silently sends an empty schedule.
        raw_slots = config_data.get("manual_cfg") or config_data.get("slots")
        if isinstance(raw_slots, dict):
            raw_slots = [raw_slots]
        if not isinstance(raw_slots, list) or not raw_slots:
            raise vol.Invalid(
                "manual mode requires at least one time slot in "
                "config.manual_cfg (experimental, format not yet confirmed)"
            )
        max_slots = MANUAL_SLOT_MAX + 1
        if len(raw_slots) > max_slots:
            raise vol.Invalid(
                f"manual mode supports at most {max_slots} time slots, "
                f"got {len(raw_slots)}"
            )
        manual_slots = tuple(_build_manual_slot(slot) for slot in raw_slots)

    ups_enabled = True
    if mode == "ups":
        ups_cfg = config_data.get("ups_cfg", config_data)
        try:
            validated = _UPS_CFG_SCHEMA(ups_cfg)
        except vol.Invalid as err:
            raise vol.Invalid(f"ups mode config is invalid: {err.msg}") from err
        ups_enabled = validated["enable"]

    return MarstekModeConfig(
        mode=mode,
        manual_slots=manual_slots,
        passive=passive,
        ups_enabled=ups_enabled,
    )


def _build_manual_slot(slot: dict) -> MarstekManualSlot:
    """Build a MarstekManualSlot from a service-supplied dict.

    The slot is validated with ``_MANUAL_SLOT_SCHEMA`` so a missing field
    or wrong type raises a clear voluptuous Invalid instead of a
    KeyError/TypeError. Range checks performed by ``MarstekManualSlot``
    itself are also surfaced as voluptuous Invalid.
    """
    try:
        return MarstekManualSlot(**_MANUAL_SLOT_SCHEMA(slot))
    except vol.Invalid:
        raise
    except ValueError as err:
        raise vol.Invalid(str(err)) from err

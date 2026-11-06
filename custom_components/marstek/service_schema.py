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

from typing import Any

import voluptuous as vol
from aiomarstek import (
    DOD_MAX,
    DOD_MIN,
    POWER_VERSIONS,
    MarstekModeConfig,
)

# Field names used in service call data.
ATTR_MODE = "mode"
ATTR_CONFIG = "config"
ATTR_VALUE = "value"
ATTR_POWER_VERSION = "power_version"

# Modes the set_mode service accepts. AI, Manual and Passive are deliberately
# absent: AI is configured in the Marstek app, Manual is not writable until its
# time-slot schedule is confirmed on a live device, and Passive is not offered
# at all. All three can still be *read* from the device (the mode sensor
# reports them), but not written.
DOCUMENTED_MODES = ["auto", "ups"]


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
    power version or discharge depth.
    """
    if isinstance(value, bool):
        raise vol.Invalid(f"expected an integer, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise vol.Invalid(f"expected an integer, got {value!r}")
    if not isinstance(value, str):
        raise vol.Invalid(f"expected an integer, got {value!r}")
    try:
        parsed = float(value)
    except ValueError as err:
        raise vol.Invalid(f"expected an integer, got {value!r}") from err
    if not parsed.is_integer():
        raise vol.Invalid(f"expected an integer, got {value!r}")
    return int(parsed)


# Per-mode nested schemas. Validating the inner structure here means a
# missing field or wrong type raises a clear voluptuous Invalid instead
# of a KeyError/TypeError from the builder.
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
# Standalone schemas (business fields only) used by the unit tests.
SET_MODE_SCHEMA = vol.Schema(SET_MODE_FIELDS)
SET_DOD_SCHEMA = vol.Schema(SET_DOD_FIELDS)
SET_POWER_VERSION_SCHEMA = vol.Schema(SET_POWER_VERSION_FIELDS)


def build_mode_config(data: dict[str, Any]) -> MarstekModeConfig:
    """Build a MarstekModeConfig from a validated service call payload.

    Auto and UPS are the only writable modes; UPS reads ``enable``
    (optionally wrapped in ``ups_cfg``). AI, Manual and Passive are rejected by
    ``SET_MODE_FIELDS`` before reaching this builder.
    """
    mode = str(data[ATTR_MODE]).casefold()
    config_data = data.get(ATTR_CONFIG) or {}

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
        ups_enabled=ups_enabled,
    )

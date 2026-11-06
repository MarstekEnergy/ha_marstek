"""Constants for the Marstek integration."""

from typing import Final

# Re-exported from the library: the DOD bounds feed the number entity so its
# limits cannot drift apart from the library's own DOD.SET validation. The
# port default is re-exported for external callers; the config flow and the
# coordinator import it from ``aiomarstek`` directly.
from aiomarstek import (
    DEFAULT_UDP_PORT as DEFAULT_UDP_PORT,
)
from aiomarstek import (
    DOD_MAX as DOD_MAX,
)
from aiomarstek import (
    DOD_MIN as DOD_MIN,
)

DOMAIN: Final = "marstek"

CONF_BLE_MAC: Final = "ble_mac"
CONF_DEVICE_TYPE: Final = "device_type"
CONF_PORT: Final = "port"
CONF_VERSION: Final = "version"
CONF_WIFI_MAC: Final = "wifi_mac"
CONF_WIFI_NAME: Final = "wifi_name"

# All device type strings the firmware may report for a supported model.
# The local API example returns "VenusC"; older firmware uses the VNS* codes.
SUPPORTED_DEVICE_TYPES: Final[frozenset[str]] = frozenset(
    {
        # Venus E / Venus E 3.0
        "VNSE3-0",
        "VenusE",
        "VenusE 3.0",
        "Venus E 3.0",
        "Venus E",
        # Venus A
        "VNSA-0",
        "VenusA",
        "Venus A",
        # Venus D
        "VNSD-0",
        "VenusD",
        "Venus D",
        # Venus C and Venus E mini are intentionally excluded: their feature
        # sets differ from the supported models (the C series power versions
        # are unconfirmed) and they will be added once verified on real
        # hardware.
    }
)

# Only Venus A and Venus D have DC PV inputs; other models never report PV.
PV_DEVICE_TYPES: Final[frozenset[str]] = frozenset(
    {"VNSA-0", "VNSD-0", "VenusA", "VenusD", "Venus A", "Venus D"}
)

PV_STATE_OPTIONS: Final = ("standby", "working")
DEVICE_MODE_OPTIONS: Final = ("auto", "ai", "manual", "passive", "ups")
# Modes offered in the operating-mode select. Manual is not offered: switching
# to it requires a time-slot schedule whose payload shape is not confirmed on
# real hardware yet, so manual schedules are configured in the Marstek app.
# AI is not offered either -- like Manual and Passive it is configured in the
# Marstek app. None of the three are written from Home Assistant at all, but
# all of them can still be read from the device (see DEVICE_MODE_OPTIONS).
SELECTABLE_MODE_OPTIONS: Final = ("auto", "ups")
BATTERY_STATUS_OPTIONS: Final = ("selling", "charging", "idle")
BLE_STATE_OPTIONS: Final = ("connect", "disconnect")

# DOD_MIN / DOD_MAX (30-88 %) are imported from aiomarstek above.

# Power versions exposed by the Set.Ver command, in watts.
POWER_VERSION_OPTIONS: Final = (800, 1200, 1500, 2200, 2500)

# Per-model power versions documented for Set.Ver:
#   1200 / 1500 W -> Venus A
#   2200 W        -> Venus D
#   2500 W        -> Venus E
# 800 W is the documented baseline for all models. Models not listed here
# expose the full option set and let the device reject unknown values.
POWER_VERSIONS_BY_TYPE: Final[dict[str, tuple[int, ...]]] = {
    "VNSA-0": (800, 1200, 1500),
    "VenusA": (800, 1200, 1500),
    "Venus A": (800, 1200, 1500),
    "VNSD-0": (800, 2200),
    "VenusD": (800, 2200),
    "Venus D": (800, 2200),
    "VNSE3-0": (800, 2500),
    "VenusE": (800, 2500),
    "VenusE 3.0": (800, 2500),
    "Venus E 3.0": (800, 2500),
    "Venus E": (800, 2500),
}


def power_versions_for(device_type: str) -> tuple[int, ...]:
    """Return the documented power versions for a device type.

    Unknown models get the full option set so users are not blocked from a
    documented value the table happens to omit; the device rejects values it
    does not support.
    """
    return POWER_VERSIONS_BY_TYPE.get(device_type, POWER_VERSION_OPTIONS)


# Minimum quiet time (seconds) a control is held before its write is sent.
# Rapid toggling of a control queues many writes; only the latest within
# this window is sent, so back-and-forth toggling fires one command, not
# every intermediate one.
WRITE_DEBOUNCE_DELAY: Final = 0.4

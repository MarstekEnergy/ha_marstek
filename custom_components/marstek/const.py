"""Constants for the Marstek integration."""

from typing import Final

from aiomarstek import DEFAULT_UDP_PORT  # noqa: F401  re-exported for config flow

DOMAIN: Final = "marstek"

CONF_BLE_MAC: Final = "ble_mac"
CONF_DEVICE_TYPE: Final = "device_type"
CONF_PORT: Final = "port"
CONF_VERSION: Final = "version"
CONF_WIFI_MAC: Final = "wifi_mac"
CONF_WIFI_NAME: Final = "wifi_name"

# All device type strings the firmware may report for a supported model.
# The Open API example returns "VenusC"; older firmware uses the VNS* codes.
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
        # sets differ (the mini has no PV inputs and fewer manual slots; the
        # C series power versions are unconfirmed) and they will be added once
        # verified on real hardware.
    }
)

# Only Venus A and Venus D have DC PV inputs; other models never report PV.
PV_DEVICE_TYPES: Final[frozenset[str]] = frozenset(
    {"VNSA-0", "VNSD-0", "VenusA", "VenusD", "Venus A", "Venus D"}
)

PV_STATE_OPTIONS: Final = ("standby", "working")
DEVICE_MODE_OPTIONS: Final = ("auto", "ai", "manual", "passive", "ups")
# Modes offered in the operating-mode select. Manual is included so the user
# can switch into it from HA; the time-slot schedule is then configured in the
# Marstek app (a notification reminds them). Passive still needs power/cd_time
# parameters and is set via the set_mode service.
SELECTABLE_MODE_OPTIONS: Final = ("auto", "ai", "manual", "ups")
BATTERY_STATUS_OPTIONS: Final = ("selling", "charging", "idle")
BLE_STATE_OPTIONS: Final = ("connect", "disconnect")

# Discharge depth bounds exposed by the DOD.SET command.
DOD_MIN: Final = 30
DOD_MAX: Final = 88

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

# Service names exposed by the integration.
SERVICE_SET_MODE: Final = "set_mode"
SERVICE_SET_DOD: Final = "set_dod"
SERVICE_SET_POWER_VERSION: Final = "set_power_version"
SERVICE_FACTORY_RESET: Final = "factory_reset"

ATTR_CONFIG: Final = "config"
ATTR_KEEP_ACCUMULATED_DATA: Final = "keep_accumulated_data"
ATTR_POWER_VERSION: Final = "power_version"

# --- Sidebar panel ---------------------------------------------------------
# The panel is a vanilla custom element (no build step, no external deps),
# registered via panel_custom. HA injects `hass` into the element; the panel
# reads marstek entities from hass.entities (filtered by platform) and matches
# them by translation_key so it survives entity renames / language changes.
PANEL_TITLE: Final = "Marstek"
PANEL_ICON: Final = "mdi:home-battery"
# URL path the panel is served at in the frontend (#/marstek-panel).
PANEL_URL_PATH: Final = "marstek-panel"
# Name of the custom element; must match customElements.define() in panel.js.
PANEL_COMPONENT_NAME: Final = "marstek-panel"
# Static URL under which panel.js / echarts are served. Every browser asset
# lives in the frontend/ subpackage, so the URL mirrors the real location and
# the component root stays readable (Python modules + brand/ + translations/).
PANEL_JS_URL: Final = "/marstek_static/frontend/panel.js"
ECHARTS_JS_URL: Final = "/marstek_static/frontend/echarts.min.js"
LITEPICKER_JS_URL: Final = "/marstek_static/frontend/litepicker.js"
LITEPICKER_CSS_URL: Final = "/marstek_static/frontend/litepicker.css"
# Device product images (one static directory; files resolved per device type).
MARSTEK_IMG_URL: Final = "/marstek_static/frontend/img"
# Panel wordmark: served from the brand asset shipped for HACS, so the
# repository holds a single copy. The URL mirrors the real location.
MARSTEK_LOGO_URL: Final = "/marstek_static/brand/logo@2x.png"

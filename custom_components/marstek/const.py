"""Constants for the Marstek integration."""

from typing import Final

DOMAIN: Final = "marstek"

CONF_BLE_MAC: Final = "ble_mac"
CONF_DEVICE_TYPE: Final = "device_type"
CONF_VERSION: Final = "version"
CONF_WIFI_MAC: Final = "wifi_mac"
CONF_WIFI_NAME: Final = "wifi_name"

SUPPORTED_DEVICE_TYPES: Final[frozenset[str]] = frozenset(
    {
        "VNSE3-0",
        "VNSD-0",
        "VNSA-0",
        "VenusA",
        "VenusD",
        "VenusE 3.0",
        "Venus A",
        "Venus D",
        "Venus E 3.0",
        "VNSEM-0",
    }
)

# Only Venus A and Venus D have DC PV inputs; other models never report PV data.
PV_DEVICE_TYPES: Final[frozenset[str]] = frozenset(
    {"VNSA-0", "VNSD-0", "VenusA", "VenusD", "Venus A", "Venus D"}
)

PV_STATE_OPTIONS: Final = ("standby", "working")
DEVICE_MODE_OPTIONS: Final = ("auto", "ai", "manual", "passive", "ups")
ERROR_NONE: Final = "none"
ERROR_NO_RESPONSE: Final = "no_response"
ERROR_NETWORK: Final = "network_error"
ERROR_INVALID_DATA: Final = "invalid_data"
ERROR_PARTIAL_RESPONSE: Final = "partial_response"
ERROR_STATE_OPTIONS: Final = (
    ERROR_NONE,
    ERROR_NO_RESPONSE,
    ERROR_NETWORK,
    ERROR_INVALID_DATA,
    ERROR_PARTIAL_RESPONSE,
)

BATTERY_STATUS_OPTIONS: Final = ("selling", "charging", "idle")

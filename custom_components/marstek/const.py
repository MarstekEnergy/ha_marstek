"""Constants for the Marstek integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform

DOMAIN: Final = "marstek"

PLATFORMS: Final[list[Platform]] = [
    Platform.SENSOR,
    Platform.SELECT,
    Platform.NUMBER,
]

# UDP Configuration
DEFAULT_UDP_PORT: Final = 30000  # Default UDP port for Marstek devices
DISCOVERY_TIMEOUT: Final = 10.0  # Wait 10s for each broadcast

# Integration options (config entry options)
CONF_OPTION_SCAN_INTERVAL: Final = "scan_interval"
CONF_OPTION_MEDIUM_SCAN_INTERVAL: Final = "medium_scan_interval"
CONF_OPTION_REQUEST_DELAY: Final = "request_delay"
CONF_OPTION_REQUEST_TIMEOUT: Final = "request_timeout"
CONF_OPTION_FAILURES_BEFORE_UNAVAILABLE: Final = "failures_before_unavailable"

# Defaults aligned with Marstek Open API community best practices
DEFAULT_SCAN_INTERVAL: Final = 30  # seconds – fast tier (ES.GetMode, PV.GetStatus)
DEFAULT_MEDIUM_SCAN_INTERVAL: Final = 300  # seconds – medium tier (ES.GetStatus)
DEFAULT_REQUEST_DELAY: Final = 4.0  # seconds between consecutive UDP requests (WiFi-safe)
DEFAULT_REQUEST_TIMEOUT: Final = 5.0  # seconds per request
DEFAULT_FAILURES_BEFORE_UNAVAILABLE: Final = 3

MIN_SCAN_INTERVAL: Final = 10
MAX_SCAN_INTERVAL: Final = 600
MIN_MEDIUM_SCAN_INTERVAL: Final = 60
MAX_MEDIUM_SCAN_INTERVAL: Final = 3600
MIN_REQUEST_DELAY: Final = 2.0
MAX_REQUEST_DELAY: Final = 10.0
MIN_REQUEST_TIMEOUT: Final = 3.0
MAX_REQUEST_TIMEOUT: Final = 30.0
MIN_FAILURES_BEFORE_UNAVAILABLE: Final = 1
MAX_FAILURES_BEFORE_UNAVAILABLE: Final = 20


@dataclass(frozen=True, slots=True)
class MarstekOptions:
    """Resolved integration options with defaults applied."""

    scan_interval: int
    medium_scan_interval: int
    request_delay: float
    request_timeout: float
    failures_before_unavailable: int


def get_entry_options(config_entry: ConfigEntry) -> MarstekOptions:
    """Return config entry options with defaults."""
    options = config_entry.options
    return MarstekOptions(
        scan_interval=int(
            options.get(CONF_OPTION_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        ),
        medium_scan_interval=int(
            options.get(
                CONF_OPTION_MEDIUM_SCAN_INTERVAL, DEFAULT_MEDIUM_SCAN_INTERVAL
            )
        ),
        request_delay=float(
            options.get(CONF_OPTION_REQUEST_DELAY, DEFAULT_REQUEST_DELAY)
        ),
        request_timeout=float(
            options.get(CONF_OPTION_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT)
        ),
        failures_before_unavailable=int(
            options.get(
                CONF_OPTION_FAILURES_BEFORE_UNAVAILABLE,
                DEFAULT_FAILURES_BEFORE_UNAVAILABLE,
            )
        ),
    )

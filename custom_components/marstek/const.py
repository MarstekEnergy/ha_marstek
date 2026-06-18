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

# Data categories for per-sensor freshness checks
DATA_CATEGORY_ES: Final = "es"
DATA_CATEGORY_PV: Final = "pv"
DATA_CATEGORY_ENERGY: Final = "energy"
DATA_CATEGORY_STATIC: Final = "static"

# Integration options (config entry options)
CONF_OPTION_SCAN_INTERVAL: Final = "scan_interval"
CONF_OPTION_MEDIUM_SCAN_INTERVAL: Final = "medium_scan_interval"
CONF_OPTION_REQUEST_DELAY: Final = "request_delay"
CONF_OPTION_REQUEST_TIMEOUT: Final = "request_timeout"
CONF_OPTION_STALENESS_THRESHOLD: Final = "staleness_threshold"
CONF_OPTION_UNAVAILABLE_AFTER: Final = "unavailable_after_seconds"

# Defaults aligned with Marstek Open API community best practices
DEFAULT_SCAN_INTERVAL: Final = 60  # seconds – fast tier (ES.GetMode, PV.GetStatus)
DEFAULT_MEDIUM_SCAN_INTERVAL: Final = 300  # seconds – medium tier (ES.GetStatus)
DEFAULT_REQUEST_DELAY: Final = 4.0  # seconds between consecutive UDP requests (WiFi-safe)
DEFAULT_REQUEST_TIMEOUT: Final = 5.0  # seconds per request
DEFAULT_STALENESS_THRESHOLD: Final = 3  # missed category updates before value → unknown
DEFAULT_UNAVAILABLE_AFTER: Final = 600  # seconds without any success before unavailable

MIN_SCAN_INTERVAL: Final = 10
MAX_SCAN_INTERVAL: Final = 600
MIN_MEDIUM_SCAN_INTERVAL: Final = 60
MAX_MEDIUM_SCAN_INTERVAL: Final = 3600
MIN_REQUEST_DELAY: Final = 2.0
MAX_REQUEST_DELAY: Final = 10.0
MIN_REQUEST_TIMEOUT: Final = 3.0
MAX_REQUEST_TIMEOUT: Final = 30.0
MIN_STALENESS_THRESHOLD: Final = 1
MAX_STALENESS_THRESHOLD: Final = 10
MIN_UNAVAILABLE_AFTER: Final = 120
MAX_UNAVAILABLE_AFTER: Final = 3600


@dataclass(frozen=True, slots=True)
class MarstekOptions:
    """Resolved integration options with defaults applied."""

    scan_interval: int
    medium_scan_interval: int
    request_delay: float
    request_timeout: float
    staleness_threshold: int
    unavailable_after_seconds: int


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
        staleness_threshold=int(
            options.get(CONF_OPTION_STALENESS_THRESHOLD, DEFAULT_STALENESS_THRESHOLD)
        ),
        unavailable_after_seconds=int(
            options.get(CONF_OPTION_UNAVAILABLE_AFTER, DEFAULT_UNAVAILABLE_AFTER)
        ),
    )

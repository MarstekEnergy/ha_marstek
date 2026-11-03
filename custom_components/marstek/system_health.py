"""System-health summary for the Marstek integration.

Shown in Settings -> System -> Repairs (system health card). A compact
at-a-glance counterpart to the per-device diagnostics dump: how many
Marstek devices are connected, how many are mid-silent-poll tolerance
(the device skipped a poll but has not yet crossed the availability
threshold), and how many are unreachable. Reads the same coordinator
attributes (``last_update_success``, ``silent_polls``) the diagnostics
module reads, so the two views never diverge.

Home Assistant auto-discovers a loaded integration's ``system_health``
module, so no registration call is needed in ``__init__.py`` (matching
the omnibattery reference integration).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components import system_health
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN


@callback
def async_register(
    hass: HomeAssistant, register: system_health.SystemHealthRegistration
) -> None:
    """Register the system-health info callback."""
    register.async_register_info(_async_system_health_info)


async def _async_system_health_info(hass: HomeAssistant) -> dict[str, Any]:
    """Return the summary counters shown on the system-health card.

    A coordinator counts as ``connected`` when its last poll succeeded.
    Within the connected set, ``silent_polling`` is the subset that skipped
    one or more recent polls without yet crossing ``MAX_SILENT_POLLS`` (the
    coordinator keeps the last reading and stays available, but the device
    is intermittently not answering). ``unreachable`` is the subset whose
    last poll failed (entities marked unavailable). Entries that have not
    finished setup (``runtime_data`` is None) are counted as unreachable so
    a half-loaded integration does not silently drop devices from the total.
    """
    coordinators = [
        coord
        for entry in hass.config_entries.async_entries(DOMAIN)
        if (coord := entry.runtime_data) is not None
    ]

    total = len(coordinators)
    connected = 0
    silent = 0
    unreachable = 0
    for coord in coordinators:
        if not coord.last_update_success:
            unreachable += 1
            continue
        connected += 1
        # silent_polls is reset to 0 on the next poll that returns data, so a
        # non-zero value here means the device is currently in the
        # skip-but-tolerate window (MarstekNoResponse returned, previous data
        # kept, UpdateFailed not yet raised).
        if coord.silent_polls > 0:
            silent += 1

    # Also count entries that never produced a coordinator (setup failed or
    # still pending) so the total reflects every configured Marstek device.
    configured = len(hass.config_entries.async_entries(DOMAIN))
    unreachable += configured - total

    return {
        "devices_connected": f"{connected}/{configured}",
        "silent_polling": silent,
        "unreachable": unreachable,
    }

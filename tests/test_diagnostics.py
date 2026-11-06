"""Behaviour tests for the diagnostics download."""

from __future__ import annotations

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN
from custom_components.marstek.diagnostics import async_get_config_entry_diagnostics


async def test_diagnostics_work_before_the_coordinator_exists(
    hass: HomeAssistant,
) -> None:
    """An entry stuck in setup retry has no runtime_data yet.

    ``ConfigEntry.runtime_data`` is only an annotation until
    ``async_setup_entry`` stores a coordinator, so reading it directly would
    raise AttributeError and fail the download exactly when it is most useful
    (device offline, setup failing). The handler guards against that, so the
    download still succeeds with ``None`` placeholders.
    """
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "192.168.1.50"})
    entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["entry"]["domain"] == DOMAIN
    assert result["device_info"] is None
    assert result["status"] is None
    assert result["polling"] is None
    assert "versions" in result

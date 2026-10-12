"""Tests for the Marstek diagnostics."""

import json

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from .conftest import ES_MODE, FakeClient, setup_entry


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    """Diagnostics include status and raw replies, without identifying data."""
    assert await async_setup_component(hass, "diagnostics", {})
    await setup_entry(hass, config_entry)

    result = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)

    assert result["device_info"]["device_type"] == "VNSEM-0"
    assert result["poll"]["error_state"] == "none"
    assert result["poll"]["interval_seconds"] == 60
    assert result["status"]["grid_power"] == 2152
    assert result["raw_results"]["ES.GetMode"] == ES_MODE
    dumped = json.dumps(result)
    for secret in ("ccc837b3e8d2", "ccc837b39e61", "home-wifi"):
        assert secret not in dumped

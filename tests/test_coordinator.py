"""Tests for the Marstek data update coordinator."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.marstek.coordinator import (
    UNAVAILABLE_POLLS,
    UNREACHABLE_POLLS,
    ZERO_SOC_POLLS,
)

from .conftest import FakeClient, setup_entry


async def _set_soc(client: FakeClient, soc: int) -> None:
    """Report the battery level from both status replies."""
    client.es_status["bat_soc"] = soc
    client.es_mode["bat_soc"] = soc


async def test_missed_polls_keep_data_then_fail(
    hass: HomeAssistant, client: FakeClient, config_entry: MockConfigEntry
) -> None:
    """A missed poll keeps the last data, the next one fails the update."""
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data
    assert coordinator.data is not None
    soc_before = coordinator.data.battery_soc

    client.online = False
    await coordinator.async_refresh()
    # The library silently returns the previous data when nothing answers;
    # the coordinator notices via the response timestamp.
    assert coordinator.last_update_success is True
    assert coordinator.failed_polls == 1
    assert coordinator.error_state == "no_response"
    assert coordinator.data.battery_soc == soc_before
    assert coordinator.last_response is not None
    answered_at = coordinator.last_response

    await coordinator.async_refresh()
    assert coordinator.last_update_success is False
    assert coordinator.failed_polls == 2
    assert coordinator.data.battery_soc == soc_before
    assert coordinator.last_response == answered_at

    # Enough missed polls in a row raise a repair issue.
    for _ in range(UNREACHABLE_POLLS - UNAVAILABLE_POLLS):
        await coordinator.async_refresh()
    issue_registry = ir.async_get(hass)
    assert (
        issue_registry.async_get_issue(
            "marstek",
            f"open_api_unreachable_{config_entry.entry_id}",
        )
        is not None
    )

    # Answering again clears the counters and the issue.
    client.online = True
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert coordinator.failed_polls == 0
    assert coordinator.error_state == "none"
    assert (
        issue_registry.async_get_issue(
            "marstek",
            f"open_api_unreachable_{config_entry.entry_id}",
        )
        is None
    )


async def test_soc_glitch_is_held_then_escapes(
    hass: HomeAssistant, client: FakeClient, config_entry: MockConfigEntry
) -> None:
    """A 0 % reading after a high level is held, but not forever."""
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data

    await _set_soc(client, 50)
    await coordinator.async_refresh()
    assert coordinator.data.battery_soc == 50

    # The device reports 0 % for a few polls: a glitch, hold the last level.
    await _set_soc(client, 0)
    for _ in range(ZERO_SOC_POLLS - 1):
        await coordinator.async_refresh()
        assert coordinator.data.battery_soc == 50

    # Still 0 % after that many polls: the battery really is empty.
    await coordinator.async_refresh()
    assert coordinator.data.battery_soc == 0
    assert coordinator.zero_soc_polls >= ZERO_SOC_POLLS

    # A recovering level resets the escape counter and is taken as is.
    await _set_soc(client, 42)
    await coordinator.async_refresh()
    assert coordinator.data.battery_soc == 42
    assert coordinator.zero_soc_polls == 0


async def test_silent_retry_refreshes_after_outage(
    hass: HomeAssistant, client: FakeClient, config_entry: MockConfigEntry
) -> None:
    """Values changed during an outage show up once the device answers."""
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data

    client.online = False
    await coordinator.async_refresh()
    assert coordinator.failed_polls == 1
    soc_during_outage = coordinator.data.battery_soc

    client.online = True
    await _set_soc(client, 42)
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert coordinator.failed_polls == 0
    assert coordinator.data.battery_soc == 42
    assert coordinator.data.battery_soc != soc_during_outage
    assert coordinator.last_response is not None

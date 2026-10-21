"""Tests for the Marstek data update coordinator."""

import logging

from aiomarstek import ZERO_SOC_POLLS, MarstekDeviceInfo
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.marstek.coordinator import UNREACHABLE_POLLS

from .conftest import DEVICE_RESPONSE, HOST, FakeClient, setup_entry


async def _set_soc(client: FakeClient, soc: int) -> None:
    """Report the battery level from both status replies."""
    client.es_status["bat_soc"] = soc
    client.es_mode["bat_soc"] = soc


async def test_missed_poll_fails_immediately(
    hass: HomeAssistant, client: FakeClient, config_entry: MockConfigEntry
) -> None:
    """A missed poll fails the update at once; diagnostics stay available."""
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data
    assert coordinator.data is not None
    soc_before = coordinator.data.battery_soc
    assert coordinator.last_response is not None
    answered_at = coordinator.last_response

    client.online = False
    await coordinator.async_refresh()
    # The update fails on the first missed poll; the last data is kept.
    assert coordinator.last_update_success is False
    assert coordinator.failed_polls == 1
    assert coordinator.error_state == "no_response"
    assert coordinator.data.battery_soc == soc_before
    # Diagnostics keep showing when the device last answered.
    assert coordinator.last_response == answered_at

    # Enough missed polls in a row raise a repair issue.
    for _ in range(UNREACHABLE_POLLS - 1):
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
    await hass.async_block_till_done()
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


async def test_firmware_refresh_failure_is_logged(
    hass: HomeAssistant,
    client: FakeClient,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed firmware re-read after an outage is logged, not raised."""
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data

    # Trigger a recovery so the firmware re-read is scheduled.
    client.online = False
    await coordinator.async_refresh()
    client.online = True

    async def _fail(device_ip: str) -> MarstekDeviceInfo:
        raise OSError("boom")

    client.get_device_info = _fail
    with caplog.at_level(logging.DEBUG, logger="custom_components.marstek"):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert "firmware re-read failed" in caplog.text
    # The status update itself is not blocked by the failed firmware read.
    assert coordinator.last_update_success is True
    assert coordinator.failed_polls == 0


async def test_invalid_data_does_not_raise_unreachable_issue(
    hass: HomeAssistant, client: FakeClient, config_entry: MockConfigEntry
) -> None:
    """Replies that fail to parse fail the update without a repair issue."""
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data

    client.es_status["bat_soc"] = "broken"
    for _ in range(UNREACHABLE_POLLS):
        await coordinator.async_refresh()

    assert coordinator.error_state == "invalid_data"
    assert coordinator.failed_polls == 0
    assert (
        ir.async_get(hass).async_get_issue(
            "marstek", f"open_api_unreachable_{config_entry.entry_id}"
        )
        is None
    )

    client.es_status["bat_soc"] = 42
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert coordinator.error_state == "none"


async def test_invalid_data_resets_unreachable_streak(
    hass: HomeAssistant, client: FakeClient, config_entry: MockConfigEntry
) -> None:
    """Invalid data breaks the streak, so a missed poll starts from one."""
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data
    issue_id = f"open_api_unreachable_{config_entry.entry_id}"

    client.online = False
    for _ in range(UNREACHABLE_POLLS - 1):
        await coordinator.async_refresh()
    assert coordinator.failed_polls == UNREACHABLE_POLLS - 1
    assert ir.async_get(hass).async_get_issue("marstek", issue_id) is None

    # The device answers with data that fails to parse.
    client.online = True
    client.es_status["bat_soc"] = "broken"
    await coordinator.async_refresh()
    assert coordinator.failed_polls == 0
    assert coordinator.error_state == "invalid_data"

    # One more missed poll must not complete the old streak.
    client.online = False
    await coordinator.async_refresh()
    assert coordinator.failed_polls == 1
    assert ir.async_get(hass).async_get_issue("marstek", issue_id) is None


async def test_invalid_data_clears_the_unreachable_issue(
    hass: HomeAssistant, client: FakeClient, config_entry: MockConfigEntry
) -> None:
    """A device that answers with bad data is reachable, so the issue clears."""
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data
    issue_id = f"open_api_unreachable_{config_entry.entry_id}"

    client.online = False
    for _ in range(UNREACHABLE_POLLS):
        await coordinator.async_refresh()
    assert ir.async_get(hass).async_get_issue("marstek", issue_id) is not None

    client.online = True
    client.es_status["bat_soc"] = "broken"
    await coordinator.async_refresh()
    assert coordinator.failed_polls == 0
    assert ir.async_get(hass).async_get_issue("marstek", issue_id) is None


async def test_partial_response_keeps_stale_values(
    hass: HomeAssistant, client: FakeClient, config_entry: MockConfigEntry
) -> None:
    """A device answering only ES.GetMode keeps the last ES.GetStatus values.

    The library counts a poll as answered when any single status request
    returns data, so a wedged status handler leaves the status fields at
    their previous values while the update itself succeeds.
    """
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data

    client.es_status_online = False
    client.es_status["total_grid_output_energy"] = 999
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.error_state == "partial_response"
    assert coordinator.data.total_grid_output_energy == 695

    client.es_status_online = True
    await coordinator.async_refresh()
    assert coordinator.data.total_grid_output_energy == 999
    assert coordinator.error_state == "none"


async def test_pv_partial_failure_shows_partial(
    hass: HomeAssistant, client: FakeClient
) -> None:
    """A PV-capable device silent on PV.GetStatus shows a partial response."""
    client.device = "VenusA"
    entry = MockConfigEntry(
        domain="marstek",
        data={CONF_HOST: HOST},
        unique_id=MarstekDeviceInfo.from_response(
            {**DEVICE_RESPONSE, "device": "VenusA"}
        ).stable_id,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data

    # First poll answers PV, so the device is known to support it.
    assert coordinator.error_state == "none"

    client.pv_online = False
    await coordinator.async_refresh()
    assert coordinator.error_state == "partial_response"

    # A device that refuses PV.GetStatus does not count it as partial.
    client.pv_supported = False
    await coordinator.async_refresh()
    assert coordinator.error_state == "none"


async def test_first_counter_drop_after_setup_does_not_raise(
    hass: HomeAssistant, client: FakeClient, config_entry: MockConfigEntry
) -> None:
    """A counter dropping on the first update after setup is held, not raised."""
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data

    # The first update after setup sees the counter fall; the drop is held.
    client.es_status["total_grid_output_energy"] = 0
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.total_grid_output_energy == 695

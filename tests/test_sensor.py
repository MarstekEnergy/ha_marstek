"""Tests for the Marstek sensors."""

from datetime import datetime, timedelta

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr, issue_registry as ir

from custom_components.marstek.const import DOMAIN

from .conftest import FakeClient, setup_entry


async def poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Advance time by one 60 second poll interval."""
    freezer.tick(timedelta(seconds=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_sensors(
    hass: HomeAssistant, client: FakeClient, config_entry: MockConfigEntry
) -> None:
    """A Venus E Mini gets battery, grid and diagnostic sensors but no PV."""
    ids = await setup_entry(hass, config_entry)

    def state(key: str) -> str:
        return hass.states.get(ids[key]).state

    assert not any(key.startswith("pv") for key in ids)
    assert "total_pv_energy" not in ids
    assert state("battery_soc") == "9"
    assert state("total_grid_output_energy") == "6950"
    assert state("stored_energy") == "180.0"
    assert state("battery_cycles") == "3.48"
    assert state("grid_power") == "2152"
    assert state("grid_phase_a_power") == "2068"
    assert state("grid_import_energy") == "0.0"
    assert state("error_state") == "none"
    assert datetime.fromisoformat(state("last_response")).tzinfo is not None


async def test_grid_energy(
    hass: HomeAssistant,
    client: FakeClient,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Grid power is integrated into import and export energy each poll."""
    ids = await setup_entry(hass, config_entry)

    client.es_mode["total_power"] = -1200
    await poll(hass, freezer)
    assert round(float(hass.states.get(ids["grid_import_energy"]).state), 2) == 35.87
    await poll(hass, freezer)
    assert hass.states.get(ids["grid_export_energy"]).state == "20.0"


async def test_grid_energy_restored(
    hass: HomeAssistant, client: FakeClient, config_entry: MockConfigEntry
) -> None:
    """Grid energy totals survive a restart."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State("sensor.marstek_vnsem_0_v301_grid_import_energy", "1234.5"),
                {"native_value": 1234.5, "native_unit_of_measurement": "Wh"},
            )
        ],
    )
    ids = await setup_entry(hass, config_entry)
    assert hass.states.get(ids["grid_import_energy"]).state == "1234.5"


async def test_missed_polls(
    hass: HomeAssistant,
    client: FakeClient,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Values survive one missed poll, then become unavailable with a repair issue."""
    ids = await setup_entry(hass, config_entry)
    answered = hass.states.get(ids["last_response"]).state
    import_energy = hass.states.get(ids["grid_import_energy"]).state

    client.online = False
    await poll(hass, freezer)
    assert hass.states.get(ids["battery_soc"]).state == "9"
    assert hass.states.get(ids["error_state"]).state == "no_response"
    assert hass.states.get(ids["grid_import_energy"]).state == import_energy

    await poll(hass, freezer)
    assert hass.states.get(ids["battery_soc"]).state == STATE_UNAVAILABLE
    assert hass.states.get(ids["error_state"]).state == "no_response"
    assert hass.states.get(ids["last_response"]).state == answered

    issue_id = f"open_api_unreachable_{config_entry.entry_id}"
    for _ in range(8):
        await poll(hass, freezer)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id)

    client.online = True
    client.version = 302
    await poll(hass, freezer)
    assert hass.states.get(ids["battery_soc"]).state == "9"
    assert hass.states.get(ids["error_state"]).state == "none"
    assert hass.states.get(ids["last_response"]).state > answered
    assert not ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    # The power during the outage is unknown, so no energy was counted for it.
    assert hass.states.get(ids["grid_import_energy"]).state == import_energy

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, config_entry.unique_id), config_entry.entry_id
    )
    assert device.sw_version == "302"


async def test_glitches_held(
    hass: HomeAssistant,
    client: FakeClient,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Implausible readings keep the previous value."""
    ids = await setup_entry(hass, config_entry)
    client.es_status.update(bat_soc=50, total_grid_output_energy=700)
    client.es_mode["bat_soc"] = 50
    await poll(hass, freezer)
    assert hass.states.get(ids["battery_soc"]).state == "50"

    client.es_status.update(bat_soc=0, bat_cap=0, total_grid_output_energy=0)
    client.es_mode["bat_soc"] = 0
    await poll(hass, freezer)
    assert hass.states.get(ids["battery_soc"]).state == "50"
    assert hass.states.get(ids["total_grid_output_energy"]).state == "7000"
    assert hass.states.get(ids["stored_energy"]).state == "1000.0"

    client.es_status.update(total_grid_output_energy=705)
    await poll(hass, freezer)
    assert hass.states.get(ids["total_grid_output_energy"]).state == "7050"


async def test_setup_retries_when_device_silent(
    hass: HomeAssistant, client: FakeClient, config_entry: MockConfigEntry
) -> None:
    """Setup is retried later when the device does not answer at all."""
    client.online = False
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state.recoverable

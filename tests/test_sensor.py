"""Tests for the Marstek sensors."""

from datetime import datetime, timedelta

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.const import CONF_HOST, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, issue_registry as ir

from aiomarstek import MarstekDeviceInfo

from custom_components.marstek.const import DOMAIN

from .conftest import DEVICE_RESPONSE, FakeClient, HOST, setup_entry


async def poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Advance time by one 30 second poll interval."""
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_sensors(
    hass: HomeAssistant, client: FakeClient, config_entry: MockConfigEntry
) -> None:
    """A Venus E Mini gets battery and diagnostic sensors but no PV."""
    ids = await setup_entry(hass, config_entry)

    def state(key: str) -> str:
        return hass.states.get(ids[key]).state

    assert not any(key.startswith("pv") for key in ids)
    assert "total_pv_energy" not in ids
    assert state("battery_soc") == "9"
    assert state("total_grid_output_energy") == "695"
    assert state("error_state") == "none"
    assert datetime.fromisoformat(state("last_response")).tzinfo is not None
    assert client.include_pv_requests[-1] is False


async def test_pv_device_sensors(
    hass: HomeAssistant, client: FakeClient
) -> None:
    """A Venus A gets PV sensors and asks the device for PV data."""
    client.device = "VenusA"
    entry = MockConfigEntry(
        domain="marstek",
        data={CONF_HOST: HOST},
        unique_id=MarstekDeviceInfo.from_response(
            {**DEVICE_RESPONSE, "device": "VenusA"}
        ).stable_id,
    )
    entry.add_to_hass(hass)
    ids = await setup_entry(hass, entry)

    assert "total_pv_energy" in ids
    assert "pv1_power" in ids
    assert "pv4_state" in ids
    assert client.include_pv_requests[-1] is True


async def test_missed_polls(
    hass: HomeAssistant,
    client: FakeClient,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Entities become unavailable on the first missed poll; diagnostics keep showing why."""
    ids = await setup_entry(hass, config_entry)
    answered = hass.states.get(ids["last_response"]).state

    client.online = False
    await poll(hass, freezer)
    # First missed poll: entities become unavailable, diagnostics show why.
    assert hass.states.get(ids["battery_soc"]).state == STATE_UNAVAILABLE
    assert hass.states.get(ids["error_state"]).state == "no_response"
    assert hass.states.get(ids["last_response"]).state == answered

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

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, config_entry.unique_id), config_entry.entry_id
    )
    assert device.sw_version == "302"


async def test_soc_glitch_is_held(
    hass: HomeAssistant,
    client: FakeClient,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A 0 % battery spike and a single counter dip are held, then accepted."""
    ids = await setup_entry(hass, config_entry)
    client.es_status.update(bat_soc=50, total_grid_output_energy=700)
    client.es_mode["bat_soc"] = 50
    await poll(hass, freezer)
    assert hass.states.get(ids["battery_soc"]).state == "50"
    assert hass.states.get(ids["total_grid_output_energy"]).state == "700"

    client.es_status.update(bat_soc=0, total_grid_output_energy=0)
    client.es_mode["bat_soc"] = 0
    await poll(hass, freezer)
    # The battery level spike to 0 % and the single counter dip are held.
    assert hass.states.get(ids["battery_soc"]).state == "50"
    assert hass.states.get(ids["total_grid_output_energy"]).state == "700"

    # A counter that keeps dropping is accepted as a real reset.
    await poll(hass, freezer)
    assert hass.states.get(ids["total_grid_output_energy"]).state == "0"

    client.es_status.update(total_grid_output_energy=705)
    await poll(hass, freezer)
    assert hass.states.get(ids["total_grid_output_energy"]).state == "705"


async def test_setup_retries_when_device_silent(
    hass: HomeAssistant, client: FakeClient, config_entry: MockConfigEntry
) -> None:
    """Setup is retried later when the device does not answer at all."""
    client.online = False
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state.recoverable

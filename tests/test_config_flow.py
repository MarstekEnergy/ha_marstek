"""Tests for the Marstek config flow."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_DEVICE, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.marstek.const import CONF_BLE_MAC, CONF_WIFI_MAC, DOMAIN

from .conftest import DEVICE_RESPONSE, FakeClient, HOST, setup_entry


async def _add_through_discovery(hass: HomeAssistant) -> ConfigFlowResult:
    """Add the first discovered device and return the final flow result."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "discover"}
    )
    # The discovery task auto-advances the flow once it finishes.
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    while result["type"] == FlowResultType.SHOW_PROGRESS_DONE:
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] == FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: "0"}
    )
    await hass.async_block_till_done()
    return result


def _make_legacy_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Turn the fixture entry into one from before the identifier switch."""
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_BLE_MAC: DEVICE_RESPONSE["ble_mac"],
            CONF_WIFI_MAC: DEVICE_RESPONSE["wifi_mac"],
        },
        unique_id=DEVICE_RESPONSE["wifi_mac"],
    )


async def test_legacy_wifi_mac_entry_is_migrated(
    hass: HomeAssistant,
    client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    """Re-adding a device re-attaches an entry that still uses the old id."""
    await setup_entry(hass, config_entry)
    stable_id = config_entry.unique_id
    _make_legacy_entry(hass, config_entry)

    result = await _add_through_discovery(hass)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert config_entry.unique_id == stable_id
    assert (
        hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, stable_id)
        is config_entry
    )


async def test_wifi_module_replacement_migrates_by_ble(
    hass: HomeAssistant,
    client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    """A replaced Wi-Fi module still migrates, matched by the BLE MAC."""
    await setup_entry(hass, config_entry)
    stable_id = config_entry.unique_id
    _make_legacy_entry(hass, config_entry)
    # The same device, now answering with a new Wi-Fi MAC.
    client.response["wifi_mac"] = "aa0000000001"

    result = await _add_through_discovery(hass)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert config_entry.unique_id == stable_id


async def test_reused_ip_does_not_migrate_another_device(
    hass: HomeAssistant,
    client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    """A different device taking over the IP is added, not migrated onto."""
    await setup_entry(hass, config_entry)
    _make_legacy_entry(hass, config_entry)
    # Another device now answers at the same address.
    client.response.update(wifi_mac="aa0000000001", ble_mac="aa0000000002")

    result = await _add_through_discovery(hass)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    # The legacy entry is untouched; both devices have their own entry.
    assert config_entry.unique_id == DEVICE_RESPONSE["wifi_mac"]
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2


async def test_leftover_entry_is_not_migrated_onto_a_used_id(
    hass: HomeAssistant,
    client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    """A leftover old entry is left alone when the new id is already in use."""
    await setup_entry(hass, config_entry)
    leftover = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: HOST,
            CONF_BLE_MAC: DEVICE_RESPONSE["ble_mac"],
            CONF_WIFI_MAC: DEVICE_RESPONSE["wifi_mac"],
        },
        unique_id=DEVICE_RESPONSE["wifi_mac"],
    )
    leftover.add_to_hass(hass)

    result = await _add_through_discovery(hass)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert leftover.unique_id == DEVICE_RESPONSE["wifi_mac"]
    assert (
        hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN, config_entry.unique_id
        )
        is config_entry
    )


async def test_entry_without_ble_mac_is_not_migrated(
    hass: HomeAssistant,
    client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    """An entry without a recorded BLE MAC cannot be matched, so is left."""
    await setup_entry(hass, config_entry)
    hass.config_entries.async_update_entry(
        config_entry, unique_id=DEVICE_RESPONSE["wifi_mac"]
    )

    result = await _add_through_discovery(hass)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert config_entry.unique_id == DEVICE_RESPONSE["wifi_mac"]
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2


async def test_setup_migrates_legacy_identifier_and_keeps_entities(
    hass: HomeAssistant,
    client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    """Setting up an old entry migrates it and keeps its entity registry row.

    The entry is re-attached before the platforms are set up, so the entity
    keeps its entity id and with it statistics and automations.
    """
    stable_id = config_entry.unique_id
    hass.config_entries.async_update_entry(
        config_entry,
        data={**config_entry.data, CONF_BLE_MAC: DEVICE_RESPONSE["ble_mac"]},
        unique_id=DEVICE_RESPONSE["wifi_mac"],
    )
    # An entity from before the identifier switch.
    registry = er.async_get(hass)
    legacy = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DEVICE_RESPONSE['wifi_mac']}_battery_soc",
        config_entry=config_entry,
    )

    await setup_entry(hass, config_entry)

    assert config_entry.unique_id == stable_id
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{stable_id}_battery_soc")
        == legacy.entity_id
    )

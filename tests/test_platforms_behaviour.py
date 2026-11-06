"""Behaviour tests for the platforms: what they create and how they write.

These replace the previous source-text assertions ("does this module contain
``coordinator.async_write``?"), which broke on every refactor and never proved
anything about runtime behaviour. Everything here drives the real platform
setup functions and real entities against a coordinator.

The translation-coverage test is the refactor-proof version of the old AST scan:
it collects ``translation_key`` from the entities the platforms actually create
and compares them with ``strings.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiomarstek import MarstekDeviceInfo, MarstekNoResponse
from aiomarstek.udp_client import MarstekSetError
from homeassistant.components.number import NumberMode
from homeassistant.const import CONF_HOST, CONF_PORT, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek import PLATFORMS, async_unload_entry
from custom_components.marstek import binary_sensor as binary_sensor_platform
from custom_components.marstek import number as number_platform
from custom_components.marstek import select as select_platform
from custom_components.marstek import sensor as sensor_platform
from custom_components.marstek import switch as switch_platform
from custom_components.marstek.binary_sensor import MarstekConnectedBinarySensor
from custom_components.marstek.const import (
    DOD_MAX,
    DOD_MIN,
    DOMAIN,
    POWER_VERSION_OPTIONS,
    SELECTABLE_MODE_OPTIONS,
)
from custom_components.marstek.coordinator import MarstekDataUpdateCoordinator
from custom_components.marstek.number import MarstekDODNumber
from custom_components.marstek.select import (
    MarstekModeSelect,
    MarstekPowerVersionSelect,
)
from custom_components.marstek.switch import MarstekBluetoothSwitch, MarstekLedSwitch

COMPONENT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "marstek"
DEVICE_IP = "192.168.1.50"
DEVICE_PORT = 30000


def _device_info(device_type: str = "Venus E") -> MarstekDeviceInfo:
    return MarstekDeviceInfo(
        id=1,
        device_type=device_type,
        version="1.2.3",
        wifi_name="HomeWiFi",
        ip=DEVICE_IP,
        wifi_mac="11:22:33:44:55:66",
        ble_mac="66:55:44:33:22:11",
        mac="AA:BB:CC:DD:EE:FF",
    )


@pytest.fixture
async def coordinator(hass: HomeAssistant):
    """A real coordinator with a fake UDP client; torn down after the test."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="stable-1",
        data={CONF_HOST: DEVICE_IP, CONF_PORT: DEVICE_PORT},
    )
    coordinator = MarstekDataUpdateCoordinator(hass, entry, MagicMock())
    coordinator.device_info = _device_info()
    coordinator.data = SimpleNamespace(
        device_mode="auto",
        get_value=lambda key: None,
    )
    yield coordinator
    await coordinator.async_shutdown()


async def _setup_platform(module, coordinator) -> list:
    """Run a platform's setup and return the entities it created."""
    added: list = []
    entry = SimpleNamespace(runtime_data=coordinator)
    await module.async_setup_entry(MagicMock(), entry, added.extend)
    return added


def _class_names(entities: list) -> list[str]:
    return [type(entity).__name__ for entity in entities]


async def test_platforms_create_the_documented_entities(coordinator) -> None:
    """Each platform creates exactly the controls the README promises."""
    assert _class_names(await _setup_platform(select_platform, coordinator)) == [
        "MarstekModeSelect",
        "MarstekPowerVersionSelect",
    ]
    assert _class_names(await _setup_platform(number_platform, coordinator)) == [
        "MarstekDODNumber"
    ]
    # The UPS switch is deliberately not created: UPS is a mode, not a switch.
    assert _class_names(await _setup_platform(switch_platform, coordinator)) == [
        "MarstekBluetoothSwitch",
        "MarstekLedSwitch",
    ]
    assert _class_names(await _setup_platform(binary_sensor_platform, coordinator)) == [
        "MarstekChargePermissionBinarySensor",
        "MarstekDischargePermissionBinarySensor",
        "MarstekConnectedBinarySensor",
    ]


async def test_pv_sensors_only_exist_for_models_with_pv_inputs(
    coordinator, hass: HomeAssistant
) -> None:
    """Venus A gets per-input PV sensors; Venus E does not."""
    coordinator.device_info = _device_info("Venus E")
    no_pv = await _setup_platform(sensor_platform, coordinator)

    coordinator.device_info = _device_info("Venus A")
    with_pv = await _setup_platform(sensor_platform, coordinator)

    per_input = {"pv_power", "pv_voltage", "pv_current", "pv_state"}
    assert not any(e.translation_key in per_input for e in no_pv)
    assert sum(e.translation_key in per_input for e in with_pv) == 16


async def test_every_created_entity_has_translations(coordinator) -> None:
    """Every entity the platforms create has a strings.json entry."""
    strings = json.loads((COMPONENT_DIR / "strings.json").read_text(encoding="utf-8"))
    modules = {
        "select": select_platform,
        "number": number_platform,
        "switch": switch_platform,
        "binary_sensor": binary_sensor_platform,
        "sensor": sensor_platform,
    }
    assert {platform.value for platform in PLATFORMS} >= set(modules)

    for platform, module in modules.items():
        known = set(strings["entity"][platform])
        for entity in await _setup_platform(module, coordinator):
            assert entity.translation_key in known, (
                f"{platform}.{type(entity).__name__} uses translation_key "
                f"{entity.translation_key!r}, missing from strings.json"
            )


async def test_mode_select_options_come_from_the_constant(coordinator) -> None:
    """Only auto/ups are selectable; AI, Manual and Passive are not offered."""
    entity = MarstekModeSelect(coordinator)

    assert entity.options == list(SELECTABLE_MODE_OPTIONS)
    assert entity.options == ["auto", "ups"]


async def test_power_version_options_follow_the_device_type(coordinator) -> None:
    """Venus A cannot be set to 2500 W, Venus E can."""
    # Select options are strings in Home Assistant.
    coordinator.device_info = _device_info("Venus A")
    assert MarstekPowerVersionSelect(coordinator).options == ["800", "1200", "1500"]

    coordinator.device_info = _device_info("Venus E")
    assert MarstekPowerVersionSelect(coordinator).options == ["800", "2500"]

    coordinator.device_info = _device_info("Unknown Model")
    assert MarstekPowerVersionSelect(coordinator).options == [
        str(version) for version in POWER_VERSION_OPTIONS
    ]


async def test_controls_never_report_an_optimistic_value(
    coordinator,
) -> None:
    """The API does not report these values, so they must stay unknown."""
    assert MarstekDODNumber(coordinator).native_value is None
    assert MarstekBluetoothSwitch(coordinator).is_on is None
    assert MarstekLedSwitch(coordinator).is_on is None
    assert MarstekPowerVersionSelect(coordinator).current_option is None


async def test_dod_is_rendered_as_a_percentage(coordinator) -> None:
    """DOD is a slider over the documented range, shown with a percent unit."""
    dod = MarstekDODNumber(coordinator)

    assert dod.native_unit_of_measurement == PERCENTAGE
    assert dod.mode is NumberMode.SLIDER
    assert dod.native_min_value == DOD_MIN
    assert dod.native_max_value == DOD_MAX


@pytest.mark.parametrize(
    ("factory", "action", "expected_key"),
    [
        (lambda c: MarstekDODNumber(c), "number", "dod"),
        (lambda c: MarstekBluetoothSwitch(c), "turn_on", "bluetooth"),
        (lambda c: MarstekLedSwitch(c), "turn_on", "led"),
        (lambda c: MarstekModeSelect(c), "select", "device_mode"),
    ],
)
async def test_control_entities_queue_under_their_own_key(
    coordinator, factory, action: str, expected_key: str
) -> None:
    """Every control queues its write under the device state it changes.

    Same-key repeats coalesce, different keys queue; so two controls changing
    different state must not share a key, and the mode select and the UPS mode
    must not be split across two keys.
    """
    coordinator.async_write = AsyncMock()
    entity = factory(coordinator)

    with patch.object(type(entity), "async_write_ha_state"):
        if action == "number":
            await entity.async_set_native_value(50)
        elif action == "turn_on":
            await entity.async_turn_on()
        elif action == "select":
            await entity.async_select_option("ups")
        else:
            await entity.async_press()

    coordinator.async_write.assert_awaited_once()
    assert coordinator.async_write.await_args.args[0] == expected_key


async def test_failed_write_raises_home_assistant_error(
    hass: HomeAssistant, coordinator
) -> None:
    """A rejected command raises and must not look successful."""
    coordinator.async_write = AsyncMock(
        side_effect=MarstekSetError(DEVICE_IP, "DOD.Set", "out of range")
    )
    entity = MarstekDODNumber(coordinator)
    # A confirmed write publishes a state, so the entity has to look added to
    # hass like a real one.
    entity.hass = hass
    entity.entity_id = "number.marstek_discharge_depth"
    entity.async_write_ha_state = MagicMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(50)
    assert entity.native_value is None


async def test_connectivity_sensor_reports_the_coordinator_state(
    coordinator,
) -> None:
    """The connectivity sensor stays available so it can report a disconnect."""
    sensor = MarstekConnectedBinarySensor(coordinator)

    coordinator.last_update_success = True
    assert sensor.available is True
    assert sensor.is_on is True

    coordinator.last_update_success = False
    assert sensor.available is True
    assert sensor.is_on is False


async def test_coordinator_keeps_last_data_until_the_silence_threshold(
    coordinator,
) -> None:
    """A couple of dropped responses keep the readings; a real outage surfaces."""
    previous = coordinator.data
    coordinator.udp_client.get_device_status = AsyncMock(
        side_effect=MarstekNoResponse("no answer")
    )

    assert await coordinator._async_update_data() is previous
    assert await coordinator._async_update_data() is previous
    assert coordinator.silent_polls == 2

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    assert coordinator.silent_polls == 3


async def test_shutdown_closes_the_write_queue(coordinator) -> None:
    """After unload, no queued command may still fire."""
    await coordinator.async_write("dod", AsyncMock())
    await coordinator.async_shutdown()

    with pytest.raises(RuntimeError):
        await coordinator.async_write("dod", AsyncMock())


async def test_failed_platform_unload_keeps_the_coordinator_alive(
    hass: HomeAssistant, coordinator
) -> None:
    """A failed unload must not leave the still-loaded entities frozen.

    The platforms (and the entry) stay loaded when unloading fails, so the
    coordinator has to keep polling: shutting it down first cancels the
    scheduled and debounced refresh, and no public API restarts them.
    """
    entry = MockConfigEntry(domain=DOMAIN, unique_id="unload-failure")
    entry.runtime_data = coordinator
    coordinator.async_shutdown = AsyncMock()

    with patch.object(
        hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)
    ):
        assert await async_unload_entry(hass, entry) is False

    coordinator.async_shutdown.assert_not_awaited()
    # The entities stayed loaded: polling may continue and controls still queue.
    assert coordinator._shutdown_requested is False
    assert coordinator._write_queue._closed is False


async def test_successful_unload_shuts_the_coordinator_down(
    hass: HomeAssistant, coordinator
) -> None:
    """A complete unload stops polling and the write queue."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id="unload-success")
    entry.runtime_data = coordinator
    coordinator.async_shutdown = AsyncMock()

    with patch.object(
        hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)
    ):
        assert await async_unload_entry(hass, entry) is True

    coordinator.async_shutdown.assert_awaited_once()

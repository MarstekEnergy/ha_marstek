"""Behaviour tests for the Marstek services.

The service handlers used to raise AttributeError on every call, because the
target-expansion helper was imported from ``config_validation`` instead of
``helpers.target``, so these tests call the registered services through
``hass.services`` rather than inspecting the source. Area/device targets are
covered too: an area that also contains another integration's entities must
still work, while an explicitly named non-Marstek entity must be rejected.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from aiomarstek import MarstekDeviceInfo, MarstekSetError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN
from custom_components.marstek.coordinator import (
    MARSTEK_UDP_CLIENT,
    MarstekDataUpdateCoordinator,
)
from custom_components.marstek.write_queue import DebouncedWriteQueue

COMPONENT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "marstek"
DEVICE_IP = "192.168.1.50"
DEVICE_PORT = 30000


async def _noop_refresh() -> None:
    """A refresh callback that does nothing."""


@pytest.fixture
async def udp_client(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Load the integration shell (services + shared client) for one test."""
    client = MagicMock()
    client.set_dod = AsyncMock()
    client.set_device_mode = AsyncMock()
    client.set_power_version = AsyncMock()
    # The integration registers a HA-stop listener that closes the client.
    client.async_cleanup = AsyncMock()
    monkeypatch.setattr(
        "custom_components.marstek.async_create_udp_client",
        AsyncMock(return_value=client),
    )
    assert await async_setup_component(hass, DOMAIN, {})
    assert hass.data[MARSTEK_UDP_CLIENT] is client
    return client


async def _loaded_entry(
    hass: HomeAssistant,
    client: MagicMock,
    unique_id: str = "stable-1",
    host: str = DEVICE_IP,
) -> tuple[MockConfigEntry, MarstekDataUpdateCoordinator]:
    """Create a loaded config entry with a coordinator, without polling."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=unique_id,
        data={CONF_HOST: host, CONF_PORT: DEVICE_PORT},
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)

    coordinator = MarstekDataUpdateCoordinator(hass, entry, client)
    coordinator.device_info = MagicMock(device_type="Venus E")
    # Short debounce and an inert post-write refresh keep the test fast and
    # leave no timer behind.
    coordinator._write_queue = DebouncedWriteQueue(0.01, _noop_refresh)
    entry.runtime_data = coordinator
    return entry, coordinator


def _register_entity(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    unique_id: str = "stable-1_device_mode_select",
    object_id: str = "marstek_operating_mode",
) -> str:
    """Put an entity in the registry the way a platform does.

    ``domain`` is the entity domain (select/sensor/...) and becomes the prefix
    of the entity_id; ``platform`` is the owning integration. Registering them
    the other way round would produce ``marstek.marstek_operating_mode`` and
    hide a domain/platform mix-up in the resolver.
    """
    entity_entry = er.async_get(hass).async_get_or_create(
        domain="select",
        platform=DOMAIN,
        unique_id=unique_id,
        suggested_object_id=object_id,
        config_entry=entry,
    )
    assert entity_entry.entity_id.startswith("select.")
    assert entity_entry.domain == "select"
    assert entity_entry.platform == DOMAIN
    return entity_entry.entity_id


async def test_all_documented_services_are_registered(
    hass: HomeAssistant, udp_client: MagicMock
) -> None:
    """Every service declared in services.yaml is registered at runtime."""
    services_yaml = yaml.safe_load(
        (COMPONENT_DIR / "services.yaml").read_text(encoding="utf-8")
    )
    registered = {
        name
        for name in (
            "set_dod",
            "set_mode",
            "set_power_version",
        )
        if hass.services.has_service(DOMAIN, name)
    }
    assert registered == set(services_yaml)


async def test_service_writes_go_through_the_coordinator_queue(
    hass: HomeAssistant, udp_client: MagicMock
) -> None:
    """A service queues the command instead of writing the client directly.

    Queuing keeps a service call ordered against a queued entity write; writing
    the client straight from the handler would let the older request land last.
    """
    entry, coordinator = await _loaded_entry(hass, udp_client)
    entity_id = _register_entity(hass, entry)
    coordinator.async_write = AsyncMock()

    await hass.services.async_call(
        DOMAIN,
        "set_dod",
        {"entity_id": entity_id, "value": 55},
        blocking=True,
    )

    coordinator.async_write.assert_awaited_once()
    key, action = coordinator.async_write.await_args.args
    assert key == "dod"
    assert callable(action)
    # Nothing is sent directly by the handler; the queue owns the action.
    udp_client.set_dod.assert_not_awaited()
    await coordinator.async_shutdown()


async def test_service_targets_a_real_platform_created_entity(
    hass: HomeAssistant, udp_client: MagicMock
) -> None:
    """End-to-end: the resolver must accept an entity HA itself created.

    The registry tests above build entries by hand, so they can accidentally
    agree with a wrong field check. Here the config entry is set up for real,
    the entity comes from the select platform, and ``entity.platform`` /
    ``entity.domain`` have exactly the values production sees.
    """
    device_info = MarstekDeviceInfo(
        id=1,
        device_type="Venus E",
        version="1.2.3",
        wifi_name="HomeWiFi",
        ip=DEVICE_IP,
        wifi_mac="11:22:33:44:55:66",
        ble_mac="66:55:44:33:22:11",
        mac="AA:BB:CC:DD:EE:FF",
    )
    status = MagicMock(device_mode="auto")
    status.get_value = MagicMock(return_value=None)
    udp_client.get_device_info = AsyncMock(return_value=device_info)
    udp_client.get_device_status = AsyncMock(return_value=status)

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=device_info.stable_id,
        data={CONF_HOST: DEVICE_IP, CONF_PORT: DEVICE_PORT},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_id = next(
        id_ for id_ in hass.states.async_entity_ids("select") if "mode" in id_
    )
    registry_entry = er.async_get(hass).async_get(entity_id)
    assert registry_entry is not None
    assert registry_entry.platform == DOMAIN
    assert registry_entry.domain == "select"

    await hass.services.async_call(
        DOMAIN,
        "set_dod",
        {"entity_id": entity_id, "value": 55},
        blocking=True,
    )

    udp_client.set_dod.assert_awaited_once_with(DEVICE_IP, 55, port=DEVICE_PORT)
    await hass.config_entries.async_unload(entry.entry_id)


async def _setup_real_entry(
    hass: HomeAssistant, udp_client: MagicMock
) -> MockConfigEntry:
    """Set up a config entry with its real platforms and a fake device."""
    device_info = MarstekDeviceInfo(
        id=1,
        device_type="Venus E",
        version="1.2.3",
        wifi_name="HomeWiFi",
        ip=DEVICE_IP,
        wifi_mac="11:22:33:44:55:66",
        ble_mac="66:55:44:33:22:11",
        mac="AA:BB:CC:DD:EE:FF",
    )
    status = MagicMock(device_mode="auto")
    status.get_value = MagicMock(return_value=None)
    udp_client.get_device_info = AsyncMock(return_value=device_info)
    udp_client.get_device_status = AsyncMock(return_value=status)

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=device_info.stable_id,
        data={CONF_HOST: DEVICE_IP, CONF_PORT: DEVICE_PORT},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_set_dod_service_updates_the_entity(
    hass: HomeAssistant, udp_client: MagicMock
) -> None:
    """A confirmed service write must move the entity, not just the device.

    The device never reports DOD back, so an entity that was not told about the
    service call would keep showing -- and restoring after a restart -- the
    value it held before.
    """
    entry = await _setup_real_entry(hass, udp_client)
    entity_id = next(iter(hass.states.async_entity_ids("number")))
    assert hass.states.get(entity_id).state == "unknown"

    await hass.services.async_call(
        DOMAIN,
        "set_dod",
        {"entity_id": entity_id, "value": 70},
        blocking=True,
    )

    state = hass.states.get(entity_id)
    assert state.state == "70"
    # The card renders "70 %" from this, not from the description.
    assert state.attributes["unit_of_measurement"] == "%"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_concurrent_service_calls_sync_the_actually_sent_value(
    hass: HomeAssistant, udp_client: MagicMock
) -> None:
    """A coalesced service call must not claim the value it asked for.

    Both calls report success, but the queue only sends the later one; syncing
    the entity from the superseded call would leave it showing a value the
    device never received.
    """
    entry = await _setup_real_entry(hass, udp_client)
    entity_id = next(iter(hass.states.async_entity_ids("number")))

    first = asyncio.create_task(
        hass.services.async_call(
            DOMAIN, "set_dod", {"entity_id": entity_id, "value": 50}, blocking=True
        )
    )
    # Let the first call enter the debounce window so the second supersedes it.
    await asyncio.sleep(0.1)
    second = asyncio.create_task(
        hass.services.async_call(
            DOMAIN, "set_dod", {"entity_id": entity_id, "value": 60}, blocking=True
        )
    )
    await asyncio.gather(first, second)

    udp_client.set_dod.assert_awaited_once_with(DEVICE_IP, 60, port=DEVICE_PORT)
    assert hass.states.get(entity_id).state == "60"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_older_service_write_does_not_override_a_newer_entity_value(
    hass: HomeAssistant, udp_client: MagicMock
) -> None:
    """A service write sent first must not outrank a newer slider move.

    Sequence: ``set_dod`` with 50 already left the queue, the user then drags
    the slider to 60, and both writes succeed, so the device holds 60. The 50
    is confirmed while the 60 is still queued and may be shown, but the control
    has to end on the 60 the device accepted last.
    """
    entry = await _setup_real_entry(hass, udp_client)
    entity_id = next(iter(hass.states.async_entity_ids("number")))

    sent: list[int] = []
    service_sending = asyncio.Event()
    release_service = asyncio.Event()

    async def set_dod(_ip: str, value: int, *, port: int) -> None:
        sent.append(value)
        if len(sent) == 1:
            # Hold the 50 on the wire while the slider move is queued behind it.
            service_sending.set()
            await release_service.wait()

    udp_client.set_dod = AsyncMock(side_effect=set_dod)

    service = asyncio.create_task(
        hass.services.async_call(
            DOMAIN, "set_dod", {"entity_id": entity_id, "value": 50}, blocking=True
        )
    )
    await service_sending.wait()
    entity_write = asyncio.create_task(
        hass.services.async_call(
            "number", "set_value", {"entity_id": entity_id, "value": 60}, blocking=True
        )
    )
    # Let the entity's write queue behind the in-flight one.
    await asyncio.sleep(0.1)
    release_service.set()
    await asyncio.gather(service, entity_write)

    assert sent == [50, 60]
    # The device holds 60, so the control must show 60, not the older 50.
    assert hass.states.get(entity_id).state == "60"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_rejected_service_write_leaves_no_unsent_click_value(
    hass: HomeAssistant, udp_client: MagicMock
) -> None:
    """A rejected service write must not strand a click's value on screen.

    Sequence: the user sets the slider to 60, ``set_dod`` (50) lands inside the
    debounce window and coalesces that write away, and the device rejects the
    50. Nothing reached the device, so the control must fall back to unknown
    instead of keeping the 60 the queue dropped.
    """
    entry = await _setup_real_entry(hass, udp_client)
    entity_id = next(iter(hass.states.async_entity_ids("number")))
    udp_client.set_dod = AsyncMock(
        side_effect=MarstekSetError(DEVICE_IP, "Set.DOD", "rejected")
    )

    click = asyncio.create_task(
        hass.services.async_call(
            "number", "set_value", {"entity_id": entity_id, "value": 60}, blocking=True
        )
    )
    # Still inside the debounce window, so the service call supersedes the
    # click's write instead of queueing behind it.
    await asyncio.sleep(0.1)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "set_dod", {"entity_id": entity_id, "value": 50}, blocking=True
        )
    # The coalesced click inherits the failure instead of reporting success.
    await asyncio.gather(click, return_exceptions=True)

    udp_client.set_dod.assert_awaited_once_with(DEVICE_IP, 50, port=DEVICE_PORT)
    assert hass.states.get(entity_id).state == "unknown"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_failed_second_service_write_keeps_the_first_confirmed_value(
    hass: HomeAssistant, udp_client: MagicMock
) -> None:
    """A failed later call must not hide the value an earlier call wrote.

    Sequence: ``set_dod`` (50) is already on the wire, ``set_dod`` (60) queues
    behind it and the device rejects the 60. The device holds the 50, so the
    control must show 50 even though 60 was the newest request.
    """
    entry = await _setup_real_entry(hass, udp_client)
    entity_id = next(iter(hass.states.async_entity_ids("number")))

    first_sent = asyncio.Event()
    release_first = asyncio.Event()
    sent: list[int] = []

    async def set_dod(_ip: str, value: int, *, port: int) -> None:
        sent.append(value)
        if len(sent) == 1:
            first_sent.set()
            await release_first.wait()
            return
        raise MarstekSetError(DEVICE_IP, "Set.DOD", "rejected")

    udp_client.set_dod = AsyncMock(side_effect=set_dod)

    first = asyncio.create_task(
        hass.services.async_call(
            DOMAIN, "set_dod", {"entity_id": entity_id, "value": 50}, blocking=True
        )
    )
    await first_sent.wait()
    second = asyncio.create_task(
        hass.services.async_call(
            DOMAIN, "set_dod", {"entity_id": entity_id, "value": 60}, blocking=True
        )
    )
    # Let the 60 claim its generation and queue behind the in-flight 50.
    await asyncio.sleep(0.1)
    release_first.set()
    outcomes = await asyncio.gather(first, second, return_exceptions=True)

    assert outcomes[0] is None
    assert isinstance(outcomes[1], HomeAssistantError)
    assert sent == [50, 60]
    # The device holds 50, so the failed 60 must not blank the control.
    assert hass.states.get(entity_id).state == "50"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_cancelled_number_write_still_shows_the_earlier_success(
    hass: HomeAssistant, udp_client: MagicMock
) -> None:
    """A cancelled slider move must not hide the write that did reach the device.

    Sequence: the 50 write is on the wire, the 60 write is queued inside the
    debounce window and then cancelled, and the 50 finally succeeds. The device
    holds 50, so the control must show 50 rather than the value it fell back to
    when the cancelled move was undone.
    """
    entry = await _setup_real_entry(hass, udp_client)
    entity_id = next(iter(hass.states.async_entity_ids("number")))

    first_sent = asyncio.Event()
    release_first = asyncio.Event()
    sent: list[int] = []

    async def set_dod(_ip: str, value: int, *, port: int) -> None:
        sent.append(value)
        if len(sent) == 1:
            first_sent.set()
            await release_first.wait()

    udp_client.set_dod = AsyncMock(side_effect=set_dod)

    first = asyncio.create_task(
        hass.services.async_call(
            "number", "set_value", {"entity_id": entity_id, "value": 50}, blocking=True
        )
    )
    await first_sent.wait()
    second = asyncio.create_task(
        hass.services.async_call(
            "number", "set_value", {"entity_id": entity_id, "value": 60}, blocking=True
        )
    )
    # The 60 is still inside the debounce window, so cancelling it removes it
    # from the queue before it is ever sent.
    await asyncio.sleep(0.1)
    second.cancel()
    await asyncio.gather(second, return_exceptions=True)
    assert sent == [50]

    release_first.set()
    await first

    assert hass.states.get(entity_id).state == "50"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_power_version_precheck_blocks_every_target(
    hass: HomeAssistant, udp_client: MagicMock
) -> None:
    """A model that cannot take the version stops the whole call, pre-write."""
    entry, coordinator = await _loaded_entry(hass, udp_client)
    coordinator.device_info = MagicMock(device_type="Venus A")
    entity_id = _register_entity(hass, entry)
    coordinator.async_write = AsyncMock()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_power_version",
            {"entity_id": entity_id, "power_version": 2500},
            blocking=True,
        )

    coordinator.async_write.assert_not_awaited()
    udp_client.set_power_version.assert_not_awaited()
    await coordinator.async_shutdown()


async def test_set_dod_service_reaches_the_device(
    hass: HomeAssistant, udp_client: MagicMock
) -> None:
    """A service call must actually run and send the command."""
    entry, coordinator = await _loaded_entry(hass, udp_client)
    entity_id = _register_entity(hass, entry)

    await hass.services.async_call(
        DOMAIN,
        "set_dod",
        {"entity_id": entity_id, "value": 55},
        blocking=True,
    )

    udp_client.set_dod.assert_awaited_once_with(DEVICE_IP, 55, port=DEVICE_PORT)
    await coordinator.async_shutdown()


async def test_set_mode_service_reaches_the_device(
    hass: HomeAssistant, udp_client: MagicMock
) -> None:
    """set_mode builds the library config and sends it."""
    entry, coordinator = await _loaded_entry(hass, udp_client)
    entity_id = _register_entity(hass, entry)

    await hass.services.async_call(
        DOMAIN,
        "set_mode",
        {"entity_id": entity_id, "mode": "ups", "config": {"enable": False}},
        blocking=True,
    )

    udp_client.set_device_mode.assert_awaited_once()
    mode_config = udp_client.set_device_mode.await_args.args[1]
    assert mode_config.mode == "ups"
    assert mode_config.ups_enabled is False
    await coordinator.async_shutdown()


async def test_area_target_skips_other_integrations_entities(
    hass: HomeAssistant, udp_client: MagicMock
) -> None:
    """An area holding another integration's entities must still work."""
    entry, coordinator = await _loaded_entry(hass, udp_client)
    reg = er.async_get(hass)
    marstek_entity_id = _register_entity(hass, entry)
    foreign = reg.async_get_or_create(
        domain="light",
        platform="demo",
        unique_id="foreign-entity",
        suggested_object_id="foreign_light",
    )

    area = ar.async_get(hass).async_create("Kitchen")
    reg.async_update_entity(marstek_entity_id, area_id=area.id)
    reg.async_update_entity(foreign.entity_id, area_id=area.id)

    await hass.services.async_call(
        DOMAIN,
        "set_dod",
        {"area_id": area.id, "value": 55},
        blocking=True,
    )

    udp_client.set_dod.assert_awaited_once_with(DEVICE_IP, 55, port=DEVICE_PORT)
    await coordinator.async_shutdown()


async def test_explicitly_targeted_foreign_entity_is_rejected(
    hass: HomeAssistant, udp_client: MagicMock
) -> None:
    """Naming a non-Marstek entity directly is a user error, not a no-op."""
    entry, coordinator = await _loaded_entry(hass, udp_client)
    foreign = er.async_get(hass).async_get_or_create(
        domain="switch",
        platform="demo",
        unique_id="foreign-entity-2",
        suggested_object_id="foreign_switch",
    )

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_dod",
            {"entity_id": foreign.entity_id, "value": 55},
            blocking=True,
        )

    udp_client.set_dod.assert_not_awaited()
    await coordinator.async_shutdown()
    assert entry.state is ConfigEntryState.LOADED


async def test_entity_of_an_unloaded_entry_is_rejected(
    hass: HomeAssistant, udp_client: MagicMock
) -> None:
    """runtime_data is absent for an entry that never finished setup."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="stable-2",
        data={CONF_HOST: DEVICE_IP, CONF_PORT: DEVICE_PORT},
    )
    entry.add_to_hass(hass)
    entity_id = _register_entity(
        hass, entry, unique_id="stable-2_device_mode_select", object_id="other_mode"
    )

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_dod",
            {"entity_id": entity_id, "value": 55},
            blocking=True,
        )

    udp_client.set_dod.assert_not_awaited()


async def test_one_device_error_does_not_skip_the_other_targets(
    hass: HomeAssistant, udp_client: MagicMock
) -> None:
    """A device-level failure must not abort a multi-device service call.

    Only ``MarstekSetError`` used to be caught, so an unreachable device
    (``OSError``) or a queue that closed mid-call (``RuntimeError``) aborted the
    loop and left the remaining devices unwritten, contradicting the documented
    "run every target" behaviour.
    """
    entry_a, coordinator_a = await _loaded_entry(
        hass, udp_client, unique_id="stable-a", host="192.168.1.50"
    )
    entry_b, coordinator_b = await _loaded_entry(
        hass, udp_client, unique_id="stable-b", host="192.168.1.51"
    )
    entity_a = _register_entity(
        hass, entry_a, unique_id="a_device_mode_select", object_id="mode_a"
    )
    entity_b = _register_entity(
        hass, entry_b, unique_id="b_device_mode_select", object_id="mode_b"
    )

    written: list[str] = []

    async def unreachable(key: str, action) -> bool:
        raise OSError("network unreachable")

    async def written_ok(key: str, action) -> bool:
        written.append(coordinator_b.device_ip)
        return True

    coordinator_a.async_write = unreachable
    coordinator_b.async_write = written_ok

    with pytest.raises(HomeAssistantError, match="network unreachable"):
        await hass.services.async_call(
            DOMAIN,
            "set_dod",
            {"entity_id": [entity_a, entity_b], "value": 55},
            blocking=True,
        )

    # The second device was still written despite the first one failing.
    assert written == ["192.168.1.51"]
    await coordinator_a.async_shutdown()
    await coordinator_b.async_shutdown()

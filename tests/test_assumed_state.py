"""Behaviour tests for the write-only controls' assumed state.

DOD, the power version select, the Bluetooth switch and the LED switch are all
write-only: the device never reports them back, so the integration shows the
last value Home Assistant itself wrote and restores it across a restart.

A write is shown only once the device accepted it, so these tests pin three
things: nothing is shown while the write is still queued, a rejected or
cancelled write leaves the last confirmed value in place, and a restored value
is adopted -- but only when the entity can still use it.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiomarstek import MarstekDeviceInfo
from aiomarstek.udp_client import MarstekSetError
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from custom_components.marstek.const import DOD_MAX, DOD_MIN, DOMAIN
from custom_components.marstek.coordinator import MarstekDataUpdateCoordinator
from custom_components.marstek.number import MarstekDODNumber
from custom_components.marstek.select import (
    MarstekModeSelect,
    MarstekPowerVersionSelect,
)
from custom_components.marstek.switch import MarstekBluetoothSwitch, MarstekLedSwitch

DEVICE_IP = "192.168.1.50"
DEVICE_PORT = 30000

DOD_ENTITY_ID = "number.marstek_discharge_depth"
VERSION_ENTITY_ID = "select.marstek_power_version"
BLUETOOTH_ENTITY_ID = "switch.marstek_bluetooth_advertising"
LED_ENTITY_ID = "switch.marstek_front_panel_led"


@pytest.fixture
async def coordinator(hass: HomeAssistant):
    """A real coordinator with a fake UDP client; torn down after the test."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="stable-1",
        data={CONF_HOST: DEVICE_IP, CONF_PORT: DEVICE_PORT},
    )
    coordinator = MarstekDataUpdateCoordinator(hass, entry, MagicMock())
    coordinator.device_info = MarstekDeviceInfo(
        id=1,
        device_type="Venus E",
        version="1.2.3",
        wifi_name="HomeWiFi",
        ip=DEVICE_IP,
        wifi_mac="11:22:33:44:55:66",
        ble_mac="66:55:44:33:22:11",
        mac="AA:BB:CC:DD:EE:FF",
    )
    yield coordinator
    await coordinator.async_shutdown()


async def _add_to_hass(hass: HomeAssistant, entity, entity_id: str) -> None:
    """Attach an entity to hass the way a platform add does, for restore."""
    entity.hass = hass
    entity.entity_id = entity_id
    # The entity is not riding an EntityPlatform here, so publishing a state
    # would fail; the tests assert on the entity's own state fields instead.
    entity.async_write_ha_state = MagicMock()
    await entity.async_added_to_hass()


def _reject() -> AsyncMock:
    """Return a write stub that the device rejects."""
    return AsyncMock(side_effect=MarstekSetError(DEVICE_IP, "Set.DOD", "rejected"))


async def test_controls_start_unknown(hass: HomeAssistant, coordinator) -> None:
    """Without a stored value there is nothing honest to show."""
    dod = MarstekDODNumber(coordinator)
    version = MarstekPowerVersionSelect(coordinator)
    bluetooth = MarstekBluetoothSwitch(coordinator)
    led = MarstekLedSwitch(coordinator)

    for entity, entity_id in (
        (dod, DOD_ENTITY_ID),
        (version, VERSION_ENTITY_ID),
        (bluetooth, BLUETOOTH_ENTITY_ID),
        (led, LED_ENTITY_ID),
    ):
        await _add_to_hass(hass, entity, entity_id)

    assert dod.native_value is None
    assert version.current_option is None
    assert bluetooth.is_on is None
    assert led.is_on is None


async def test_coalesced_writes_keep_the_last_confirmed_value(
    hass: HomeAssistant, coordinator
) -> None:
    """A superseded write must not show the value it never sent.

    The write queue collapses repeated writes to one and reports the surviving
    write's success to every superseded caller, so both calls return without
    error. Only the value that actually reached the device may be shown --
    otherwise the control would claim a value the device never received.
    """
    second_started = asyncio.Event()
    calls = 0

    async def coalescing_write(key: str, action) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            # Superseded: this action never runs, but the caller is still told
            # the surviving write succeeded -- and is told it was not its own
            # value that reached the device.
            await second_started.wait()
            return False
        second_started.set()
        await action()
        return True

    coordinator.async_write = coalescing_write
    coordinator.udp_client.set_dod = AsyncMock()
    dod = MarstekDODNumber(coordinator)
    await _add_to_hass(hass, dod, DOD_ENTITY_ID)

    first = asyncio.create_task(dod.async_set_native_value(50))
    await asyncio.sleep(0)
    second = asyncio.create_task(dod.async_set_native_value(60))
    await asyncio.gather(first, second)

    assert dod.native_value == 60
    assert dod._confirmed_value == 60

    coordinator.async_write = _reject()
    with pytest.raises(HomeAssistantError):
        await dod.async_set_native_value(70)

    # Still the value that reached the device, not the rejected 70.
    assert dod.native_value == 60


async def test_confirmed_value_advances_past_a_superseded_success(
    hass: HomeAssistant, coordinator
) -> None:
    """A write that reached the device counts even when a later one supersedes it.

    Sequence: 40 confirmed, 50 already in flight, 60 queued behind it. The
    device ends up holding 50 (60 was rejected), so the control must show 50 --
    not stay on the 40 and not move to the 60 that never took.
    """
    coordinator.async_write = AsyncMock(return_value=True)
    coordinator.udp_client.set_dod = AsyncMock()
    dod = MarstekDODNumber(coordinator)
    await _add_to_hass(hass, dod, DOD_ENTITY_ID)
    await dod.async_set_native_value(40)
    assert dod._confirmed_value == 40

    in_flight = asyncio.Event()
    superseded_queued = asyncio.Event()
    previous_done = asyncio.Event()
    calls = 0

    async def queue_like(key: str, action) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            # The 50 write has already been picked up by the drain; it only
            # completes after the 60 write has queued behind it.
            in_flight.set()
            await superseded_queued.wait()
            await action()
            previous_done.set()
            return True
        superseded_queued.set()
        # The real queue runs one write at a time, so this one only starts
        # after the 50 has finished and been confirmed.
        await previous_done.wait()
        raise MarstekSetError(DEVICE_IP, "Set.DOD", "rejected")

    coordinator.async_write = queue_like

    accepted = asyncio.create_task(dod.async_set_native_value(50))
    await in_flight.wait()
    rejected = asyncio.create_task(dod.async_set_native_value(60))
    outcomes = await asyncio.gather(accepted, rejected, return_exceptions=True)

    assert isinstance(outcomes[1], HomeAssistantError)
    assert dod._confirmed_value == 50  # the 50 that really reached the device
    assert dod.native_value == 50


async def test_service_write_updates_the_entity(
    hass: HomeAssistant, coordinator
) -> None:
    """A confirmed service write must move the control, not just the device."""
    dod = MarstekDODNumber(coordinator)
    await _add_to_hass(hass, dod, DOD_ENTITY_ID)
    assert dod.native_value is None

    # What set_dod does once the device confirms the write: the token is
    # reserved before queueing, so a click during the write can outrank it.
    generation = coordinator.async_reserve_assumed_write("dod")
    coordinator.async_note_assumed_write("dod", 70, generation)

    assert dod.native_value == 70
    assert dod._confirmed_value == 70


async def test_older_service_confirmation_keeps_the_newer_click(
    hass: HomeAssistant, coordinator
) -> None:
    """A confirmation that lands first must not outrank a later accepted write.

    Sequence: ``set_dod`` already started sending 50, the user then drags the
    slider to 60, and both writes succeed, so the device ends up holding 60.
    The 50 is confirmed while the 60 is still queued and is shown -- it is what
    the device holds at that moment -- but once the 60 is accepted the control
    has to follow it.
    """
    service_sent = asyncio.Event()
    click_queued = asyncio.Event()
    service_confirmed = asyncio.Event()
    device_seen: list[int] = []

    async def queue_like(key: str, action) -> bool:
        if not service_sent.is_set():
            service_sent.set()
            # The 50 write is already on the wire; the click lands meanwhile.
            await click_queued.wait()
            await action()
            device_seen.append(50)
            return True
        click_queued.set()
        # The device answers one write at a time, so the 60 only goes out after
        # the 50 has been confirmed and shown.
        await service_confirmed.wait()
        await action()
        device_seen.append(60)
        return True

    coordinator.async_write = queue_like
    coordinator.udp_client.set_dod = AsyncMock()
    dod = MarstekDODNumber(coordinator)
    await _add_to_hass(hass, dod, DOD_ENTITY_ID)

    async def service_dod() -> None:
        generation = coordinator.async_reserve_assumed_write("dod")
        await coordinator.async_write("dod", AsyncMock())
        coordinator.async_note_assumed_write("dod", 50, generation)
        service_confirmed.set()

    service = asyncio.create_task(service_dod())
    await service_sent.wait()
    click = asyncio.create_task(dod.async_set_native_value(60))
    await asyncio.gather(service, click)

    assert device_seen == [50, 60]
    # The device holds 60, so that is what the control shows.
    assert dod._confirmed_value == 60
    assert dod.native_value == 60


async def test_failed_service_write_does_not_strand_an_unsent_click_value(
    hass: HomeAssistant, coordinator
) -> None:
    """A failed service write must not leave the control claiming a value.

    Sequence: the user drags the slider to 60, ``set_dod`` (50) arrives while
    that write is still queued and coalesces it away, and the device then
    rejects the 50. Nothing reached the device, so the control has to stay on
    the last confirmed value (unknown here) instead of adopting either number.
    """
    click_queued = asyncio.Event()
    service_queued = asyncio.Event()

    async def queue_like(key: str, action) -> bool:
        if not click_queued.is_set():
            # This click is coalesced into the service write queued behind it,
            # so it learns that write's outcome.
            click_queued.set()
            await service_queued.wait()
            raise MarstekSetError(DEVICE_IP, "Set.DOD", "rejected")
        service_queued.set()
        raise MarstekSetError(DEVICE_IP, "Set.DOD", "rejected")

    coordinator.async_write = queue_like
    coordinator.udp_client.set_dod = AsyncMock()
    dod = MarstekDODNumber(coordinator)
    await _add_to_hass(hass, dod, DOD_ENTITY_ID)

    click = asyncio.create_task(dod.async_set_native_value(60))
    await click_queued.wait()
    # What set_dod does against a device that rejects the write.
    generation = coordinator.async_reserve_assumed_write("dod")
    with pytest.raises(MarstekSetError):
        await coordinator.async_write("dod", AsyncMock())
    assert generation  # the call did claim a generation before queueing
    await asyncio.gather(click, return_exceptions=True)

    # Nothing was written, so the dropped 60 must not remain as an assumed value.
    assert dod._confirmed_value is None
    assert dod.native_value is None


async def test_later_failed_service_write_keeps_the_earlier_confirmed_value(
    hass: HomeAssistant, coordinator
) -> None:
    """A failed newer request must not hide the value an earlier one wrote.

    Sequence: ``set_dod`` (50) is already on the wire, ``set_dod`` (60) queues
    behind it and the device rejects the 60. The device holds the 50, so the
    control has to show it even though the 60 was the newest request.
    """
    first_sent = asyncio.Event()
    release_first = asyncio.Event()
    first_confirmed = asyncio.Event()

    async def queue_like(key: str, action) -> bool:
        if not first_sent.is_set():
            first_sent.set()
            await release_first.wait()
            return True
        # The device settles one write at a time, so the 60 only fails after
        # the 50 has been confirmed.
        await first_confirmed.wait()
        raise MarstekSetError(DEVICE_IP, "Set.DOD", "rejected")

    coordinator.async_write = queue_like
    dod = MarstekDODNumber(coordinator)
    await _add_to_hass(hass, dod, DOD_ENTITY_ID)

    async def service_dod(value: int) -> None:
        generation = coordinator.async_reserve_assumed_write("dod")
        try:
            await coordinator.async_write("dod", AsyncMock())
        except MarstekSetError:
            return
        coordinator.async_note_assumed_write("dod", value, generation)
        first_confirmed.set()

    first = asyncio.create_task(service_dod(50))
    await first_sent.wait()
    second = asyncio.create_task(service_dod(60))
    # Let the 60 claim its generation before the 50 is confirmed.
    await asyncio.sleep(0.01)
    release_first.set()
    await asyncio.gather(first, second)

    # The 50 reached the device, so it stays on screen despite the newer failure.
    assert dod._confirmed_value == 50
    assert dod.native_value == 50


async def test_cancelled_click_still_shows_the_earlier_write_that_succeeded(
    hass: HomeAssistant, coordinator
) -> None:
    """A cancelled click must not leave the earlier successful write unshown.

    Sequence: the 50 write is on the wire, the 60 write queues behind it and is
    then cancelled before it is sent, and the 50 finally succeeds. The device
    holds 50, so the control must show 50 once that write is confirmed.
    """
    first_sent = asyncio.Event()
    release_first = asyncio.Event()
    second_queued = asyncio.Event()

    async def queue_like(key: str, action) -> bool:
        if not first_sent.is_set():
            first_sent.set()
            await release_first.wait()
            return True
        # This queued write is cancelled by the test before it is sent.
        second_queued.set()
        await asyncio.Event().wait()
        return True  # pragma: no cover - unreachable, the wait is cancelled

    coordinator.async_write = queue_like
    coordinator.udp_client.set_dod = AsyncMock()
    dod = MarstekDODNumber(coordinator)
    await _add_to_hass(hass, dod, DOD_ENTITY_ID)

    first = asyncio.create_task(dod.async_set_native_value(50))
    await first_sent.wait()
    second = asyncio.create_task(dod.async_set_native_value(60))
    await second_queued.wait()
    second.cancel()
    await asyncio.gather(second, return_exceptions=True)

    # The cancelled click never showed its value, so the control is untouched.
    assert dod.native_value is None

    release_first.set()
    await first

    # The 50 write did reach the device, so it becomes the shown value.
    assert dod._confirmed_value == 50
    assert dod.native_value == 50


async def test_value_appears_only_after_the_device_confirms(
    hass: HomeAssistant, coordinator
) -> None:
    """A click must not show anything before the device took the value."""
    gate = asyncio.Event()

    async def blocked_write(*args: object, **kwargs: object) -> bool:
        await gate.wait()
        return True

    coordinator.async_write = blocked_write
    led = MarstekLedSwitch(coordinator)
    await _add_to_hass(hass, led, LED_ENTITY_ID)
    assert led.is_on is None

    pending = asyncio.create_task(led.async_turn_on())
    await asyncio.sleep(0)
    # Still queued, so there is nothing to show yet.
    assert led.is_on is None

    gate.set()
    await pending
    assert led.is_on is True


async def test_confirmed_writes_are_remembered(
    hass: HomeAssistant, coordinator
) -> None:
    """A write the device accepted becomes the shown value."""
    coordinator.async_write = AsyncMock()
    dod = MarstekDODNumber(coordinator)
    version = MarstekPowerVersionSelect(coordinator)
    led = MarstekLedSwitch(coordinator)
    for entity, entity_id in (
        (dod, DOD_ENTITY_ID),
        (version, VERSION_ENTITY_ID),
        (led, LED_ENTITY_ID),
    ):
        await _add_to_hass(hass, entity, entity_id)

    await dod.async_set_native_value(55)
    await version.async_select_option("2500")
    await led.async_turn_on()

    assert dod.native_value == 55
    assert version.current_option == "2500"
    assert led.is_on is True


async def test_rejected_writes_leave_the_shown_value_alone(
    hass: HomeAssistant, coordinator
) -> None:
    """A value the device refused must not be presented as if it took."""
    coordinator.async_write = AsyncMock()
    dod = MarstekDODNumber(coordinator)
    led = MarstekLedSwitch(coordinator)
    await _add_to_hass(hass, dod, DOD_ENTITY_ID)
    await _add_to_hass(hass, led, LED_ENTITY_ID)
    await dod.async_set_native_value(55)
    await led.async_turn_on()

    coordinator.async_write = _reject()
    with pytest.raises(HomeAssistantError):
        await dod.async_set_native_value(70)
    with pytest.raises(HomeAssistantError):
        await led.async_turn_off()

    assert dod.native_value == 55
    assert led.is_on is True


async def test_restored_values_survive_a_restart(
    hass: HomeAssistant, coordinator
) -> None:
    """The stored value is adopted on setup instead of blanking the control."""
    mock_restore_cache(
        hass,
        [
            State(DOD_ENTITY_ID, "55"),
            State(VERSION_ENTITY_ID, "2500"),
            State(BLUETOOTH_ENTITY_ID, "on"),
            State(LED_ENTITY_ID, "off"),
        ],
    )
    dod = MarstekDODNumber(coordinator)
    version = MarstekPowerVersionSelect(coordinator)
    bluetooth = MarstekBluetoothSwitch(coordinator)
    led = MarstekLedSwitch(coordinator)
    for entity, entity_id in (
        (dod, DOD_ENTITY_ID),
        (version, VERSION_ENTITY_ID),
        (bluetooth, BLUETOOTH_ENTITY_ID),
        (led, LED_ENTITY_ID),
    ):
        await _add_to_hass(hass, entity, entity_id)

    assert dod.native_value == 55
    assert version.current_option == "2500"
    assert bluetooth.is_on is True
    assert led.is_on is False


async def test_restore_does_not_overwrite_a_write_confirmed_while_loading(
    hass: HomeAssistant, coordinator
) -> None:
    """A write confirmed during the restore read must win over the cache.

    The control registers itself before awaiting the restore cache, so a
    service call can be confirmed in that window. Adopting the cached value
    afterwards would replace a value the device really took.
    """
    mock_restore_cache(hass, (State(DOD_ENTITY_ID, "30"),))
    dod = MarstekDODNumber(coordinator)
    dod.hass = hass
    dod.entity_id = DOD_ENTITY_ID
    dod.async_write_ha_state = MagicMock()

    gate = asyncio.Event()
    read_cache = dod.async_get_last_state

    async def slow_read_cache():
        await gate.wait()
        return await read_cache()

    dod.async_get_last_state = slow_read_cache

    added = asyncio.create_task(dod.async_added_to_hass())
    await asyncio.sleep(0.01)
    # What set_dod does while the restore read is still pending.
    generation = coordinator.async_reserve_assumed_write("dod")
    coordinator.async_note_assumed_write("dod", 70, generation)
    gate.set()
    await added

    assert dod.native_value == 70
    assert dod._confirmed_value == 70  # not the cached 30


async def test_cancelled_mode_change_falls_back_to_the_reported_mode(
    hass: HomeAssistant, coordinator
) -> None:
    """A cancelled mode switch must not show a mode the device never adopted.

    The mode select reads the mode back from the device instead of assuming
    state, but it shows the requested option optimistically; a write that is
    cancelled has to fall back to the reported mode rather than keep a value the
    device never took until the next poll.
    """
    coordinator.data = MagicMock(device_mode="auto")
    gate = asyncio.Event()

    async def blocked_write(*args: object, **kwargs: object) -> bool:
        await gate.wait()
        return True

    coordinator.async_write = blocked_write
    mode = MarstekModeSelect(coordinator)
    await _add_to_hass(hass, mode, "select.marstek_operating_mode")
    assert mode.current_option == "auto"

    pending = asyncio.create_task(mode.async_select_option("ups"))
    await asyncio.sleep(0.01)
    assert mode.current_option == "ups"  # optimistic

    pending.cancel()
    await asyncio.gather(pending, return_exceptions=True)

    assert mode.current_option == "auto"


async def test_unusable_restored_values_are_dropped(
    hass: HomeAssistant, coordinator
) -> None:
    """A stored value the entity cannot use must not be shown as valid.

    The DOD range and the per-model power versions both change, so a value
    restored from an older setup can be out of bounds or no longer offered.
    """
    mock_restore_cache(
        hass,
        [
            State(DOD_ENTITY_ID, str(DOD_MAX + 1)),
            # 2500 W is a Venus E option; this entity is built for Venus A.
            State(VERSION_ENTITY_ID, "2500"),
            State(LED_ENTITY_ID, "unavailable"),
        ],
    )
    coordinator.device_info = replace(coordinator.device_info, device_type="Venus A")
    dod = MarstekDODNumber(coordinator)
    version = MarstekPowerVersionSelect(coordinator)
    led = MarstekLedSwitch(coordinator)
    for entity, entity_id in (
        (dod, DOD_ENTITY_ID),
        (version, VERSION_ENTITY_ID),
        (led, LED_ENTITY_ID),
    ):
        await _add_to_hass(hass, entity, entity_id)

    assert dod.native_value is None
    assert version.current_option is None
    assert led.is_on is None

    # The bounds themselves stay usable.
    ok = MarstekDODNumber(coordinator)
    await _add_to_hass(hass, ok, "number.other_discharge_depth")
    ok._async_restore_assumed_state(str(DOD_MIN))
    assert ok.native_value == DOD_MIN

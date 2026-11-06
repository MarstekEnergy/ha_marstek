"""Behaviour tests for the operating-mode select's coordinator sync.

``_handle_coordinator_update`` is called by the coordinator as a plain callback
(its return value is discarded), so an ``async def`` override would build a
coroutine that never runs -- the select would keep showing whatever the user
last picked. These tests call the callback the same way the coordinator does
(a synchronous call), so that regression fails here instead of silently
passing a source-only assertion.

``CoordinatorEntity._handle_coordinator_update`` is what pushes the new value
to HA, so the assertions check three things: the callback is synchronous, it
replaces the stale option, and it triggers the state write.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiomarstek import MarstekDeviceInfo
from aiomarstek.udp_client import MarstekSetError
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN
from custom_components.marstek.coordinator import MarstekDataUpdateCoordinator
from custom_components.marstek.select import MarstekModeSelect

DEVICE_IP = "192.168.1.50"


def _make_device_info() -> MarstekDeviceInfo:
    """Build a device info record for the entity's device registry entry."""
    return MarstekDeviceInfo(
        id=1,
        device_type="Venus E",
        version="1.2.3",
        wifi_name="HomeWiFi",
        ip=DEVICE_IP,
        wifi_mac="11:22:33:44:55:66",
        ble_mac="66:55:44:33:22:11",
        mac="AA:BB:CC:DD:EE:FF",
    )


def _make_select(hass: HomeAssistant) -> MarstekModeSelect:
    """Create a mode select bound to a real (never-polled) coordinator."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: DEVICE_IP})
    coordinator = MarstekDataUpdateCoordinator(hass, entry, MagicMock())
    coordinator.device_info = _make_device_info()
    return MarstekModeSelect(coordinator)


def _poll_returns(entity: MarstekModeSelect, mode: str | None) -> None:
    """Pretend the last poll returned ``mode`` for the device."""
    entity.coordinator.data = (
        None if mode is None else SimpleNamespace(device_mode=mode)
    )


def test_mode_select_callback_is_synchronous(hass: HomeAssistant) -> None:
    """The override is a plain callback, not a coroutine the coordinator drops."""
    entity = _make_select(hass)
    callback = entity._handle_coordinator_update

    assert not inspect.iscoroutinefunction(callback), (
        "the coordinator calls _handle_coordinator_update without awaiting it; "
        "an async override creates a coroutine that never runs"
    )
    # A plain callback runs on the event loop; HA marks those with this flag.
    assert getattr(
        MarstekModeSelect._handle_coordinator_update, "_hass_callback", False
    )


def test_poll_sync_overwrites_stale_option_and_writes_state(
    hass: HomeAssistant,
) -> None:
    """A poll replaces the optimistically shown mode and writes the HA state."""
    entity = _make_select(hass)
    # What the user last picked in the UI, before the device answered.
    entity._attr_current_option = "ups"
    _poll_returns(entity, "auto")

    with patch.object(entity, "async_write_ha_state") as write_state:
        result = entity._handle_coordinator_update()

    assert result is None, "the callback must not return a coroutine"
    assert entity.current_option == "auto"
    # The parent's state write ran: the UI is refreshed with the device mode.
    write_state.assert_called_once_with()


def test_initial_option_comes_from_the_coordinator(hass: HomeAssistant) -> None:
    """A new select shows the mode already known at construction time.

    The config entry refreshes the coordinator before the platforms are set up
    and subscribing does not replay that result, so without seeding in
    ``__init__`` the select would read unknown until the next poll.
    """
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: DEVICE_IP})
    coordinator = MarstekDataUpdateCoordinator(hass, entry, MagicMock())
    coordinator.device_info = _make_device_info()
    coordinator.data = SimpleNamespace(device_mode="auto")

    entity = MarstekModeSelect(coordinator)

    assert entity.current_option == "auto"
    assert entity.state == "auto"


def test_poll_without_data_reports_unknown(hass: HomeAssistant) -> None:
    """No data means no known mode, so the option is cleared."""
    entity = _make_select(hass)
    entity._attr_current_option = "ups"
    _poll_returns(entity, None)

    with patch.object(entity, "async_write_ha_state") as write_state:
        entity._handle_coordinator_update()

    assert entity.current_option is None
    assert entity.state is None
    write_state.assert_called_once_with()


async def test_failed_write_restores_the_reported_mode(hass: HomeAssistant) -> None:
    """A rejected command must not leave the optimistic option on screen.

    The fallback comes from the coordinator's current data, not from a cached
    "previous" value: the device only ever reports the truth.
    """
    entity = _make_select(hass)
    _poll_returns(entity, "auto")
    entity.coordinator.async_write = AsyncMock(
        side_effect=MarstekSetError(DEVICE_IP, "SetMode", "invalid mode")
    )
    entity.async_write_ha_state = MagicMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_select_option("ups")

    assert entity.current_option == "auto"
    # Once for the optimistic value, once to undo it.
    assert entity.async_write_ha_state.call_count == 2


def test_unoffered_mode_is_unknown_in_the_select(hass: HomeAssistant) -> None:
    """Manual/Passive are reported by the sensor, not by the select.

    HA maps a ``current_option`` outside the select's ``options`` to unknown
    (``SelectEntity.state`` returns None), so the select only carries the
    writable modes; the device-mode sensor is what shows the real mode. The
    modes are deliberately not re-added to ``options`` just to display them.
    """
    entity = _make_select(hass)
    _poll_returns(entity, "manual")

    with patch.object(entity, "async_write_ha_state") as write_state:
        entity._handle_coordinator_update()

    # The sync still records the device's real mode ...
    assert entity.current_option == "manual"
    # ... but HA publishes it as unknown because "manual" is not selectable.
    assert "manual" not in entity.options
    assert entity.state is None
    write_state.assert_called_once_with()

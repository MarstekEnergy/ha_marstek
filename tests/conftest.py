"""Fixtures for Marstek integration tests."""

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

from aiomarstek import MarstekDeviceInfo, MarstekDeviceStatus, MarstekNoResponse
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

HOST = "192.168.1.132"

# Responses captured from a Venus E Mini (VNSEM-0, firmware 301).
DEVICE_RESPONSE = {
    "device": "VNSEM-0",
    "ver": 301,
    "ble_mac": "ccc837b39e61",
    "wifi_mac": "ccc837b3e8d2",
    "wifi_name": "home-wifi",
    "ip": HOST,
}
ES_STATUS = {
    "id": 0,
    "bat_soc": 9,
    "bat_cap": 200,
    "pv_power": 0,
    "ongrid_power": -10,
    "offgrid_power": 4,
    "total_pv_energy": 0,
    "total_grid_output_energy": 695,
    "total_grid_input_energy": 0,
    "total_load_energy": 12,
}
ES_MODE = {
    "id": 0,
    "mode": "Auto",
    "ongrid_power": -11,
    "offgrid_power": 5,
    "bat_soc": 9,
    "ct_state": 1,
    "a_power": 2068,
    "b_power": 43,
    "c_power": 41,
    "total_power": 2152,
    "input_energy": 0,
    "output_energy": 695,
}
PV_STATUS = {
    "id": 0,
    "pv1_power": 100,
    "pv1_voltage": 50,
    "pv1_current": 2,
    "pv1_state": 1,
    "pv2_power": 0,
    "pv2_voltage": 0,
    "pv2_current": 0,
    "pv2_state": 0,
    "pv3_power": 0,
    "pv3_voltage": 0,
    "pv3_current": 0,
    "pv3_state": 0,
    "pv4_power": 0,
    "pv4_voltage": 0,
    "pv4_current": 0,
    "pv4_state": 0,
}


class FakeClient:
    """Stand-in for MarstekUDPClient answering like a Venus E Mini."""

    def __init__(self) -> None:
        """Initialize the fake device state."""
        self.online = True
        self.version = 301
        self.device: str = str(DEVICE_RESPONSE["device"])
        self.es_status: dict[str, Any] = dict(ES_STATUS)
        self.es_mode: dict[str, Any] = dict(ES_MODE)
        # The discovery response; tests override fields to fake other devices.
        self.response: dict[str, Any] = dict(DEVICE_RESPONSE)
        # Whether the device answers ES.GetStatus; ES.GetMode always answers.
        self.es_status_online = True
        self.results: dict[str, dict[str, Any]] = {}
        self.include_pv_requests: list[bool] = []
        self.pv_status: dict[str, Any] = dict(PV_STATUS)
        self.pv_online = True
        self.pv_supported: bool | None = None

    async def get_device_info(self, device_ip: str) -> MarstekDeviceInfo:
        """Return the device information."""
        if not self.online:
            raise TimeoutError
        return MarstekDeviceInfo.from_response(
            {**self.response, "device": self.device, "ver": self.version}
        )

    async def discover_devices(self) -> list[MarstekDeviceInfo]:
        """Return the fake device when it is online, like the library."""
        if not self.online:
            return []
        return [
            MarstekDeviceInfo.from_response(
                {**self.response, "device": self.device, "ver": self.version}
            )
        ]

    async def get_device_status(
        self,
        device_ip: str,
        *,
        previous_data: MarstekDeviceStatus | None = None,
        include_pv: bool = True,
    ) -> MarstekDeviceStatus:
        """Return status, or raise MarstekNoResponse when offline, like the library."""
        self.include_pv_requests.append(include_pv)
        if not self.online:
            raise MarstekNoResponse(device_ip)
        # Each poll records only the methods that answered, like the library.
        self.results = {}
        status = previous_data or MarstekDeviceStatus(device_ip=device_ip)
        if self.es_status_online:
            self.results["ES.GetStatus"] = self.es_status
            status = status.with_es_status_response(self.es_status)
        # ES.GetMode answers even when ES.GetStatus does not, like a device
        # whose status handler has wedged while the rest still works.
        self.results["ES.GetMode"] = self.es_mode
        status = status.with_es_mode_response(self.es_mode)
        if include_pv and self.pv_online:
            self.results["PV.GetStatus"] = self.pv_status
            self.pv_supported = True
            status = status.with_pv_status_response(self.pv_status)
        return status

    def get_last_results(self, device_ip: str) -> dict[str, dict[str, Any]]:
        """Return the latest raw results."""
        return dict(self.results)

    def is_pv_status_supported(self, device_ip: str) -> bool | None:
        """Return whether the device answers PV.GetStatus, or None if unknown."""
        return self.pv_supported

    async def async_cleanup(self) -> None:
        """Close the fake client."""


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading the custom integration in every test."""


@pytest.fixture
def client() -> Generator[FakeClient]:
    """Patch the UDP client with a fake device."""
    fake = FakeClient()
    with patch("custom_components.marstek.async_create_udp_client", return_value=fake):
        yield fake


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a config entry for the fake device."""
    entry = MockConfigEntry(
        domain="marstek",
        data={CONF_HOST: HOST},
        unique_id=MarstekDeviceInfo.from_response(DEVICE_RESPONSE).stable_id,
    )
    entry.add_to_hass(hass)
    return entry


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, str]:
    """Set up the entry and return its entity IDs by entity key."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return {
        e.unique_id.split("_", 1)[1]: e.entity_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }

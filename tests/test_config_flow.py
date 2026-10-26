"""Config flow tests for the Marstek integration."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from aiomarstek import DEFAULT_UDP_PORT, MarstekDeviceInfo
from homeassistant import config_entries
from homeassistant.const import CONF_DEVICE, CONF_HOST, CONF_MAC, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN
from custom_components.marstek.coordinator import MARSTEK_UDP_CLIENT

DEVICE_IP = "192.168.1.50"
DEVICE_MAC = "AA:BB:CC:DD:EE:FF"


def make_device(**overrides: object) -> MarstekDeviceInfo:
    """Build a normalized Marstek device info record."""
    fields: dict[str, object] = {
        "id": 1,
        "device_type": "Venus E",
        "version": "1.2.3",
        "wifi_name": "HomeWiFi",
        "ip": DEVICE_IP,
        "wifi_mac": "11:22:33:44:55:66",
        "ble_mac": "66:55:44:33:22:11",
        "mac": DEVICE_MAC,
    }
    fields.update(overrides)
    return MarstekDeviceInfo(**fields)  # type: ignore[arg-type]


class FakeUDPClient:
    """Offline stand-in for the Marstek UDP client."""

    def __init__(self) -> None:
        self.discover_result: list[MarstekDeviceInfo] = []
        self.device: MarstekDeviceInfo | None = None
        self.discover_error: Exception | None = None
        self.info_error: Exception | None = None

    async def discover_devices(
        self, use_cache: bool = False
    ) -> list[MarstekDeviceInfo]:
        """Return the configured discovery result or raise."""
        if self.discover_error is not None:
            raise self.discover_error
        return list(self.discover_result)

    async def get_device_info(
        self, device_ip: str, *, port: int | None = None, timeout: float = 5.0
    ) -> MarstekDeviceInfo:
        """Return the configured device or raise."""
        if self.info_error is not None:
            raise self.info_error
        assert self.device is not None
        return self.device

    async def async_cleanup(self) -> None:
        """No-op cleanup."""


@pytest.fixture
def client(hass: HomeAssistant) -> Generator[FakeUDPClient]:
    """Inject a fake UDP client shared by the config flow."""
    fake = FakeUDPClient()
    hass.data[MARSTEK_UDP_CLIENT] = fake
    yield fake


async def _start_user_flow(hass: HomeAssistant) -> config_entries.ConfigFlowResult:
    """Start a config flow from the user source."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def _advance_to_manual(
    hass: HomeAssistant, result: config_entries.ConfigFlowResult
) -> config_entries.ConfigFlowResult:
    """Navigate the user menu into the manual step."""
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )


async def test_user_step_shows_menu(hass: HomeAssistant) -> None:
    """The user step offers discover and manual options."""
    result = await _start_user_flow(hass)
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "user"
    assert set(result["menu_options"]) == {"discover", "manual"}


async def test_manual_flow_creates_entry(
    hass: HomeAssistant, client: FakeUDPClient
) -> None:
    """A valid manual host/port input creates a config entry."""
    device = make_device()
    client.device = device

    result = await _start_user_flow(hass)
    result = await _advance_to_manual(hass, result)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: device.ip, CONF_PORT: DEFAULT_UDP_PORT},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY

    entry = result["result"]
    assert entry.unique_id == DEVICE_MAC
    assert entry.data[CONF_HOST] == DEVICE_IP
    assert entry.data[CONF_MAC] == DEVICE_MAC
    assert entry.data["device_type"] == "Venus E"
    assert entry.data["version"] == "1.2.3"
    assert entry.data[CONF_PORT] == DEFAULT_UDP_PORT


async def test_manual_flow_cannot_connect(
    hass: HomeAssistant, client: FakeUDPClient
) -> None:
    """A device that never answers reports cannot_connect."""
    client.info_error = TimeoutError()

    result = await _start_user_flow(hass)
    result = await _advance_to_manual(hass, result)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: DEVICE_IP}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_manual_flow_device_not_found(
    hass: HomeAssistant, client: FakeUDPClient
) -> None:
    """A host with no Marstek device reports device_not_found."""
    client.info_error = TypeError()

    result = await _start_user_flow(hass)
    result = await _advance_to_manual(hass, result)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: DEVICE_IP}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "device_not_found"}


async def test_manual_flow_unsupported_device(
    hass: HomeAssistant, client: FakeUDPClient
) -> None:
    """An unsupported device type is rejected."""
    client.device = make_device(device_type="Venus C")

    result = await _start_user_flow(hass)
    result = await _advance_to_manual(hass, result)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: DEVICE_IP}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "unsupported_device"}


async def test_manual_flow_aborts_duplicate_host(
    hass: HomeAssistant, client: FakeUDPClient
) -> None:
    """A host that is already configured aborts the flow."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: DEVICE_IP})
    entry.add_to_hass(hass)

    client.device = make_device()

    result = await _start_user_flow(hass)
    result = await _advance_to_manual(hass, result)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: DEVICE_IP}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_discover_flow_selects_device(
    hass: HomeAssistant, client: FakeUDPClient
) -> None:
    """Discovery yields a device list and selecting one creates an entry."""
    device = make_device()
    client.discover_result = [device]
    client.device = device

    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "discover"}
    )
    # The fake discovery resolves synchronously, so the flow advances straight
    # through the progress step into the discovery results form.
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "discovery_done"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: "0"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == DEVICE_MAC

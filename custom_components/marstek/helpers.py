"""Helpers for the Marstek integration."""

from aiomarstek import REQUEST_QUIET_GAP, MarstekUDPClient
from homeassistant.components import network
from homeassistant.core import HomeAssistant


async def async_create_udp_client(hass: HomeAssistant) -> MarstekUDPClient:
    """Create a configured UDP client for this Home Assistant instance.

    The request quiet gap is enabled because field logs show Marstek
    devices silently dropping a request that arrives moments after their
    previous response; spacing requests keeps the device answering.
    """
    client = MarstekUDPClient(min_request_gap=REQUEST_QUIET_GAP)
    try:
        await client.async_setup()
        addresses = await network.async_get_ipv4_broadcast_addresses(hass)
    except (TimeoutError, OSError, TypeError):
        await client.async_cleanup()
        raise

    client.set_broadcast_addresses([str(address) for address in addresses])
    return client

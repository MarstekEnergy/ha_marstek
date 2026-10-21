"""Helpers for the Marstek integration."""

import logging

from aiomarstek import MarstekUDPClient

from homeassistant.components import network
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import CONF_BLE_MAC, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_create_udp_client(hass: HomeAssistant) -> MarstekUDPClient:
    """Create a configured UDP client for this Home Assistant instance."""
    client = MarstekUDPClient()
    try:
        await client.async_setup()
        addresses = await network.async_get_ipv4_broadcast_addresses(hass)
    except TimeoutError, OSError, TypeError:
        await client.async_cleanup()
        raise

    client.set_broadcast_addresses([str(address) for address in addresses])
    return client


def migrate_legacy_identifier(
    hass: HomeAssistant, entry: ConfigEntry, stable_id: str
) -> bool:
    """Re-attach an entry that still carries the pre-0.1.9 Wi-Fi MAC id.

    The entry is matched to the device by the BLE MAC it recorded when it
    was created, which also covers a device whose Wi-Fi hardware has since
    been replaced. Entity registry rows move with it — the old unique_id
    prefix is replaced with the new one — so entity ids, statistics, and
    automations survive the identifier switch. Returns whether the entry
    was migrated; it is skipped when the new identifier is already in use.
    """
    if entry.unique_id == stable_id:
        return False
    if entry.data.get(CONF_BLE_MAC) != stable_id:
        return False
    if any(
        other.unique_id == stable_id
        for other in hass.config_entries.async_entries(DOMAIN)
        if other.entry_id != entry.entry_id
    ):
        return False
    _LOGGER.info(
        "Migrating entry %s from unique_id %s to %s",
        entry.title,
        entry.unique_id,
        stable_id,
    )
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        old_unique_id = entity.unique_id
        if not old_unique_id.startswith(entry.unique_id):
            continue
        new_unique_id = f"{stable_id}{old_unique_id[len(entry.unique_id):]}"
        if registry.async_get_entity_id(entity.domain, entity.platform, new_unique_id):
            continue
        registry.async_update_entity(entity.entity_id, new_unique_id=new_unique_id)
    hass.config_entries.async_update_entry(entry, unique_id=stable_id)
    return True

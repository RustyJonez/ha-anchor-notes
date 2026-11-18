"""Anchor Notes: read-only sensors for a self-hosted Anchor notes account."""

from __future__ import annotations

from homeassistant.const import CONF_API_TOKEN, CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AnchorClient
from .coordinator import AnchorConfigEntry, AnchorCoordinator

PLATFORMS: list[Platform] = [Platform.SELECT, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: AnchorConfigEntry) -> bool:
    """Set up one Anchor account."""
    client = AnchorClient(
        async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_API_TOKEN]
    )
    coordinator = AnchorCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AnchorConfigEntry) -> bool:
    """Unload an Anchor account."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(hass: HomeAssistant, entry: AnchorConfigEntry) -> None:
    """Apply new options (interval, list sizes) by reloading."""
    await hass.config_entries.async_reload(entry.entry_id)

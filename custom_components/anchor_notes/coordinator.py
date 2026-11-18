"""Polls one Anchor account."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import AnchorAuthError, AnchorClient, AnchorError
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN, LOGGER
from .models import AnchorData

type AnchorConfigEntry = ConfigEntry[AnchorCoordinator]


class AnchorCoordinator(DataUpdateCoordinator[AnchorData]):
    """Fetches GET /api/notes once per interval; all sensors read from it."""

    config_entry: AnchorConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: AnchorConfigEntry, client: AnchorClient
    ) -> None:
        minutes = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(minutes=minutes),
        )
        self.client = client
        self.server_version: str | None = None

    async def _async_setup(self) -> None:
        """Read the server version once, for the device page. Failure is fine."""
        try:
            health = await self.client.get_health()
        except AnchorError:
            return
        version = health.get("version")
        self.server_version = str(version) if version else None

    async def _async_update_data(self) -> AnchorData:
        try:
            raw_notes = await self.client.get_notes()
        except AnchorAuthError as err:
            raise ConfigEntryAuthFailed("Anchor rejected the API token") from err
        except AnchorError as err:
            raise UpdateFailed(str(err)) from err
        return AnchorData.from_api(raw_notes, dt_util.get_default_time_zone())

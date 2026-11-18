"""Diagnostics download for Anchor Notes (token redacted, no note text)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_TOKEN
from homeassistant.core import HomeAssistant

from .coordinator import AnchorConfigEntry

TO_REDACT = {CONF_API_TOKEN, "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AnchorConfigEntry
) -> dict[str, Any]:
    """Counts and settings only; titles and content stay out of the file."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "server_version": coordinator.server_version,
        "last_update_success": coordinator.last_update_success,
        "notes": len(data.notes),
        "pinned": len(data.pinned()),
        "with_reminder": len(data.reminders()),
    }

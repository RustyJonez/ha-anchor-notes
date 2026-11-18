"""Per-account dashboard switch: show newly created or recently edited notes."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import AnchorConfigEntry, AnchorCoordinator
from .entity import device_info, identity_attributes

PARALLEL_UPDATES = 0
RECENT_VIEW_OPTIONS = ["created", "edited"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnchorConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the recent-view select for one Anchor account."""
    async_add_entities([AnchorRecentViewSelect(entry.runtime_data)])


class AnchorRecentViewSelect(SelectEntity, RestoreEntity):
    """Local UI state only; nothing is sent to Anchor. Cycle it with select.select_next."""

    _attr_has_entity_name = True
    _attr_translation_key = "recent_view"
    _attr_options = RECENT_VIEW_OPTIONS
    _attr_current_option = RECENT_VIEW_OPTIONS[0]

    def __init__(self, coordinator: AnchorCoordinator) -> None:
        key = "recent_view"
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{key}"
        self._attr_device_info = device_info(coordinator)
        self._attr_extra_state_attributes = identity_attributes(coordinator, key)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in RECENT_VIEW_OPTIONS:
            self._attr_current_option = last.state

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        self.async_write_ha_state()

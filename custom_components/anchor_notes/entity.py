"""Bits shared by every Anchor Notes entity."""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_URL
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import ATTR_ANCHOR_SENSOR, ATTR_HA_USER_ID, CONF_HA_USER, DOMAIN
from .coordinator import AnchorCoordinator


def device_info(coordinator: AnchorCoordinator) -> DeviceInfo:
    """One device per Anchor account."""
    entry = coordinator.config_entry
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Anchor {entry.title}",
        manufacturer="Anchor",
        model="Notes account",
        sw_version=coordinator.server_version,
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=entry.data[CONF_URL],
    )


def identity_attributes(coordinator: AnchorCoordinator, key: str) -> dict[str, Any]:
    """Lets dashboards find the viewer's entities by HA user, whatever they're named."""
    return {
        ATTR_ANCHOR_SENSOR: key,
        ATTR_HA_USER_ID: coordinator.config_entry.options.get(CONF_HA_USER),
    }

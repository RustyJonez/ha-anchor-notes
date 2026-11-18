"""Anchor Notes sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_NOTE_ID,
    ATTR_NOTES,
    ATTR_OVERDUE,
    ATTR_OVERDUE_COUNT,
    ATTR_RECURRENCE,
    ATTR_TITLE,
    ATTR_UPCOMING,
    CONF_EXCLUDE_PINNED,
    CONF_PINNED_LIMIT,
    CONF_RECENT_COUNT,
    CONF_REMINDER_LIMIT,
    DEFAULT_EXCLUDE_PINNED,
    DEFAULT_PINNED_LIMIT,
    DEFAULT_RECENT_COUNT,
    DEFAULT_REMINDER_LIMIT,
    DOMAIN,
)
from .coordinator import AnchorConfigEntry, AnchorCoordinator
from .models import WALL_FORMAT, AnchorData, Note

PARALLEL_UPDATES = 0  # all sensors share one coordinator fetch


@dataclass(frozen=True, slots=True)
class View:
    """What a sensor needs to compute its state at write time."""

    data: AnchorData
    now_wall: str
    recent_count: int
    pinned_limit: int
    reminder_limit: int
    exclude_pinned: bool

    @property
    def overdue(self) -> list[Note]:
        return self.data.split_reminders(self.now_wall)[0]

    @property
    def upcoming(self) -> list[Note]:
        return self.data.split_reminders(self.now_wall)[1]

    @property
    def next_reminder(self) -> Note | None:
        upcoming = self.upcoming
        return upcoming[0] if upcoming else None

    def created(self) -> list[Note]:
        return self.data.recent_created(exclude_pinned=self.exclude_pinned)[
            : self.recent_count
        ]

    def edited(self) -> list[Note]:
        return self.data.recent_edited(exclude_pinned=self.exclude_pinned)[
            : self.recent_count
        ]


@dataclass(frozen=True, kw_only=True)
class AnchorSensorDescription(SensorEntityDescription):
    """Sensor description with value and attribute functions."""

    value_fn: Callable[[View], int | datetime | None]
    attrs_fn: Callable[[View], dict[str, Any]]


def _next_attrs(view: View) -> dict[str, Any]:
    note = view.next_reminder
    return {
        ATTR_TITLE: note.title if note else None,
        ATTR_NOTE_ID: note.id if note else None,
        ATTR_RECURRENCE: note.reminder.recurrence if note and note.reminder else None,
    }


def _first(notes: list[Note], field: str) -> datetime | None:
    return getattr(notes[0], field) if notes else None


SENSORS: tuple[AnchorSensorDescription, ...] = (
    AnchorSensorDescription(
        key="pending_reminders",
        translation_key="pending_reminders",
        value_fn=lambda v: len(v.data.reminders()),
        attrs_fn=lambda v: {
            ATTR_OVERDUE_COUNT: len(v.overdue),
            ATTR_OVERDUE: [n.reminder_dict() for n in v.overdue],
            ATTR_UPCOMING: [n.reminder_dict() for n in v.upcoming[: v.reminder_limit]],
        },
    ),
    AnchorSensorDescription(
        key="next_reminder",
        translation_key="next_reminder",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda v: (
            v.next_reminder.reminder.at
            if v.next_reminder and v.next_reminder.reminder
            else None
        ),
        attrs_fn=_next_attrs,
    ),
    AnchorSensorDescription(
        key="pinned_notes",
        translation_key="pinned_notes",
        value_fn=lambda v: len(v.data.pinned()),
        attrs_fn=lambda v: {
            ATTR_NOTES: [n.as_dict() for n in v.data.pinned()[: v.pinned_limit]]
        },
    ),
    AnchorSensorDescription(
        key="recent_created",
        translation_key="recent_created",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda v: _first(v.created(), "created"),
        attrs_fn=lambda v: {ATTR_NOTES: [n.as_dict() for n in v.created()]},
    ),
    AnchorSensorDescription(
        key="recent_edited",
        translation_key="recent_edited",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda v: _first(v.edited(), "updated"),
        attrs_fn=lambda v: {ATTR_NOTES: [n.as_dict() for n in v.edited()]},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnchorConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors for one Anchor account."""
    coordinator = entry.runtime_data
    async_add_entities(AnchorSensor(coordinator, d) for d in SENSORS)


class AnchorSensor(CoordinatorEntity[AnchorCoordinator], SensorEntity):
    """One Anchor sensor; state and attributes are derived from the shared fetch."""

    entity_description: AnchorSensorDescription
    _attr_has_entity_name = True
    # Note lists change constantly and aren't useful history.
    _unrecorded_attributes = frozenset({ATTR_NOTES, ATTR_OVERDUE, ATTR_UPCOMING})

    def __init__(
        self, coordinator: AnchorCoordinator, description: AnchorSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Anchor {entry.title}",
            manufacturer="Anchor",
            model="Notes account",
            sw_version=coordinator.server_version,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=entry.data[CONF_URL],
        )

    def _view(self) -> View:
        options = self.coordinator.config_entry.options
        return View(
            data=self.coordinator.data,
            now_wall=dt_util.now().strftime(WALL_FORMAT),
            recent_count=int(options.get(CONF_RECENT_COUNT, DEFAULT_RECENT_COUNT)),
            pinned_limit=int(options.get(CONF_PINNED_LIMIT, DEFAULT_PINNED_LIMIT)),
            reminder_limit=int(
                options.get(CONF_REMINDER_LIMIT, DEFAULT_REMINDER_LIMIT)
            ),
            exclude_pinned=bool(
                options.get(CONF_EXCLUDE_PINNED, DEFAULT_EXCLUDE_PINNED)
            ),
        )

    @property
    def native_value(self) -> int | datetime | None:
        return self.entity_description.value_fn(self._view())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.entity_description.attrs_fn(self._view())

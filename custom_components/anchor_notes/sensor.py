"""Anchor Notes sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import AnchorAuthError, AnchorError, AnchorNotFoundError
from .const import (
    ATTR_ERROR,
    ATTR_NOTE,
    ATTR_NOTE_ID,
    ATTR_NOTES,
    ATTR_OVERDUE,
    ATTR_OVERDUE_COUNT,
    ATTR_RECURRENCE,
    ATTR_STATUS,
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
    LOGGER,
    SERVICE_CLEAR_NOTE,
    SERVICE_SHOW_NOTE,
)
from .coordinator import AnchorConfigEntry, AnchorCoordinator
from .entity import device_info, identity_attributes
from .models import WALL_FORMAT, AnchorData, Note, parse_note_detail

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
    async_add_entities(
        [
            *(AnchorSensor(coordinator, d) for d in SENSORS),
            AnchorNoteViewer(coordinator),
        ]
    )

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SHOW_NOTE,
        {vol.Required(ATTR_NOTE_ID): cv.string},
        "async_show_note",
    )
    platform.async_register_entity_service(SERVICE_CLEAR_NOTE, None, "async_clear_note")


class _NotAViewer:
    """The note actions only work on the Note viewer sensor; say so clearly."""

    async def async_show_note(self, note_id: str) -> None:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_note_viewer"
        )

    async def async_clear_note(self) -> None:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_note_viewer"
        )


class AnchorSensor(_NotAViewer, CoordinatorEntity[AnchorCoordinator], SensorEntity):
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
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{description.key}"
        self._attr_device_info = device_info(coordinator)
        self._identity = identity_attributes(coordinator, description.key)

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
        return {**self._identity, **self.entity_description.attrs_fn(self._view())}


class AnchorNoteViewer(SensorEntity):
    """One note, fetched in full when asked (action anchor_notes.show_note).

    State is when it was loaded. The note itself is an attribute the recorder skips,
    so opening notes leaves nothing but a timestamp in history.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "note_view"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _unrecorded_attributes = frozenset({ATTR_NOTE, ATTR_NOTE_ID, ATTR_STATUS, ATTR_ERROR})

    def __init__(self, coordinator: AnchorCoordinator) -> None:
        self.coordinator = coordinator
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_note_view"
        self._attr_device_info = device_info(coordinator)
        self._identity = identity_attributes(coordinator, "note_view")
        self._request = 0
        self._reset()

    def _reset(self) -> None:
        self._status = "idle"
        self._note_id: str | None = None
        self._note: dict[str, Any] | None = None
        self._error: str | None = None
        self._attr_native_value = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            **self._identity,
            ATTR_STATUS: self._status,
            ATTR_NOTE_ID: self._note_id,
            ATTR_NOTE: self._note,
            ATTR_ERROR: self._error,
        }

    async def async_show_note(self, note_id: str) -> None:
        """Fetch the note now; dashboards show 'loading' until it arrives."""
        self._request += 1
        request = self._request
        self._reset()
        self._status, self._note_id = "loading", note_id
        self.async_write_ha_state()
        try:
            raw = await self.coordinator.client.get_note(note_id)
            note = parse_note_detail(raw, dt_util.get_default_time_zone())
        except AnchorNotFoundError:
            status, note, error = "not_found", None, None
        except AnchorAuthError:
            status, note, error = "error", None, "Anchor rejected the API token"
            self.coordinator.config_entry.async_start_reauth(self.hass)
        except AnchorError as err:
            LOGGER.debug("Fetching note %s failed: %s", note_id, err)
            status, note, error = "error", None, str(err)
        else:
            status, error = "ready", None
        if request != self._request:  # another note was asked for meanwhile
            return
        self._status, self._note, self._error = status, note, error
        self._attr_native_value = dt_util.utcnow() if note else None
        self.async_write_ha_state()

    async def async_clear_note(self) -> None:
        """Forget the shown note."""
        self._request += 1
        self._reset()
        self.async_write_ha_state()

"""Constants for the Anchor Notes integration."""

from __future__ import annotations

import logging

DOMAIN = "anchor_notes"
LOGGER = logging.getLogger(__package__)

# Options (set via the integration's Configure button).
CONF_HA_USER = "ha_user_id"  # Home Assistant user this Anchor account belongs to
CONF_SCAN_INTERVAL = "scan_interval"
CONF_RECENT_COUNT = "recent_count"
CONF_PINNED_LIMIT = "pinned_limit"
CONF_REMINDER_LIMIT = "reminder_limit"
CONF_EXCLUDE_PINNED = "exclude_pinned_from_recent"

DEFAULT_SCAN_INTERVAL = 5  # minutes
DEFAULT_RECENT_COUNT = 5
DEFAULT_PINNED_LIMIT = 10
DEFAULT_REMINDER_LIMIT = 10
DEFAULT_EXCLUDE_PINNED = True

# On every entity, so dashboards can find "my" Anchor entities without knowing entity IDs:
#   Object.values(states).filter(s => s.attributes.ha_user_id === user.id && s.attributes.anchor_sensor)
ATTR_HA_USER_ID = "ha_user_id"
ATTR_ANCHOR_SENSOR = "anchor_sensor"  # the entity's key, e.g. "pending_reminders"

# Sensor attribute names.
ATTR_NOTES = "notes"
ATTR_OVERDUE = "overdue"
ATTR_OVERDUE_COUNT = "overdue_count"
ATTR_UPCOMING = "upcoming"
ATTR_TITLE = "title"
ATTR_NOTE_ID = "note_id"
ATTR_RECURRENCE = "recurrence"

"""Parse Anchor API notes into the shapes the sensors expose.

No Home Assistant imports here, so this module can be tested on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
import json
import re
from typing import Any

# Anchor stores reminders as local wall-clock text with no zone, e.g. "2026-09-04T09:00".
WALL_FORMAT = "%Y-%m-%dT%H:%M"
PREVIEW_LENGTH = 80
TITLE_FALLBACK_LENGTH = 40
UNTITLED = "Untitled"

_WHITESPACE = re.compile(r"\s+")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def delta_to_text(content: Any) -> str:
    """Flatten a stored Quill delta ('{"ops": [...]}') to one line of plain text."""
    if not isinstance(content, str) or not content:
        return ""
    try:
        parsed = json.loads(content)
    except ValueError:
        return ""
    ops = parsed.get("ops") if isinstance(parsed, dict) else None
    if not isinstance(ops, list):
        return ""
    parts = [
        op["insert"]
        for op in ops
        if isinstance(op, dict) and isinstance(op.get("insert"), str)
    ]
    return _WHITESPACE.sub(" ", " ".join(parts)).strip()


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


@dataclass(frozen=True, slots=True)
class Reminder:
    """A user's reminder on a note."""

    wall: str  # exactly as Anchor stores it; compared against local wall-clock now
    at: datetime  # same moment, with the Home Assistant time zone attached
    recurrence: str


@dataclass(frozen=True, slots=True)
class Note:
    """The parts of an Anchor note worth showing on a dashboard."""

    id: str
    title: str
    preview: str
    color: str | None
    pinned: bool
    created: datetime | None
    updated: datetime | None
    shared_by: str | None
    reminder: Reminder | None

    def as_dict(self) -> dict[str, Any]:
        """Attribute form used in the sensors' note lists."""
        return {
            "id": self.id,
            "title": self.title,
            "preview": self.preview,
            "color": self.color,
            "created": _iso(self.created),
            "updated": _iso(self.updated),
            "shared_by": self.shared_by,
            "reminder": (
                {
                    "remind_at": self.reminder.at.isoformat(),
                    "recurrence": self.reminder.recurrence,
                }
                if self.reminder
                else None
            ),
        }

    def reminder_dict(self) -> dict[str, Any]:
        """Attribute form used in the reminder lists (only for notes with a reminder)."""
        assert self.reminder is not None
        return {
            "id": self.id,
            "title": self.title,
            "color": self.color,
            "remind_at": self.reminder.at.isoformat(),
            "recurrence": self.reminder.recurrence,
            "shared_by": self.shared_by,
        }


def parse_reminder(raw: Any, tz: tzinfo) -> Reminder | None:
    """Parse a note's `reminder` field; None if absent or malformed."""
    if not isinstance(raw, dict):
        return None
    wall = raw.get("remindAt")
    if not isinstance(wall, str):
        return None
    try:
        at = datetime.strptime(wall, WALL_FORMAT).replace(tzinfo=tz)
    except ValueError:
        return None
    return Reminder(wall=wall, at=at, recurrence=str(raw.get("recurrence") or "none"))


def parse_note(raw: dict[str, Any], tz: tzinfo) -> Note:
    """Parse one note from GET /api/notes."""
    text = delta_to_text(raw.get("content"))
    title = str(raw.get("title") or "").strip()
    if not title:
        title = text[:TITLE_FALLBACK_LENGTH] if text else UNTITLED
    shared = raw.get("sharedBy")
    return Note(
        id=str(raw.get("id")),
        title=title,
        preview=text[:PREVIEW_LENGTH],
        color=raw.get("background") or None,
        pinned=bool(raw.get("isPinned")),
        created=_parse_timestamp(raw.get("createdAt")),
        updated=_parse_timestamp(raw.get("updatedAt")),
        shared_by=shared.get("name") if isinstance(shared, dict) else None,
        reminder=parse_reminder(raw.get("reminder"), tz),
    )


def owner_id_from_notes(raw_notes: list[dict[str, Any]]) -> str | None:
    """The API token's user ID, taken from any note that user owns.

    Anchor has no "who am I" endpoint that accepts API tokens.
    """
    for raw in raw_notes:
        user_id = raw.get("userId")
        if raw.get("permission") == "owner" and isinstance(user_id, str):
            return user_id
    return None


class AnchorData:
    """One fetch of a user's notes, with the views the sensors need."""

    def __init__(self, notes: list[Note]) -> None:
        self.notes = notes

    @classmethod
    def from_api(cls, raw_notes: list[dict[str, Any]], tz: tzinfo) -> AnchorData:
        return cls([parse_note(raw, tz) for raw in raw_notes])

    def pinned(self) -> list[Note]:
        """Pinned notes, most recently edited first."""
        return sorted(
            (n for n in self.notes if n.pinned),
            key=lambda n: n.updated or _EPOCH,
            reverse=True,
        )

    def recent_created(self, *, exclude_pinned: bool) -> list[Note]:
        """Notes, newest first."""
        return sorted(
            self._candidates(exclude_pinned),
            key=lambda n: n.created or _EPOCH,
            reverse=True,
        )

    def recent_edited(self, *, exclude_pinned: bool) -> list[Note]:
        """Notes, most recently edited first."""
        return sorted(
            self._candidates(exclude_pinned),
            key=lambda n: n.updated or _EPOCH,
            reverse=True,
        )

    def reminders(self) -> list[Note]:
        """Notes with a reminder, soonest first."""
        return sorted(
            (n for n in self.notes if n.reminder),
            key=lambda n: n.reminder.wall,  # type: ignore[union-attr]
        )

    def split_reminders(self, now_wall: str) -> tuple[list[Note], list[Note]]:
        """(overdue, upcoming) relative to a local wall-clock time in WALL_FORMAT."""
        overdue: list[Note] = []
        upcoming: list[Note] = []
        for note in self.reminders():
            assert note.reminder is not None
            (overdue if note.reminder.wall < now_wall else upcoming).append(note)
        return overdue, upcoming

    def _candidates(self, exclude_pinned: bool) -> list[Note]:
        return [n for n in self.notes if not (exclude_pinned and n.pinned)]

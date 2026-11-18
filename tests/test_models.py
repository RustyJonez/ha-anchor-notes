"""Tests for note parsing and the sensor views (no Home Assistant needed)."""

from __future__ import annotations

from datetime import datetime
import json
from zoneinfo import ZoneInfo

from custom_components.anchor_notes.models import (
    UNTITLED,
    AnchorData,
    delta_to_text,
    owner_id_from_notes,
    parse_note,
)

TZ = ZoneInfo("America/New_York")


def _content(*inserts: object) -> str:
    return json.dumps({"ops": [{"insert": i} for i in inserts]})


def _raw(note_id: str, **overrides: object) -> dict:
    raw = {
        "id": note_id,
        "title": f"Note {note_id}",
        "content": None,
        "isPinned": False,
        "background": None,
        "createdAt": "2026-11-01T12:00:00.000Z",
        "updatedAt": "2026-11-01T12:00:00.000Z",
        "userId": "user-1",
        "permission": "owner",
        "reminder": None,
    }
    raw.update(overrides)
    return raw


def test_delta_to_text_flattens_and_skips_embeds() -> None:
    content = _content("Milk\n", {"image": "x.png"}, "Eggs\n\n")
    assert delta_to_text(content) == "Milk Eggs"


def test_delta_to_text_handles_garbage() -> None:
    assert delta_to_text(None) == ""
    assert delta_to_text("") == ""
    assert delta_to_text("plain text, not json") == ""
    assert delta_to_text(json.dumps({"no": "ops"})) == ""


def test_blank_title_falls_back_to_text_then_untitled() -> None:
    assert parse_note(_raw("1", title="", content=_content("Buy milk\n")), TZ).title == (
        "Buy milk"
    )
    assert parse_note(_raw("2", title="  "), TZ).title == UNTITLED


def test_reminder_wall_clock_gets_local_zone_including_dst() -> None:
    summer = parse_note(
        _raw("1", reminder={"remindAt": "2026-07-04T09:00", "recurrence": "none"}), TZ
    )
    winter = parse_note(
        _raw("2", reminder={"remindAt": "2026-12-04T09:00", "recurrence": "weekly"}), TZ
    )
    assert summer.reminder is not None and winter.reminder is not None
    assert summer.reminder.at.isoformat() == "2026-07-04T09:00:00-04:00"
    assert winter.reminder.at.isoformat() == "2026-12-04T09:00:00-05:00"
    assert winter.reminder.recurrence == "weekly"


def test_malformed_reminder_is_ignored() -> None:
    note = parse_note(_raw("1", reminder={"remindAt": "tomorrow"}), TZ)
    assert note.reminder is None


def test_shared_by_name() -> None:
    note = parse_note(
        _raw("1", permission="editor", sharedBy={"id": "u2", "name": "Sam"}), TZ
    )
    assert note.shared_by == "Sam"


def test_split_reminders_overdue_and_upcoming_sorted() -> None:
    data = AnchorData.from_api(
        [
            _raw("late", reminder={"remindAt": "2026-11-17T08:00"}),
            _raw("soon", reminder={"remindAt": "2026-11-17T18:00"}),
            _raw("later", reminder={"remindAt": "2026-11-20T09:00"}),
            _raw("none"),
        ],
        TZ,
    )
    overdue, upcoming = data.split_reminders("2026-11-17T12:00")
    assert [n.id for n in overdue] == ["late"]
    assert [n.id for n in upcoming] == ["soon", "later"]
    # A reminder at exactly "now" is still upcoming.
    _, upcoming = data.split_reminders("2026-11-17T18:00")
    assert upcoming[0].id == "soon"


def test_recent_lists_sort_and_exclude_pinned() -> None:
    data = AnchorData.from_api(
        [
            _raw("old", createdAt="2026-01-01T00:00:00Z", updatedAt="2026-11-16T00:00:00Z"),
            _raw("new", createdAt="2026-11-10T00:00:00Z", updatedAt="2026-11-10T00:00:00Z"),
            _raw(
                "pinned",
                isPinned=True,
                createdAt="2026-11-15T00:00:00Z",
                updatedAt="2026-11-15T00:00:00Z",
            ),
        ],
        TZ,
    )
    assert [n.id for n in data.recent_created(exclude_pinned=True)] == ["new", "old"]
    assert [n.id for n in data.recent_created(exclude_pinned=False)] == [
        "pinned",
        "new",
        "old",
    ]
    assert [n.id for n in data.recent_edited(exclude_pinned=True)] == ["old", "new"]
    assert [n.id for n in data.pinned()] == ["pinned"]


def test_as_dict_shapes() -> None:
    note = parse_note(
        _raw(
            "1",
            content=_content("Hello\n"),
            background="yellow",
            reminder={"remindAt": "2026-12-04T09:00", "recurrence": "none"},
        ),
        TZ,
    )
    assert note.as_dict() == {
        "id": "1",
        "title": "Note 1",
        "preview": "Hello",
        "color": "yellow",
        "created": datetime(2026, 11, 1, 12, tzinfo=ZoneInfo("UTC")).isoformat(),
        "updated": datetime(2026, 11, 1, 12, tzinfo=ZoneInfo("UTC")).isoformat(),
        "shared_by": None,
        "reminder": {"remind_at": "2026-12-04T09:00:00-05:00", "recurrence": "none"},
    }
    assert note.reminder_dict()["remind_at"] == "2026-12-04T09:00:00-05:00"


def test_owner_id_ignores_shared_notes() -> None:
    raw = [_raw("1", userId="someone-else", permission="viewer"), _raw("2")]
    assert owner_id_from_notes(raw) == "user-1"
    assert owner_id_from_notes([raw[0]]) is None

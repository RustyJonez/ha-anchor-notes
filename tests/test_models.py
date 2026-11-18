"""Tests for note parsing and the sensor views (no Home Assistant needed)."""

from __future__ import annotations

from datetime import datetime
import json
from zoneinfo import ZoneInfo

from custom_components.anchor_notes.models import (
    UNTITLED,
    AnchorData,
    MAX_LINES,
    DETAIL_TEXT_LENGTH,
    delta_to_blocks,
    delta_to_lines,
    delta_to_text,
    owner_id_from_notes,
    parse_note,
    parse_note_detail,
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
        "lines": [
            {"text": "Hello", "list": None, "indent": 0, "header": False, "number": None}
        ],
        "line_count": 1,
        "reminder": {"remind_at": "2026-12-04T09:00:00-05:00", "recurrence": "none"},
    }
    assert note.reminder_dict()["remind_at"] == "2026-12-04T09:00:00-05:00"


def test_owner_id_ignores_shared_notes() -> None:
    raw = [_raw("1", userId="someone-else", permission="viewer"), _raw("2")]
    assert owner_id_from_notes(raw) == "user-1"
    assert owner_id_from_notes([raw[0]]) is None


def _ops(*ops: dict) -> str:
    return json.dumps({"ops": list(ops)})


def test_lines_keep_list_formats() -> None:
    content = _ops(
        {"insert": "Shopping"},
        {"insert": "\n", "attributes": {"header": 2}},
        {"insert": "Milk"},
        {"insert": "\n", "attributes": {"list": "checked"}},
        {"insert": "Eggs"},
        {"insert": "\n", "attributes": {"list": "unchecked"}},
        {"insert": "Vents"},
        {"insert": "\n", "attributes": {"list": "bullet"}},
        {"insert": "\n"},
        {"insert": "Plain paragraph\n"},
    )
    lines = delta_to_lines(content)
    assert [(ln.text, ln.list, ln.header) for ln in lines] == [
        ("Shopping", None, True),
        ("Milk", "checked", False),
        ("Eggs", "unchecked", False),
        ("Vents", "bullet", False),
        ("Plain paragraph", None, False),
    ]
    # The flat preview still works, now without run-ons from empty lines.
    assert delta_to_text(content) == "Shopping Milk Eggs Vents Plain paragraph"


def test_ordered_lists_are_numbered_per_indent_and_restart() -> None:
    content = _ops(
        {"insert": "One"},
        {"insert": "\n", "attributes": {"list": "ordered"}},
        {"insert": "One-a"},
        {"insert": "\n", "attributes": {"list": "ordered", "indent": 1}},
        {"insert": "One-b"},
        {"insert": "\n", "attributes": {"list": "ordered", "indent": 1}},
        {"insert": "Two"},
        {"insert": "\n", "attributes": {"list": "ordered"}},
        {"insert": "Break\n"},
        {"insert": "Again"},
        {"insert": "\n", "attributes": {"list": "ordered"}},
    )
    got = [(ln.text, ln.indent, ln.number) for ln in delta_to_lines(content)]
    assert got == [
        ("One", 0, 1),
        ("One-a", 1, 1),
        ("One-b", 1, 2),
        ("Two", 0, 2),
        ("Break", 0, None),
        ("Again", 0, 1),
    ]


def test_formatted_text_split_across_ops_is_one_line() -> None:
    content = _ops(
        {"insert": "Call "},
        {"insert": "dentist", "attributes": {"bold": True}},
        {"insert": " today"},
        {"insert": "\n", "attributes": {"list": "bullet"}},
    )
    assert [(ln.text, ln.list) for ln in delta_to_lines(content)] == [
        ("Call dentist today", "bullet")
    ]


def test_note_keeps_first_lines_and_counts_all() -> None:
    body = "".join(f"Item {i}\n" for i in range(MAX_LINES + 3))
    note = parse_note(_raw("1", content=_content(body)), TZ)
    assert len(note.lines) == MAX_LINES
    assert note.line_count == MAX_LINES + 3
    assert note.as_dict()["lines"][0]["text"] == "Item 0"


def test_blank_title_uses_first_line_not_whole_body() -> None:
    note = parse_note(_raw("1", title="", content=_content("Groceries\nMilk\nEggs\n")), TZ)
    assert note.title == "Groceries"


def _ops(*ops: dict) -> str:
    return json.dumps({"ops": list(ops)})


def test_blocks_keep_inline_and_line_formats() -> None:
    blocks = delta_to_blocks(
        _ops(
            {"insert": "Plan"},
            {"insert": "\n", "attributes": {"header": 2}},
            {"insert": "Buy "},
            {"insert": "milk", "attributes": {"bold": True, "italic": True}},
            {"insert": " at "},
            {"insert": "store", "attributes": {"link": "https://shop.example"}},
            {"insert": "\n", "attributes": {"list": "unchecked"}},
            {"insert": "first\nsecond\n", "attributes": {}},
            {"insert": "\n\n\nquoted"},
            {"insert": "\n", "attributes": {"blockquote": True}},
            {"insert": "  x = 1"},
            {"insert": "\n", "attributes": {"code-block": "plain"}},
            {"insert": "bad", "attributes": {"link": "javascript:alert(1)"}},
            {"insert": {"image": "a.png"}},
            {"insert": "\n\n"},
        )
    )
    assert blocks[0]["header"] == 2 and blocks[0]["spans"] == [{"text": "Plan"}]
    assert blocks[1]["list"] == "unchecked"
    assert blocks[1]["spans"] == [
        {"text": "Buy "},
        {"text": "milk", "bold": True, "italic": True},
        {"text": " at "},
        {"text": "store", "link": "https://shop.example"},
    ]
    assert [b["spans"][0]["text"] for b in blocks[2:4]] == ["first", "second"]
    assert blocks[4]["spans"] == []  # one blank line kept for the paragraph break
    assert blocks[5]["quote"] and blocks[5]["spans"][0]["text"] == "quoted"
    assert blocks[6]["code"] and blocks[6]["spans"][0]["text"] == "  x = 1"
    assert blocks[7]["spans"] == [{"text": "bad"}, {"text": "[image]", "embed": True}]
    assert len(blocks) == 8  # trailing blank lines dropped


def test_blocks_number_ordered_lists() -> None:
    blocks = delta_to_blocks(
        _ops(
            {"insert": "a"},
            {"insert": "\n", "attributes": {"list": "ordered"}},
            {"insert": "b"},
            {"insert": "\n", "attributes": {"list": "ordered"}},
            {"insert": "b1"},
            {"insert": "\n", "attributes": {"list": "ordered", "indent": 1}},
            {"insert": "c"},
            {"insert": "\n", "attributes": {"list": "ordered"}},
        )
    )
    assert [b["number"] for b in blocks] == [1, 2, 1, 3]


def test_note_detail() -> None:
    raw = _raw(
        "n1",
        title="",
        content=_ops({"insert": "x" * (DETAIL_TEXT_LENGTH + 50) + "\nmore\n"}),
        isArchived=True,
        state="active",
        attachmentCount=2,
        reminder={"remindAt": "2026-11-20T09:00", "recurrence": "weekly"},
    )
    detail = parse_note_detail(raw, TZ)
    assert detail["title"] == ""
    assert detail["archived"] and not detail["trashed"]
    assert detail["attachments"] == 2
    assert detail["reminder"]["recurrence"] == "weekly"
    assert detail["truncated"]
    assert len(detail["blocks"]) == 1
    assert len(detail["blocks"][0]["spans"][0]["text"]) == DETAIL_TEXT_LENGTH

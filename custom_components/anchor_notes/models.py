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


# How much of each note's body is kept for dashboards (the full rich text never leaves the integration).
MAX_LINES = 6
LINE_LENGTH = 100
LIST_KINDS = ("bullet", "ordered", "checked", "unchecked")


@dataclass(frozen=True, slots=True)
class Line:
    """One line of a note body, with Anchor's (Quill's) line format."""

    text: str
    list: str | None = None  # bullet | ordered | checked | unchecked
    indent: int = 0
    header: bool = False
    number: int | None = None  # position within a numbered list

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "list": self.list,
            "indent": self.indent,
            "header": self.header,
            "number": self.number,
        }


def _load_ops(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, str) or not content:
        return []
    try:
        parsed = json.loads(content)
    except ValueError:
        return []
    ops = parsed.get("ops") if isinstance(parsed, dict) else None
    return [op for op in ops if isinstance(op, dict)] if isinstance(ops, list) else []


def delta_to_lines(content: Any) -> list[Line]:
    """Split a stored Quill delta into non-empty lines, keeping list/indent/header formats.

    In Quill, a line's block format is carried by the newline that ends it.
    Embeds (images etc.) are skipped.
    """
    lines: list[Line] = []
    current = ""
    for op in _load_ops(content):
        insert = op.get("insert")
        if not isinstance(insert, str):
            continue
        attrs = op.get("attributes") if isinstance(op.get("attributes"), dict) else {}
        parts = insert.split("\n")
        for i, part in enumerate(parts):
            current += part
            if i == len(parts) - 1:
                break  # no newline after the last part: the line continues in the next op
            text = _WHITESPACE.sub(" ", current).strip()
            current = ""
            if not text:
                continue
            kind = attrs.get("list")
            try:
                indent = max(0, int(attrs.get("indent") or 0))
            except (TypeError, ValueError):
                indent = 0
            lines.append(
                Line(
                    text=text,
                    list=kind if kind in LIST_KINDS else None,
                    indent=indent,
                    header=bool(attrs.get("header")),
                )
            )
    tail = _WHITESPACE.sub(" ", current).strip()
    if tail:
        lines.append(Line(text=tail))
    return _number_ordered(lines)


def _number_ordered(lines: list[Line]) -> list[Line]:
    """Give ordered-list lines their 1, 2, 3… per indent level, restarting after other lines."""
    counters: dict[int, int] = {}
    out: list[Line] = []
    for line in lines:
        if line.list == "ordered":
            counters = {k: v for k, v in counters.items() if k <= line.indent}
            counters[line.indent] = counters.get(line.indent, 0) + 1
            out.append(
                Line(line.text, line.list, line.indent, line.header, counters[line.indent])
            )
        else:
            if line.list is None or line.indent == 0:
                counters = {}
            out.append(line)
    return out


def delta_to_text(content: Any) -> str:
    """Flatten a stored Quill delta ('{"ops": [...]}') to one line of plain text."""
    return " ".join(line.text for line in delta_to_lines(content))


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
    lines: tuple[Line, ...] = ()
    line_count: int = 0

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
            "lines": [line.as_dict() for line in self.lines],
            "line_count": self.line_count,
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
    lines = delta_to_lines(raw.get("content"))
    text = " ".join(line.text for line in lines)
    title = str(raw.get("title") or "").strip()
    if not title:
        title = lines[0].text[:TITLE_FALLBACK_LENGTH] if lines else UNTITLED
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
        lines=tuple(
            Line(line.text[:LINE_LENGTH], line.list, line.indent, line.header, line.number)
            for line in lines[:MAX_LINES]
        ),
        line_count=len(lines),
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


# ── Full note, fetched on demand for the note viewer ──────────────────────────
# Kept in attributes the recorder skips, so none of this ends up in history.
DETAIL_MAX_LINES = 400
DETAIL_TEXT_LENGTH = 20_000  # whole note; the rest is cut and `truncated` is set
INLINE_FLAGS = ("bold", "italic", "underline", "strike", "highlight")
SAFE_LINK = re.compile(r"^(https?:|mailto:|tel:)", re.IGNORECASE)


def _span(text: str, attrs: dict[str, Any]) -> dict[str, Any]:
    span: dict[str, Any] = {"text": text}
    for flag in INLINE_FLAGS:
        if attrs.get(flag):
            span[flag] = True
    link = attrs.get("link")
    if isinstance(link, str) and SAFE_LINK.match(link.strip()):
        span["link"] = link.strip()
    return span


def _block(spans: list[dict[str, Any]], attrs: dict[str, Any]) -> dict[str, Any]:
    kind = attrs.get("list")
    try:
        indent = max(0, int(attrs.get("indent") or 0))
    except (TypeError, ValueError):
        indent = 0
    try:
        header = max(0, int(attrs.get("header") or 0))
    except (TypeError, ValueError):
        header = 0
    merged: list[dict[str, Any]] = []
    for span in spans:  # join neighbours with the same formatting
        if merged and {k: v for k, v in merged[-1].items() if k != "text"} == {
            k: v for k, v in span.items() if k != "text"
        }:
            merged[-1] = {**merged[-1], "text": merged[-1]["text"] + span["text"]}
        else:
            merged.append(span)
    return {
        "spans": merged,
        "list": kind if kind in LIST_KINDS else None,
        "indent": indent,
        "header": header,
        "quote": bool(attrs.get("blockquote")),
        "code": bool(attrs.get("code-block")),
        "number": None,
    }


def delta_to_blocks(content: Any) -> list[dict[str, Any]]:
    """Every line of a stored Quill delta with its inline and line formats.

    Blank lines are kept (once in a row) because they're paragraph breaks.
    """
    blocks: list[dict[str, Any]] = []
    spans: list[dict[str, Any]] = []
    for op in _load_ops(content):
        insert = op.get("insert")
        attrs = op.get("attributes") if isinstance(op.get("attributes"), dict) else {}
        if isinstance(insert, dict):  # embed (image, divider…): show a marker
            name = next(iter(insert), "embed")
            spans.append({"text": f"[{name}]", "embed": True})
            continue
        if not isinstance(insert, str):
            continue
        parts = insert.split("\n")
        for i, part in enumerate(parts):
            if part:
                spans.append(_span(part, attrs))
            if i == len(parts) - 1:
                break
            blocks.append(_block(spans, attrs))
            spans = []
    if spans:
        blocks.append(_block(spans, {}))

    def blank(b: dict[str, Any]) -> bool:
        return not b["code"] and not "".join(s["text"] for s in b["spans"]).strip()

    out: list[dict[str, Any]] = []
    for block in blocks:
        if blank(block) and (not out or blank(out[-1])):
            continue
        out.append(block)
    while out and blank(out[-1]):
        out.pop()

    counters: dict[int, int] = {}
    for block in out:
        if block["list"] == "ordered":
            counters = {k: v for k, v in counters.items() if k <= block["indent"]}
            counters[block["indent"]] = counters.get(block["indent"], 0) + 1
            block["number"] = counters[block["indent"]]
        elif block["list"] is None or block["indent"] == 0:
            counters = {}
    return out


def _limit_blocks(blocks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    kept: list[dict[str, Any]] = []
    budget = DETAIL_TEXT_LENGTH
    for block in blocks[:DETAIL_MAX_LINES]:
        spans = []
        for span in block["spans"]:
            if budget <= 0:
                break
            text = span["text"][:budget]
            budget -= len(text)
            spans.append({**span, "text": text})
        kept.append({**block, "spans": spans})
        if budget <= 0:
            break
    return kept, len(kept) < len(blocks) or budget <= 0


def parse_note_detail(raw: dict[str, Any], tz: tzinfo) -> dict[str, Any]:
    """One note from GET /api/notes/:id, shaped for the note viewer."""
    note = parse_note(raw, tz)
    blocks, truncated = _limit_blocks(delta_to_blocks(raw.get("content")))
    try:
        attachments = int(raw.get("attachmentCount") or 0)
    except (TypeError, ValueError):
        attachments = 0
    return {
        "id": note.id,
        "title": str(raw.get("title") or "").strip(),  # empty: Anchor shows the body only
        "color": note.color,
        "pinned": note.pinned,
        "archived": bool(raw.get("isArchived")),
        "trashed": raw.get("state") == "trashed",
        "permission": raw.get("permission"),
        "shared_by": note.shared_by,
        "created": _iso(note.created),
        "updated": _iso(note.updated),
        "reminder": note.as_dict()["reminder"],
        "attachments": attachments,
        "blocks": blocks,
        "truncated": truncated,
    }

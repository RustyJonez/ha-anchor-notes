"""Set up a real entry against a mocked Anchor and check the entities dashboards rely on."""

from __future__ import annotations

from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from homeassistant.const import CONF_API_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant

import pytest

from homeassistant.exceptions import ServiceValidationError

from custom_components.anchor_notes.api import AnchorNotFoundError
from custom_components.anchor_notes.const import CONF_HA_USER, DOMAIN

NOTES = [
    {
        "id": "n1",
        "title": "Groceries",
        "content": '{"ops":[{"insert":"Milk\\n"}]}',
        "isPinned": True,
        "background": "color_yellow",
        "createdAt": "2026-11-01T12:00:00.000Z",
        "updatedAt": "2026-11-02T12:00:00.000Z",
        "userId": "user-1",
        "permission": "owner",
        "reminder": None,
    },
    {
        "id": "n2",
        "title": "Dentist",
        "content": None,
        "isPinned": False,
        "background": None,
        "createdAt": "2026-11-03T12:00:00.000Z",
        "updatedAt": "2026-11-03T12:00:00.000Z",
        "userId": "user-1",
        "permission": "owner",
        "reminder": {"remindAt": "2099-01-01T09:00", "recurrence": "none", "version": 1},
    },
]
KEYS = {
    "pending_reminders",
    "next_reminder",
    "pinned_notes",
    "recent_created",
    "recent_edited",
    "recent_view",
    "note_view",
}


async def test_entities_are_linked_to_ha_user(hass: HomeAssistant) -> None:
    user = MockUser(name="Alex Doe").add_to_hass(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Alex",
        unique_id="user-1",
        data={CONF_URL: "http://anchor.local:3009", CONF_API_TOKEN: "t"},
        options={CONF_HA_USER: user.id},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.anchor_notes.AnchorClient", autospec=True) as cls:
        cls.return_value.get_health.return_value = {"app": "anchor", "version": "0.18.0"}
        cls.return_value.get_notes.return_value = NOTES
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # This is exactly how the dashboard cards find the viewer's entities.
    mine = {
        s.attributes["anchor_sensor"]: s
        for s in hass.states.async_all()
        if s.attributes.get("ha_user_id") == user.id and "anchor_sensor" in s.attributes
    }
    assert set(mine) == KEYS

    assert mine["pending_reminders"].state == "1"
    assert mine["pending_reminders"].attributes["upcoming"][0]["title"] == "Dentist"
    assert mine["pinned_notes"].state == "1"
    assert mine["pinned_notes"].attributes["notes"][0]["color"] == "color_yellow"
    assert [n["id"] for n in mine["recent_created"].attributes["notes"]] == ["n2"]
    assert mine["next_reminder"].state.startswith("2099-01-01T")
    assert mine["recent_view"].state == "created"

    # Entity IDs come from the device name ("Anchor Alex") + the entity name.
    assert mine["pending_reminders"].entity_id == "sensor.anchor_alex_pending_reminders"

    await hass.services.async_call(
        "select",
        "select_next",
        {"entity_id": mine["recent_view"].entity_id},
        blocking=True,
    )
    assert hass.states.get(mine["recent_view"].entity_id).state == "edited"


async def test_unlinked_entry_has_no_user(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Sam",
        data={CONF_URL: "http://anchor.local:3009", CONF_API_TOKEN: "t"},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.anchor_notes.AnchorClient", autospec=True) as cls:
        cls.return_value.get_health.return_value = {"app": "anchor"}
        cls.return_value.get_notes.return_value = []
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    anchor = [s for s in hass.states.async_all() if "anchor_sensor" in s.attributes]
    assert len(anchor) == len(KEYS)
    assert all(s.attributes["ha_user_id"] is None for s in anchor)


async def test_note_viewer(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Alex",
        unique_id="user-1",
        data={CONF_URL: "http://anchor.local:3009", CONF_API_TOKEN: "t"},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.anchor_notes.AnchorClient", autospec=True) as cls:
        client = cls.return_value
        client.get_health.return_value = {"app": "anchor"}
        client.get_notes.return_value = NOTES
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    viewer = "sensor.anchor_alex_note_viewer"
    state = hass.states.get(viewer)
    assert state.attributes["status"] == "idle"
    assert state.attributes["note"] is None

    client.get_note.return_value = {
        **NOTES[0],
        "content": '{"ops":[{"insert":"Milk","attributes":{"bold":true}},{"insert":"\\n"}]}',
    }
    await hass.services.async_call(
        DOMAIN, "show_note", {"note_id": "n1"}, target={"entity_id": viewer}, blocking=True
    )
    client.get_note.assert_awaited_with("n1")
    state = hass.states.get(viewer)
    assert state.attributes["status"] == "ready"
    assert state.attributes["note"]["title"] == "Groceries"
    assert state.attributes["note"]["blocks"][0]["spans"] == [{"text": "Milk", "bold": True}]
    assert state.state != "unknown"

    client.get_note.side_effect = AnchorNotFoundError("404")
    await hass.services.async_call(
        DOMAIN, "show_note", {"note_id": "gone"}, target={"entity_id": viewer}, blocking=True
    )
    state = hass.states.get(viewer)
    assert state.attributes["status"] == "not_found"
    assert state.attributes["note"] is None

    await hass.services.async_call(
        DOMAIN, "clear_note", target={"entity_id": viewer}, blocking=True
    )
    assert hass.states.get(viewer).attributes["status"] == "idle"

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "show_note",
            {"note_id": "n1"},
            target={"entity_id": "sensor.anchor_alex_pending_reminders"},
            blocking=True,
        )

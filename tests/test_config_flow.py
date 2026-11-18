"""Config flow tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_TOKEN, CONF_NAME, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.anchor_notes.api import AnchorAuthError, AnchorConnectionError
from custom_components.anchor_notes.const import CONF_HA_USER, CONF_RECENT_COUNT, DOMAIN

CLIENT = "custom_components.anchor_notes.config_flow.AnchorClient"
HEALTH = {"status": "ok", "app": "anchor", "version": "0.18.0"}
NOTES = [{"id": "n1", "userId": "user-1", "permission": "owner"}]
USER_INPUT = {
    CONF_NAME: "Alex",
    CONF_URL: "http://anchor.local:3009/api/",
    CONF_API_TOKEN: " token-1 ",
}


@pytest.fixture
def client() -> Generator[AsyncMock]:
    """A mocked AnchorClient that reports a healthy server and one owned note."""
    with patch(CLIENT, autospec=True) as cls:
        instance = cls.return_value
        instance.get_health.return_value = HEALTH
        instance.get_notes.return_value = NOTES
        instance.get_tags.return_value = []
        yield instance


@pytest.fixture(autouse=True)
def no_setup() -> Generator[None]:
    """Don't actually set up the entry after the flow creates it."""
    with patch("custom_components.anchor_notes.async_setup_entry", return_value=True):
        yield


async def test_user_flow_creates_entry(hass: HomeAssistant, client: AsyncMock) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Alex"
    assert result["data"] == {
        CONF_URL: "http://anchor.local:3009",
        CONF_API_TOKEN: "token-1",
    }
    assert result["result"].unique_id == "user-1"


async def test_linked_ha_user_names_entry(hass: HomeAssistant, client: AsyncMock) -> None:
    """With no name given, the entry takes the HA user's name and stores the link."""
    user = MockUser(name="Alex Doe").add_to_hass(hass)
    data = {k: v for k, v in USER_INPUT.items() if k != CONF_NAME} | {CONF_HA_USER: user.id}
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=data
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Alex Doe"
    assert result["result"].options == {CONF_HA_USER: user.id}


async def test_owner_from_tags_when_no_owned_notes(
    hass: HomeAssistant, client: AsyncMock
) -> None:
    client.get_notes.return_value = []
    client.get_tags.return_value = [{"id": "t1", "userId": "user-9"}]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "user-9"


@pytest.mark.parametrize(
    ("side_effects", "error"),
    [
        ({"get_health": AnchorConnectionError("down")}, "cannot_connect"),
        ({"get_notes": AnchorAuthError("401")}, "invalid_auth"),
    ],
)
async def test_user_flow_errors_then_recovers(
    hass: HomeAssistant, client: AsyncMock, side_effects: dict, error: str
) -> None:
    for method, effect in side_effects.items():
        getattr(client, method).side_effect = effect
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    for method in side_effects:
        getattr(client, method).side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_not_an_anchor_server(hass: HomeAssistant, client: AsyncMock) -> None:
    client.get_health.return_value = {"status": "ok", "app": "something-else"}
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=USER_INPUT
    )
    assert result["errors"] == {"base": "not_anchor"}


async def test_same_account_twice_aborts(hass: HomeAssistant, client: AsyncMock) -> None:
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="user-1",
        data={CONF_URL: "http://anchor.local:3009", CONF_API_TOKEN: "old"},
    ).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=USER_INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_token(hass: HomeAssistant, client: AsyncMock) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Alex",
        unique_id="user-1",
        data={CONF_URL: "http://anchor.local:3009", CONF_API_TOKEN: "old"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "new"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_TOKEN] == "new"


async def test_reauth_rejects_other_account(
    hass: HomeAssistant, client: AsyncMock
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user-2",
        data={CONF_URL: "http://anchor.local:3009", CONF_API_TOKEN: "old"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "someone-elses"}
    )
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_API_TOKEN] == "old"


async def test_options_flow(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_URL: "http://anchor.local:3009", CONF_API_TOKEN: "t"},
    )
    entry.add_to_hass(hass)
    user = MockUser(name="Alex Doe").add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_RECENT_COUNT: 8, CONF_HA_USER: user.id}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_RECENT_COUNT] == 8
    assert entry.options[CONF_HA_USER] == user.id

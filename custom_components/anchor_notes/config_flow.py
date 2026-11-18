"""UI setup for Anchor Notes: one config entry per Anchor account."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_API_TOKEN, CONF_NAME, CONF_URL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    AnchorAuthError,
    AnchorClient,
    AnchorConnectionError,
    AnchorError,
    normalize_url,
)
from .const import (
    CONF_EXCLUDE_PINNED,
    CONF_PINNED_LIMIT,
    CONF_RECENT_COUNT,
    CONF_REMINDER_LIMIT,
    CONF_SCAN_INTERVAL,
    DEFAULT_EXCLUDE_PINNED,
    DEFAULT_PINNED_LIMIT,
    DEFAULT_RECENT_COUNT,
    DEFAULT_REMINDER_LIMIT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
)
from .coordinator import AnchorConfigEntry
from .models import owner_id_from_notes

_URL = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))
_TOKEN = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): TextSelector(),
        vol.Required(CONF_URL): _URL,
        vol.Required(CONF_API_TOKEN): _TOKEN,
    }
)
TOKEN_SCHEMA = vol.Schema({vol.Required(CONF_API_TOKEN): _TOKEN})
RECONFIGURE_SCHEMA = vol.Schema(
    {vol.Required(CONF_URL): _URL, vol.Required(CONF_API_TOKEN): _TOKEN}
)


def _number(minimum: int, maximum: int, unit: str | None = None) -> NumberSelector:
    config = NumberSelectorConfig(
        min=minimum, max=maximum, step=1, mode=NumberSelectorMode.BOX
    )
    if unit:  # the selector rejects unit_of_measurement=None
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): _number(
            1, 60, "min"
        ),
        vol.Optional(CONF_RECENT_COUNT, default=DEFAULT_RECENT_COUNT): _number(1, 20),
        vol.Optional(CONF_PINNED_LIMIT, default=DEFAULT_PINNED_LIMIT): _number(1, 50),
        vol.Optional(CONF_REMINDER_LIMIT, default=DEFAULT_REMINDER_LIMIT): _number(
            1, 50
        ),
        vol.Optional(
            CONF_EXCLUDE_PINNED, default=DEFAULT_EXCLUDE_PINNED
        ): BooleanSelector(),
    }
)


class AnchorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add an Anchor account by server URL + that user's API token."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: AnchorConfigEntry) -> AnchorOptionsFlow:
        """Options: refresh interval and list sizes."""
        return AnchorOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Name, URL, token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = normalize_url(user_input[CONF_URL])
            token = user_input[CONF_API_TOKEN].strip()
            self._async_abort_entries_match({CONF_API_TOKEN: token})
            owner_id, errors = await self._async_validate(url, token)
            if not errors:
                if owner_id:
                    await self.async_set_unique_id(owner_id)
                    self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_NAME].strip(),
                    data={CONF_URL: url, CONF_API_TOKEN: token},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Token was rejected (revoked or regenerated in Anchor)."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new token."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_API_TOKEN].strip()
            owner_id, errors = await self._async_validate(entry.data[CONF_URL], token)
            if not errors:
                if owner_id and entry.unique_id and owner_id != entry.unique_id:
                    return self.async_abort(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_TOKEN: token}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=TOKEN_SCHEMA,
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the server URL or token."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            url = normalize_url(user_input[CONF_URL])
            token = user_input[CONF_API_TOKEN].strip()
            owner_id, errors = await self._async_validate(url, token)
            if not errors:
                if owner_id and entry.unique_id and owner_id != entry.unique_id:
                    return self.async_abort(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_URL: url, CONF_API_TOKEN: token}
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                RECONFIGURE_SCHEMA, user_input or {CONF_URL: entry.data[CONF_URL]}
            ),
            errors=errors,
        )

    async def _async_validate(
        self, url: str, token: str
    ) -> tuple[str | None, dict[str, str]]:
        """Check the server is Anchor and the token works.

        Returns (user ID if it could be determined, errors).
        """
        client = AnchorClient(async_get_clientsession(self.hass), url, token)
        try:
            health = await client.get_health()
        except AnchorConnectionError:
            return None, {"base": "cannot_connect"}
        except AnchorError:
            return None, {"base": "not_anchor"}
        if health.get("app") != "anchor":
            return None, {"base": "not_anchor"}

        try:
            owner_id = owner_id_from_notes(await client.get_notes())
            if owner_id is None:
                # No notes of their own yet: tags belong to the user too.
                tags = await client.get_tags()
                owner_id = next(
                    (t["userId"] for t in tags if isinstance(t.get("userId"), str)),
                    None,
                )
        except AnchorAuthError:
            return None, {"base": "invalid_auth"}
        except AnchorConnectionError:
            return None, {"base": "cannot_connect"}
        except AnchorError:
            LOGGER.exception("Unexpected response from Anchor at %s", url)
            return None, {"base": "unknown"}
        return owner_id, {}


class AnchorOptionsFlow(OptionsFlow):
    """Refresh interval and list sizes."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, self.config_entry.options
            ),
        )

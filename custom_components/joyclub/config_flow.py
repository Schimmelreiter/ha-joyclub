"""Config flow: one entry per JOYclub account, logged in with the SSOAUTOID cookie."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import JoyclubAuthError, JoyclubClient, JoyclubError
from .const import (
    CONF_BOOKMARKED,
    CONF_CANCELLED,
    CONF_CLUBMAIL_MINUTES,
    CONF_DEFAULT_HOURS,
    CONF_FLARESOLVERR,
    CONF_SCAN_HOURS,
    CONF_SSOAUTOID,
    CONF_SYNC,
    CONF_TARGET,
    CONF_WAITLIST,
    DEFAULT_CLUBMAIL_MINUTES,
    DEFAULT_DEFAULT_HOURS,
    DEFAULT_SCAN_HOURS,
    DOMAIN,
    MODE_ASK,
    MODE_NO,
    MODE_YES,
)
from .parser import extract_ssoautoid

_COOKIES = selector.TextSelector(selector.TextSelectorConfig(multiline=True))
_URL = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL))


async def _check(hass, ssoautoid: str, flaresolverr: str | None) -> str:
    """Log in once; returns the member id. Raises JoyclubAuthError/JoyclubError."""
    session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
    client = JoyclubClient(session, ssoautoid.strip(), flaresolverr)
    await client.login()
    return client.user_id or ""


class JoyclubConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a JOYclub account."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None and not (sso := extract_ssoautoid(user_input[CONF_SSOAUTOID])):
            errors[CONF_SSOAUTOID] = "no_cookie"
        elif user_input is not None:
            try:
                user_id = await _check(self.hass, sso, user_input.get(CONF_FLARESOLVERR))
            except JoyclubAuthError:
                errors["base"] = "invalid_auth"
            except JoyclubError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={
                        CONF_SSOAUTOID: sso,
                        CONF_FLARESOLVERR: user_input.get(CONF_FLARESOLVERR) or "",
                    },
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_SSOAUTOID): _COOKIES,
                vol.Optional(CONF_FLARESOLVERR): _URL,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=self.add_suggested_values_to_schema(schema, user_input or {}), errors=errors
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None and not (sso := extract_ssoautoid(user_input[CONF_SSOAUTOID])):
            errors[CONF_SSOAUTOID] = "no_cookie"
        elif user_input is not None:
            try:
                user_id = await _check(self.hass, sso, entry.data.get(CONF_FLARESOLVERR))
            except JoyclubAuthError:
                errors["base"] = "invalid_auth"
            except JoyclubError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(entry, data_updates={CONF_SSOAUTOID: sso})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_SSOAUTOID): _COOKIES}),
            description_placeholders={"name": entry.title},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return JoyclubOptionsFlow()


def _mode() -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[MODE_YES, MODE_NO, MODE_ASK], translation_key="mode", mode=selector.SelectSelectorMode.DROPDOWN
        )
    )


class JoyclubOptionsFlow(OptionsFlow):
    """Calendar sync and polling options of one account."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_SYNC) and not user_input.get(CONF_TARGET):
                errors[CONF_TARGET] = "target_required"
            else:
                return self.async_create_entry(data=user_input)
        o = {**self.config_entry.options, **(user_input or {})}
        schema = vol.Schema(
            {
                vol.Required(CONF_SYNC, default=o.get(CONF_SYNC, False)): bool,
                vol.Optional(CONF_TARGET): selector.EntitySelector(selector.EntitySelectorConfig(domain="calendar")),
                vol.Required(CONF_WAITLIST, default=o.get(CONF_WAITLIST, False)): bool,
                vol.Required(CONF_BOOKMARKED, default=o.get(CONF_BOOKMARKED, MODE_NO)): _mode(),
                vol.Required(CONF_CANCELLED, default=o.get(CONF_CANCELLED, MODE_ASK)): _mode(),
                vol.Required(
                    CONF_DEFAULT_HOURS, default=o.get(CONF_DEFAULT_HOURS, DEFAULT_DEFAULT_HOURS)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=16, step=0.5, unit_of_measurement="h", mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_SCAN_HOURS, default=o.get(CONF_SCAN_HOURS, DEFAULT_SCAN_HOURS)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=24, step=1, unit_of_measurement="h", mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_CLUBMAIL_MINUTES, default=o.get(CONF_CLUBMAIL_MINUTES, DEFAULT_CLUBMAIL_MINUTES)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=5, max=240, step=5, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Optional(CONF_FLARESOLVERR): _URL,
            }
        )
        suggested = {k: v for k, v in o.items() if k in (CONF_TARGET, CONF_FLARESOLVERR)}
        if CONF_FLARESOLVERR not in suggested and self.config_entry.data.get(CONF_FLARESOLVERR):
            suggested[CONF_FLARESOLVERR] = self.config_entry.data[CONF_FLARESOLVERR]
        return self.async_show_form(
            step_id="init", data_schema=self.add_suggested_values_to_schema(schema, suggested), errors=errors
        )

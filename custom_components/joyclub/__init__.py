"""JOYclub: own party registrations as a calendar, optional sync into another calendar, unread ClubMails."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.typing import ConfigType

from .api import JoyclubAuthError, JoyclubClient, JoyclubError
from .const import CONF_FLARESOLVERR, CONF_SSOAUTOID, DOMAIN, MODE_NO, MODE_YES
from .coordinator import ClubmailCoordinator, EventsCoordinator
from .sync import CalendarSync

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CALENDAR, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

ANSWERS = {"accept": MODE_YES, "reject": MODE_NO, "delete": "delete", "keep": "keep"}


@dataclass
class JoyclubData:
    """Runtime data of one account."""

    client: JoyclubClient
    events: EventsCoordinator
    clubmail: ClubmailCoordinator


type JoyclubConfigEntry = ConfigEntry[JoyclubData]


def _loaded(hass: HomeAssistant) -> list[JoyclubConfigEntry]:
    return [e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Shared calendar sync and the actions."""
    sync = CalendarSync(hass)
    await sync.async_load()
    hass.data.setdefault(DOMAIN, {})["sync"] = sync

    async def _run_sync() -> None:
        await sync.async_sync([(e, e.runtime_data.events) for e in _loaded(hass)])

    hass.data[DOMAIN]["debouncer"] = Debouncer(hass, _LOGGER, cooldown=10, immediate=False, function=_run_sync)

    async def refresh(call: ServiceCall) -> None:
        for entry in _loaded(hass):
            await entry.runtime_data.events.async_request_refresh()

    async def answer(call: ServiceCall) -> None:
        sync.decide(str(call.data["event_id"]), ANSWERS[call.data["answer"]])
        await _run_sync()

    hass.services.async_register(DOMAIN, "refresh", refresh)
    hass.services.async_register(
        DOMAIN,
        "answer",
        answer,
        schema=vol.Schema({vol.Required("event_id"): cv.string, vol.Required("answer"): vol.In(list(ANSWERS))}),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: JoyclubConfigEntry) -> bool:
    """Log in with the account's SSOAUTOID and start polling."""
    session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
    client = JoyclubClient(
        session, entry.data[CONF_SSOAUTOID], entry.options.get(CONF_FLARESOLVERR) or entry.data.get(CONF_FLARESOLVERR)
    )
    try:
        await client.login()
    except JoyclubAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except JoyclubError as err:
        raise ConfigEntryNotReady(str(err)) from err

    events = EventsCoordinator(hass, entry, client)
    clubmail = ClubmailCoordinator(hass, entry, client)
    await events.async_config_entry_first_refresh()
    await clubmail.async_config_entry_first_refresh()
    entry.runtime_data = JoyclubData(client, events, clubmail)

    debouncer: Debouncer = hass.data[DOMAIN]["debouncer"]

    @callback
    def _events_updated() -> None:
        if events.last_update_success:
            hass.async_create_task(debouncer.async_call())

    entry.async_on_unload(events.async_add_listener(_events_updated))
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    hass.async_create_task(debouncer.async_call())
    return True


async def _options_updated(hass: HomeAssistant, entry: JoyclubConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: JoyclubConfigEntry) -> bool:
    """Unload an account; the shared sync stops with the last one."""
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok and not [e for e in _loaded(hass) if e.entry_id != entry.entry_id]:
        hass.data[DOMAIN]["debouncer"].async_cancel()
    return ok

"""Coordinators: own events (every few hours) and unread ClubMails (every few minutes)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import parser
from .api import JoyclubAuthError, JoyclubClient, JoyclubError
from .const import (
    CONF_ABBREVIATIONS,
    CONF_CLEAN_TITLE,
    CONF_CLUBMAIL_MINUTES,
    CONF_DEFAULT_HOURS,
    CONF_DESCRIPTION,
    CONF_SCAN_HOURS,
    DEFAULT_ABBREVIATIONS,
    DEFAULT_CLUBMAIL_MINUTES,
    DEFAULT_DEFAULT_HOURS,
    DEFAULT_SCAN_HOURS,
    DESCRIPTION_FULL,
    DESCRIPTION_SHORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

DETAILS_MAX_AGE = timedelta(hours=24)
CLUB_MAX_AGE = timedelta(days=7)

END_DESCRIPTION = "description"
END_RULE = "rule"
END_CLUB = "opening_hours"
END_DEFAULT = "default"


@dataclass
class JoyEvent:
    """An own event (registered, waiting list or bookmarked)."""

    event_id: str
    url: str
    status: str
    title: str
    start: datetime
    end: datetime
    end_source: str
    location: str
    address: str
    city: str = ""
    latitude: float | None = None
    longitude: float | None = None
    display_title: str = ""
    description: str = ""

    @property
    def rule_key(self) -> str:
        return parser.rule_key(self.title, self.location or self.city)


@dataclass
class _Cached:
    details: parser.EventDetails
    fetched: datetime = field(default_factory=dt_util.utcnow)


class EventsCoordinator(DataUpdateCoordinator[dict[str, JoyEvent]]):
    """Own future events of one account."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: JoyclubClient) -> None:
        hours = entry.options.get(CONF_SCAN_HOURS, DEFAULT_SCAN_HOURS)
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN} {entry.title}", update_interval=timedelta(hours=hours), config_entry=entry
        )
        self.client = client
        self._cache: dict[str, _Cached] = {}
        self._clubs: dict[str, tuple[dict[int, tuple[str, str]], datetime]] = {}

    def _end_rule(self, key: str) -> str | None:
        sync = self.hass.data.get(DOMAIN, {}).get("sync")
        return sync.rules.get(key) if sync else None

    async def _async_update_data(self) -> dict[str, JoyEvent]:
        try:
            cards = await self.client.registrations()
            now = dt_util.now()
            events: dict[str, JoyEvent] = {}
            for card in cards:
                if card.day and card.day < (now - timedelta(days=1)).date():
                    continue
                cached = self._cache.get(card.event_id)
                if cached is None or dt_util.utcnow() - cached.fetched > DETAILS_MAX_AGE:
                    try:
                        cached = _Cached(await self.client.event_details(card))
                    except JoyclubAuthError:
                        raise
                    except JoyclubError as err:
                        _LOGGER.warning("Event %s: %s", card.event_id, err)
                        if cached is None:
                            continue
                    self._cache[card.event_id] = cached
                hours = await self._club_hours(cached.details.club_url)
                ev = self._build(card, cached.details, hours)
                if ev and ev.end >= now:
                    events[ev.event_id] = ev
            self._cache = {k: v for k, v in self._cache.items() if k in {c.event_id for c in cards}}
            return events
        except JoyclubAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except JoyclubError as err:
            raise UpdateFailed(str(err)) from err

    async def _club_hours(self, url: str) -> dict[int, tuple[str, str]]:
        """Opening hours of the club (cached for a week; on errors the last known ones or none)."""
        if not url:
            return {}
        cached = self._clubs.get(url)
        if cached and dt_util.utcnow() - cached[1] < CLUB_MAX_AGE:
            return cached[0]
        try:
            hours = await self.client.club_hours(url)
        except JoyclubAuthError:
            raise
        except JoyclubError as err:
            _LOGGER.debug("Club %s: %s", url, err)
            return cached[0] if cached else {}
        self._clubs[url] = (hours, dt_util.utcnow())
        return hours

    def _build(
        self, card: parser.Card, d: parser.EventDetails, hours: dict[int, tuple[str, str]] | None = None
    ) -> JoyEvent | None:
        start = d.start
        if start is None:
            return None
        title = d.title or card.title
        location = d.location_name or card.city
        key = parser.rule_key(title, location)
        end = (
            parser.end_from_description(d.description, start)
            or parser.end_from_description(d.ics_description, start)
            or parser.end_from_description(d.extra.get("page_text", ""), start)
        )
        source = END_DESCRIPTION
        if end is None and (rule := self._end_rule(key)):
            h, m = (int(x) for x in rule.split(":"))
            end = parser.end_after(start, time(h, m))
            source = END_RULE
        if end is None and (end := parser.end_from_opening_hours(hours or {}, start)):
            source = END_CLUB
        if end is None:
            end = start + timedelta(hours=self.config_entry.options.get(CONF_DEFAULT_HOURS, DEFAULT_DEFAULT_HOURS))
            source = END_DEFAULT
        url = card.url or f"https://www.joyclub.de/event/{card.event_id}.html"
        options = self.config_entry.options
        display = title
        if options.get(CONF_CLEAN_TITLE, True):
            display = parser.clean_title(
                title, parser.parse_abbreviations(options.get(CONF_ABBREVIATIONS, DEFAULT_ABBREVIATIONS))
            )
        return JoyEvent(
            event_id=card.event_id,
            url=url,
            status=card.status or d.status or parser.STATUS_REGISTERED,
            title=title,
            start=start,
            end=end,
            end_source=source,
            location=location,
            address=d.address,
            city=card.city,
            latitude=d.latitude,
            longitude=d.longitude,
            display_title=display,
            description=_description(d, url, options.get(CONF_DESCRIPTION, DESCRIPTION_SHORT)),
        )


def _description(d: parser.EventDetails, url: str, mode: str) -> str:
    """Text for the calendar event; the JOYclub link is always in it (it identifies the event)."""
    short = d.ics_description  # as in JOYclub's "Add to calendar": shortened text and the link
    full = d.description if d.description != d.extra.get("page_text") else ""  # not the whole page as fallback
    text = (full or short) if mode == DESCRIPTION_FULL else (short or full)
    if parser.event_url_in(text):
        return text.strip()
    return f"{text.strip()}\n\n{url}".strip()


class ClubmailCoordinator(DataUpdateCoordinator[dict]):
    """Unread ClubMails of one account."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: JoyclubClient) -> None:
        minutes = entry.options.get(CONF_CLUBMAIL_MINUTES, DEFAULT_CLUBMAIL_MINUTES)
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {entry.title} ClubMail",
            update_interval=timedelta(minutes=minutes),
            config_entry=entry,
        )
        self.client = client

    async def _async_update_data(self) -> dict:
        try:
            state = await self.client.refresh_clubmail()
        except JoyclubAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except JoyclubError as err:
            raise UpdateFailed(str(err)) from err
        if state is None:
            raise UpdateFailed("ClubMail counter not found")
        return state

"""Direct access to a Google calendar of Home Assistant's Google integration.

Home Assistant's calendar entity of Google can only create and delete events, without guests
and without a say on notifications. The library below it talks to the Calendar API with the
integration's login, so this module uses it for what the entity cannot do:

- change the title of an existing event (guests, reminders and invitations stay),
- create events with guests (Google sends the invitations),
- choose whether guests are notified (sendUpdates).

It reaches into the Google integration (entity.calendar_id, the config entry's runtime data);
if that changes in a Home Assistant release, google_target() returns None and the sync falls
back to what the calendar entity offers.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from urllib.parse import quote

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


class GoogleError(Exception):
    """A Calendar API request failed."""


def _time(value: datetime) -> dict[str, str]:
    return {"dateTime": dt_util.as_local(value).isoformat(), "timeZone": str(dt_util.get_default_time_zone())}


class GoogleTarget:
    """One Google calendar, addressed through the Google integration's login."""

    def __init__(self, entity: Any, auth: Any, calendar_id: str) -> None:
        self.entity = entity
        self._auth = auth
        self._events = f"calendars/{quote(calendar_id, safe='')}/events"
        self.changed = False

    async def _call(self, method: str, url: str, **kwargs: Any) -> Any:
        try:
            resp = await self._auth.request(method, url, **kwargs)
        except Exception as err:  # noqa: BLE001 - token or connection problem of the Google integration
            raise GoogleError(f"{method} {url}: {err}") from err
        if resp.status >= 400:
            raise GoogleError(f"{method} {url}: {resp.status} {(await resp.text())[:300]}")
        if resp.status == 204 or method == "delete":
            return None
        return await resp.json()

    async def find(self, ical_uid: str) -> dict | None:
        """The API event (with id and attendees) for the uid Home Assistant shows."""
        data = await self._call("get", self._events, params={"iCalUID": ical_uid, "maxResults": "5"})
        items = [i for i in (data or {}).get("items", []) if i.get("status") != "cancelled"]
        return items[0] if items else None

    async def create(
        self,
        *,
        summary: str,
        start: datetime | None = None,
        end: datetime | None = None,
        description: str = "",
        location: str = "",
        attendees: list[str] | None = None,
        notify: bool,
        raw: dict | None = None,
    ) -> None:
        body: dict[str, Any] = dict(raw or {})
        body["summary"] = summary
        if start is not None and end is not None:
            body["start"], body["end"] = _time(start), _time(end)
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        if attendees:
            body["attendees"] = [{"email": a} for a in attendees]
        await self._call("post", self._events, params={"sendUpdates": "all" if notify else "none"}, json=body)
        self.changed = True

    async def patch(self, event_id: str, body: dict[str, Any], *, notify: bool) -> None:
        await self._call(
            "patch",
            f"{self._events}/{quote(event_id, safe='')}",
            params={"sendUpdates": "all" if notify else "none"},
            json=body,
        )
        self.changed = True

    async def delete(self, event_id: str, *, notify: bool) -> None:
        await self._call(
            "delete", f"{self._events}/{quote(event_id, safe='')}", params={"sendUpdates": "all" if notify else "none"}
        )
        self.changed = True

    async def async_refresh(self) -> None:
        """Let the calendar entity see the changes (it reads from its own synced copy)."""
        if self.changed:
            self.changed = False
            await self.entity.coordinator.async_refresh()


def google_target(hass: HomeAssistant, entity: Any) -> GoogleTarget | None:
    """GoogleTarget for a calendar entity of the Google integration, else None."""
    try:
        if entity.platform.platform_name != "google":
            return None
        calendar_id = entity.calendar_id
        auth = entity.coordinator.config_entry.runtime_data.service._auth  # noqa: SLF001
    except AttributeError:
        _LOGGER.debug("No direct Google access for %s", getattr(entity, "entity_id", entity))
        return None
    if not calendar_id or not hasattr(auth, "request"):
        return None
    return GoogleTarget(entity, auth, calendar_id)

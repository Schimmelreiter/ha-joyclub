"""Calendar with the account's own events (registered, waiting list, bookmarked)."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import JoyclubConfigEntry
from .coordinator import EventsCoordinator, JoyEvent
from .entity import device_info, suffix


def _to_calendar(hass: HomeAssistant, ev: JoyEvent) -> CalendarEvent:
    return CalendarEvent(
        start=ev.start,
        end=ev.end,
        summary=ev.title + suffix(hass, ev.status),
        location=ev.address or ev.location,
        description=ev.url,
        uid=f"joyclub-{ev.event_id}",
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: JoyclubConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """One calendar per account."""
    async_add_entities([JoyclubCalendar(entry.runtime_data.events, entry)])


class JoyclubCalendar(CoordinatorEntity[EventsCoordinator], CalendarEntity):
    """Own JOYclub events."""

    _attr_has_entity_name = True
    _attr_translation_key = "events"

    def __init__(self, coordinator: EventsCoordinator, entry: JoyclubConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id}_events"
        self._attr_device_info = device_info(entry)

    def _sorted(self) -> list[JoyEvent]:
        return sorted((self.coordinator.data or {}).values(), key=lambda e: e.start)

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now()
        for ev in self._sorted():
            if ev.end > now:
                return _to_calendar(self.hass, ev)
        return None

    @property
    def extra_state_attributes(self) -> dict:
        nxt = next((e for e in self._sorted() if e.end > dt_util.now()), None)
        if nxt is None:
            return {}
        return {
            "event_id": nxt.event_id,
            "status": nxt.status,
            "url": nxt.url,
            "end_source": nxt.end_source,
            "latitude": nxt.latitude,
            "longitude": nxt.longitude,
        }

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return [_to_calendar(hass, e) for e in self._sorted() if e.end > start_date and e.start < end_date]

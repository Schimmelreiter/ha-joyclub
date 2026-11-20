"""Sensors: unread ClubMails and the next party."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import JoyclubConfigEntry
from .coordinator import ClubmailCoordinator, EventsCoordinator
from .entity import device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: JoyclubConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Sensors of one account."""
    data = entry.runtime_data
    async_add_entities([ClubmailSensor(data.clubmail, entry), NextPartySensor(data.events, entry)])


class ClubmailSensor(CoordinatorEntity[ClubmailCoordinator], SensorEntity):
    """Unread ClubMails counted like the badge on joyclub.de (conversations); messages as attribute."""

    _attr_has_entity_name = True
    _attr_translation_key = "clubmail"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: ClubmailCoordinator, entry: JoyclubConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id}_clubmail"
        self._attr_device_info = device_info(entry)

    @property
    def native_value(self) -> int | None:
        return (self.coordinator.data or {}).get("unread_conversation_count")

    @property
    def extra_state_attributes(self) -> dict:
        d = self.coordinator.data or {}
        return {
            k: d.get(k)
            for k in ("unread_message_count", "new_compliment_count", "incoming_express_mail")
            if k in d
        }


class NextPartySensor(CoordinatorEntity[EventsCoordinator], SensorEntity):
    """Start of the next registered party."""

    _attr_has_entity_name = True
    _attr_translation_key = "next_party"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: EventsCoordinator, entry: JoyclubConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id}_next_party"
        self._attr_device_info = device_info(entry)

    def _next(self):
        now = dt_util.now()
        upcoming = [e for e in (self.coordinator.data or {}).values() if e.end > now and e.status == "registered"]
        return min(upcoming, key=lambda e: e.start) if upcoming else None

    @property
    def native_value(self) -> datetime | None:
        ev = self._next()
        return ev.start if ev else None

    @property
    def extra_state_attributes(self) -> dict:
        ev = self._next()
        if ev is None:
            return {}
        return {
            "title": ev.display_title or ev.title,
            "original_title": ev.title,
            "location": ev.location,
            "address": ev.address,
            "end": ev.end.isoformat(),
            "end_source": ev.end_source,
            "url": ev.url,
            "event_id": ev.event_id,
        }

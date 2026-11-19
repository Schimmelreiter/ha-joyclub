"""Copy own JOYclub events into a calendar of Home Assistant (e.g. Google) and learn end times.

Shared by all accounts: an event both partners are registered for is created once.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.calendar import DATA_COMPONENT, CalendarEntityFeature, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import parser
from .const import (
    CONF_BOOKMARKED,
    CONF_CANCELLED,
    CONF_SYNC,
    CONF_TARGET,
    CONF_WAITLIST,
    EVENT_ASK_BOOKMARKED,
    EVENT_ASK_CANCELLED,
    EVENT_CREATED,
    EVENT_DELETED,
    EVENT_END_GUESSED,
    EVENT_RULE_LEARNED,
    MARKER,
    MODE_ASK,
    MODE_NO,
    MODE_YES,
    STORE_KEY,
    STORE_VERSION,
)
from .coordinator import END_DEFAULT

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .coordinator import EventsCoordinator, JoyEvent

_LOGGER = logging.getLogger(__name__)
_PRIO = {parser.STATUS_REGISTERED: 3, parser.STATUS_WAITLIST: 2, parser.STATUS_BOOKMARKED: 1}


class CalendarSync:
    """State: learned end times, events created per target calendar, answers to questions."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, STORE_KEY)
        self.rules: dict[str, str] = {}
        self.created: dict[str, dict[str, dict]] = {}
        self.decisions: dict[str, str] = {}
        self.asked: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.rules = data.get("rules", {})
        self.created = data.get("created", {})
        self.decisions = data.get("decisions", {})
        self.asked = data.get("asked", {})

    def _save(self) -> None:
        self._store.async_delay_save(
            lambda: {"rules": self.rules, "created": self.created, "decisions": self.decisions, "asked": self.asked}, 2
        )

    # ------------------------------------------------------------ helpers
    def _entity(self, entity_id: str):
        component = self.hass.data.get(DATA_COMPONENT)
        return component.get_entity(entity_id) if component else None

    @staticmethod
    def _url_of(ev: CalendarEvent) -> str:
        for line in (ev.description or "").splitlines():
            if "joyclub.de/event/" in line:
                return line.split(MARKER)[-1].strip()
        return ""

    @staticmethod
    def _eid_of_url(url: str) -> str:
        part = url.rsplit("/event/", 1)[-1]
        return part.split(".", 1)[0] if part else ""

    def _wanted(self, entry: ConfigEntry, ev: JoyEvent) -> bool:
        if ev.status == parser.STATUS_REGISTERED:
            return True
        if ev.status == parser.STATUS_WAITLIST:
            return bool(entry.options.get(CONF_WAITLIST, False))
        mode = entry.options.get(CONF_BOOKMARKED, MODE_NO)
        if mode == MODE_YES:
            return True
        if mode == MODE_ASK:
            if self.decisions.get(ev.event_id) == MODE_YES:
                return True
            if ev.event_id not in self.asked:
                self.asked[ev.event_id] = "bookmarked"
                self._fire(EVENT_ASK_BOOKMARKED, entry, ev)
        return False

    def _fire(self, event_type: str, entry: ConfigEntry, ev: JoyEvent | None = None, **extra: Any) -> None:
        data: dict[str, Any] = {"account": entry.title, "config_entry_id": entry.entry_id, **extra}
        if ev is not None:
            data.update(
                event_id=ev.event_id,
                title=ev.title,
                start=ev.start.isoformat(),
                end=ev.end.isoformat(),
                location=ev.location,
                url=ev.url,
                status=ev.status,
                end_source=ev.end_source,
            )
        self.hass.bus.async_fire(event_type, data)

    # ------------------------------------------------------------ sync
    async def async_sync(self, entries: list[tuple[ConfigEntry, EventsCoordinator]]) -> None:
        """Sync every target calendar from all accounts that use it."""
        async with self._lock:
            targets: dict[str, list[tuple[ConfigEntry, EventsCoordinator]]] = {}
            for entry, coord in entries:
                if entry.options.get(CONF_SYNC) and entry.options.get(CONF_TARGET) and coord.data is not None:
                    targets.setdefault(entry.options[CONF_TARGET], []).append((entry, coord))
            for target, accounts in targets.items():
                try:
                    await self._sync_target(target, accounts)
                except Exception:  # noqa: BLE001 - one calendar must not stop the others
                    _LOGGER.exception("Sync into %s failed", target)
            self._save()

    async def _sync_target(self, target: str, accounts: list[tuple[ConfigEntry, EventsCoordinator]]) -> None:
        cal = self._entity(target)
        if cal is None:
            _LOGGER.warning("Target calendar %s not found", target)
            return
        features = cal.supported_features or 0
        if not features & CalendarEntityFeature.CREATE_EVENT:
            _LOGGER.warning("Calendar %s cannot create events", target)
            return
        now = dt_util.now()
        # Best status per event over all accounts, and who wants it
        seen: dict[str, tuple[ConfigEntry, JoyEvent]] = {}
        wanted: dict[str, tuple[ConfigEntry, JoyEvent]] = {}
        for entry, coord in accounts:
            for ev in coord.data.values():
                if ev.event_id not in seen or _PRIO.get(ev.status, 0) > _PRIO.get(seen[ev.event_id][1].status, 0):
                    seen[ev.event_id] = (entry, ev)
                if ev.start > now and self._wanted(entry, ev):
                    if ev.event_id not in wanted or _PRIO.get(ev.status, 0) > _PRIO.get(
                        wanted[ev.event_id][1].status, 0
                    ):
                        wanted[ev.event_id] = (entry, ev)
        created = self.created.setdefault(target, {})
        if wanted or created:
            starts = [ev.start for _, ev in wanted.values()] + [
                dt_util.parse_datetime(r["start"]) for r in created.values()
            ]
            starts = [s for s in starts if s]
            window_start = min(starts + [now]) - timedelta(days=1)
            window_end = max(starts + [now]) + timedelta(days=2)
            existing = await cal.async_get_events(self.hass, window_start, window_end)
        else:
            existing = []
        by_eid: dict[str, CalendarEvent] = {}
        for item in existing:
            if eid := self._eid_of_url(self._url_of(item)):
                by_eid.setdefault(eid, item)

        for eid, (entry, ev) in wanted.items():
            item = by_eid.get(eid)
            record = created.get(eid)
            if item is not None:
                self._learn(entry, ev, item, record)
                continue
            if record and record.get("removed_by_user"):
                continue
            if record:
                # Created before, gone now: the user deleted it in the calendar - do not recreate
                record["removed_by_user"] = True
                continue
            summary = ev.title + (" (Warteliste)" if ev.status == parser.STATUS_WAITLIST else "")
            description = "\n".join(p for p in (f"{MARKER} {ev.url}", f"Konto: {entry.title}") if p)
            await cal.async_create_event(
                summary=summary,
                dtstart=ev.start,
                dtend=ev.end,
                description=description,
                location=ev.address or ev.location,
            )
            created[eid] = {
                "start": ev.start.isoformat(),
                "end": ev.end.isoformat(),
                "title": ev.title,
                "location": ev.location or ev.city,
                "account": entry.title,
            }
            self._fire(EVENT_CREATED, entry, ev, target=target)
            if ev.end_source == END_DEFAULT:
                self._fire(EVENT_END_GUESSED, entry, ev, target=target)

        # Events created by us that no account has any more (cancelled) - only if every account answered
        if not all(coord.last_update_success for _, coord in accounts):
            return
        for eid, record in list(created.items()):
            start = dt_util.parse_datetime(record.get("start", ""))
            if start and start < now - timedelta(days=1):
                created.pop(eid, None)  # past: forget
                continue
            if eid in seen or record.get("removed_by_user"):
                continue
            entry = accounts[0][0]
            mode = entry.options.get(CONF_CANCELLED, MODE_ASK)
            item = by_eid.get(eid)
            if mode == MODE_NO or item is None:
                continue
            if mode == MODE_YES or self.decisions.get(eid) == "delete":
                await self.async_delete(target, item)
                created.pop(eid, None)
                self.hass.bus.async_fire(
                    EVENT_DELETED,
                    {
                        "account": entry.title,
                        "event_id": eid,
                        "title": record.get("title"),
                        "start": record.get("start"),
                        "target": target,
                    },
                )
            elif self.asked.get(eid) != "cancelled":
                self.asked[eid] = "cancelled"
                self.hass.bus.async_fire(
                    EVENT_ASK_CANCELLED,
                    {
                        "account": entry.title,
                        "config_entry_id": entry.entry_id,
                        "event_id": eid,
                        "title": record.get("title"),
                        "start": record.get("start"),
                        "target": target,
                    },
                )

    def _learn(self, entry: ConfigEntry, ev: JoyEvent, item: CalendarEvent, record: dict | None) -> None:
        """The user changed the end of an event we created: remember it for this title and location."""
        if not record or not isinstance(item.end, type(ev.start)):
            return
        end = dt_util.as_local(item.end)
        if record.get("end") == end.isoformat():
            return
        old = dt_util.parse_datetime(record.get("end", ""))
        record["end"] = end.isoformat()
        if old and old != end:
            self.rules[ev.rule_key] = end.strftime("%H:%M")
            self._fire(EVENT_RULE_LEARNED, entry, ev, learned_end=end.strftime("%H:%M"))

    async def async_delete(self, target: str, item: CalendarEvent) -> None:
        cal = self._entity(target)
        if cal is None or not (cal.supported_features or 0) & CalendarEntityFeature.DELETE_EVENT or not item.uid:
            _LOGGER.warning("Cannot delete %s from %s", item.summary, target)
            return
        await cal.async_delete_event(item.uid, recurrence_id=item.recurrence_id)

    def decide(self, event_id: str, decision: str) -> None:
        """Answer to a question: MODE_YES/MODE_NO for bookmarked events, "delete"/"keep" for cancelled ones."""
        self.decisions[event_id] = decision
        self._save()

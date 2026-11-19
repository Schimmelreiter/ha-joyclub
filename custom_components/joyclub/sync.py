"""Copy own JOYclub events into a calendar of Home Assistant (e.g. Google) and learn end times.

Shared by all accounts: an event both partners are registered for is created once.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.calendar import DATA_COMPONENT, CalendarEntityFeature, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import parser
from .const import (
    CONF_BOOKMARKED,
    CONF_CANCELLED,
    CONF_INVITE,
    CONF_SYNC,
    CONF_TARGET,
    CONF_WAITLIST,
    EVENT_ASK_BOOKMARKED,
    EVENT_ASK_CANCELLED,
    EVENT_CREATED,
    EVENT_DELETED,
    EVENT_END_GUESSED,
    EVENT_RETITLED,
    EVENT_RULE_LEARNED,
    MODE_ASK,
    MODE_NO,
    MODE_YES,
    STORE_KEY,
    STORE_VERSION,
)
from .coordinator import END_DEFAULT
from .entity import suffix
from .google import GoogleError, GoogleTarget, google_target

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
        return parser.event_url_in(ev.description or "")

    @staticmethod
    def _eid_of_url(url: str) -> str:
        part = url.rsplit("/event/", 1)[-1]
        return part.split(".", 1)[0] if part else ""

    def _wanted(self, entry: ConfigEntry, ev: JoyEvent, dry_run: bool = False) -> bool:
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
            if ev.event_id not in self.asked and not dry_run:
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
    async def async_sync(
        self, entries: list[tuple[ConfigEntry, EventsCoordinator]], dry_run: bool = False
    ) -> list[dict[str, Any]]:
        """Sync every target calendar from all accounts that use it.

        dry_run: change nothing, only return what would be done (also for accounts with a target
        calendar whose sync is still switched off, to check before switching it on).
        """
        actions: list[dict[str, Any]] = []
        async with self._lock:
            targets: dict[str, list[tuple[ConfigEntry, EventsCoordinator]]] = {}
            for entry, coord in entries:
                if (
                    (entry.options.get(CONF_SYNC) or dry_run)
                    and entry.options.get(CONF_TARGET)
                    and coord.data is not None
                ):
                    targets.setdefault(entry.options[CONF_TARGET], []).append((entry, coord))
            for target, accounts in targets.items():
                try:
                    actions += await self._sync_target(target, accounts, dry_run)
                except Exception:  # noqa: BLE001 - one calendar must not stop the others
                    _LOGGER.exception("Sync into %s failed", target)
            if not dry_run:
                self._save()
        return actions

    def _summary(self, ev: JoyEvent) -> str:
        return (ev.display_title or ev.title) + suffix(self.hass, ev.status)

    async def _sync_target(
        self, target: str, accounts: list[tuple[ConfigEntry, EventsCoordinator]], dry_run: bool
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        cal = self._entity(target)
        if cal is None:
            _LOGGER.warning("Target calendar %s not found", target)
            return actions
        features = cal.supported_features or 0
        if not features & CalendarEntityFeature.CREATE_EVENT:
            _LOGGER.warning("Calendar %s cannot create events", target)
            return actions
        google = google_target(self.hass, cal)
        invite = sorted({a.strip() for e, _ in accounts for a in e.options.get(CONF_INVITE) or [] if a.strip()})
        now = dt_util.now()
        # Best status per event over all accounts, and who wants it
        seen: dict[str, tuple[ConfigEntry, JoyEvent]] = {}
        wanted: dict[str, tuple[ConfigEntry, JoyEvent]] = {}
        for entry, coord in accounts:
            for ev in coord.data.values():
                if ev.event_id not in seen or _PRIO.get(ev.status, 0) > _PRIO.get(seen[ev.event_id][1].status, 0):
                    seen[ev.event_id] = (entry, ev)
                if ev.start > now and self._wanted(entry, ev, dry_run):
                    if ev.event_id not in wanted or _PRIO.get(ev.status, 0) > _PRIO.get(
                        wanted[ev.event_id][1].status, 0
                    ):
                        wanted[ev.event_id] = (entry, ev)
        created = dict(self.created.get(target, {})) if dry_run else self.created.setdefault(target, {})
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

        def act(kind: str, eid: str, title: str, start: Any, **extra: Any) -> dict[str, Any]:
            a = {"action": kind, "target": target, "event_id": eid, "title": title, "start": str(start), **extra}
            actions.append(a)
            return a

        for eid, (entry, ev) in wanted.items():
            item = by_eid.get(eid)
            record = created.get(eid)
            summary = self._summary(ev)
            if item is not None:
                # Ours, or one the user imported before (identified by the link): keep its title readable
                may_retitle = record is None or record.get("summary") in (None, item.summary)
                if record is None and not dry_run:
                    record = created[eid] = {
                        "start": ev.start.isoformat(),
                        "end": dt_util.as_local(item.end).isoformat() if isinstance(item.end, datetime) else "",
                        "title": ev.title,
                        "location": ev.location or ev.city,
                        "account": entry.title,
                        "summary": item.summary,
                        "adopted": True,
                    }
                elif not dry_run:
                    self._learn(entry, ev, item, record)
                if may_retitle and item.summary != summary:
                    a = act("retitle", eid, summary, ev.start, old_title=item.summary)
                    if await self._retitle(cal, google, item, summary, a, dry_run) and record is not None:
                        record["summary"] = summary
                        if not dry_run:
                            self._fire(EVENT_RETITLED, entry, ev, target=target, old_title=item.summary)
                continue
            if record:
                # Created before, gone now: the user deleted it in the calendar - do not recreate
                if not dry_run:
                    record["removed_by_user"] = True
                continue
            guests = invite if google else []
            act("create", eid, summary, ev.start, end=str(ev.end), end_source=ev.end_source, invite=guests)
            if dry_run:
                continue
            location = ev.address or ev.location
            if guests and google:
                await google.create(
                    summary=summary,
                    start=ev.start,
                    end=ev.end,
                    description=ev.description,
                    location=location,
                    attendees=guests,
                    notify=True,
                )
            else:
                await cal.async_create_event(
                    summary=summary, dtstart=ev.start, dtend=ev.end, description=ev.description, location=location
                )
            created[eid] = {
                "start": ev.start.isoformat(),
                "end": ev.end.isoformat(),
                "title": ev.title,
                "location": ev.location or ev.city,
                "account": entry.title,
                "summary": summary,
            }
            self._fire(EVENT_CREATED, entry, ev, target=target)
            if ev.end_source == END_DEFAULT:
                self._fire(EVENT_END_GUESSED, entry, ev, target=target)

        # Events created by us that no account has any more (cancelled) - only if every account answered
        if all(coord.last_update_success for _, coord in accounts):
            await self._cancelled(target, accounts, created, seen, by_eid, now, act, dry_run)
        if google is not None and not dry_run:
            await google.async_refresh()
        return actions

    async def _cancelled(self, target, accounts, created, seen, by_eid, now, act, dry_run: bool) -> None:
        for eid, record in list(created.items()):
            start = dt_util.parse_datetime(record.get("start", ""))
            if start and start < now - timedelta(days=1):
                if not dry_run:
                    created.pop(eid, None)  # past: forget
                continue
            if eid in seen or record.get("removed_by_user"):
                continue
            entry = accounts[0][0]
            mode = entry.options.get(CONF_CANCELLED, MODE_ASK)
            item = by_eid.get(eid)
            if mode == MODE_NO or item is None:
                continue
            title = item.summary or record.get("title")
            if mode == MODE_YES or self.decisions.get(eid) == "delete":
                act("delete", eid, title, record.get("start"))
                if dry_run:
                    continue
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
                act("ask_delete", eid, title, record.get("start"))
                if dry_run:
                    continue
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

    async def _retitle(
        self, cal, google: GoogleTarget | None, item: CalendarEvent, summary: str, a: dict, dry_run: bool
    ) -> bool:
        """Change only the title of an existing event; guests are not notified, nothing is lost.

        Deleting and creating it again is only a fallback for events without guests.
        """
        if google is not None:
            try:
                gev = await google.find(item.uid) if item.uid else None
            except GoogleError as err:
                _LOGGER.warning("Cannot look up %s in Google: %s", item.summary, err)
                gev = None
            if gev is None:
                a["result"] = "skipped: not found in Google"
                return False
            guests = [g.get("email") for g in gev.get("attendees") or [] if not g.get("self")]
            a["guests"] = guests
            a["how"] = "change title, guests not notified"
            if dry_run:
                return False
            try:
                await google.patch(gev["id"], {"summary": summary}, notify=False)
                return True
            except GoogleError as err:
                _LOGGER.warning("Changing the title of %s failed: %s", item.summary, err)
            if guests:
                a["result"] = "skipped: change failed and the event has guests"
                return False
            # No guests: create a copy with the new title (times, text, reminders, colour), then delete the old one
            raw = {k: gev[k] for k in ("start", "end", "description", "location", "reminders", "colorId") if k in gev}
            try:
                await google.create(summary=summary, notify=False, raw=raw)
                await google.delete(gev["id"], notify=False)
            except GoogleError as err:
                _LOGGER.warning("Recreating %s failed: %s", item.summary, err)
                a["result"] = "failed"
                return False
            a["how"] = "recreated (no guests)"
            return True
        if (cal.supported_features or 0) & CalendarEntityFeature.UPDATE_EVENT and item.uid:
            a["how"] = "change title"
            if dry_run:
                return False
            await cal.async_update_event(
                item.uid,
                {
                    "summary": summary,
                    "dtstart": item.start,
                    "dtend": item.end,
                    "description": item.description,
                    "location": item.location,
                },
                recurrence_id=item.recurrence_id,
            )
            return True
        # Unknown whether there are guests: leave it
        a["result"] = "skipped: calendar cannot change events"
        return False

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
        """Delete an event (deregistered); guests are notified."""
        cal = self._entity(target)
        if cal is None:
            return
        if (google := google_target(self.hass, cal)) is not None and item.uid:
            try:
                if gev := await google.find(item.uid):
                    await google.delete(gev["id"], notify=True)
                    await google.async_refresh()
                    return
            except GoogleError as err:
                _LOGGER.warning("Deleting %s in Google failed, trying the calendar entity: %s", item.summary, err)
        if not (cal.supported_features or 0) & CalendarEntityFeature.DELETE_EVENT or not item.uid:
            _LOGGER.warning("Cannot delete %s from %s", item.summary, target)
            return
        await cal.async_delete_event(item.uid, recurrence_id=item.recurrence_id)

    def decide(self, event_id: str, decision: str) -> None:
        """Answer to a question: MODE_YES/MODE_NO for bookmarked events, "delete"/"keep" for cancelled ones."""
        self.decisions[event_id] = decision
        self._save()

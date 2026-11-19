"""Parsers for JOYclub pages (pure functions, no I/O)."""

from __future__ import annotations

import html as html_lib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")

# Registration status
STATUS_REGISTERED = "registered"
STATUS_WAITLIST = "waitlist"
STATUS_BOOKMARKED = "bookmarked"

_CARD_RE = re.compile(
    r'<div\s+class="card-list-ui-list-item event-card-ui[^"]*"[^>]*data-element-id="(\d+)"',
)
_SECTION_RE = re.compile(r'<section[^>]*data-date="([^"]*)"')
_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


def _text(fragment: str) -> str:
    """HTML fragment to plain text (tags removed, entities decoded, spaces collapsed)."""
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


@dataclass
class Card:
    """One event on the page "Dates & Events → registrations"."""

    event_id: str
    url: str = ""
    day: date | None = None
    title: str = ""
    city: str = ""
    status: str = ""


def parse_registration_cards(page: str) -> list[Card]:
    """Cards of the registrations list (first page or appended scroll elements).

    Every event appears twice: the card and its detail container (with the buttons).
    """
    cards: dict[str, Card] = {}
    # Positions of the day sections, to assign each card its day
    sections = [(m.start(), m.group(1)) for m in _SECTION_RE.finditer(page)]
    starts = [m for m in _CARD_RE.finditer(page)]
    for i, m in enumerate(starts):
        eid = m.group(1)
        body = page[m.end() : starts[i + 1].start() if i + 1 < len(starts) else len(page)]
        card = cards.setdefault(eid, Card(eid))
        day_label = ""
        for pos, label in sections:
            if pos < m.start():
                day_label = label
        if card.day is None and (dm := _DATE_RE.search(day_label)):
            card.day = date(int(dm.group(3)), int(dm.group(2)), int(dm.group(1)))
        if not card.url and (lm := re.search(r'href="(https://www\.joyclub\.de/event/\d+[^"]*)"', body)):
            card.url = html_lib.unescape(lm.group(1))
        if not card.title and (hm := re.search(r"card-ui-information-headline[^>]*>(.*?)</", body, re.S)):
            card.title = _text(hm.group(1))
        if not card.city and (cm := re.search(r'class="location[^"]*"[^>]*>(.*?)</', body, re.S)):
            card.city = _text(cm.group(1))
        buttons = [_text(b).lower() for b in re.findall(r"<j-button[^>]*>(.*?)</j-button>", body, re.S)]
        if any("warteliste" in b for b in buttons):
            card.status = STATUS_WAITLIST
        elif any(b == "angemeldet" for b in buttons):
            card.status = card.status if card.status == STATUS_WAITLIST else STATUS_REGISTERED
        elif not card.status and (bm := re.search(r"event_bookmark_btn[^>]*>(.*?)</j-button>", body, re.S)):
            if "gemerkt" in _text(bm.group(1)).lower():
                card.status = STATUS_BOOKMARKED
    return list(cards.values())


def scroll_params(page: str) -> tuple[str, dict] | None:
    """cache_killer and the parameters for dates_partys_scroll_data from the first page."""
    ck = re.search(r'data-cache-killer="([0-9a-f]+)"', page)
    pm = re.search(r'jc-infinite-scroll"[^>]*data-params="([^"]+)"', page)
    if not ck or not pm:
        return None
    try:
        return ck.group(1), json.loads(html_lib.unescape(pm.group(1)))
    except ValueError:
        return None


_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


def extract_ssoautoid(text: str) -> str | None:
    """SSOAUTOID from whatever the user pastes.

    Accepts the bare value, a JSON cookie export (cookie editors: list of objects with
    "name"/"value", or a {"name": "value"} mapping), a Netscape cookies.txt and a
    Cookie header ("a=b; SSOAUTOID=…").
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        data = [{"name": k, "value": v} for k, v in data.items()] if "name" not in data else [data]
    if isinstance(data, list):
        for c in data:
            if isinstance(c, dict) and c.get("name") == "SSOAUTOID" and isinstance(c.get("value"), str):
                return c["value"].strip() or None
        return None
    for line in text.splitlines():  # Netscape cookies.txt: domain flag path secure expiry name value
        parts = line.strip().split("\t")
        if len(parts) >= 7 and parts[5] == "SSOAUTOID":
            return parts[6].strip() or None
    if m := re.search(r"SSOAUTOID\s*[=:]\s*\"?([^\s;\",]+)", text):
        return m.group(1)
    if re.fullmatch(_UUID, text) or re.fullmatch(r"[^\s;=]{16,}", text):
        return text
    return None


def session_user_id(page: str) -> str | None:
    """Id of the logged in member, None if the page is not logged in."""
    m = re.search(r'data-session-user-id="(\d+)"', page)
    return m.group(1) if m else None


def clubmail_state(page: str) -> dict | None:
    """Unread ClubMails from the navigation (data-clubmail-state)."""
    m = re.search(r'id="clubmail_notify"[^>]*data-clubmail-state="([^"]*)"', page)
    if not m:
        return None
    try:
        return json.loads(html_lib.unescape(m.group(1)))
    except ValueError:
        return None


@dataclass
class EventDetails:
    """What the event page and the iCal file tell about one event."""

    title: str = ""
    start: datetime | None = None
    location_name: str = ""
    address: str = ""
    latitude: float | None = None
    longitude: float | None = None
    description: str = ""
    status: str = ""
    extra: dict = field(default_factory=dict)


def _unfold_ics(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _ics_value(value: str) -> str:
    return value.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def parse_ics(text: str, details: EventDetails | None = None) -> EventDetails:
    """Title, start, location and coordinates from JOYclub's iCal download.

    DTEND is useless there (always equal to DTSTART), so it is ignored.
    """
    d = details or EventDetails()
    for line in _unfold_ics(text):
        name, _, value = line.partition(":")
        key, *params = name.split(";")
        if key == "SUMMARY" and not d.title:
            d.title = _ics_value(value).strip()
        elif key == "LOCATION" and not d.address:
            d.address = _ics_value(value).strip()
        elif key == "GEO" and d.latitude is None:
            try:
                lat, lon = value.split(";")
                d.latitude, d.longitude = float(lat), float(lon)
            except ValueError:
                pass
        elif key == "DTSTART" and d.start is None:
            tz = TZ
            for p in params:
                if p.startswith("TZID="):
                    try:
                        tz = ZoneInfo(p[5:])
                    except (KeyError, ValueError):
                        tz = TZ
            try:
                if value.endswith("Z"):
                    d.start = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ)
                else:
                    d.start = datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=tz)
            except ValueError:
                pass
    return d


def parse_event_page(page: str, details: EventDetails | None = None) -> EventDetails:
    """Structured data, description and own registration status of an event page."""
    d = details or EventDetails()
    for m in re.finditer(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", page, re.S):
        try:
            ld = json.loads(m.group(1))
        except ValueError:
            continue
        if not isinstance(ld, dict) or ld.get("@type") != "Event":
            continue
        d.title = d.title or (ld.get("name") or "").strip()
        if d.start is None and ld.get("startDate"):
            try:
                d.start = datetime.fromisoformat(ld["startDate"]).astimezone(TZ)
            except ValueError:
                pass
        loc = ld.get("location") or {}
        d.location_name = d.location_name or (loc.get("name") or "").strip()
        if not d.address:
            addr = loc.get("address") or {}
            parts = [
                loc.get("name"),
                addr.get("streetAddress"),
                " ".join(p for p in (addr.get("postalCode"), addr.get("addressLocality")) if p),
            ]
            d.address = ", ".join(p for p in parts if p)
        if not d.description:
            d.description = (ld.get("description") or "").strip()
        break
    # Full description text of the page (the structured one is shortened)
    body = re.search(r'<div[^>]*class="[^"]*event_detail_text[^"]*"[^>]*>(.*?)</div>\s*</div>', page, re.S)
    full = _text(re.sub(r"<br\s*/?>", "\n", body.group(1))) if body else ""
    if len(full) > len(d.description):
        d.description = full
    d.extra["page_text"] = _text(re.sub(r"<br\s*/?>", "\n", page))
    if not d.description:
        d.description = d.extra["page_text"]
    box = re.search(r"event_registration_buttons(.*?)(?:</form>|</section>)", page, re.S)
    if box:
        main = re.search(r"<j-button[^>]*link_to_tab[^>]*>(.*?)</j-button>", box.group(1), re.S)
        bm = re.search(r"<j-button[^>]*js_submit_bookmark_form[^>]*>(.*?)</j-button>", box.group(1), re.S)
        mt = _text(main.group(1)).lower() if main else ""
        if "warteliste" in mt:
            d.status = STATUS_WAITLIST
        elif mt == "angemeldet":
            d.status = STATUS_REGISTERED
        elif bm and "gemerkt" in _text(bm.group(1)).lower():
            d.status = STATUS_BOOKMARKED
    return d


# ---------------------------------------------------------------- end time

_T = r"(\d{1,2})(?:[.:](\d{2}))?"
_RANGE_RE = re.compile(_T + r"\s*Uhr\s*(?:bis|-|–|—)\s*(?:ca\.?\s*)?" + _T + r"\s*Uhr", re.I)
_END_RE = re.compile(
    r"(?:party|event|veranstaltungs)?ende\s*:?\s*(?:ist\s*)?(?:ca\.?\s*|gegen\s*|um\s*)?" + _T + r"\s*Uhr", re.I
)


def _hm(h: str, m: str | None) -> time | None:
    hour, minute = int(h), int(m or 0)
    if hour == 24:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def end_after(start: datetime, end_t: time) -> datetime:
    end = datetime.combine(start.date(), end_t, tzinfo=start.tzinfo)
    if end <= start:
        end += timedelta(days=1)
    return end


def _plausible(start: datetime, end: datetime) -> bool:
    return timedelta(hours=1) <= end - start <= timedelta(hours=16)


def end_from_description(description: str, start: datetime) -> datetime | None:
    """End of the party from the organiser's text, e.g. "19.30 Uhr bis 03.00 Uhr" or "Partyende 2:00 Uhr".

    A range whose start matches the event start wins; otherwise the range ending latest;
    then an explicit "…ende …Uhr". Loose "bis 17.00 Uhr" (deadlines) is ignored.
    """
    text = description.replace(" ", " ")
    ranges: list[tuple[time, datetime]] = []
    for m in _RANGE_RE.finditer(text):
        a, b = _hm(m.group(1), m.group(2)), _hm(m.group(3), m.group(4))
        if a is None or b is None:
            continue
        begin = datetime.combine(start.date(), a, tzinfo=start.tzinfo)
        end = end_after(begin, b)
        if _plausible(start, end) and end > start:
            ranges.append((a, end))
    exact = [e for a, e in ranges if a == start.timetz().replace(tzinfo=None)]
    if exact:
        return max(exact)
    if ranges:
        return max(e for _, e in ranges)
    for m in _END_RE.finditer(text):
        t = _hm(m.group(1), m.group(2))
        if t is None:
            continue
        end = end_after(start, t)
        if _plausible(start, end):
            return end
    return None


def rule_key(title: str, location: str) -> str:
    """Key for a learned end time: normalised title and location."""

    def norm(s: str) -> str:
        s = unicodedata.normalize("NFKC", s).lower()
        return re.sub(r"[^a-z0-9äöüß]+", " ", s).strip()

    return f"{norm(title)}|{norm(location)}"

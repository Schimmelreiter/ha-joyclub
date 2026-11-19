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
        if not card.title and (hm := re.search(r"card-ui-information-headline[^>]*>(.*?)</", body, re.DOTALL)):
            card.title = _text(hm.group(1))
        if not card.city and (cm := re.search(r'class="location[^"]*"[^>]*>(.*?)</', body, re.DOTALL)):
            card.city = _text(cm.group(1))
        buttons = [_text(b).lower() for b in re.findall(r"<j-button[^>]*>(.*?)</j-button>", body, re.DOTALL)]
        if any("warteliste" in b for b in buttons):
            card.status = STATUS_WAITLIST
        elif any(b == "angemeldet" for b in buttons):
            card.status = card.status if card.status == STATUS_WAITLIST else STATUS_REGISTERED
        elif not card.status and (bm := re.search(r"event_bookmark_btn[^>]*>(.*?)</j-button>", body, re.DOTALL)):
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


def cookie_file_path(text: str) -> str | None:
    """The input is a path to a cookie file (cookies.json / cookies.txt) rather than the cookies themselves."""
    text = (text or "").strip()
    if "\n" in text or not text.startswith("/"):
        return None
    return text


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
    ics_description: str = ""
    club_url: str = ""
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
        elif key == "DESCRIPTION" and not d.ics_description:
            d.ics_description = _ics_value(value).strip()
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
    for m in re.finditer(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", page, re.DOTALL):
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
    body = re.search(r'<div[^>]*class="[^"]*event_detail_text[^"]*"[^>]*>(.*?)</div>\s*</div>', page, re.DOTALL)
    full = _text(re.sub(r"<br\s*/?>", "\n", body.group(1))) if body else ""
    if len(full) > len(d.description):
        d.description = full
    d.extra["page_text"] = _text(re.sub(r"<br\s*/?>", "\n", page))
    if not d.description:
        d.description = d.extra["page_text"]
    d.club_url = d.club_url or club_url(page)
    box = re.search(r"event_registration_buttons(.*?)(?:</form>|</section>)", page, re.DOTALL)
    if box:
        main = re.search(r"<j-button[^>]*link_to_tab[^>]*>(.*?)</j-button>", box.group(1), re.DOTALL)
        bm = re.search(r"<j-button[^>]*js_submit_bookmark_form[^>]*>(.*?)</j-button>", box.group(1), re.DOTALL)
        mt = _text(main.group(1)).lower() if main else ""
        if "warteliste" in mt:
            d.status = STATUS_WAITLIST
        elif mt == "angemeldet":
            d.status = STATUS_REGISTERED
        elif bm and "gemerkt" in _text(bm.group(1)).lower():
            d.status = STATUS_BOOKMARKED
    return d


def club_url(page: str) -> str:
    """Profile of the club where the event takes place (the location, not the organiser).

    The page links it as "event_club". The structured data's location url often points to the
    organiser's profile (/party/...), so it is only used if it is a club profile.
    """
    if m := re.search(r'<a[^>]+href="(https://www\.joyclub\.de/club/\d+[^"#]*)"[^>]*class="[^"]*event_club', page):
        return html_lib.unescape(m.group(1))
    if m := re.search(
        r'<a[^>]+class="[^"]*event_club[^"]*"[^>]*href="(https://www\.joyclub\.de/club/\d+[^"#]*)"', page
    ):
        return html_lib.unescape(m.group(1))
    for m in re.finditer(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", page, re.DOTALL):
        try:
            ld = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(ld, dict) and ld.get("@type") == "Event":
            url = ((ld.get("location") or {}).get("url") or "").strip()
            if re.match(r"https://www\.joyclub\.de/club/\d+", url):
                return url
    return ""


_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def opening_hours(page: str) -> dict[int, tuple[str, str]]:
    """Regular opening hours of a club profile: weekday (0 = Monday) -> ("20:00", "03:00")."""
    hours: dict[int, tuple[str, str]] = {}
    for m in re.finditer(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", page, re.DOTALL):
        try:
            ld = json.loads(m.group(1))
        except ValueError:
            continue
        for spec in (ld.get("openingHoursSpecification") or []) if isinstance(ld, dict) else []:
            days = spec.get("dayOfWeek")
            for day in days if isinstance(days, list) else [days]:
                name = str(day or "").rsplit("/", 1)[-1]
                if name in _WEEKDAYS and spec.get("opens") and spec.get("closes"):
                    hours[_WEEKDAYS.index(name)] = (str(spec["opens"]), str(spec["closes"]))
    return hours


def end_from_opening_hours(hours: dict[int, tuple[str, str]], start: datetime) -> datetime | None:
    """Closing time of the club on the weekday the event starts, if plausible."""
    day = hours.get(start.weekday())
    if not day:
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", day[1].strip())
    if not m or (t := _hm(m.group(1), m.group(2))) is None:
        return None
    end = end_after(start, t)
    return end if _plausible(start, end) else None


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


# ---------------------------------------------------------------- titles

_EVENT_URL_RE = re.compile(r"https://www\.joyclub\.de/event/\d+[^\s<>\"]*")


def event_url_in(text: str) -> str:
    """First JOYclub event link in a text (description of a calendar event)."""
    m = _EVENT_URL_RE.search(text or "")
    return m.group(0) if m else ""


# Letters of other scripts that organisers use because they look like Latin ones
# fmt: off
_HOMOGLYPHS = {
    # Cyrillic
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T",
    "Х": "X", "У": "Y", "Ѕ": "S", "І": "I", "Ј": "J", "а": "a", "е": "e", "о": "o", "р": "p", "с": "c",
    "у": "y", "х": "x", "ѕ": "s", "і": "i", "ј": "j", "ԁ": "d", "ԛ": "q", "ԝ": "w",
    # Greek
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O",
    "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X", "ο": "o", "ν": "v",
    # Tifinagh
    "ⵏ": "I", "ⵑ": "I", "ⵔ": "O", "ⵙ": "O",
    # Small capitals and similar
    "ᴀ": "A", "ʙ": "B", "ᴄ": "C", "ᴅ": "D", "ᴇ": "E", "ɢ": "G", "ʜ": "H", "ɪ": "I", "ᴊ": "J", "ᴋ": "K",
    "ʟ": "L", "ᴍ": "M", "ɴ": "N", "ᴏ": "O", "ᴘ": "P", "ʀ": "R", "ꜱ": "S", "ᴛ": "T", "ᴜ": "U", "ᴠ": "V",
    "ᴡ": "W", "ʏ": "Y", "ᴢ": "Z", "ɛ": "E", "Ɛ": "E",
}
# fmt: on
# Letters in circles/squares without a compatibility decomposition (negative circled/squared, flags)
for _base in (0x1F150, 0x1F170, 0x1F1E6):
    for _i in range(26):
        _HOMOGLYPHS[chr(_base + _i)] = chr(ord("A") + _i)
_HOMOGLYPH_TABLE = str.maketrans(_HOMOGLYPHS)
_DROP_CATEGORIES = {"So", "Sk", "Cs", "Co", "Cn", "Cf", "Mn", "Me"}


def parse_abbreviations(text: str) -> dict[str, str]:
    """ "HÜ=H Ü" per line -> {"HÜ": "H Ü"}."""
    result: dict[str, str] = {}
    for line in (text or "").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip():
            result[key.strip()] = value.strip()
    return result


def _collapse_spaced(text: str) -> str:
    """ "N O L I M I T S" -> "NOLIMITS": runs of three or more single letters are joined."""
    tokens = text.split(" ")
    out: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if len(run) >= 3:
            out.append("".join(run))
        else:
            out.extend(run)
        run.clear()

    for tok in tokens:
        if len(tok) == 1 and tok.isalpha():
            run.append(tok)
        else:
            flush()
            out.append(tok)
    flush()
    return " ".join(out)


def clean_title(title: str, abbreviations: dict[str, str] | None = None) -> str:
    """Readable title: decorative letters to plain ones, no emojis, abbreviations replaced.

    "𝗡𝗢 𝗟 ⵑ 𝗠 ⵑ 𝗧 𝗦 𝑯Ü X-MAS🎄" -> "NO LIMITS HÜ X-MAS" (with {"HÜ": "H Ü"}: "NO LIMITS H Ü X-MAS").
    """
    s = unicodedata.normalize("NFKC", title or "")
    if re.search(r"[A-Za-z]", s):
        s = s.translate(_HOMOGLYPH_TABLE)
    s = "".join(" " if unicodedata.category(ch) == "Zs" else ch for ch in s)
    s = "".join(ch for ch in s if unicodedata.category(ch) not in _DROP_CATEGORIES)
    s = unicodedata.normalize("NFC", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = _collapse_spaced(s)
    for short, spoken in (abbreviations or {}).items():
        s = re.sub(r"(?<!\w)" + re.escape(short) + r"(?!\w)", spoken, s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" -–—|·*")
    return s or (title or "").strip()

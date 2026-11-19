# ruff: noqa: E501
"""Parser tests with made-up pages in JOYclub's structure."""

from __future__ import annotations

from datetime import date, datetime

from custom_components.joyclub import parser

TZ = parser.TZ

CARDS = """
<div class="jc-infinite-scroll" data-source="dates_partys_scroll_data" data-params="{&quot;page&quot;:1,&quot;additional_ajax_data&quot;:{&quot;last_item_timestamp&quot;:1}}">
<section class="card-list-ui-list" data-date="Samstag, 28.11.2026">
<div class="card-list-ui-list-item event-card-ui premium" data-card-id="a" data-element-id="111"><div class="card-ui">
<a class="card-ui-link" href="https://www.joyclub.de/event/111.test_party_marl.html"></a>
<div class="card-ui-information-headline">Test &amp; Party</div><span class="location">Marl</span></div></div>
<div class="card-list-ui-list-item event-card-ui card-ui-detail-container" data-element-id="111">
<j-button>Angemeldet</j-button><j-button>Details ansehen</j-button></div>
</section>
<section class="card-list-ui-list" data-date="Freitag, 04.12.2026">
<div class="card-list-ui-list-item event-card-ui" data-element-id="222"><a href="https://www.joyclub.de/event/222.zwei.html"></a>
<div class="card-ui-information-headline">Zwei</div></div>
<div class="card-list-ui-list-item event-card-ui card-ui-detail-container" data-element-id="222"><j-button>Warteliste</j-button></div>
<div class="card-list-ui-list-item event-card-ui" data-element-id="333"><a href="https://www.joyclub.de/event/333.drei.html"></a>
<j-button class="event_bookmark_btn">Gemerkt</j-button></div>
</section>
"""


def test_cards() -> None:
    cards = {c.event_id: c for c in parser.parse_registration_cards(CARDS)}
    assert set(cards) == {"111", "222", "333"}
    assert cards["111"].title == "Test & Party"
    assert cards["111"].city == "Marl"
    assert cards["111"].day == date(2026, 11, 28)
    assert cards["111"].status == parser.STATUS_REGISTERED
    assert cards["111"].url.endswith("/event/111.test_party_marl.html")
    assert cards["222"].status == parser.STATUS_WAITLIST
    assert cards["222"].day == date(2026, 12, 4)
    assert cards["333"].status == parser.STATUS_BOOKMARKED


def test_scroll_params_and_session() -> None:
    page = '<body data-cache-killer="0123456789abcdef0123456789abcdef01234567" data-session-user-id="42">' + CARDS
    ck, params = parser.scroll_params(page)
    assert ck.startswith("0123") and params["page"] == 1
    assert parser.session_user_id(page) == "42"
    assert parser.session_user_id(CARDS) is None


def test_clubmail() -> None:
    page = '<li  id="clubmail_notify" data-max-count="100" data-clubmail-state="{&quot;unread_message_count&quot;:7,&quot;unread_conversation_count&quot;:6}">'  # noqa: E501
    assert parser.clubmail_state(page) == {"unread_message_count": 7, "unread_conversation_count": 6}


ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:KAIs Party – HÜ-Edition
URL:https://www.joyclub.de/event/1.kais.html
DTSTART;TZID=Europe/Berlin:20261128T193000
DTEND;TZID=Europe/Berlin:20261128T193000
LOCATION:Faro 8\\, Ottostr.8\\, 45770
  Marl
GEO:51.656600;7.110100
END:VEVENT
END:VCALENDAR
"""


def test_ics() -> None:
    d = parser.parse_ics(ICS)
    assert d.title == "KAIs Party – HÜ-Edition"
    assert d.start == datetime(2026, 11, 28, 19, 30, tzinfo=TZ)
    assert d.address == "Faro 8, Ottostr.8, 45770 Marl"
    assert (d.latitude, d.longitude) == (51.6566, 7.1101)


def test_event_page() -> None:
    page = """<script type="application/ld+json">{"@type":"Event","name":"X","startDate":"2026-12-05T19:00:00+01:00",
    "endDate":"2026-12-05T23:59:00+01:00","location":{"name":"Faro 8","address":{"streetAddress":"Ottostr.8","postalCode":"45770"}},
    "description":"kurz"}</script>
    <div class="event_registration_buttons"><j-button class="link_to_tab">Angemeldet</j-button></div></form>"""
    d = parser.parse_event_page(page)
    assert d.title == "X" and d.location_name == "Faro 8"
    assert d.start == datetime(2026, 12, 5, 19, tzinfo=TZ)
    assert d.status == parser.STATUS_REGISTERED


START = datetime(2026, 11, 28, 19, 30, tzinfo=TZ)


def test_end_range_matching_start() -> None:
    text = "Einlass\nEvent-Zeit: 19.30 Uhr bis 03.00 Uhr!\nSpäter bitte bis 17.00 Uhr melden"
    assert parser.end_from_description(text, START) == datetime(2026, 11, 29, 3, 0, tzinfo=TZ)


def test_end_range_several() -> None:
    start = datetime(2026, 12, 31, 19, tzinfo=TZ)
    text = "Dinner 16.00 Uhr - 19.00 Uhr, Party 19.00 Uhr - 05.00 Uhr"
    assert parser.end_from_description(text, start) == datetime(2027, 1, 1, 5, tzinfo=TZ)


def test_end_partyende() -> None:
    start = datetime(2026, 12, 5, 19, tzinfo=TZ)
    assert parser.end_from_description("[h4]Partyende 2:00 Uhr [/h4]", start) == datetime(2026, 12, 6, 2, tzinfo=TZ)
    assert parser.end_from_description("Ende: 01 Uhr", start) == datetime(2026, 12, 6, 1, tzinfo=TZ)


def test_end_ignores_noise() -> None:
    text = "Orgien am Ende der gotischen Halle gegen 23.00 Uhr. Anmeldung bis 17.00 Uhr."
    assert parser.end_from_description(text, START) is None


def test_rule_key_normalises() -> None:
    assert parser.rule_key("𝑵𝑶 𝑳𝑰𝑴𝑰𝑻𝑺", "Faro 8") == parser.rule_key("NO LIMITS", "faro 8")


def test_extract_ssoautoid() -> None:
    v = "0f0f0f0f-1234-4abc-8def-0123456789ab"
    export = (
        '[{"domain": ".identity.joyclub.com", "name": "SSOID", "value": "x"}, {"domain": ".identity.joyclub.com", "name": "SSOAUTOID", "value": "%s"}]'
        % v
    )
    assert parser.extract_ssoautoid(export) == v
    assert parser.extract_ssoautoid('{"SSOAUTOID": "%s", "VID": "y"}' % v) == v
    assert parser.extract_ssoautoid(f"FUP_vid=a; SSOAUTOID={v}; VID=b") == v
    assert parser.extract_ssoautoid(f".identity.joyclub.com\tTRUE\t/\tTRUE\t1826589975\tSSOAUTOID\t{v}") == v
    assert parser.extract_ssoautoid(f"  {v}\n") == v
    assert parser.extract_ssoautoid('[{"name": "FUP_sid", "value": "z"}]') is None
    assert parser.extract_ssoautoid("kein Cookie") is None

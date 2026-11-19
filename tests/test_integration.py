"""Setup, entities and calendar sync against a local calendar, with a mocked JOYclub client."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.components.calendar import DATA_COMPONENT
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.joyclub import parser
from custom_components.joyclub.api import JoyclubAuthError
from custom_components.joyclub.const import (
    CONF_CANCELLED,
    CONF_CLEAN_TITLE,
    CONF_COOKIE_FILE,
    CONF_DESCRIPTION,
    CONF_INVITE,
    CONF_SSOAUTOID,
    CONF_SYNC,
    CONF_TARGET,
    DESCRIPTION_FULL,
    DOMAIN,
    EVENT_END_GUESSED,
    EVENT_RULE_LEARNED,
    MODE_YES,
)
from custom_components.joyclub.google import GoogleError, GoogleTarget

TZ = parser.TZ


class FakeJoy:
    """State behind the patched JoyclubClient methods."""

    def __init__(self) -> None:
        base = dt_util.now().astimezone(TZ).replace(hour=19, minute=0, second=0, microsecond=0) + timedelta(days=10)
        self.events = {
            "111": (
                parser.Card(
                    "111",
                    "https://www.joyclub.de/event/111.a.html",
                    base.date(),
                    "Party A",
                    "Marl",
                    parser.STATUS_REGISTERED,
                ),
                parser.EventDetails(
                    title="Party A",
                    start=base,
                    location_name="Faro 8",
                    address="Faro 8, Ottostr.8, 45770 Marl",
                    description="Event-Zeit: 19.00 Uhr bis 03.00 Uhr",
                ),
            ),
            "222": (
                parser.Card(
                    "222",
                    "https://www.joyclub.de/event/222.b.html",
                    (base + timedelta(days=7)).date(),
                    "Party B",
                    "Witten",
                    parser.STATUS_REGISTERED,
                ),
                parser.EventDetails(
                    title="Party B",
                    start=base + timedelta(days=7),
                    location_name="Treff 83",
                    description="Keine Zeiten",
                ),
            ),
        }
        self.auth_ok = True
        self.valid: set[str] | None = None  # accepted SSOAUTOID values (None: any)
        self.club_calls: list[str] = []

    async def login(self, client) -> None:
        if not self.auth_ok or (self.valid is not None and client.ssoautoid not in self.valid):
            raise JoyclubAuthError("no")
        client.user_id = "4711"
        client.clubmail = {"unread_message_count": 7, "unread_conversation_count": 6}

    async def registrations(self, client):
        return [c for c, _ in self.events.values()]

    async def details(self, client, card):
        return self.events[card.event_id][1]

    async def clubmail(self, client):
        return client.clubmail

    async def club_hours(self, client, url):
        self.club_calls.append(url)
        return {d: ("20:00", "03:00") for d in range(7)}


@pytest.fixture
def joy():
    fake = FakeJoy()
    with (
        patch("custom_components.joyclub.api.JoyclubClient._login_once", lambda c: fake.login(c)),
        patch("custom_components.joyclub.api.JoyclubClient.registrations", lambda c: fake.registrations(c)),
        patch("custom_components.joyclub.api.JoyclubClient.event_details", lambda c, card: fake.details(c, card)),
        patch("custom_components.joyclub.api.JoyclubClient.refresh_clubmail", lambda c: fake.clubmail(c)),
        patch("custom_components.joyclub.api.JoyclubClient.club_hours", lambda c, url: fake.club_hours(c, url)),
    ):
        yield fake


async def _target(hass: HomeAssistant, tmp_path) -> str:
    hass.config.config_dir = str(tmp_path)
    (tmp_path / ".storage").mkdir(exist_ok=True)
    entry = MockConfigEntry(domain="local_calendar", data={"calendar_name": "Ziel", "storage_key": "ziel"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return "calendar.ziel"


async def _events(hass: HomeAssistant, entity_id: str):
    ent = hass.data[DATA_COMPONENT].get_entity(entity_id)
    now = dt_util.now()
    return await ent.async_get_events(hass, now, now + timedelta(days=60))


async def _sync(hass: HomeAssistant) -> None:
    entries = [
        (e, e.runtime_data.events)
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED
    ]
    await hass.data[DOMAIN]["sync"].async_sync(entries)
    await hass.async_block_till_done()


async def test_config_flow(hass: HomeAssistant, joy: FakeJoy) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    joy.auth_ok = False
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Hauke", CONF_SSOAUTOID: "11111111-2222-3333-4444-555555555555"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    joy.auth_ok = True
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Hauke", CONF_SSOAUTOID: "kein Cookie"}
    )
    assert result["errors"] == {CONF_SSOAUTOID: "no_cookie"}
    export = (
        '[{"domain": ".identity.joyclub.com", "name": "SSOAUTOID", "value": "0f0f0f0f-1234-4abc-8def-0123456789ab"},'
    )
    export += ' {"domain": ".joyclub.com", "name": "FUP_sid", "value": "x"}]'
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Hauke", CONF_SSOAUTOID: export}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "4711"
    assert result["data"][CONF_SSOAUTOID] == "0f0f0f0f-1234-4abc-8def-0123456789ab"
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(result["result"].entry_id)


async def test_entities_and_sync(hass: HomeAssistant, joy: FakeJoy, tmp_path) -> None:
    target = await _target(hass, tmp_path)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hauke",
        unique_id="4711",
        data={CONF_SSOAUTOID: "abc"},
        options={CONF_SYNC: True, CONF_TARGET: target, CONF_CANCELLED: MODE_YES},
    )
    guessed = async_capture_events(hass, EVENT_END_GUESSED)
    learned = async_capture_events(hass, EVENT_RULE_LEARNED)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.joyclub_hauke_unread_clubmails").state == "7"
    assert hass.states.get("sensor.joyclub_hauke_next_party").state != "unknown"
    assert hass.states.get("calendar.joyclub_hauke_events") is not None

    await _sync(hass)
    items = {e.summary: e for e in await _events(hass, target)}
    assert set(items) == {"Party A", "Party B"}
    a = items["Party A"]
    assert (dt_util.as_local(a.end) - dt_util.as_local(a.start)) == timedelta(hours=8)  # 19:00 - 03:00 from the text
    assert len(guessed) == 1 and guessed[0].data["event_id"] == "222"  # no end in the text: default 6 h

    # Synced twice: nothing duplicated
    await _sync(hass)
    assert len(await _events(hass, target)) == 2

    # The user corrects the end of B to 01:00 -> learned for title + location
    ent = hass.data[DATA_COMPONENT].get_entity(target)
    b = items["Party B"]
    new_end = (dt_util.as_local(b.start) + timedelta(days=1)).replace(hour=1, minute=0)
    await ent.async_update_event(
        b.uid, {"summary": b.summary, "dtstart": b.start, "dtend": new_end, "description": b.description}
    )
    await _sync(hass)
    assert len(learned) == 1 and learned[0].data["learned_end"] == "01:00"
    assert hass.data[DOMAIN]["sync"].rules[parser.rule_key("Party B", "Treff 83")] == "01:00"

    # Deregistered from A: deleted from the target calendar (option yes)
    joy.events.pop("111")
    await entry.runtime_data.events.async_refresh()
    await _sync(hass)
    assert {e.summary for e in await _events(hass, target)} == {"Party B"}
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_reauth_on_rejected_cookie(hass: HomeAssistant, joy: FakeJoy) -> None:
    joy.auth_ok = False
    entry = MockConfigEntry(domain=DOMAIN, title="Hauke", unique_id="4711", data={CONF_SSOAUTOID: "alt"})
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


# ---------------------------------------------------------------- readable titles, preview, Google


def _decorate(joy: FakeJoy) -> None:
    """Party A gets a decorative title and the iCal text; Party B none."""
    _, d = joy.events["111"]
    d.title = "\U0001d5e3\U0001d5ee\U0001d5ff\U0001d601\U0001d606 A HÜ\U0001f389"
    d.ics_description = "Event-Zeit: 19.00 Uhr bis 03.00 Uhr\nGekürzt...\nhttps://www.joyclub.de/event/111.a.html"
    d.description = "Event-Zeit: 19.00 Uhr bis 03.00 Uhr\nDer ganze lange Text des Veranstalters."


async def _import_old(hass: HomeAssistant, target: str, joy: FakeJoy, summary: str) -> None:
    """An event the user imported from the iCal file before (DTEND = DTSTART fixed by hand)."""
    ent = hass.data[DATA_COMPONENT].get_entity(target)
    _, d = joy.events["111"]
    await ent.async_create_event(
        summary=summary,
        dtstart=d.start,
        dtend=d.start + timedelta(hours=8),
        description="Gekürzt...\nhttps://www.joyclub.de/event/111.a.html",
    )
    await hass.async_block_till_done()


async def test_preview_then_retitle_imported(hass: HomeAssistant, joy: FakeJoy, tmp_path) -> None:
    _decorate(joy)
    target = await _target(hass, tmp_path)
    await _import_old(hass, target, joy, "\U0001d5e3\U0001d5ee\U0001d5ff\U0001d601\U0001d606 A HÜ\U0001f389")
    entry = MockConfigEntry(
        domain=DOMAIN, title="Hauke", unique_id="4711", data={CONF_SSOAUTOID: "abc"}, options={CONF_TARGET: target}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Own calendar shows the readable title (default list: HÜ -> H Ü)
    state = hass.states.get("calendar.joyclub_hauke_events")
    assert state.attributes["message"] == "Party A H Ü"

    # Sync still off: preview lists what would happen, nothing changes
    resp = await hass.services.async_call(DOMAIN, "preview", {}, blocking=True, return_response=True)
    kinds = {(a["action"], a["event_id"]) for a in resp["actions"]}
    assert kinds == {("retitle", "111"), ("create", "222")}
    assert [e.summary for e in await _events(hass, target)] == [
        "\U0001d5e3\U0001d5ee\U0001d5ff\U0001d601\U0001d606 A HÜ\U0001f389"
    ]
    assert hass.data[DOMAIN]["sync"].created == {}

    hass.config_entries.async_update_entry(entry, options={CONF_SYNC: True, CONF_TARGET: target})
    await hass.async_block_till_done()
    await _sync(hass)
    items = {e.summary: e for e in await _events(hass, target)}
    assert set(items) == {"Party A H Ü", "Party B"}
    a = items["Party A H Ü"]
    assert (dt_util.as_local(a.end) - dt_util.as_local(a.start)) == timedelta(hours=8)  # end kept
    assert a.description.startswith("Gekürzt")  # imported text kept
    assert items["Party B"].description.endswith("https://www.joyclub.de/event/222.b.html")

    # The user renames it by hand: not changed back
    ent = hass.data[DATA_COMPONENT].get_entity(target)
    await ent.async_update_event(
        a.uid, {"summary": "Meine Party", "dtstart": a.start, "dtend": a.end, "description": a.description}
    )
    await _sync(hass)
    assert "Meine Party" in {e.summary for e in await _events(hass, target)}
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_description_option(hass: HomeAssistant, joy: FakeJoy, tmp_path) -> None:
    _decorate(joy)
    target = await _target(hass, tmp_path)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hauke",
        unique_id="4711",
        data={CONF_SSOAUTOID: "abc"},
        options={CONF_SYNC: True, CONF_TARGET: target, CONF_DESCRIPTION: DESCRIPTION_FULL, CONF_CLEAN_TITLE: False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _sync(hass)
    a = next(e for e in await _events(hass, target) if "A" in e.summary)
    assert a.summary == "\U0001d5e3\U0001d5ee\U0001d5ff\U0001d601\U0001d606 A HÜ\U0001f389"  # cleaning switched off
    assert "Der ganze lange Text" in a.description
    assert a.description.endswith("https://www.joyclub.de/event/111.a.html")
    assert await hass.config_entries.async_unload(entry.entry_id)


class FakeGoogle:
    """Stands in for GoogleTarget; the events themselves live in the local test calendar."""

    def __init__(self, guests: list[str], fail_patch: bool = False) -> None:
        self.guests = guests
        self.fail_patch = fail_patch
        self.calls: list[tuple] = []

    async def find(self, uid: str) -> dict:
        return {
            "id": f"g-{uid}",
            "attendees": [{"email": "hauke@example.com", "self": True}] + [{"email": g} for g in self.guests],
            "start": {"dateTime": "x"},
            "end": {"dateTime": "y"},
            "reminders": {"useDefault": False},
        }

    async def patch(self, event_id: str, body: dict, *, notify: bool) -> None:
        if self.fail_patch:
            raise GoogleError("nope")
        self.calls.append(("patch", event_id, body, notify))

    async def create(self, **kwargs) -> None:
        self.calls.append(("create", kwargs))

    async def delete(self, event_id: str, *, notify: bool) -> None:
        self.calls.append(("delete", event_id, notify))

    async def async_refresh(self) -> None:
        self.calls.append(("refresh",))


async def _google_setup(hass, joy, tmp_path, google: FakeGoogle, **options):
    _decorate(joy)
    target = await _target(hass, tmp_path)
    await _import_old(hass, target, joy, "Alter Titel")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hauke",
        unique_id="4711",
        data={CONF_SSOAUTOID: "abc"},
        options={CONF_SYNC: True, CONF_TARGET: target, **options},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.joyclub.sync.google_target", return_value=google):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await _sync(hass)
    return entry, target


async def test_google_retitle_without_notification_and_invite(hass: HomeAssistant, joy: FakeJoy, tmp_path) -> None:
    google = FakeGoogle(guests=["silvia@example.com"])
    entry, _ = await _google_setup(hass, joy, tmp_path, google, **{CONF_INVITE: ["hauke@example.com"]})
    patches = [c for c in google.calls if c[0] == "patch"]
    assert len(patches) == 1 and patches[0][2] == {"summary": "Party A H Ü"} and patches[0][3] is False
    creates = [c[1] for c in google.calls if c[0] == "create"]
    assert len(creates) == 1  # Party B, with the invitation, Google notifies
    assert creates[0]["summary"] == "Party B" and creates[0]["attendees"] == ["hauke@example.com"]
    assert creates[0]["notify"] is True
    assert not [c for c in google.calls if c[0] == "delete"]  # nothing deleted and recreated
    assert ("refresh",) in google.calls

    # Deregistered from A: deleted with notification of the guests
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_CANCELLED: MODE_YES})
    await hass.async_block_till_done()
    joy.events.pop("111")
    google.calls.clear()
    with patch("custom_components.joyclub.sync.google_target", return_value=google):
        await entry.runtime_data.events.async_refresh()
        await _sync(hass)
    deletes = [c for c in google.calls if c[0] == "delete"]
    assert len(deletes) == 1 and deletes[0][2] is True
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_google_patch_fails_with_guests_left_alone(hass: HomeAssistant, joy: FakeJoy, tmp_path) -> None:
    google = FakeGoogle(guests=["silvia@example.com"], fail_patch=True)
    entry, target = await _google_setup(hass, joy, tmp_path, google)
    assert not [c for c in google.calls if c[0] in ("delete", "create")]  # no invitations: created the normal way
    assert {e.summary for e in await _events(hass, target)} == {"Alter Titel", "Party B"}
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_google_patch_fails_without_guests_recreated(hass: HomeAssistant, joy: FakeJoy, tmp_path) -> None:
    google = FakeGoogle(guests=[], fail_patch=True)
    entry, _ = await _google_setup(hass, joy, tmp_path, google)
    old = await google.find("x")
    recreated = [c[1] for c in google.calls if c[0] == "create" and c[1].get("raw")]
    assert len(recreated) == 1 and recreated[0]["summary"] == "Party A H Ü" and recreated[0]["notify"] is False
    assert recreated[0]["raw"]["reminders"] == old["reminders"]  # reminders kept
    deletes = [c for c in google.calls if c[0] == "delete"]
    assert len(deletes) == 1 and deletes[0][2] is False
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_google_target_requests() -> None:
    """Parameters that reach the Calendar API."""

    class Resp:
        def __init__(self, status: int, data: dict | None = None) -> None:
            self.status, self._data = status, data

        async def json(self):
            return self._data

        async def text(self):
            return "err"

    class Auth:
        def __init__(self) -> None:
            self.calls: list = []

        async def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            if method == "get":
                return Resp(200, {"items": [{"id": "e1", "status": "confirmed"}]})
            return Resp(204 if method == "delete" else 200, {})

    auth = Auth()
    g = GoogleTarget(object(), auth, "abc@group.calendar.google.com")
    assert (await g.find("uid1"))["id"] == "e1"
    await g.patch("e1", {"summary": "S"}, notify=False)
    await g.delete("e1", notify=True)
    start = dt_util.now()
    await g.create(summary="N", start=start, end=start + timedelta(hours=6), attendees=["a@b.de"], notify=True)
    _, url, kw = auth.calls[0]
    assert url == "calendars/abc%40group.calendar.google.com/events" and kw["params"]["iCalUID"] == "uid1"
    assert auth.calls[1][0] == "patch" and auth.calls[1][2]["params"] == {"sendUpdates": "none"}
    assert auth.calls[1][1].endswith("/events/e1")
    assert auth.calls[2][0] == "delete" and auth.calls[2][2]["params"] == {"sendUpdates": "all"}
    body = auth.calls[3][2]["json"]
    assert body["attendees"] == [{"email": "a@b.de"}] and body["start"]["timeZone"]
    assert auth.calls[3][2]["params"] == {"sendUpdates": "all"}


async def test_cookie_file(hass: HomeAssistant, joy: FakeJoy, tmp_path) -> None:
    """Setup with a path; a renewed file is read again when JOYclub rejects the cookie."""
    old = "11111111-1111-4111-8111-111111111111"
    new = "22222222-2222-4222-8222-222222222222"
    f = tmp_path / "cookies.json"
    f.write_text(f'[{{"name": "SSOAUTOID", "value": "{old}", "domain": ".identity.joyclub.com"}}]')
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Hauke", CONF_SSOAUTOID: str(tmp_path / "fehlt.json")}
    )
    assert result["errors"] == {CONF_SSOAUTOID: "cookie_file"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Hauke", CONF_SSOAUTOID: str(f)}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SSOAUTOID] == old and result["data"][CONF_COOKIE_FILE] == str(f)
    entry = result["result"]
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    # Cookie renewed outside (file rewritten); JOYclub no longer takes the old one
    f.write_text(f".identity.joyclub.com\tTRUE\t/\tTRUE\t1826589975\tSSOAUTOID\t{new}\n")
    joy.valid = {new}
    client = entry.runtime_data.client
    await client.login()
    assert client.ssoautoid == new
    await entry.runtime_data.clubmail.async_refresh()
    await hass.async_block_till_done()
    assert entry.data[CONF_SSOAUTOID] == new  # remembered, without a reload
    assert entry.state is ConfigEntryState.LOADED

    # File not renewed: rejected as before (reauth)
    joy.valid = {"33333333-3333-4333-8333-333333333333"}
    with pytest.raises(JoyclubAuthError):
        await client.login()
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_end_from_club_opening_hours(hass: HomeAssistant, joy: FakeJoy, tmp_path) -> None:
    """No end in the text: the club's closing time on that weekday; one request per club."""
    _, d = joy.events["222"]
    d.club_url = "https://www.joyclub.de/club/9294867.club_o6.html"
    _, a = joy.events["111"]
    a.club_url = d.club_url
    target = await _target(hass, tmp_path)
    guessed = async_capture_events(hass, EVENT_END_GUESSED)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hauke",
        unique_id="4711",
        data={CONF_SSOAUTOID: "abc"},
        options={CONF_SYNC: True, CONF_TARGET: target},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    events = entry.runtime_data.events.data
    assert events["222"].end_source == "opening_hours"
    assert events["222"].end.astimezone(TZ).strftime("%H:%M") == "03:00"
    assert events["111"].end_source == "description"  # the text wins
    assert joy.club_calls == [d.club_url]  # cached
    await _sync(hass)
    assert [g.data["event_id"] for g in guessed] == ["222"]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_sync_waits_for_target_calendar(hass: HomeAssistant, joy: FakeJoy, tmp_path, caplog) -> None:
    """Right after a restart Google may not be readable yet: no error, retried later."""
    from homeassistant.exceptions import HomeAssistantError
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    from custom_components.joyclub import sync as sync_mod

    target = await _target(hass, tmp_path)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hauke",
        unique_id="4711",
        data={CONF_SSOAUTOID: "abc"},
        options={CONF_SYNC: True, CONF_TARGET: target},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ent = hass.data[DATA_COMPONENT].get_entity(target)
    real = type(ent).async_get_events

    async def not_ready(self, hass, start, end):
        raise HomeAssistantError("Unable to get events: Sync from server has not completed")

    with patch.object(type(ent), "async_get_events", not_ready):
        await _sync(hass)
        assert "Sync into" not in caplog.text
        preview = await hass.services.async_call(DOMAIN, "preview", {}, blocking=True, return_response=True)
        assert preview["actions"] == [
            {"target": target, "result": "calendar not ready: Unable to get events: Sync from server has not completed"}
        ]
    assert await real(ent, hass, dt_util.now(), dt_util.now() + timedelta(days=60)) == []

    # The retry runs the sync again once the calendar can be read
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=sync_mod.RETRY_DELAY + 1))
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=sync_mod.RETRY_DELAY + 15))
    await hass.async_block_till_done()
    assert {e.summary for e in await _events(hass, target)} == {"Party A", "Party B"}
    assert hass.data[DOMAIN]["sync"]._retry_unsub is None
    assert await hass.config_entries.async_unload(entry.entry_id)


def test_brand_images(tmp_path) -> None:
    """Own images from the configuration directory replace the shipped icon, which comes back without them."""
    from custom_components.joyclub.brand import apply_brand

    own, brand, default = tmp_path / "own", tmp_path / "brand", tmp_path / "default"
    default.mkdir()
    (default / "icon.png").write_bytes(b"neutral")
    (default / "icon@2x.png").write_bytes(b"neutral2")
    brand.mkdir()
    (brand / "icon.png").write_bytes(b"neutral")

    assert apply_brand(own, brand, default) is False  # no own folder
    assert sorted(p.name for p in brand.iterdir()) == ["icon.png", "icon@2x.png"]

    own.mkdir()
    (own / "icon.png").write_bytes(b"own")
    (own / "dark_icon.png").write_bytes(b"own-dark")
    (own / "notes.txt").write_text("ignored")
    (own / "other.png").write_bytes(b"ignored")
    assert apply_brand(own, brand, default) is True
    assert {p.name: p.read_bytes() for p in brand.iterdir()} == {"icon.png": b"own", "dark_icon.png": b"own-dark"}

    for p in own.iterdir():
        p.unlink()
    assert apply_brand(own, brand, default) is False
    assert {p.name: p.read_bytes() for p in brand.iterdir()} == {"icon.png": b"neutral", "icon@2x.png": b"neutral2"}

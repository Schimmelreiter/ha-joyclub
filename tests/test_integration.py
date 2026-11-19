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
    CONF_SSOAUTOID,
    CONF_SYNC,
    CONF_TARGET,
    DOMAIN,
    EVENT_END_GUESSED,
    EVENT_RULE_LEARNED,
    MODE_YES,
)

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

    async def login(self, client) -> None:
        if not self.auth_ok:
            raise JoyclubAuthError("no")
        client.user_id = "4711"
        client.clubmail = {"unread_message_count": 7, "unread_conversation_count": 6}

    async def registrations(self, client):
        return [c for c, _ in self.events.values()]

    async def details(self, client, card):
        return self.events[card.event_id][1]

    async def clubmail(self, client):
        return client.clubmail


@pytest.fixture
def joy():
    fake = FakeJoy()
    with (
        patch("custom_components.joyclub.api.JoyclubClient.login", lambda c: fake.login(c)),
        patch("custom_components.joyclub.api.JoyclubClient.registrations", lambda c: fake.registrations(c)),
        patch("custom_components.joyclub.api.JoyclubClient.event_details", lambda c, card: fake.details(c, card)),
        patch("custom_components.joyclub.api.JoyclubClient.refresh_clubmail", lambda c: fake.clubmail(c)),
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
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "Hauke", CONF_SSOAUTOID: "x"})
    assert result["errors"] == {"base": "invalid_auth"}
    joy.auth_ok = True
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Hauke", CONF_SSOAUTOID: " abc "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "4711"
    assert result["data"][CONF_SSOAUTOID] == "abc"
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

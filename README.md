# JOYclub for Home Assistant

Your JOYclub party registrations as a calendar in Home Assistant – and, if you like,
copied into another calendar (e.g. Google Calendar) **with real end times**. Plus a sensor
for unread ClubMails. Several accounts (e.g. both partners) are supported.

JOYclub's own iCal export always ends a party at its start (DTEND = DTSTART), so calendars
show it without duration. This integration takes the end from the organiser's text
(“Event-Zeit: 19.30 Uhr bis 03.00 Uhr”, “Partyende 2:00 Uhr”, “Ende: 01 Uhr”), from end times
you corrected once, or else uses a default duration and tells you.

## Installation

Marketplace (formerly HACS): add this repository as a custom repository of type
*Integration*, install **JOYclub**, restart Home Assistant.

Manual: copy `custom_components/joyclub` into your `config/custom_components`, restart.

## Setup

*Settings → Devices & services → Add integration → JOYclub*, one entry per account:

1. Log in to JOYclub in a browser with **“Angemeldet bleiben” (stay logged in)**.
2. Copy the cookie **`SSOAUTOID`** of `identity.joyclub.com` (browser developer tools →
   Application/Storage → Cookies, or a cookie export extension). It is valid for one year.
3. Paste it together with a name for the account.

The password is never needed or stored: JOYclub's login form is protected by Cloudflare
Turnstile, which an integration cannot solve. With `SSOAUTOID` the integration gets a
session the same way the website does when you return with “stay logged in”. When JOYclub
stops accepting the cookie, Home Assistant asks for a new one (*Repairs*).

Optional: the URL of a [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr)
instance. It is only used if Cloudflare ever answers a request with a challenge page.

## Entities (per account)

| Entity | Description |
| --- | --- |
| Calendar *Events* | Own future events: registered, waiting list “(Warteliste)”, bookmarked “(gemerkt)”. |
| Sensor *Unread ClubMails* | Unread messages; unread conversations as attribute. |
| Sensor *Next party* | Start of the next registered party; title, location, end and link as attributes. |

## Calendar sync (options)

| Option | Meaning |
| --- | --- |
| Copy events into a calendar | Per account; off by default. |
| Target calendar | Any calendar that can create events (Google, Local Calendar, CalDAV …). |
| Include waiting lists | Copy events you are on the waiting list for (title gets “(Warteliste)”). |
| Bookmarked events | yes / no / ask. |
| Delete events you deregistered from | yes / no / ask (default). |
| Duration if no end is known | Default 6 h. |
| Check registrations every | Default 6 h. |
| Check ClubMails every | Default 15 min. |

- An event is created once per target calendar, even if both partners are registered.
  Events already there with the JOYclub link (e.g. imported from the iCal file before)
  are not added again.
- **Learning end times:** correct the end of a created event once in the target calendar.
  On the next sync the integration remembers it for this title and location and uses it
  whenever the organiser's text does not state an end.
- An event you delete in the target calendar is not created again.

### Events on the bus

| Event | When |
| --- | --- |
| `joyclub_event_created` | An event was added to the target calendar. |
| `joyclub_end_time_guessed` | Added with the default duration – correct it once to teach the end. |
| `joyclub_end_time_learned` | A corrected end was learned (`learned_end`). |
| `joyclub_bookmarked_pending` | “Ask” for bookmarked events: answer with `joyclub.answer`. |
| `joyclub_cancelled_pending` | “Ask” for deregistered events: answer with `joyclub.answer`. |
| `joyclub_event_deleted` | A deregistered event was deleted from the target calendar. |

All carry `account`, `event_id`, `title`, `start` and (where known) `end`, `location`, `url`.

### Actions

```yaml
action: joyclub.refresh            # read all accounts now and sync

action: joyclub.answer
data:
  event_id: "1735481"
  answer: accept                   # accept / reject (bookmarked), delete / keep (deregistered)
```

Example: a notification with buttons for `joyclub_end_time_guessed` or
`joyclub_cancelled_pending` (e.g. Telegram) whose callback calls `joyclub.answer`.

## How it reads JOYclub

- Session: `www.joyclub.de/webauth/login/` → `identity.joyclub.com/authorize` (code issued
  thanks to `SSOAUTOID`) → `www.joyclub.de/webauth/authenticated/`.
- Registrations: `/dates_partys/events/registrations/` and the following pages from
  `/dates_partys/dates_partys_scroll_data`.
- Per event (cached for a day): the iCal download (start, address, coordinates) and the event
  page (description, own status).
- ClubMails: the counter in the page navigation.

Read only – the integration never registers, deregisters or writes anything on JOYclub.

## License

MIT

---

# JOYclub für Home Assistant (Deutsch)

Deine JOYclub-Anmeldungen als Kalender in Home Assistant – auf Wunsch übernommen in einen
anderen Kalender (z. B. Google) **mit echter Endzeit**, dazu ein Sensor für ungelesene
ClubMails. Mehrere Konten (z. B. beide Partner) möglich.

**Einrichtung:** bei JOYclub mit „Angemeldet bleiben“ einloggen, das Cookie `SSOAUTOID` von
`identity.joyclub.com` kopieren (ein Jahr gültig) und in *Einstellungen → Geräte & Dienste →
Integration hinzufügen → JOYclub* einfügen. Ein Passwort braucht die Integration nicht.

**Endzeit:** aus dem Text des Veranstalters („19.30 Uhr bis 03.00 Uhr“, „Partyende 2:00 Uhr“),
sonst aus einer gelernten Regel (Ende einmal im Zielkalender korrigieren – gilt dann für diese
Party an diesem Ort), sonst Standarddauer mit Hinweis-Ereignis.

**Optionen je Konto:** Kalender-Abgleich an/aus, Zielkalender, Wartelisten, Gemerkte
(ja/nein/nachfragen), Abgemeldete löschen (ja/nein/nachfragen), Standarddauer, Abfrageintervalle,
FlareSolverr-Adresse. Details siehe oben.

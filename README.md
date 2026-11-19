# JOYclub for Home Assistant

> **Unofficial.** This integration is a private, non-commercial open-source project. It is not
> affiliated with, endorsed, supported or authorised by JOYclub or F&P GmbH. JOYclub is a
> trademark of its owner.

Your JOYclub party registrations as a calendar in Home Assistant – and, if you like,
copied into another calendar (e.g. Google Calendar) **with real end times**. Plus a sensor
for unread ClubMails. Several accounts (e.g. both partners) are supported.

JOYclub's own *Add to calendar* file always ends a party at its start (DTEND = DTSTART), so
calendars show it without duration. This integration takes the end from the organiser's
description (a time range such as 19.30–03.00 or a stated end of the party), from an end time
you corrected once, or else uses a default duration and tells you.

## Installation

Marketplace (formerly HACS): add this repository as a custom repository of type
*Integration*, install **JOYclub**, restart Home Assistant.

Manual: copy `custom_components/joyclub` into your `config/custom_components`, restart.

## Setup

*Settings → Devices & services → Add integration → JOYclub*, one entry per account:

1. Log in to JOYclub in a browser with **Stay logged in** ticked.
2. Export the cookies of `joyclub.com` (e.g. with a cookie editor extension, filtered to
   joyclub.com) and paste the export – JSON, a `cookies.txt` or just the value of `SSOAUTOID`.
3. Give the account a name.

Only the cookie **`SSOAUTOID`** of `identity.joyclub.com` is kept; it is valid for one year.

Instead of pasting the cookies you can enter the **path to a cookie file** (JSON export or
`cookies.txt`, e.g. `/config/joyclub/cookies.json`). The file is read at every start and again
whenever JOYclub rejects the cookie, so renewing it can be automated by replacing the file.

The password is never needed or stored: JOYclub's login form is protected by Cloudflare
Turnstile, which an integration cannot solve. With `SSOAUTOID` the integration gets a
session the same way the website does when you return with “stay logged in”. When JOYclub
stops accepting the cookie (and a cookie file has no newer one), Home Assistant asks for a new
one (*Repairs*).

Optional: the URL of a [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr)
instance. It is only used if Cloudflare ever answers a request with a challenge page.

## Entities (per account)

| Entity | Description |
| --- | --- |
| Calendar *Events* | Own future events from *My event registrations*: registered, on the waiting list, bookmarked (marked in the title). |
| Sensor *Unread ClubMails* | Unread messages; unread conversations as attribute. |
| Sensor *Next party* | Start of the next registered party; title, location, end and link as attributes. |

## Calendar sync (options)

| Option | Meaning |
| --- | --- |
| Copy events into a calendar | Per account; off by default. |
| Target calendar | Any calendar that can create events (Google, Local Calendar, CalDAV …). |
| Include waiting lists | Also copy events you are on the waiting list for (marked in the title). |
| Invite to new parties | E-mail addresses Google sends an invitation to for every new event (Google Calendar only). |
| Description | Shortened as in *Add to calendar*, or the organiser's full text. The JOYclub link is always included. |
| Readable titles | Decorative letters (𝗡𝗢, Ⓐ, ᴀʙᴄ, look-alikes from other alphabets) become plain ones, emojis are removed. On by default. |
| Abbreviations | One per line, `abbreviation=replacement`. Default `HÜ=H Ü`, so voice assistants spell it out instead of reading the word “hü”. |
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
- **Titles of existing events** (also ones you imported yourself before) are made readable once.
  Only the title is changed: guests, reminders and the end you set stay, and guests are not
  notified. An event is never deleted and created again for this – except in Google Calendar
  when changing it fails and the event has no guests. A title you change by hand afterwards is
  left alone.
- Guests are notified when an event is deleted because you deregistered.
- **Before switching the sync on**, choose the target calendar and call `joyclub.preview`
  (*Developer tools → Actions*, with *return response*): it lists what the sync would create,
  rename and delete, without changing anything.

### Google Calendar

Home Assistant's Google calendar can only create and delete events. For changing titles,
invitations and the notification setting, the integration uses the Google integration's own
connection to the Calendar API directly (`sendUpdates`). If a Home Assistant release changes the
Google integration's internals, this falls back to plain create/delete and titles of existing
events are left unchanged (logged).

### Events on the bus

| Event | When |
| --- | --- |
| `joyclub_event_created` | An event was added to the target calendar. |
| `joyclub_end_time_guessed` | Added with the default duration – correct it once to teach the end. |
| `joyclub_end_time_learned` | A corrected end was learned (`learned_end`). |
| `joyclub_bookmarked_pending` | “Ask” for bookmarked events: answer with `joyclub.answer`. |
| `joyclub_cancelled_pending` | “Ask” for deregistered events: answer with `joyclub.answer`. |
| `joyclub_event_deleted` | A deregistered event was deleted from the target calendar. |
| `joyclub_event_retitled` | The title of an existing event was made readable (`old_title`). |

All carry `account`, `event_id`, `title`, `start` and (where known) `end`, `location`, `url`.

### Actions

```yaml
action: joyclub.refresh            # read all accounts now and sync

action: joyclub.preview            # what the sync would do, changes nothing (returns a list)

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
- *My event registrations*: `/dates_partys/events/registrations/` and the following pages from
  `/dates_partys/dates_partys_scroll_data`.
- Per event (cached for a day): the *Add to calendar* file (title, start, address, coordinates,
  shortened description) and the event page (full description, own status). If the page cannot
  be read, the file alone is used.
- ClubMails: the counter in the page navigation.

Read only – the integration never registers, deregisters or writes anything on JOYclub.

## License

MIT

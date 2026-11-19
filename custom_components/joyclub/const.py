"""Constants for the JOYclub integration."""

from __future__ import annotations

DOMAIN = "joyclub"

CONF_SSOAUTOID = "ssoautoid"
CONF_FLARESOLVERR = "flaresolverr"

# Options
CONF_SYNC = "sync"
CONF_TARGET = "target_calendar"
CONF_WAITLIST = "waitlist"
CONF_BOOKMARKED = "bookmarked"
CONF_CANCELLED = "cancelled"
CONF_DEFAULT_HOURS = "default_hours"
CONF_SCAN_HOURS = "scan_hours"
CONF_CLUBMAIL_MINUTES = "clubmail_minutes"

MODE_YES = "yes"
MODE_NO = "no"
MODE_ASK = "ask"

DEFAULT_DEFAULT_HOURS = 6
DEFAULT_SCAN_HOURS = 6
DEFAULT_CLUBMAIL_MINUTES = 15

# Events fired on the bus (for notifications/automations)
EVENT_CREATED = "joyclub_event_created"
EVENT_END_GUESSED = "joyclub_end_time_guessed"
EVENT_RULE_LEARNED = "joyclub_end_time_learned"
EVENT_ASK_BOOKMARKED = "joyclub_bookmarked_pending"
EVENT_ASK_CANCELLED = "joyclub_cancelled_pending"
EVENT_DELETED = "joyclub_event_deleted"

STORE_VERSION = 1
STORE_KEY = DOMAIN

# Marker in the description of calendar events created by the integration
MARKER = "JOYclub:"

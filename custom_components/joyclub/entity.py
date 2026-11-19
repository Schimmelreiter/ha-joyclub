"""Base entity: one device per JOYclub account."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import DOMAIN
from .parser import STATUS_BOOKMARKED, STATUS_WAITLIST


def device_info(entry: ConfigEntry) -> DeviceInfo:
    """The account as a service device."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
        name=f"JOYclub {entry.title}",
        manufacturer="JOYclub",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url="https://www.joyclub.de/dates_partys/events/registrations/",
    )


SUFFIXES = {
    "de": {STATUS_WAITLIST: " (Warteliste)", STATUS_BOOKMARKED: " (gemerkt)"},
    "en": {STATUS_WAITLIST: " (waiting list)", STATUS_BOOKMARKED: " (bookmarked)"},
}


def suffix(hass: HomeAssistant, status: str) -> str:
    """Status marker for the title in the language of Home Assistant."""
    lang = "de" if (hass.config.language or "").startswith("de") else "en"
    return SUFFIXES[lang].get(status, "")

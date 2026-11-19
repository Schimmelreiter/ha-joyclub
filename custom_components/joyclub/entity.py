"""Base entity: one device per JOYclub account."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import DOMAIN


def device_info(entry: ConfigEntry) -> DeviceInfo:
    """The account as a service device."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
        name=f"JOYclub {entry.title}",
        manufacturer="JOYclub",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url="https://www.joyclub.de/dates_partys/events/registrations/",
    )

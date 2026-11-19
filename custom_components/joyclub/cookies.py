"""The SSOAUTOID cookie: pasted once, or read from a cookie file that is renewed from outside."""

from __future__ import annotations

from pathlib import Path

from homeassistant.core import HomeAssistant

from .parser import cookie_file_path, extract_ssoautoid


class CookieFileError(Exception):
    """The cookie file cannot be read or contains no SSOAUTOID."""


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as err:
        raise CookieFileError(str(err)) from err


async def async_read_cookie_file(hass: HomeAssistant, path: str) -> str:
    """SSOAUTOID from a cookies.json / cookies.txt (export of a cookie editor)."""
    sso = extract_ssoautoid(await hass.async_add_executor_job(_read, path))
    if not sso:
        raise CookieFileError(f"No SSOAUTOID in {path}")
    return sso


async def async_resolve(hass: HomeAssistant, text: str) -> tuple[str | None, str | None]:
    """(SSOAUTOID, cookie file path) from the setup field: pasted cookies or a path to a file.

    Raises CookieFileError for a path that cannot be read; returns (None, None) if nothing is found.
    """
    if path := cookie_file_path(text):
        return await async_read_cookie_file(hass, path), path
    return extract_ssoautoid(text), None

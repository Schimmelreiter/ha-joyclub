"""JOYclub client: session from the long-lived SSOAUTOID cookie, registrations, events, ClubMail."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import aiohttp
from yarl import URL

from . import parser

_LOGGER = logging.getLogger(__name__)

BASE = "https://www.joyclub.de"
IDENTITY = "https://identity.joyclub.com"
REGISTRATIONS = BASE + "/dates_partys/events/registrations/"
SCROLL = BASE + "/dates_partys/dates_partys_scroll_data"
ICS = BASE + "/ical/download/?i_cal_id={id}&i_cal_type=i_cal_event"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0 Safari/537.36"
)
MAX_PAGES = 30


class JoyclubError(Exception):
    """JOYclub not reachable or answered unexpectedly."""


class JoyclubAuthError(JoyclubError):
    """The SSOAUTOID cookie is no longer accepted."""


def _is_challenge(status: int, text: str) -> bool:
    return status in (403, 503) and ("challenge-platform" in text or "Just a moment" in text)


class JoyclubClient:
    """One JOYclub account."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        ssoautoid: str,
        flaresolverr: str | None = None,
        cookie_reader: Callable[[], Awaitable[str | None]] | None = None,
    ) -> None:
        self._session = session
        self._ssoautoid = ssoautoid
        # Reads the cookie again (from a file) when JOYclub rejects the current one
        self._cookie_reader = cookie_reader
        self._flaresolverr = (flaresolverr or "").rstrip("/") or None
        self._user_agent = USER_AGENT
        self._lock = asyncio.Lock()
        self.user_id: str | None = None
        self.nick: str | None = None
        self.clubmail: dict | None = None
        self._set_sso_cookie()

    def _set_sso_cookie(self) -> None:
        self._session.cookie_jar.update_cookies({"SSOAUTOID": self._ssoautoid}, URL(IDENTITY + "/"))

    # ------------------------------------------------------------ transport
    async def _request(self, method: str, url: str, **kwargs: Any) -> tuple[int, str, str]:
        headers = {"User-Agent": self._user_agent, "Accept-Language": "de-DE,de;q=0.9", **kwargs.pop("headers", {})}
        try:
            async with self._session.request(
                method, url, headers=headers, timeout=aiohttp.ClientTimeout(total=40), **kwargs
            ) as r:
                text = await r.text(errors="replace")
                status, final = r.status, str(r.url)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise JoyclubError(f"{method} {url}: {err}") from err
        if _is_challenge(status, text) and self._flaresolverr:
            await self._solve_challenge(url)
            return await self._request(method, url, headers=headers, **kwargs)
        return status, final, text

    async def _solve_challenge(self, url: str) -> None:
        """Let FlareSolverr pass the Cloudflare challenge and take over its cookies and user agent."""
        cookies = [
            {"name": c.key, "value": c.value, "domain": c["domain"] or ".joyclub.de"} for c in self._session.cookie_jar
        ]
        try:
            async with self._session.post(
                self._flaresolverr + "/v1",
                json={"cmd": "request.get", "url": url, "cookies": cookies, "maxTimeout": 60000},
                timeout=aiohttp.ClientTimeout(total=90),
            ) as r:
                data = await r.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise JoyclubError(f"FlareSolverr: {err}") from err
        sol = (data or {}).get("solution") or {}
        if data.get("status") != "ok" or not sol:
            raise JoyclubError(f"FlareSolverr: {data.get('message')}")
        self._user_agent = sol.get("userAgent") or self._user_agent
        for c in sol.get("cookies") or []:
            self._session.cookie_jar.update_cookies(
                {c["name"]: c["value"]}, URL("https://" + c["domain"].lstrip(".") + "/")
            )
        _LOGGER.debug("Cloudflare challenge solved by FlareSolverr")

    @property
    def ssoautoid(self) -> str:
        return self._ssoautoid

    async def login(self) -> None:
        """Log in; if the cookie is rejected and it comes from a file, read the file again once."""
        try:
            await self._login_once()
        except JoyclubAuthError:
            if self._cookie_reader is None:
                raise
            new = await self._cookie_reader()
            if not new or new == self._ssoautoid:
                raise
            _LOGGER.info("JOYclub cookie renewed from the cookie file")
            self._ssoautoid = new
            self._set_sso_cookie()
            await self._login_once()

    async def _login_once(self) -> None:
        """OAuth flow of the website; SSOAUTOID makes identity.joyclub.com issue a code without password."""
        status, url, _ = await self._request("GET", BASE + "/webauth/login/")
        query = dict(parse_qsl(urlsplit(url).query))
        if "code_challenge" not in query:
            # already logged in (the website sent us on)
            status, _, page = await self._request("GET", REGISTRATIONS)
            if parser.session_user_id(page):
                self._remember(page)
                return
            raise JoyclubError(f"Unexpected login page {urlsplit(url).path} ({status})")
        auth = {
            k: query[k]
            for k in (
                "client_id",
                "redirect_uri",
                "response_type",
                "scope",
                "state",
                "code_challenge",
                "code_challenge_method",
            )
        }
        auth["desire"] = "login"
        status, _, body = await self._request(
            "GET", IDENTITY + "/authorize?" + urlencode(auth), headers={"Accept": "application/json", "Referer": url}
        )
        try:
            answer = json.loads(body)
        except ValueError as err:
            raise JoyclubError(f"authorize: no JSON ({status})") from err
        if not answer.get("code"):
            raise JoyclubAuthError("SSOAUTOID not accepted (login required)")
        await self._request(
            "GET",
            auth["redirect_uri"]
            + "?"
            + urlencode(
                {"code": answer["code"], "state": answer.get("state", ""), "origin_url": query.get("origin_url", "")}
            ),
        )
        status, _, page = await self._request("GET", REGISTRATIONS)
        if not parser.session_user_id(page):
            raise JoyclubAuthError("Login did not result in a session")
        self._remember(page)

    def _remember(self, page: str) -> None:
        self.user_id = parser.session_user_id(page)
        self.clubmail = parser.clubmail_state(page) or self.clubmail

    async def _get_page(self, url: str) -> str:
        """GET a page of the logged in area; logs in again once if the session has expired."""
        async with self._lock:
            for attempt in (1, 2):
                status, final, page = await self._request("GET", url)
                if status == 200 and parser.session_user_id(page):
                    self._remember(page)
                    return page
                if attempt == 1 and (
                    status in (401, 403) or "identity.joyclub.com" in final or not parser.session_user_id(page)
                ):
                    await self.login()
                    continue
                raise JoyclubError(f"GET {urlsplit(url).path}: HTTP {status}")
        raise JoyclubError("unreachable")

    # ------------------------------------------------------------ data
    async def registrations(self) -> list[parser.Card]:
        """All own events from "Dates & Events → registrations" (all pages)."""
        page = await self._get_page(REGISTRATIONS)
        cards = {c.event_id: c for c in parser.parse_registration_cards(page)}
        sp = parser.scroll_params(page)
        if sp:
            cache_killer, params = sp
            for number in range(2, MAX_PAGES + 1):
                data = dict(params, page=number)
                status, _, body = await self._request(
                    "POST",
                    SCROLL,
                    data={"cache_killer": cache_killer, "data": json.dumps(data, separators=(",", ":"))},
                    headers={"X-Requested-With": "XMLHttpRequest", "Referer": REGISTRATIONS},
                )
                try:
                    content = json.loads(body)["content"]
                except (ValueError, KeyError, TypeError) as err:
                    raise JoyclubError(f"scroll page {number}: HTTP {status}") from err
                for c in parser.parse_registration_cards("".join(content.get("elements") or [])):
                    cards.setdefault(c.event_id, c)
                if content.get("is_last_page"):
                    break
        return list(cards.values())

    async def event_details(self, card: parser.Card) -> parser.EventDetails:
        """Start, location and coordinates from the iCal file, description and status from the event page."""
        details = parser.EventDetails()
        page_error: JoyclubError | None = None
        try:
            page = await self._get_page(card.url or f"{BASE}/event/{card.event_id}.html")
        except JoyclubAuthError:
            raise
        except JoyclubError as err:  # the iCal file alone still gives title, start and a shortened text
            page, page_error = "", err
        try:
            status, _, ics = await self._request("GET", ICS.format(id=card.event_id))
        except JoyclubError:
            if page_error is not None:
                raise
            status, ics = 0, ""
        if status == 200 and "BEGIN:VEVENT" in ics:
            parser.parse_ics(ics, details)  # structured start/location/coordinates first
        elif page_error is not None:
            raise page_error
        if page:
            parser.parse_event_page(page, details)
        else:
            details.description = details.ics_description
        return details

    async def refresh_clubmail(self) -> dict | None:
        """Unread ClubMails (from the navigation of a light page)."""
        await self._get_page(BASE + "/my_joy/feed/friends/")
        return self.clubmail

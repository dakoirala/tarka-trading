"""Minimal async client for the nepalstock.com JSON API.

Politeness is built in, because nepalstock.com is fragile and shared by every investor
in Nepal:

* every request (token refreshes included) passes one global rate limiter;
* throttling or server distress (429, 5xx, connection errors) pauses *all* requests
  with exponential backoff, honouring ``Retry-After``;
* the User-Agent identifies this tool instead of posing as a browser.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from . import __version__
from .auth import TokenParser, default_token_parser, parse_prove_response

log = logging.getLogger(__name__)

BASE_URL = "https://www.nepalstock.com"

PATH_PROVE = "/api/authenticate/prove"
PATH_MARKET_STATUS = "/api/nots/nepse-data/market-open"
PATH_SECURITIES = "/api/nots/security?nonDelisted=true"
PATH_LIVE_MARKET = "/api/nots/lives-market"
PATH_MARKET_DEPTH = "/api/nots/nepse-data/marketdepth/{security_id}/"
PATH_FLOORSHEET = "/api/nots/nepse-data/floorsheet?size=500&sort=contractId,desc&page={page}"
PATH_SECURITY_FLOORSHEET = (
    "/api/nots/security/floorsheet/{security_id}?businessDate={business_date}"
    "&size=500&sort=contractid,desc&page={page}"
)

# NEPSE tokens are short-lived; the reference client treats them as stale after 45s.
TOKEN_MAX_AGE_S = 40.0

DEFAULT_RATE_PER_S = 2.0
BACKOFF_BASE_S = 2.0
BACKOFF_MAX_S = 120.0


def default_user_agent() -> str:
    override = os.environ.get("TARKA_MD_USER_AGENT")
    if override:
        return override
    contact = os.environ.get("TARKA_MD_CONTACT")
    return f"tarka-md/{__version__}" + (f" (+{contact})" if contact else "")


def default_headers() -> dict[str, str]:
    return {
        "User-Agent": default_user_agent(),
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{BASE_URL}/",
    }


class RateLimiter:
    """Spaces request starts at least ``1/rate`` apart, plus a shared cooldown."""

    def __init__(self, rate_per_s: float) -> None:
        self._interval = 1.0 / rate_per_s if rate_per_s > 0 else 0.0
        self._next = 0.0
        self._cooldown_until = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            start = max(now, self._next, self._cooldown_until)
            self._next = start + self._interval
        if start > now:
            await asyncio.sleep(start - now)

    def cool_down(self, seconds: float) -> None:
        self._cooldown_until = max(self._cooldown_until, time.monotonic() + seconds)


@dataclass(slots=True)
class Fetch:
    """One HTTP exchange, kept with enough metadata to audit gaps and latency later."""

    path: str
    req_ns: int
    recv_ns: int
    status: int | None
    body: Any
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300 and self.error is None


# Builds a POST body from the current token salts (see auth.floorsheet_payload_id).
PayloadFn = Callable[[list[int]], dict[str, Any]]


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return float(resp.headers["Retry-After"])
    except (KeyError, ValueError):
        return None


class NepseClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        *,
        rate_per_s: float = DEFAULT_RATE_PER_S,
        timeout_s: float = 15.0,
        max_retries: int = 2,
        token_parser: TokenParser | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        verify: bool | str = True,
        backoff_base_s: float = BACKOFF_BASE_S,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers=default_headers(),
            timeout=timeout_s,
            http2=transport is None,
            transport=transport,
            verify=verify,
        )
        self.limiter = RateLimiter(rate_per_s)
        self._max_retries = max_retries
        self._backoff_base_s = backoff_base_s
        self._strikes = 0
        self._token_parser = token_parser
        self._token: str | None = None
        self._salts: list[int] = []
        self._token_at = 0.0
        self._token_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "NepseClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- backoff ---------------------------------------------------------------

    def _distress(self, retry_after: float | None = None) -> None:
        self._strikes += 1
        delay = min(self._backoff_base_s * 2 ** (self._strikes - 1), BACKOFF_MAX_S)
        if retry_after is not None:
            delay = max(delay, min(retry_after, BACKOFF_MAX_S * 5))
        log.warning("NEPSE under strain (strike %d); pausing all requests %.1fs", self._strikes, delay)
        self.limiter.cool_down(delay)

    def _healthy(self) -> None:
        self._strikes = 0

    # -- auth ------------------------------------------------------------------

    async def _access_token(self, *, force: bool = False) -> tuple[str, list[int]]:
        async with self._token_lock:
            if not force and self._token and time.monotonic() - self._token_at < TOKEN_MAX_AGE_S:
                return self._token, self._salts
            if self._token_parser is None:
                self._token_parser = default_token_parser()
            await self.limiter.acquire()
            resp = await self._http.get(PATH_PROVE)
            resp.raise_for_status()
            self._token, self._salts = parse_prove_response(self._token_parser, resp.json())
            self._token_at = time.monotonic()
            log.debug("refreshed NEPSE access token")
            return self._token, self._salts

    # -- requests --------------------------------------------------------------

    async def request(self, path: str, payload_fn: PayloadFn | None = None) -> Fetch:
        """GET ``path``, or POST it with ``payload_fn(salts)`` as JSON body.

        Never raises for HTTP/transport failures; they come back as ``Fetch.error``.
        """
        req_ns = time.time_ns()
        last_error: str | None = None
        status: int | None = None
        force_token = False
        for _attempt in range(self._max_retries + 1):
            try:
                token, salts = await self._access_token(force=force_token)
                headers = {"Authorization": f"Salter {token}"}
                await self.limiter.acquire()
                if payload_fn is None:
                    resp = await self._http.get(path, headers=headers)
                else:
                    resp = await self._http.post(path, headers=headers, json=payload_fn(salts))
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                self._distress()
                continue
            except httpx.HTTPStatusError as exc:  # token endpoint failed
                code = exc.response.status_code
                last_error = f"token: HTTP {code}"
                if code == 429 or code >= 500:
                    self._distress(_retry_after(exc.response))
                continue
            except (ValueError, KeyError) as exc:  # token response not in the expected shape
                last_error = f"token: {type(exc).__name__}: {exc}"
                continue

            status = resp.status_code
            if status == 401 and not force_token:
                force_token = True
                last_error = "HTTP 401"
                continue
            if status == 429 or status >= 500:
                last_error = f"HTTP {status}"
                self._distress(_retry_after(resp))
                continue

            recv_ns = time.time_ns()
            if status >= 400:
                return Fetch(path, req_ns, recv_ns, status, None, f"HTTP {status}")
            self._healthy()
            try:
                body = resp.json()
            except ValueError:
                return Fetch(path, req_ns, recv_ns, status, resp.text, "invalid JSON")
            return Fetch(path, req_ns, recv_ns, status, body)
        return Fetch(path, req_ns, time.time_ns(), status, None, last_error)

    async def get(self, path: str) -> Fetch:
        return await self.request(path)

    async def market_status(self) -> Fetch:
        return await self.get(PATH_MARKET_STATUS)

    async def securities(self) -> Fetch:
        return await self.get(PATH_SECURITIES)

    async def live_market(self) -> Fetch:
        return await self.get(PATH_LIVE_MARKET)

    async def market_depth(self, security_id: int) -> Fetch:
        return await self.get(PATH_MARKET_DEPTH.format(security_id=security_id))

    async def floorsheet_page(self, page: int, payload_fn: PayloadFn) -> Fetch:
        return await self.request(PATH_FLOORSHEET.format(page=page), payload_fn)

    async def security_floorsheet_page(
        self, security_id: int, business_date: str, page: int, payload_fn: PayloadFn
    ) -> Fetch:
        path = PATH_SECURITY_FLOORSHEET.format(
            security_id=security_id, business_date=business_date, page=page
        )
        return await self.request(path, payload_fn)

"""Minimal async client for the nepalstock.com JSON API.

Only GET endpoints are needed for L1/L2 capture, which avoids the date- and salt-derived
POST payload ids that other endpoints (floorsheet, today-price, ...) require.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .auth import TokenParser, default_token_parser, parse_prove_response

log = logging.getLogger(__name__)

BASE_URL = "https://www.nepalstock.com"

PATH_PROVE = "/api/authenticate/prove"
PATH_MARKET_STATUS = "/api/nots/nepse-data/market-open"
PATH_SECURITIES = "/api/nots/security?nonDelisted=true"
PATH_LIVE_MARKET = "/api/nots/lives-market"
PATH_MARKET_DEPTH = "/api/nots/nepse-data/marketdepth/{security_id}/"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:89.0) Gecko/20100101 Firefox/89.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": f"{BASE_URL}/",
    "Pragma": "no-cache",
    "Cache-Control": "no-cache",
}

# NEPSE tokens are short-lived; the reference client treats them as stale after 45s.
TOKEN_MAX_AGE_S = 40.0

RETRYABLE = (httpx.TransportError,)


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


class NepseClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        *,
        timeout_s: float = 15.0,
        max_retries: int = 2,
        token_parser: TokenParser | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        verify: bool | str = True,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers=DEFAULT_HEADERS,
            timeout=timeout_s,
            http2=transport is None,
            transport=transport,
            verify=verify,
        )
        self._max_retries = max_retries
        self._token_parser = token_parser
        self._token: str | None = None
        self._token_at = 0.0
        self._token_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "NepseClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _access_token(self, *, force: bool = False) -> str:
        async with self._token_lock:
            if not force and self._token and time.monotonic() - self._token_at < TOKEN_MAX_AGE_S:
                return self._token
            if self._token_parser is None:
                self._token_parser = default_token_parser()
            resp = await self._http.get(PATH_PROVE)
            resp.raise_for_status()
            self._token, _salts = parse_prove_response(self._token_parser, resp.json())
            self._token_at = time.monotonic()
            log.debug("refreshed NEPSE access token")
            return self._token

    async def get(self, path: str) -> Fetch:
        """GET an authenticated endpoint. Never raises for HTTP/transport failures."""
        req_ns = time.time_ns()
        last_error: str | None = None
        status: int | None = None
        force_token = False
        for attempt in range(self._max_retries + 1):
            try:
                token = await self._access_token(force=force_token)
                resp = await self._http.get(path, headers={"Authorization": f"Salter {token}"})
            except RETRYABLE as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            except httpx.HTTPStatusError as exc:  # token endpoint failed
                last_error = f"token: HTTP {exc.response.status_code}"
            except (ValueError, KeyError) as exc:  # token response not in the expected shape
                last_error = f"token: {type(exc).__name__}: {exc}"
            else:
                status = resp.status_code
                if status == 401 and not force_token:
                    force_token = True
                    last_error = "HTTP 401"
                    continue
                if status < 500:
                    recv_ns = time.time_ns()
                    if status >= 400:
                        return Fetch(path, req_ns, recv_ns, status, None, f"HTTP {status}")
                    try:
                        body = resp.json()
                    except ValueError:
                        return Fetch(path, req_ns, recv_ns, status, resp.text, "invalid JSON")
                    return Fetch(path, req_ns, recv_ns, status, body)
                last_error = f"HTTP {status}"
            if attempt < self._max_retries:
                await asyncio.sleep(0.5 * 2**attempt)
        return Fetch(path, req_ns, time.time_ns(), status, None, last_error)

    async def market_status(self) -> Fetch:
        return await self.get(PATH_MARKET_STATUS)

    async def securities(self) -> Fetch:
        return await self.get(PATH_SECURITIES)

    async def live_market(self) -> Fetch:
        return await self.get(PATH_LIVE_MARKET)

    async def market_depth(self, security_id: int) -> Fetch:
        return await self.get(PATH_MARKET_DEPTH.format(security_id=security_id))

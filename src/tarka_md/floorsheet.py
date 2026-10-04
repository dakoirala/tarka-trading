"""Floorsheet (executed trades with buyer/seller broker) capture.

Two sources:

* market-wide ``/api/nots/nepse-data/floorsheet`` - the current/latest business day only;
  run it after the close so pages don't shift while trades are still arriving.
* per security ``/api/nots/security/floorsheet/{id}?businessDate=...`` - accepts a date,
  which makes it the candidate for backfilling history. How far back NEPSE serves it is
  not known yet; ``tarka-md floorsheet --symbols X --date D`` answers that.

Both are POSTs whose body id depends on the date, the token salts and the market-open id.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date

from .auth import floorsheet_payload_id
from .client import Fetch, NepseClient, PayloadFn
from .normalize import floorsheet_content
from .schedule import now_npt
from .storage import RawStore, fetch_record

log = logging.getLogger(__name__)


@dataclass
class FloorsheetResult:
    pages: int = 0
    trades: int = 0
    failed_pages: int = 0
    empty: list[str] = field(default_factory=list)  # symbols with no trades returned


def _total_pages(body) -> int:
    sheet = body.get("floorsheets", body) if isinstance(body, dict) else None
    try:
        return int(sheet.get("totalPages", 1)) if isinstance(sheet, dict) else 1
    except (TypeError, ValueError):
        return 1


def _business_date(body, fallback: str) -> str:
    rows = floorsheet_content(body)
    value = rows[0].get("businessDate") if rows else None
    return str(value)[:10] if value else fallback


async def _payload_fn(client: NepseClient, store: RawStore) -> PayloadFn:
    status = await client.market_status()
    store.write([fetch_record("market_status", status)])
    if not status.ok or not isinstance(status.body, dict) or "id" not in status.body:
        raise RuntimeError(f"cannot derive floorsheet payload: market-open failed ({status.error})")
    market_id = int(status.body["id"])

    def build(salts: list[int]) -> dict:
        return {"id": floorsheet_payload_id(market_id, salts, now_npt().day)}

    return build


async def _paginate(
    fetch_page: Callable[[int], Awaitable[Fetch]],
    store: RawStore,
    result: FloorsheetResult,
    fallback_date: str,
    **extra,
) -> int:
    """Fetch every page; returns trades seen. Each page is one raw record."""
    page, total, trades = 0, 1, 0
    while page < total:
        fetch = await fetch_page(page)
        result.pages += 1
        part = _business_date(fetch.body, fallback_date) if fetch.ok else fallback_date
        store.write([fetch_record("floorsheet", fetch, page=page, partition_date=part, **extra)])
        if not fetch.ok:
            result.failed_pages += 1
            log.warning("floorsheet page %d failed: %s", page, fetch.error)
            break
        total = _total_pages(fetch.body)
        trades += len(floorsheet_content(fetch.body))
        page += 1
    result.trades += trades
    return trades


async def capture_market_floorsheet(client: NepseClient, store: RawStore) -> FloorsheetResult:
    payload = await _payload_fn(client, store)
    result = FloorsheetResult()
    today = now_npt().date().isoformat()
    await _paginate(lambda p: client.floorsheet_page(p, payload), store, result, today)
    log.info("market floorsheet: %d trades over %d pages", result.trades, result.pages)
    return result


async def capture_security_floorsheets(
    client: NepseClient, store: RawStore, universe: dict[int, str], business_date: date
) -> FloorsheetResult:
    payload = await _payload_fn(client, store)
    result = FloorsheetResult()
    day = business_date.isoformat()
    for sid, sym in universe.items():
        n = await _paginate(
            lambda p, sid=sid: client.security_floorsheet_page(sid, day, p, payload),
            store, result, day, security_id=sid, symbol=sym, business_date=day,
        )
        if n == 0:
            result.empty.append(sym)
        log.info("%s %s: %d trades", sym, day, n)
    return result

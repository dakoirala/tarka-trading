"""Raw NEPSE payloads -> flat rows for the L1 and L2 tables.

Field names are taken from nepalstock.com responses. Anything not recognised stays in
the raw store, so a missed field is a re-compaction away, not lost data.
"""

from __future__ import annotations

from typing import Any

from .schedule import ns_to_npt


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    f = _num(value)
    return None if f is None else int(f)


def _sorted_levels(levels: list[dict[str, Any]] | None, *, descending: bool) -> list[dict[str, Any]]:
    clean = [lvl for lvl in (levels or []) if _num(lvl.get("orderBookOrderPrice")) is not None]
    return sorted(clean, key=lambda lvl: _num(lvl["orderBookOrderPrice"]), reverse=descending)


def depth_rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per price level per side (L2). Bids level 1 = highest price, asks = lowest."""
    body = record.get("body")
    if not isinstance(body, dict):
        return []
    book = body.get("marketDepth") or {}
    base = {
        "recv_ns": record["recv_ns"],
        "trade_date": ns_to_npt(record["recv_ns"]).date(),
        "security_id": _int(record.get("security_id") or body.get("securityId")),
        "symbol": record.get("symbol") or body.get("symbol"),
        "total_buy_qty": _int(body.get("totalBuyQty")),
        "total_sell_qty": _int(body.get("totalSellQty")),
    }
    rows = []
    for side, key, desc in (("B", "buyMarketDepthList", True), ("S", "sellMarketDepthList", False)):
        for level, lvl in enumerate(_sorted_levels(book.get(key), descending=desc), start=1):
            rows.append(
                {
                    **base,
                    "side": side,
                    "level": level,
                    "price": _num(lvl.get("orderBookOrderPrice")),
                    "quantity": _int(lvl.get("quantity")),
                    "order_count": _int(lvl.get("orderCount")),
                }
            )
    return rows


def bbo_row(record: dict[str, Any]) -> dict[str, Any] | None:
    """Best bid/offer (L1 quote) derived from a depth snapshot."""
    body = record.get("body")
    if not isinstance(body, dict):
        return None
    book = body.get("marketDepth") or {}
    bids = _sorted_levels(book.get("buyMarketDepthList"), descending=True)
    asks = _sorted_levels(book.get("sellMarketDepthList"), descending=False)
    bid, ask = (bids[0] if bids else {}), (asks[0] if asks else {})
    return {
        "recv_ns": record["recv_ns"],
        "trade_date": ns_to_npt(record["recv_ns"]).date(),
        "security_id": _int(record.get("security_id") or body.get("securityId")),
        "symbol": record.get("symbol") or body.get("symbol"),
        "bid_price": _num(bid.get("orderBookOrderPrice")),
        "bid_qty": _int(bid.get("quantity")),
        "bid_orders": _int(bid.get("orderCount")),
        "ask_price": _num(ask.get("orderBookOrderPrice")),
        "ask_qty": _int(ask.get("quantity")),
        "ask_orders": _int(ask.get("orderCount")),
        "total_buy_qty": _int(body.get("totalBuyQty")),
        "total_sell_qty": _int(body.get("totalSellQty")),
    }


def live_rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-security last-trade / session stats (L1 trade side) from the lives-market feed."""
    body = record.get("body")
    if isinstance(body, dict):  # tolerate a paged {"content": [...]} wrapper
        body = body.get("content")
    if not isinstance(body, list):
        return []
    recv_ns = record["recv_ns"]
    td = ns_to_npt(recv_ns).date()
    return [
        {
            "recv_ns": recv_ns,
            "trade_date": td,
            "security_id": _int(item.get("securityId")),
            "symbol": item.get("symbol"),
            "ltp": _num(item.get("lastTradedPrice")),
            "ltv": _int(item.get("lastTradedVolume")),
            "open": _num(item.get("openPrice")),
            "high": _num(item.get("highPrice")),
            "low": _num(item.get("lowPrice")),
            "prev_close": _num(item.get("previousClose")),
            "avg_price": _num(item.get("averageTradedPrice")),
            "total_qty": _int(item.get("totalTradeQuantity")),
            "total_value": _num(item.get("totalTradeValue")),
            "pct_change": _num(item.get("percentageChange")),
            "exchange_updated": item.get("lastUpdatedDateTime"),
        }
        for item in body
        if isinstance(item, dict)
    ]

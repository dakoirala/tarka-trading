from datetime import date

from conftest import load

from tarka_md.normalize import bbo_row, depth_rows, live_rows

# 2026-10-04 07:20:00 UTC == 13:05 NPT
RECV_NS = 1_791_098_400 * 10**9


def depth_record():
    return {"stream": "depth", "recv_ns": RECV_NS, "security_id": 131, "symbol": "NABIL",
            "body": load("marketdepth.json"), "error": None}


def test_depth_rows_orders_levels_by_price():
    rows = depth_rows(depth_record())
    bids = [(r["level"], r["price"]) for r in rows if r["side"] == "B"]
    asks = [(r["level"], r["price"]) for r in rows if r["side"] == "S"]
    assert bids == [(1, 500.5), (2, 499.0), (3, 498.0)]
    assert asks == [(1, 501.0), (2, 503.0)]
    assert rows[0]["total_buy_qty"] == 5210 and rows[0]["trade_date"] == date(2026, 10, 4)


def test_bbo_from_depth():
    bbo = bbo_row(depth_record())
    assert (bbo["bid_price"], bbo["bid_qty"], bbo["ask_price"], bbo["ask_orders"]) == (500.5, 310, 501.0, 1)


def test_empty_book_side():
    rec = depth_record()
    rec["body"]["marketDepth"]["sellMarketDepthList"] = []
    assert bbo_row(rec)["ask_price"] is None
    assert all(r["side"] == "B" for r in depth_rows(rec))


def test_non_dict_body_is_skipped():
    assert depth_rows({"recv_ns": RECV_NS, "body": None}) == []
    assert bbo_row({"recv_ns": RECV_NS, "body": "oops"}) is None


def test_live_rows_coerce_types():
    rows = live_rows({"recv_ns": RECV_NS, "body": load("lives_market.json")})
    assert rows[0]["ltp"] == 500.5 and rows[0]["ltv"] == 50
    assert rows[1]["ltp"] == 412.0 and rows[1]["total_qty"] is None

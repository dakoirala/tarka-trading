"""Build daily Parquet tables from the raw capture.

Tables (``<root>/parquet/<table>/date=YYYY-MM-DD/part-0.parquet``):

* ``l2_depth``  one row per (snapshot, side, level) from market depth
* ``l1_bbo``    best bid/offer per depth snapshot
* ``l1_trades`` last trade + session stats per security per lives-market poll
* ``trades``    floorsheet: every executed contract with buyer/seller broker,
                de-duplicated on ``contract_id``

Re-running for a date overwrites that date's files, so parsing fixes can be backfilled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .normalize import bbo_row, depth_rows, live_rows, trade_rows
from .storage import RawStore

# Microseconds: ample for HTTP polling and friendlier to downstream tools than ns.
TS = pa.timestamp("us", tz="UTC")

_KEY = [
    ("recv_ts", TS),
    ("trade_date", pa.date32()),
    ("security_id", pa.int32()),
    ("symbol", pa.string()),
]

SCHEMAS = {
    "l2_depth": pa.schema(
        _KEY
        + [
            ("side", pa.string()),
            ("level", pa.int16()),
            ("price", pa.float64()),
            ("quantity", pa.int64()),
            ("order_count", pa.int32()),
            ("total_buy_qty", pa.int64()),
            ("total_sell_qty", pa.int64()),
        ]
    ),
    "l1_bbo": pa.schema(
        _KEY
        + [
            ("bid_price", pa.float64()),
            ("bid_qty", pa.int64()),
            ("bid_orders", pa.int32()),
            ("ask_price", pa.float64()),
            ("ask_qty", pa.int64()),
            ("ask_orders", pa.int32()),
            ("total_buy_qty", pa.int64()),
            ("total_sell_qty", pa.int64()),
        ]
    ),
    "l1_trades": pa.schema(
        _KEY
        + [
            ("ltp", pa.float64()),
            ("ltv", pa.int64()),
            ("open", pa.float64()),
            ("high", pa.float64()),
            ("low", pa.float64()),
            ("prev_close", pa.float64()),
            ("avg_price", pa.float64()),
            ("total_qty", pa.int64()),
            ("total_value", pa.float64()),
            ("pct_change", pa.float64()),
            ("exchange_updated", pa.string()),
        ]
    ),
    "trades": pa.schema(
        [
            ("recv_ts", TS),
            ("business_date", pa.date32()),
            ("contract_id", pa.int64()),
            ("security_id", pa.int32()),
            ("symbol", pa.string()),
            ("buyer_broker", pa.int32()),
            ("seller_broker", pa.int32()),
            ("quantity", pa.int64()),
            ("price", pa.float64()),
            ("amount", pa.float64()),
            ("trade_time", pa.string()),
            ("trade_book_id", pa.int64()),
        ]
    ),
}

SORT_KEYS = {"trades": [("contract_id", "ascending")]}


def _business_date(value) -> _date | None:
    try:
        return _date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


@dataclass
class CompactStats:
    table_rows: dict[str, int]
    raw_records: dict[str, int]
    failed_requests: dict[str, int]


def _rename_ts(row: dict) -> dict:
    row = dict(row)
    row["recv_ts"] = row.pop("recv_ns") // 1000
    return row


def compact_date(root: Path, date: str) -> CompactStats:
    store = RawStore(root)
    tables: dict[str, list[dict]] = {name: [] for name in SCHEMAS}
    raw_counts: dict[str, int] = {}
    failures: dict[str, int] = {}

    for stream in ("depth", "live_market", "floorsheet"):
        n = bad = 0
        for rec in store.read(stream, date):
            n += 1
            if rec.get("error"):
                bad += 1
                continue
            if stream == "depth":
                tables["l2_depth"].extend(depth_rows(rec))
                bbo = bbo_row(rec)
                if bbo is not None:
                    tables["l1_bbo"].append(bbo)
            elif stream == "live_market":
                tables["l1_trades"].extend(live_rows(rec))
            else:
                tables["trades"].extend(trade_rows(rec))
        raw_counts[stream], failures[stream] = n, bad

    # Pages overlap when re-fetched; keep the latest observation of each contract.
    latest: dict[int, dict] = {}
    for row in tables["trades"]:
        row["business_date"] = _business_date(row["business_date"])
        key = row["contract_id"]
        if key is not None and (key not in latest or row["recv_ns"] >= latest[key]["recv_ns"]):
            latest[key] = row
    tables["trades"] = list(latest.values())

    out_counts = {}
    for name, rows in tables.items():
        out = Path(root) / "parquet" / name / f"date={date}" / "part-0.parquet"
        if not rows:
            out_counts[name] = 0
            continue
        table = pa.Table.from_pylist([_rename_ts(r) for r in rows], schema=SCHEMAS[name])
        table = table.sort_by(SORT_KEYS.get(name, [("recv_ts", "ascending"), ("security_id", "ascending")]))
        out.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, out, compression="zstd")
        out_counts[name] = table.num_rows
    return CompactStats(out_counts, raw_counts, failures)

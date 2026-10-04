from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from datetime import date
from pathlib import Path

from .client import DEFAULT_RATE_PER_S, NepseClient
from .collector import Collector, Config, select_universe
from .compact import compact_date
from .floorsheet import capture_market_floorsheet, capture_security_floorsheets
from .schedule import now_npt
from .storage import RawStore


def _symbols(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [s.strip().upper() for s in value.split(",") if s.strip()]


async def _collect(cfg: Config, rate: float) -> None:
    async with NepseClient(rate_per_s=rate) as client:
        collector = Collector(cfg, client)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, collector.stop)
        await collector.run()


async def _floorsheet(data_dir: Path, rate: float, symbols: list[str] | None, day: str | None) -> dict:
    store = RawStore(data_dir)
    async with NepseClient(rate_per_s=rate) as client:
        if not symbols:
            result = await capture_market_floorsheet(client, store)
        else:
            secs = await client.securities()
            if not secs.ok:
                raise SystemExit(f"security list failed: {secs.error}")
            universe = select_universe(secs.body, symbols)
            business_date = date.fromisoformat(day) if day else now_npt().date()
            result = await capture_security_floorsheets(client, store, universe, business_date)
    return result.__dict__


async def _probe(symbol: str) -> dict:
    """Connectivity check from the deploy host: auth, status, live feed, one depth book."""
    async with NepseClient() as client:
        status = await client.market_status()
        out: dict = {"market_status": status.body if status.ok else status.error}
        secs = await client.securities()
        if not secs.ok:
            out["securities"] = secs.error
            return out
        universe = select_universe(secs.body, [symbol])
        out["securities"] = f"{len(secs.body)} listed"
        live = await client.live_market()
        out["live_market"] = (
            f"{len(live.body)} rows" if live.ok and isinstance(live.body, list) else live.error or live.body
        )
        if universe:
            sid = next(iter(universe))
            depth = await client.market_depth(sid)
            out["depth"] = depth.body if depth.ok else depth.error
        return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tarka-md", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    data_dir = Path(os.environ.get("TARKA_MD_DATA_DIR", "data"))
    rate_help = f"max requests/second to NEPSE across everything (default {DEFAULT_RATE_PER_S})"

    p = sub.add_parser("collect", help="run the capture loop")
    p.add_argument("--data-dir", type=Path, default=data_dir)
    p.add_argument("--symbols", default=os.environ.get("TARKA_MD_SYMBOLS"),
                   help="comma-separated depth watchlist (or set TARKA_MD_SYMBOLS)")
    p.add_argument("--all-securities", action="store_true",
                   help="sweep depth for every active security (several hundred requests per sweep)")
    p.add_argument("--rate", type=float, default=DEFAULT_RATE_PER_S, help=rate_help)
    p.add_argument("--depth-interval", type=float, default=15.0)
    p.add_argument("--live-interval", type=float, default=5.0)
    p.add_argument("--concurrency", type=int, default=2)
    p.add_argument("--no-floorsheet", action="store_true",
                   help="don't pull the day's floorsheet after the close")
    p.add_argument("--ignore-schedule", action="store_true",
                   help="capture even outside the session window / when closed")
    p.add_argument("--once", action="store_true", help="single pass then exit")

    p = sub.add_parser("floorsheet", help="capture executed trades (buyer/seller broker) once")
    p.add_argument("--data-dir", type=Path, default=data_dir)
    p.add_argument("--rate", type=float, default=DEFAULT_RATE_PER_S, help=rate_help)
    p.add_argument("--symbols", help="per-security floorsheets (needed for --date)")
    p.add_argument("--date", help="YYYY-MM-DD business date (with --symbols); default today")

    p = sub.add_parser("compact", help="build Parquet tables for a trade date from raw capture")
    p.add_argument("--data-dir", type=Path, default=data_dir)
    p.add_argument("--date", default=None, help="YYYY-MM-DD (default: today, Nepal time)")

    p = sub.add_parser("probe", help="check connectivity and print one sample of each feed")
    p.add_argument("--symbol", default="NABIL")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.cmd == "collect":
        symbols = _symbols(args.symbols)
        if not symbols and not args.all_securities:
            parser.error("collect needs --symbols (or TARKA_MD_SYMBOLS) or --all-securities")
        cfg = Config(
            data_dir=args.data_dir,
            symbols=symbols,
            all_securities=args.all_securities,
            depth_interval_s=args.depth_interval,
            live_interval_s=args.live_interval,
            concurrency=args.concurrency,
            floorsheet_after_close=not args.no_floorsheet,
            ignore_schedule=args.ignore_schedule,
            once=args.once,
        )
        asyncio.run(_collect(cfg, args.rate))
    elif args.cmd == "floorsheet":
        symbols = _symbols(args.symbols)
        if args.date and not symbols:
            parser.error("--date needs --symbols: the market-wide floorsheet only serves the latest day")
        print(json.dumps(asyncio.run(_floorsheet(args.data_dir, args.rate, symbols, args.date)), indent=2))
    elif args.cmd == "compact":
        date = args.date or now_npt().date().isoformat()
        stats = compact_date(args.data_dir, date)
        print(json.dumps({"date": date, **stats.__dict__}, indent=2))
    elif args.cmd == "probe":
        print(json.dumps(asyncio.run(_probe(args.symbol)), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

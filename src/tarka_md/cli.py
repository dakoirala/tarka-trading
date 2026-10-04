from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

from .client import NepseClient
from .collector import Collector, Config, select_universe
from .compact import compact_date
from .schedule import now_npt


def _symbols(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [s.strip().upper() for s in value.split(",") if s.strip()]


async def _collect(cfg: Config) -> None:
    async with NepseClient() as client:
        collector = Collector(cfg, client)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, collector.stop)
        await collector.run()


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

    p = sub.add_parser("collect", help="run the capture loop")
    p.add_argument("--data-dir", type=Path, default=Path(os.environ.get("TARKA_MD_DATA_DIR", "data")))
    p.add_argument("--symbols", default=os.environ.get("TARKA_MD_SYMBOLS"),
                   help="comma-separated watchlist (default: all active securities)")
    p.add_argument("--depth-interval", type=float, default=15.0)
    p.add_argument("--live-interval", type=float, default=5.0)
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--ignore-schedule", action="store_true",
                   help="capture even outside the session window / when closed")
    p.add_argument("--once", action="store_true", help="single pass then exit")

    p = sub.add_parser("compact", help="build Parquet tables for a trade date from raw capture")
    p.add_argument("--data-dir", type=Path, default=Path(os.environ.get("TARKA_MD_DATA_DIR", "data")))
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
        cfg = Config(
            data_dir=args.data_dir,
            symbols=_symbols(args.symbols),
            depth_interval_s=args.depth_interval,
            live_interval_s=args.live_interval,
            concurrency=args.concurrency,
            ignore_schedule=args.ignore_schedule,
            once=args.once,
        )
        asyncio.run(_collect(cfg))
    elif args.cmd == "compact":
        date = args.date or now_npt().date().isoformat()
        stats = compact_date(args.data_dir, date)
        print(json.dumps({"date": date, **stats.__dict__}, indent=2))
    elif args.cmd == "probe":
        print(json.dumps(asyncio.run(_probe(args.symbol)), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

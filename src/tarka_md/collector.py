"""Long-running capture loop.

Three cooperating tasks share a ``MarketState``:

* status   - polls market-open (and refreshes the security universe once per trade date)
* live     - polls lives-market (all securities in one call) -> L1 trades
* depth    - sweeps market depth for the watchlist -> L2 + L1 BBO

After a session it was open for, the status task pulls that day's floorsheet (every trade
with buyer/seller broker). Outside the NEPSE session window everything sleeps.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .client import Fetch, NepseClient
from .floorsheet import capture_market_floorsheet
from .schedule import in_window, next_window_start, now_npt, trade_date
from .storage import RawStore, fetch_record

log = logging.getLogger(__name__)

# Values of `isOpen` seen from the market-open endpoint include "OPEN", "CLOSE" and
# "Pre Open CLOSE". Anything other than a plain close is worth capturing.
CLOSED_STATES = {"CLOSE"}


@dataclass
class Config:
    data_dir: Path = Path("data")
    # Depth is one request per security, so it needs a watchlist unless explicitly told
    # to sweep everything (all_securities) - a full sweep is several hundred requests.
    symbols: list[str] | None = None
    all_securities: bool = False
    depth_interval_s: float = 15.0  # minimum time between the starts of two depth sweeps
    live_interval_s: float = 5.0
    status_interval_s: float = 60.0
    concurrency: int = 2
    floorsheet_after_close: bool = True
    ignore_schedule: bool = False  # capture regardless of clock/market status (for testing)
    once: bool = False  # one status + live + depth pass, then exit


@dataclass
class MarketState:
    is_open: bool = False
    status_text: str | None = None
    universe: dict[int, str] = field(default_factory=dict)  # security_id -> symbol
    universe_date: date | None = None
    universe_ready: asyncio.Event = field(default_factory=asyncio.Event)
    opened_on: date | None = None  # last trade date the market was seen open
    floorsheet_on: date | None = None  # last trade date the floorsheet was captured


def select_universe(securities: list[dict], symbols: list[str] | None) -> dict[int, str]:
    wanted = {s.upper() for s in symbols} if symbols else None
    universe = {}
    for sec in securities:
        sym, sid = sec.get("symbol"), sec.get("id")
        if sym is None or sid is None:
            continue
        if wanted is not None:
            if sym.upper() in wanted:
                universe[int(sid)] = sym
        elif sec.get("activeStatus", "A") == "A":
            universe[int(sid)] = sym
    if wanted:
        missing = wanted - {s.upper() for s in universe.values()}
        if missing:
            log.warning("symbols not found in NEPSE security list: %s", sorted(missing))
    return universe


class Collector:
    def __init__(self, cfg: Config, client: NepseClient, store: RawStore | None = None) -> None:
        if not cfg.symbols and not cfg.all_securities:
            raise ValueError("depth needs a watchlist: pass symbols, or all_securities=True")
        self.cfg = cfg
        self.client = client
        self.store = store or RawStore(cfg.data_dir)
        self.state = MarketState()
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(0.0, seconds))
        except TimeoutError:
            pass

    def _write(self, stream: str, fetch: Fetch, **extra) -> None:
        self.store.write([fetch_record(stream, fetch, **extra)])
        if not fetch.ok:
            log.warning("%s %s failed: %s", stream, fetch.path, fetch.error)

    def _capturing(self) -> bool:
        return self.cfg.ignore_schedule or self.state.is_open

    # -- tasks -----------------------------------------------------------------

    async def refresh_universe(self) -> None:
        fetch = await self.client.securities()
        self._write("securities", fetch)
        if fetch.ok and isinstance(fetch.body, list):
            symbols = None if self.cfg.all_securities else self.cfg.symbols
            self.state.universe = select_universe(fetch.body, symbols)
            self.state.universe_date = trade_date(now_npt())
            self.state.universe_ready.set()
            log.info("universe: %d securities", len(self.state.universe))

    async def poll_status(self) -> None:
        fetch = await self.client.market_status()
        self._write("market_status", fetch)
        if fetch.ok and isinstance(fetch.body, dict):
            text = str(fetch.body.get("isOpen", ""))
            was_open = self.state.is_open
            self.state.status_text = text
            self.state.is_open = text not in CLOSED_STATES
            if self.state.is_open:
                self.state.opened_on = trade_date(now_npt())
            if was_open != self.state.is_open:
                log.info("market status: %s (asOf %s)", text, fetch.body.get("asOf"))
            if was_open and not self.state.is_open:
                await self.after_close()

    async def after_close(self) -> None:
        today = trade_date(now_npt())
        if (
            not self.cfg.floorsheet_after_close
            or self.state.opened_on != today
            or self.state.floorsheet_on == today
        ):
            return
        try:
            result = await capture_market_floorsheet(self.client, self.store)
        except RuntimeError as exc:
            log.warning("floorsheet capture skipped: %s", exc)
            return
        if result.failed_pages == 0:
            self.state.floorsheet_on = today

    async def status_loop(self) -> None:
        while not self._stop.is_set():
            now = now_npt()
            if not self.cfg.ignore_schedule and not in_window(now):
                if self.state.is_open:
                    self.state.is_open = False
                    await self.after_close()
                wake = next_window_start(now)
                log.info("outside session window; sleeping until %s", wake.isoformat())
                # Wake at least every 15 min so clock jumps / stop requests are noticed.
                await self._sleep(min((wake - now).total_seconds(), 900))
                continue
            if self.state.universe_date != trade_date(now):
                await self.refresh_universe()
            await self.poll_status()
            await self._sleep(self.cfg.status_interval_s)

    async def live_once(self) -> None:
        self._write("live_market", await self.client.live_market())

    async def live_loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            if self._capturing():
                await self.live_once()
            await self._sleep(self.cfg.live_interval_s - (time.monotonic() - started))

    async def depth_sweep(self) -> tuple[int, int]:
        sem = asyncio.Semaphore(self.cfg.concurrency)
        universe = dict(self.state.universe)

        async def one(sid: int, sym: str) -> dict:
            async with sem:
                fetch = await self.client.market_depth(sid)
            return fetch_record("depth", fetch, security_id=sid, symbol=sym)

        records = await asyncio.gather(*(one(sid, sym) for sid, sym in universe.items()))
        self.store.write(records)
        failed = sum(1 for r in records if r["error"])
        if failed:
            log.warning("depth sweep: %d/%d requests failed", failed, len(records))
        return len(records), failed

    async def depth_loop(self) -> None:
        await self.state.universe_ready.wait()
        while not self._stop.is_set():
            started = time.monotonic()
            if self._capturing():
                n, failed = await self.depth_sweep()
                log.info("depth sweep: %d symbols, %d failed, %.1fs", n, failed, time.monotonic() - started)
            await self._sleep(self.cfg.depth_interval_s - (time.monotonic() - started))

    async def run_once(self) -> None:
        await self.refresh_universe()
        await self.poll_status()
        await self.live_once()
        if self.state.universe:
            n, failed = await self.depth_sweep()
            log.info("depth: %d symbols, %d failed", n, failed)

    async def run(self) -> None:
        if self.cfg.once:
            await self.run_once()
            return
        tasks = [
            asyncio.create_task(self.status_loop(), name="status"),
            asyncio.create_task(self.live_loop(), name="live"),
            asyncio.create_task(self.depth_loop(), name="depth"),
        ]
        stop_waiter = asyncio.create_task(self._stop.wait())
        done, _ = await asyncio.wait([*tasks, stop_waiter], return_when=asyncio.FIRST_COMPLETED)
        # A task only finishes early if it crashed; surface that instead of hanging.
        self._stop.set()
        self.state.universe_ready.set()  # release depth_loop if still waiting
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for task, result in zip(tasks, results):
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                raise RuntimeError(f"{task.get_name()} task crashed") from result

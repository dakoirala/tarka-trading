# tarka-trading

Market data capture for the Nepal Stock Exchange (NEPSE), starting with **level 1**
(last trade + best bid/offer) and **level 2** (market depth) data.

## How it works

NEPSE has no public market data API. `nepalstock.com` serves its website from a JSON
API guarded by an obfuscated, short-lived token. `tarka-md` polls that API during market
hours and stores every response verbatim. It then derives clean Parquet tables from
those stored responses. See [docs/nepse-market-data.md](docs/nepse-market-data.md) for the
endpoints, the auth scheme, data limitations and the storage layout.

| Feed | Endpoint | Gives |
|---|---|---|
| L1 trades | `GET /api/nots/lives-market` (all securities, one call) | LTP, LTV, OHLC, volume, turnover |
| L2 depth  | `GET /api/nots/nepse-data/marketdepth/{id}/` (one call per security) | top price levels per side: price, qty, order count |
| L1 quotes | derived from depth | best bid/ask, size, order count |
| Trades | `POST /api/nots/nepse-data/floorsheet` (after the close) | every executed contract with buyer and seller broker |

## Quick start

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

export TARKA_MD_CONTACT="you@example.com"   # sent in the User-Agent so NEPSE can reach you

tarka-md probe --symbol NABIL                # can this host reach NEPSE? one sample per feed
tarka-md collect --symbols NABIL,NICA,HIDCL  # run all session; floorsheet pulled after the close
tarka-md floorsheet                          # today's full floorsheet, on demand
tarka-md floorsheet --symbols NABIL --date 2025-01-05   # test how far back history goes
tarka-md compact --date 2026-10-04           # raw JSONL -> Parquet tables for that date
pytest
```

### Being a polite client

nepalstock.com is the site every investor in Nepal uses, and it is fragile. NEPSE has
publicly complained that scrapers slow it down. The collector therefore:

* caps **all** requests at `--rate` per second (default 2) through one shared limiter;
* pauses everything with exponential backoff on 429 / 5xx / connection errors, and
  honours `Retry-After`;
* identifies itself (`tarka-md/<version> (+$TARKA_MD_CONTACT)`) instead of posing as a
  browser;
* requires a depth watchlist (`--symbols`). `--all-securities` exists, but a full sweep
  is several hundred requests; at 2 req/s that is a few minutes per sweep.

Data goes to `./data` (override with `--data-dir` or `TARKA_MD_DATA_DIR`):

```
data/raw/{market_status,securities,live_market,depth,floorsheet}/date=YYYY-MM-DD/<stream>-HH.jsonl.gz
data/parquet/{l1_trades,l1_bbo,l2_depth,trades}/date=YYYY-MM-DD/part-0.parquet
```

Query with DuckDB, Polars or pandas, for example
`duckdb -c "select * from 'data/parquet/l1_bbo/*/*.parquet' where symbol='NABIL'"`.

## Deploying

Run it as one long-lived process (`docker build -t tarka-md . && docker run -v $PWD/data:/data tarka-md`).
Schedule `tarka-md compact` after 15:10 NPT each trading day. Test the host with `probe`
before relying on it: nepalstock.com is often slow, or unreachable from outside Nepal.

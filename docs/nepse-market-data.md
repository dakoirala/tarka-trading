# NEPSE market data: research notes and design

## What exists

* **Licensed data exists.** In December 2020 NEPSE launched a paid data API with
  real-time (reported as under 30 s delay) and historical data. Fees differ for
  individuals, education/research and redistributors. Licensed vendors (npstocks,
  SmartWealthPro's MDP, ...) resell it. Whether it includes market depth or order-level
  history is unconfirmed; see `docs/outreach/`.
* **No trading API for brokers.** Brokers trade through NEPSE's web TMS. SEBON has been
  working on an API policy that would allow broker-chosen TMS / API access, but nothing
  sanctioned for automated order entry exists yet.
* **The free route** is the JSON API behind `https://www.nepalstock.com`. It is
  undocumented and changes without notice, so treat everything below as
  reverse-engineered and verify it with `tarka-md probe` on the deploy host.
* **Market depth is top-of-book aggregated by price, not full order-by-order (L3).** Each side
  lists price levels with total quantity and order count. NEPSE added market depth to
  the site in 2021; the website shows 5 levels per side. We store whatever comes back.
* There is **no streaming/websocket feed**, only polling. Updates between two polls are
  never seen. Lower intervals and a smaller watchlist give finer resolution.

Sources: [NEPSE data API launch (ShareSansar, Dec 2020)](https://www.sharesansar.com/newsdetail/another-milestone-nepse-finally-brings-data-api-after-a-long-wait-real-time-data-with-only-30-second-delay-2020-12-27),
[SEBON API policy (Merolagani)](https://eng.merolagani.com/NewsDetail.aspx?newsID=62089), and the community clients
[NepseUnofficialApi](https://github.com/basic-bgnr/NepseUnofficialApi) (commit `180a550`,
June 2026), [`nepse-scraper`](https://pypi.org/project/nepse-scraper/) and
[`nepsense`](https://pypi.org/project/nepsense/), which agree on the endpoints and depth
fields below.

## Session

* Sunday–Thursday, Nepal time (UTC+05:45). Pre-open 10:30–10:45, continuous 11:00–15:00.
* The collector's clock window is padded to 10:25–15:10. Inside it, `market-open` is
  polled every 60 s and decides whether to capture (holidays come back as closed).
  `isOpen` has been seen as `OPEN`, `CLOSE` and `Pre Open CLOSE`. Only a plain `CLOSE`
  stops capture, so pre-open books are recorded.

## Auth

1. `GET /api/authenticate/prove` (no auth) returns `accessToken`, `refreshToken`,
   `salt1..5` and `serverTime`.
2. The real token is `accessToken` with 5 characters removed. Their positions come from
   functions `cdx/rdx/bdx/ndx/mdx` in a WASM module (`css.wasm`) that ships with the site.
   We call that module through the `nepse` package (`src/tarka_md/auth.py`), pinned to a
   commit.
3. Send `Authorization: Salter <token>`. Tokens go stale in under a minute; the client
   refreshes every 40 s and on any 401.

Some endpoints (floorsheet, today-price, graphs) are `POST` with an `{"id": n}` body:

```
e   = DUMMY_DATA[market_open.id] + market_open.id + 2 * day_of_month
i   = 1 if e % 10 < 4 else 3          # floorsheet variant
id  = e + salt[i+1] * day_of_month - salt[i]   # salt1..salt5, 1-based
```

`DUMMY_DATA` is a 100-entry table from the site's JS (read from the `nepse` package).
We use the Nepal-time day of month. Implemented in `auth.floorsheet_payload_id`.

## Endpoints used

| Endpoint | Notes |
|---|---|
| `GET /api/nots/nepse-data/market-open` | `{"isOpen", "asOf", "id"}` |
| `GET /api/nots/security?nonDelisted=true` | `[{"id", "symbol", "securityName", "activeStatus", ...}]`; refreshed once per trade date |
| `GET /api/nots/lives-market` | list, one item per security traded today: `lastTradedPrice`, `lastTradedVolume`, `openPrice`, `highPrice`, `lowPrice`, `previousClose`, `averageTradedPrice`, `totalTradeQuantity`, `totalTradeValue`, `percentageChange`, `lastUpdatedDateTime` |
| `POST /api/nots/nepse-data/floorsheet?size=500&sort=contractId,desc&page=N` | latest business day, all trades: `{"floorsheets": {"content": [...], "totalPages"}}`; rows have `contractId`, `stockId`, `stockSymbol`, `buyerMemberId`, `sellerMemberId`, `contractQuantity`, `contractRate`, `contractAmount`, `businessDate`, `tradeTime` |
| `POST /api/nots/security/floorsheet/{securityId}?businessDate=YYYY-MM-DD&...` | same shape, one security, any date NEPSE still serves (range unknown) |
| `GET /api/nots/nepse-data/marketdepth/{securityId}/` | `{"totalBuyQty", "totalSellQty", "marketDepth": {"buyMarketDepthList": [...], "sellMarketDepthList": [...]}}`; each level has `orderBookOrderPrice`, `quantity`, `orderCount` |

The depth fields are confirmed by two independent clients. The `lives-market` field
names are best-effort. They have not been verified against a live response from this
codebase yet, which is why the raw capture is the source of truth.

## Storage design

**Raw first.** Every HTTP exchange becomes one JSON line, including failures:
`stream`, `req_ns`, `recv_ns` (local UTC epoch ns), `path`, `status`, `error`,
`security_id`/`symbol` for depth, and the untouched `body`. Lines are gzip-appended per
stream / NPT trade date / hour. Reasons:

* The API is undocumented. If we parse a field wrongly or miss one, we fix `normalize.py`
  and re-run `compact` instead of losing history.
* Failed requests are recorded, so gaps in the book can be told apart from a quiet market.
* `req_ns`/`recv_ns` bound when each snapshot was observed. NEPSE gives no exchange
  timestamp on depth.

**Parquet for analysis**, rebuilt per date by `tarka-md compact` (zstd, UTC `recv_ts`):

| Table | Grain |
|---|---|
| `l2_depth` | snapshot × side (`B`/`S`) × level (1 = best) |
| `l1_bbo` | snapshot: best bid/ask price, qty, orders, total buy/sell qty |
| `l1_trades` | lives-market poll × security |
| `trades` | floorsheet contract (deduped on `contract_id`), partitioned by business date |

Snapshots are stored even when unchanged. Dedupe or diff in analysis if needed.

## Load and politeness

NEPSE's 2020 data API announcement explicitly cited scrapers slowing the site down. All
requests go through one limiter (`--rate`, default 2/s). Any 429/5xx/connection error
pauses every request with exponential backoff (2 s doubling to 120 s, or `Retry-After`).
The User-Agent names the tool and `TARKA_MD_CONTACT`.

Budget at 2 req/s: the live feed takes 0.2 req/s (every 5 s), leaving about 1.8 req/s
for depth. A 30-symbol watchlist then refreshes about every 17 s. `--all-securities`
(several hundred names) takes minutes per sweep. Raising `--rate` is a deliberate choice,
not a default.

## Known risks / next steps

* NEPSE has changed endpoint prefixes before (`/api/nots` vs others), the token scheme,
  and hostnames (`newweb.nepalstock.com.np` → `www.nepalstock.com`). Watch for runs of
  401/404 errors in the raw `error` field.
* nepalstock.com is slow under load and sometimes unreachable from outside Nepal. Host
  the collector somewhere `probe` works reliably.
* Gap monitoring and alerting are not built yet (e.g. failed-request rate per sweep, sweep duration).
* The after-close floorsheet is tried once per day. If it fails, run
  `tarka-md floorsheet` manually before the next session.
* How far back the per-security floorsheet serves history is untested. Check it with
  `tarka-md floorsheet --symbols NABIL --date <old date>` before planning a backfill.
  A full backfill is securities × days × pages of requests, so do it slowly or buy it.

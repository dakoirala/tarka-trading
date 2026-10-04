# Data access request: NEPSE and licensed vendors

Draft emails asking about historical and real-time order-book data. Fill in the
`[brackets]`. Send the NEPSE one first: vendors can only resell what NEPSE licenses.

## Who to contact

| Who | Site | Notes |
|---|---|---|
| Nepal Stock Exchange (market data / IT) | https://www.nepalstock.com | Runs the official data API (launched Dec 2020). Use the contact / information-officer details on the site. |
| npstocks | https://npstocks.com | Describes itself as a NEPSE-licensed data vendor. |
| MDP by SmartWealthPro (Source Code Pvt. Ltd.) | https://data.smartwealthpro.com | Describes itself as a NEPSE-licensed market data API. Docs at `/documentation/`. |

This is everyone found so far who publicly claims a NEPSE licence. Ask NEPSE for its
official list of licensed vendors in the same email.

---

## 1. To NEPSE

**Subject:** Market data licence enquiry: historical market depth and floorsheet for research

Dear NEPSE Market Data team,

I am [name], [role] at [company / independent researcher] in [city]. We are building
quantitative research on Nepal's equity market. We would like to license data through
the official NEPSE data API rather than collect it from the public website.

Could you tell us whether the following is available, from what start date, at what
granularity and on what terms?

1. **Market depth history:** the buy/sell price levels (price, quantity, order count) as
   shown in the website's market-depth view, as timestamped snapshots, for all securities.
2. **Order-level data, if offered:** individual order entries, modifications and
   cancellations with exchange timestamps.
3. **Trade history (floorsheet):** every contract with buyer/seller broker, quantity,
   rate and exchange trade time, as far back as available.
4. **Real-time access:** whether the API provides live market depth, and its update
   frequency or delay.
5. **Licensing:** the fee schedule for an [individual / research firm] user with **no
   redistribution**, delivery format (API, bulk files), and any usage restrictions.
6. **Licensed vendors:** your current list of licensed data vendors.

We are also following SEBON's work on intraday trading and the broker API policy, and
would welcome any information on timelines for programmatic market access.

Kind regards,
[name]
[phone] · [email]

---

## 2. To licensed vendors

**Subject:** Historical market depth / tick data for NEPSE: availability and pricing

Hello,

We are evaluating NEPSE data providers for quantitative research (internal use, no
redistribution). Could you tell us:

1. Do you provide **market depth (order book) history**? How many levels, at what
   snapshot frequency, and from what date?
2. Do you provide **tick-level trades / floorsheet history** with exchange timestamps and
   broker IDs? From what date?
3. **Real-time:** do you stream live depth and trades (websocket?), and what is the
   latency relative to the exchange?
4. Is your data sourced under a NEPSE licence that covers our use?
5. Pricing for historical bulk data and for an ongoing real-time feed.

A sample file (one symbol, one day) would help us evaluate.

Thanks,
[name]
[email]

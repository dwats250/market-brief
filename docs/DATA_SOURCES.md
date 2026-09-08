# Data sources: bounded acquisition inventory

Research date: 2026-09-07. The links below are provider/agency documentation, not
proof that this project has working integrations or entitlements. No credentials
were acquired, no paid service was selected, and no provider market payload is
included. Implementers must verify the selected account's actual access and use
terms before integration. Free viewing, API access, local display, LLM processing,
caching, and redistribution are separate capabilities.

## Recommended first acquisition bundle

1. One documented equities/ETF feed. First candidate: Alpaca historical REST
   with explicit `feed=sip`, ending at least 16 minutes before the run cutoff,
   subject to the account's verified historical access. Use completed daily
   history and bounded premarket bars; verify session coverage rather than assume
   an IEX live quote represents the whole premarket. No trading API routes.
2. Keyless official Treasury daily XML, BLS calendar, BEA schedule, and Fed
   announcement feeds. These establish background and scheduled/reported context.
3. A small sourced local input file for issuer earnings or material news not
   covered by the official feeds. It must contain URLs, timestamps, source kind,
   short factual notes, and allowed-use status. This is an honest assisted v0,
   not a claim of comprehensive automated news coverage.
4. Optional published Cuttingboard contract, without source-provider substitution
   or implied rights to redistribute upstream data.

Alpaca needs a key/secret pair; none is assumed present. If no permitted account
is available at implementation time, support the same normalized equity inputs
through an explicitly supplied, permitted export. Demonstrate the real brief
with that input and disclose the manual collection step. Do not buy a service or
implement an undocumented Yahoo scraper to avoid this limitation. Automated
equity acquisition remains unverified until a real permitted source succeeds.

## Inventory

E = essential domain for first-live acceptance; O = optional enhancement; L = later.
An essential domain can be sourced through a permitted export if the API path is
unavailable. Optional failures are visible and do not invite invented facts.

| Domain | Cuttingboard reuse | Local derivation | Public/free candidate and access limits | Freshness and v0 need |
|---|---|---|---|---|
| SPY/QQQ and daily equity history | Some contract context, not a complete bar history | 5 daily returns, 20-session returns, SMA50 | Alpaca documented historical bars; account required [S1–S3]. Permitted local export fallback | Last completed exchange session required; E |
| Pre-market equity direction | Do not assume board generation time is a quote timestamp | Last eligible bar versus compatible prior regular close | Delayed historical SIP candidate; probe actual extended-hours coverage [S2–S3] | ≤20 min old and labeled delayed; O; absent means current direction unknown |
| Index futures / overnight global equities | Not a guaranteed public contract | No futures derivation from SPY/QQQ | CME delayed viewing [S8]; automated use not established here | Timestamp, delay, contract month/settlement; O, no v0 scraper |
| Eleven sector ETFs | Movement exists conceptually; no confirmed public sidecar endpoint | Same-horizon returns/spreads, count up/down with denominator | Same single equity feed [S1–S3] | Previous close adequate in premarket; O beyond SPY/QQQ |
| Actual market breadth | No confirmed current public breadth field | Requires defined constituent universe and usable prices | No unrestricted comprehensive free breadth API verified tonight | Intraday timestamp + universe/denominator; L |
| Participation/concentration proxies | Measurement membership mirrored | Sector ETF count; selected mega-cap return spread | Same equity feed | Never call 22 names market breadth or actual weighted index contribution; O |
| US 2Y/5Y/10Y yields | Optional macro drivers; mixed source/date semantics | Change in percentage-point yields ×100 gives bp | Treasury daily XML [S4]; FRED DGS2/DGS5/DGS10 with registered key [S5] | Daily observation date, not live yields; O; live yield reaction L |
| DXY / USD backdrop | Dollar driver may be present | Do not recreate DXY using a different basket | ICE identifies DXY [S9]; no free automated license verified. FRED broad dollar series is a separately named daily alternative [S5] | DXY current only with dated entitled observation; O |
| USDJPY | Optional driver | FX return with explicit base/quote and baseline | Twelve Data documents forex access [S10]; verify local display/processing terms | Intraday ≤20 min or explicitly dated daily background; O |
| VIX | Volatility driver may be present | Prior-close change, not a forecast | Cboe daily VIX history [S6]; intraday access separate | Daily history cannot describe current volatility reaction; O |
| Crude, gold, silver | Optional macro drivers use futures; metals ETFs in mirror | ETFs describe fund returns, not spot/continuous futures returns | CME delayed viewing [S8]; GLD/SLV/GDX from equity feed | Date, contract/roll, delay and benchmark explicit; O; no futures scraper |
| FRED / economic backdrop | Some daily yields available | Release-to-release comparisons with revision context | FRED API [S5], registered key; keyless Treasury for initial rates | Latest known published vintage; optional in v0, no realtime-market inference |
| Economic calendar | Some board event context | Order events, time-until event | BLS ICS, BEA schedule, Fed official calendar [S7] | Checked during run or dated cache ≤24h; E; calendar is bounded, not exhaustive |
| Earnings | No comprehensive confirmed surface | Match attention symbols to dated issuer items | Issuer investor-relations earnings notices; SEC for filed results [S12] | Confirmed versus estimated date, BMO/AMC or unknown; O; no huge calendar engine |
| Macro news | Some limited context | Deduplicate titles/URLs/time; match events | Federal Reserve RSS; BLS/BEA releases [S7] | Published by cutoff, usually last 24h; E context attempt, emptiness is not no news |
| Geopolitics/global developments | No assumed comprehensive coverage | No deterministic causal inference | Official institutional/government statements and permitted publisher feeds or sourced manual notes | Publication vs event time; corroborate consequential contested reports; O |
| Higher-timeframe attention | Mirror relationships only | Two triggers specified in architecture | Same completed daily history | Date of last completed session; O |
| Cuttingboard state | Public `/contract.json` verified | Quote only; no decision computation | HTTPS GET, no repo auth required | Source generation/session and per-field age; O, offline must work |
| Exchange session calendar | Do not reuse runtime | Session open/close and last completed session | Maintained calendar package checked against NYSE [S13] | DST, holidays, early closes; E |

## Source findings and primary references

- **[S1] Alpaca coverage:** Basic is free and its live equities coverage is IEX,
  not all exchanges. Market-data endpoints other than historical crypto require
  authentication. Do not turn this into a dependency on brokerage actions.
  [About Market Data API](https://docs.alpaca.markets/us/docs/about-market-data-api)
- **[S2] Alpaca historical entitlement:** the FAQ documents unsubscribed SIP
  historical requests with an end time at least 15 minutes old; latest/snapshot
  SIP endpoints have different subscription requirements. Verify the actual
  account and never silently change the feed.
  [Market Data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq)
- **[S3] Bar semantics:** the API exposes feed, adjustment, timeframe, and
  pagination. Results are ordered by symbol first, so one page is not necessarily
  all requested symbols. Fetch all pages within a bounded budget, then report
  omissions. Confirm session-close semantics in the first integration probe.
  [Historical bars](https://docs.alpaca.markets/us/reference/stockbars)
- **[S4] Treasury:** the documented XML feed supplies daily interest rates. It is
  a concrete small background collector, not a source of intraday 2Y reactions.
  [Treasury Daily Interest Rate XML Feed](https://home.treasury.gov/treasury-daily-interest-rate-xml-feed)
- **[S5] FRED:** observations require a registered API key. Its "real-time
  periods" concern information vintages/revisions, not tick freshness. Series
  usage rights must still be checked individually.
  [Observations](https://fred.stlouisfed.org/docs/api/fred/series_observations.html),
  [Real-time periods](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html)
- **[S6] Cboe:** the VIX historical page offers daily closing data. It does not
  establish an intraday quote feed or blanket redistribution permission.
  [VIX historical data](https://www.cboe.com/tradable_products/vix/vix_historical_data)
- **[S7] Official context:** BLS offers an ICS schedule; BEA publishes release
  dates; the Fed offers press-release and speech feeds. These cover official
  sources, not every economic event or market-moving headline. No surveyed
  consensus forecast was verified; omit "beat/miss expectations" without one.
  [BLS iCal](https://www.bls.gov/help/hlpiCAL.htm),
  [BEA schedule](https://www.bea.gov/news/schedule),
  [Fed feeds](https://www.federalreserve.gov/feeds/feeds.htm),
  [Fed calendar](https://www.federalreserve.gov/newsevents/calendar.htm)
- **[S8] CME:** public quotes are delayed at least 10 minutes. The displayed
  change uses prior settlement, which differs from an equity prior-close return.
  Public viewing is not evidence of permission for programmatic scraping.
  [Delayed quotes](https://www.cmegroup.com/market-data/browse-data/delayed-quotes.html),
  [Quote explanation](https://www.cmegroup.com/trading/about-all-delayed-quotes.html)
- **[S9] ICE:** DXY identifies the ICE U.S. Dollar Index. A Fed broad dollar
  index or currency ETF must keep its own name and cannot silently replace it.
  [Currency indices](https://www.ice.com/fixed-income-data-services/index-solutions/currency-indices)
- **[S10] Twelve Data:** Basic advertises limited free access, including forex,
  but describes internal non-display usage. Live US extended-hours data is
  documented for Pro or higher. Do not select it as a proven free displayable
  premarket solution or assume model processing rights.
  [Pricing](https://twelvedata.com/pricing),
  [Pre/post-market data](https://support.twelvedata.com/en/articles/5195429-pre-post-market-data)
- **[S11] Yahoo/Google:** Yahoo's help page explicitly prohibits redistribution;
  Google's finance disclaimer restricts copying, storage, and redistribution
  without consent. Neither is selected as the default scraper or shareable-data
  foundation. Stooq terms/API stability were not verified; defer it too.
  [Yahoo data providers](https://help.yahoo.com/kb/yahoo-finance-plus/exchanges-data-providers-yahoo-finance-sln2310.html),
  [Google Finance disclaimer](https://www.google.com/intl/en-US_US/googlefinance/disclaimer/)
- **[S12] SEC:** official EDGAR APIs are candidates for filed company information,
  not a substitute for a forward earnings schedule. Use issuer notices for
  announced timing. The first build need not implement an EDGAR collector.
  [SEC APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- **[S13] Session truth:** validate the chosen calendar's holidays and early closes
  against the exchange. Do not implement a weekday-only clock.
  [NYSE hours and calendars](https://www.nyse.com/trade/hours-calendars)

## Provenance without a bibliography wall

Every displayed factual row carries a short source ID and observation age/date.
Paragraphs link to supporting IDs; a compact end-of-brief source strip exposes
publisher, title, URL, observation/publication time, and checked time. Store only
small factual extracts where permitted. No full articles, proprietary bulk
payloads, account identifiers, secrets, or signed URLs in reports or commits.

Keep local display, retention, LLM processing, and sharing status separately per
source: permitted, restricted, or unverified, with the documentation link and
checked date. Do not send restricted/unverified data to a remote model merely
because a quote can be fetched. Such a source is disabled for synthesis until a
permitted route is established; use other sources or report missing coverage.
Future share export must omit unsupported content and private personal-list /
Cuttingboard sections by default. A static HTML file is easy to send technically;
this design does not claim all its future contents will be redistributable.

## Verification limits

Documentation and Cuttingboard's public JSON shape were checked tonight. No
authenticated equities, FX, FRED, news subscription, or model call was tested.
The initial implementation begins with a short source-access probe, not an
assumption that documented access is already configured. These limitations do
not block committing this design package and require no owner product ruling now.

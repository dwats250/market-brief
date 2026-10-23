# Architecture

This is how Market Brief works today (October 2026). The original September design, written before any of it was
built, is kept unchanged in [history/2026-09-07-v0-architecture.md](history/2026-09-07-v0-architecture.md). Some of its
plans happened, some changed and some were dropped. When this page and [DECISIONS.md](../DECISIONS.md) disagree on a
detail, DECISIONS is the precise rule and this page needs fixing.

The short version: it's a small Python package with a command-line entry point, ordinary HTTP collectors, JSON files
on disk, one model adapter and one HTML template. There's no service, database, queue or framework, and no long-running
process. The interesting parts are the rules about time, evidence and state, not the plumbing.

## The pieces

| Module | Job |
|---|---|
| `schedule.py` | The exchange calendar: which checkpoint is due, its kind (synthesis, refresh or close), and the next one |
| `collect.py` | HTTP collection from Alpaca, Treasury, BLS, the Fed and (optionally) Cuttingboard, with bounded timeouts |
| `actuals.py` | Reading BLS release values (CPI, Employment Situation) once the scheduled release time has passed |
| `evidence.py` | Admitting raw observations: clocks, freshness, identity, coverage, and the record the model is allowed to see |
| `metrics.py`, `curve.py` | Deterministic calculations: returns, spreads, 50-day averages, magnitudes, Treasury curve spreads and moves |
| `context.py` | Building the bounded analyst context for a synthesis, under its edition's budget profile |
| `synthesize.py` | The one model request, and validating what comes back |
| `continuity.py` | Comparisons across runs, watch and relationship state, the frozen analysis, and the continuity bundle |
| `render.py` + `templates/brief.html.j2` | The page (HTML, plus a Markdown edition with the same facts) |
| `receipts.py` | The per-run receipt |
| `glance.py` | The Global Equity Proxies line (display only) |
| `cli.py` | Wiring it together: `premarket`, `schedule`, `publish`, `continuity-restore` and friends |

Configuration is plain JSON: `config/editions.json` (the analyst and the budget profiles), `config/sources.json`
(what's wired and what's deferred), `config/universe.json` (about twenty ETFs and large caps, with their benchmarks)
and `config/magnitude.json` (the thresholds for "small" and "large" moves). The prompt is `prompts/synthesis.md`.

## Runtime: what actually runs where

Production is all GitHub, with Cloudflare used only as a clock.

GitHub's own `schedule` trigger fires too late and too unreliably to run a brief on, so `schedule.yml` has no cron. A
Cloudflare Worker (`cloudflare/`) wakes it with a `workflow_dispatch` one minute past each hour from 13:00 to 21:00 UTC,
plus a :31 wake for the open +1 minute refresh. Those times cover both New York seasons. Most wakes resolve to SKIP;
the Python scheduler decides what's actually due from the NYSE calendar, holidays and early closes. The Worker holds
no market data or state. It does one other job: if a wake has been sitting in GitHub's queue without ever getting a
runner (it happened on 2026-09-30 and blocked the rest of the afternoon), it cancels that one stuck run before the next
dispatch. The rules for that are deliberately narrow; see [cloudflare/README.md](../cloudflare/README.md).

Each run then goes through these steps inside one GitHub Actions job:

1. **Resolve** the checkpoint, or skip. A synthesis is only due within twenty minutes of its scheduled time, so a
   late wake can't turn into a second paid attempt. Refreshes and the close get forty-five minutes, since a late
   deterministic run costs nothing.
2. **Restore** the last accepted continuity bundle from an Actions artifact (details below).
3. **Collect, calculate, synthesize or carry, validate and render.**
4. **Upload** the new continuity bundle, but only if every earlier step succeeded.
5. **Publish**: commit `publish/index.html` to `main` (never force-pushed, at most three tries, and it stops if `main`
   has moved) and deploy GitHub Pages, but only from a checkout that holds `main`'s page. That way a delayed duplicate
   run can't put an older page back.
6. **Account**: archive the run's evidence and metadata, upload the paid-attempt record if there is one, and write the
   receipt. These steps run whatever happened earlier and can't block continuity or publication.

The workflow has one concurrency group (`market-brief-pages`) without cancel-in-progress, so runs are strictly one at a
time. A few other things live in the same workflow: commissioning runs (a live brief at the current market phase,
never published as production), experiments (a chosen checkpoint, not published), and a continuity check.

## Collection and evidence

The sources are Alpaca (IEX trades and split-adjusted daily bars for the universe), the U.S. Treasury (the daily
nominal par curve and, since W43, the real TIPS curve), BLS (the release calendar, plus CPI and Employment Situation
values on release mornings), and the Federal Reserve's press-release feed. The list of what's wired and what's
deliberately not (live yields, futures, VIX, breadth, general news) is in `config/sources.json`, and the original
source research is in [DATA_SOURCES.md](DATA_SOURCES.md).

Every observation becomes a typed row with a stable identity (instrument, metric, window, benchmark, basis), a value
and unit, the time it was observed, the time it was retrieved, its source and a status. The admission rules are where
most of the care goes:

- An intraday print more than twenty minutes old is STALE; one to twenty minutes is DELAYED. IEX is one exchange's
  trades, so a "current print" here is the latest IEX trade, and it's labelled that way.
- A trade stamped after the regular-session close is after-hours. It's refused as session evidence and named, never
  used as a closing print.
- Daily rows (closes, yields) are dated, not clocked, and they're background, never "now".
- A BLS release value is current only in the session that published it, and only if the page proves it's the
  scheduled release (its official time and reference month match the calendar).
- Anything missing is named as missing with a reason. There are exactly three words for absence on the page:
  `no print`, a plain dash for "not applicable", and `not collected`.

A run keeps four clocks apart, because mixing them up caused real bugs:

| Clock | What it is | What it decides |
|---|---|---|
| Run start | When the run began (`collection_started_at`, "Generated UTC") | The session, the date and the collection windows |
| Observation window | Each request's own span, ending when its response arrives | The latest trade a response can report |
| Evidence cutoff | When collection finished (`run.target_time`) | Ages (live, delayed, stale); nothing may be observed after it |
| Session cutoff | The exchange's regular-session close | What a print can stand for: anything later is after-hours |

The evidence cutoff used to be the run's start. A trade that happened during the few seconds of collection then looked
like it came from the future and was silently dropped, which hit the busiest tickers hardest (NVDA had no current
print on 25 of 35 in-session pages in the week of 5 October). That's why the cutoff is now the end of collection and
why nothing is dropped without a reason.

## Calculations

All of it is ordinary arithmetic in versioned code, so it's easy to check:

- Daily return `100 × (C[t] / C[t−1] − 1)`, the last five kept; a 20-session return (needs 21 closes).
- Relative spread: an instrument's 20-session return minus its benchmark's, in percentage points (sectors against SPY,
  large caps against QQQ, silver and miners against gold). It's not a factor model, and the benchmark overlaps its
  constituents.
- Distance from the 50-day average, and a 50-day cross only when yesterday's and today's signs actually differ.
- Treasury changes in whole basis points; 2s10s and 5s30s spreads as long minus short; and a named curve move
  (bear steepener, parallel shift and so on) from an ordered rule table in `curve.py` (`curve-move.v2`). The model
  never names the curve move itself.
- Inflation compensation is the nominal yield minus the real yield, in whole basis points, and only when both curves
  share a date. The page calls it that, never "expected inflation".
- Comparisons across runs are keyed by identity. The same value seen again is `no_new_observation`, a missing one is
  `unavailable`, an incompatible one is `not_comparable`, and only a genuinely new observation is `changed`.

Rates are never coloured on the page. Rising yields aren't good or bad, and colour would suggest they are.

## Synthesis

Only `PREMARKET` and `OPEN_30M` call the model. Each builds an `analyst_context.json`: the admitted rows the model may
cite (one copy of each fact), the deterministic comparisons, the structured prior state (open watches with their
criteria, relationships, the closing character), the day's events and the output schema. No prior prose goes in.
The context is saved exactly as sent and hashed, so any synthesis can be replayed offline without paying again.

Budgets come from `config/editions.json`. PREMARKET uses the `rich` profile. OPEN_30M has used the `opening` profile
since W43: the full evidence record, but only the comparisons that changed. Both are capped at a 64,000-byte context
and 7,000 output tokens. Those caps are hard exposure limits, not targets. A smaller `light` profile is still defined
and tested, but nothing uses it. The prompt has an 11,000-byte ceiling.

There's exactly one request per synthesis. The model is Opus 5.5 through OpenRouter, pinned to Anthropic's endpoints,
with structured output. If OpenRouter says the primary has no available endpoint at all (a routing failure, before
anything is billed), one call goes to the fallback model. Nothing else retries: not a timeout, not a bad answer, not a
5xx. There's no repair conversation either. The provider-enforced schema is kept inside the size Anthropic's grammar
compiler has accepted (it rejected a slightly larger one once), and every bound is enforced again locally.

## Validation

Validation is code, and it fails closed. The answer must parse against the narrative schema
(`market-brief.narrative.v2`), and then:

- every evidence reference has to exist in the admitted evidence **and** in the context the model was shown;
- numbers appear only as placeholders that resolve to rows the record cites; any other digit in the prose is
  rejected, apart from a few fixed labels such as `10Y`, `2s10s` or `50-day`;
- a "change" can cite only the two observations of the comparison it names;
- attention items have to be admitted deterministic triggers;
- the take and the attention reasons can't use trade language (buy, sell, entry, target, sizing, order).

One narrow repair is allowed: if the model cites a comparison by its id where operand rows belong, and both operands
were supplied, the id is rewritten to those two rows before validation. That's logged and kept with the original
response.

Passing validation shows the citations exist. It doesn't prove the prose is right, and I still read it.

**When validation fails.** Nothing that failed is ever published as analysis. What the reader sees depends on whether
there's an accepted analysis to fall back on:

- **A rejected `PREMARKET`** publishes nothing. The page stays on the last accepted brief, which is yesterday's close.
- **A rejected `OPEN_30M`** (or a failure after its request was sent, or a context over budget) publishes as a refresh:
  fresh numbers under the premarket analysis, with a note that the opening update wasn't accepted and which analysis
  stands. The run is recorded as `DEGRADED`, the checkpoint counts as done and it isn't paid for twice.

Either way the paid call is recorded as a call, with its usage and cost, and the response is archived for diagnosis.

## Continuity

Continuity is what lets a refresh at 1 PM know what the morning said, and lets tomorrow's premarket rule on today's
watches.

All of it is in one file, `runs/continuity/bundle.json` (`market-brief.continuity-bundle.v1`), with every record
content-hashed. It holds the previous close's handoff, this session's premarket anchor, the latest accepted edition,
and the **interpretation** slot: the last accepted synthesis of the session, frozen with every row it cited at the
values the model saw, the prior state it assessed and its watch horizons. A refresh renders that frozen record under
its own fresh numbers and never writes the slot. Only a new accepted synthesis replaces it.

Code assigns every watch and relationship id; the model proposes new ones and assesses old ones but never names or
renames them. A watch carries its confirm and changes-it criteria at the values its author saw, so a threshold never
drifts with newer data. A later synthesis can call it strengthened, weakened or reversed. If it can't be judged
(missing or incomparable evidence), it's unresolved, and when its horizon passes it expires. At most three watches and
three relationships carry forward.

The close is a snapshot. Because a provider's official daily bar isn't available until the next day, a close run may
use clearly labelled PROVISIONAL prints from the last fifteen minutes of the session. It hands the session off only if
it actually observed the session.

On GitHub the bundle is restored before collection from the newest unexpired artifact of this repository's own
`schedule.yml` runs on `main`. "Own" is checked by repository id, because a fork's pull request can run a modified
workflow and upload an artifact with the same name. A run whose only failure was in publishing still counts, because
the bundle upload happens only after everything before it succeeded. If no bundle is eligible, the run cold-starts and
says so on the page. If restoring fails partway, the run stops before collecting, because a cold bundle would
otherwise overwrite good state. Artifacts expire after thirty days, so this is short-term operational memory, not
long-term storage.

## Accounting: attempts and receipts

Two small records exist only for bookkeeping. Nothing restores, admits or renders them.

- **Paid attempts.** Immediately before a production synthesis sends its request, it writes
  `runs/attempts/<session>-<checkpoint>.json`; no record, no request. The workflow uploads it whatever happens next.
  Before paying, the scheduler looks for a local record or an uploaded one and skips if it finds either. If it can't
  check, it doesn't pay. A manual rerun of a paid checkpoint is refused unless dispatched with `repay`.
- **Receipts** (`market-brief.receipt.v1`, since W43). Every checkpoint run writes one as its last step: each stage's
  outcome, the publish commit, validation result, the paid call's model, tokens and cost, and a single outcome class
  (`published`, `published_degraded`, `refused_stale_data`, `synthesis_rejected`, `push_failed`, `deploy_failed`,
  `skipped` and so on). They're kept for ninety days. A receipt records what GitHub said happened. Whether a reader
  actually saw the page is checked separately.

## Global Equity Proxies

One collapsed line under the clocks shows five US-listed country ETFs (EWY, EWJ, EWT, EWH, EWG) against their previous
US close. They're proxies, not the foreign indices: dollar-priced funds trading in US hours, carrying currency moves.
No free source I could find allowed public display of the native indices. The line costs one snapshot request, uses
the same freshness rules, sits outside the evidence and the model's context, and fails quietly. An optional static
baseline (`config/glance_baseline.json`, built with `python -m market_brief glance-baseline --from-file closes.csv`)
bolds a move larger than 95% of the fund's daily moves; without it nothing is bolded.

## The coordinator experiment (not in production)

The weak spots in the runtime above are all about where authority lives. Payment dedupe depends on artifact listings
and a concurrency group, continuity depends on artifact retention and GitHub's run history, and the reader page is a
commit to `main`. They work, but they're spread across GitHub features that weren't designed for this.

The experiment is a Cloudflare coordinator: one Worker with one SQLite-backed Durable Object that would be the single
authority for execution claims, payment authorization, accepted continuity and the reader page. A run claims its
checkpoint, asks permission at the moment it would pay, saves its candidate state and has it accepted in order. The
protocol, Worker, Python client and tests are on the unmerged branch `feat/coordinator-g1-g2`.

Where it stands:

- **G1–G2** (correct claims, payment, ordering, idempotent retries, real process kills, a point-in-time recovery
  drill): passing locally and on a staging Worker.
- **G3** (shadow equivalence): completed production runs are replayed out of band into an isolated coordinator, and
  it has to make exactly the same decisions, with byte-identical continuity and pages, for five consecutive sessions.
  One session has replayed cleanly. The five-session window hasn't started.
- **Production:** the client is off by default and no production run talks to it. There's no production environment
  for it. Deploying the newer staging build, running the replay window, merging, and moving any authority over are
  each separate decisions I haven't made yet.

Either way, GitHub stays the home of the code and its history.

## What isn't here

No intraday yields (the Treasury curve is the previous business day's official curve, and the page says so), no
futures, no VIX, no broad breadth, no general news search, no index constituent weights, and no OHLC candles (only
closes are kept). No trading interface of any kind. Some of these are deferred and some are just out of scope; the
reasons are in [DECISIONS.md](../DECISIONS.md) and `config/sources.json`.

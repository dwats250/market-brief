# Market Brief

Market Brief started as a market note I read before and during the US trading day. It's grown into the centre of a
small research system I've been building around it, but the note is still the heart of it. It collects market data
on a schedule and asks a model to interpret it only twice a day: once before the open and once thirty minutes after.
For the rest of the session the page keeps refreshing its numbers without calling the model again, and the morning's
analysis stays exactly as it was accepted.

It started because I was putting together the same picture by hand every morning: the indices, the sectors, a handful
of big names, the Treasury curve, metals, and whatever BLS was releasing that day. Gathering it was tedious. The part I
actually enjoyed was working out what had changed and whether yesterday's read still held up. So I wanted something
that did the gathering reliably and was honest about the interpreting.

Most of the engineering goes into keeping those two jobs apart. External sources (Alpaca, the U.S. Treasury, BLS, the
Federal Reserve) supply the observations. My code admits them, timestamps them, and does every calculation on top of
them: returns, spreads, curve moves, comparisons with the last session. The model gets a prepared, size-limited record
and writes the interpretation, but it can't put a figure of its own into the text, and every claim has to cite
evidence that's actually in the record it was given. Code does that checking too. If the answer fails, it's never
published as analysis. Before the open that means the last good page stays up. After the open the page still
updates, but with fresh numbers under the morning's analysis and a plain note that the new one wasn't accepted. It's
happened a few times in production, and I'd rather lose an update than publish one I can't check.

It isn't a trading system. It doesn't tell me what to buy, and nothing in it can place an order. It's a reading tool.
Lately it's also become the base for a few side projects I'm using to learn more about options, rates and how market
data actually behaves. The [project charter](docs/PROJECT_CHARTER.md) explains where that's going.

## A trading day

| Checkpoint | When | What it does | Model calls |
|---|---|---|---:|
| `PREMARKET` | 30 minutes before the NYSE open | The main read: prior close, overnight and premarket moves, rates, today's releases, open questions from yesterday | 1 |
| `OPEN_1M` | 1 minute after the open | First opening prints, under the premarket analysis | 0 |
| `OPEN_30M` | 30 minutes after the open | The opening-structure update: what the open did to the premarket read | 1 |
| `HOURLY_1100` … `HOURLY_1500` | On the hour, exchange time | Fresh numbers under the opening analysis | 0 |
| `CLOSE_1M` | 1 minute after the close | Closing snapshot, and the handoff to tomorrow's premarket | 0 |

Times follow the NYSE calendar, including holidays and early closes. The page shows them in Pacific time because
that's where I read it.

### Why only two model calls

There are really only two moments in a day when fresh interpretation earns its cost. One is before the open, when the
overnight picture is set. The other is half an hour in, once the open has shown whether that picture survived. After
that, most of what happens intraday changes the numbers more than the meaning.

So the rest of the day is deterministic. Each refresh collects, calculates and renders exactly as a synthesis does,
but instead of calling the model it carries the accepted analysis forward, frozen, with the values the analyst
actually saw. That keeps the page current for free, stops the story drifting with every print, and leaves one clear
record of what the analysis said and when. Each checkpoint gets at most one paid attempt, even across separate
runners, unless I explicitly ask to pay again.

Because the numbers and the analysis can be different ages, every page shows three clocks: **Prices** (the latest
print in the tables), **Analysis** (when the interpretation was written, and by which edition) and **Next** (the next
scheduled update, which turns into "Update due … has not published" in the browser if it's late). Treasury rates carry
their own date, since the official par curve is published once a day for the previous business day.

## How it runs

```text
  Cloudflare Worker (cron)  ──wakes──▶  GitHub Actions: schedule.yml, one run at a time
                                              │
     resolve the checkpoint from the exchange calendar (or skip)
     restore the last accepted continuity bundle (an Actions artifact, hash-checked)
                                              │
     collect:  Alpaca IEX prints and daily bars · Treasury nominal and real curves ·
               BLS calendar and release values · Federal Reserve press releases
                                              │
     evidence: admitted rows with observation times, returns, spreads, curve moves, comparisons
                                              │
            ┌──────────── synthesis checkpoint? ────────────┐
            │ yes                                           │ no
   bounded analyst context → one model call          carry the frozen
   → validation by code                              accepted analysis
            └─────────────────────┬─────────────────────────┘
                                  │
     render HTML → upload continuity → commit publish/index.html → GitHub Pages
     write a receipt for the run: each stage's outcome and any paid call
```

Cloudflare is only the clock here. GitHub Actions runs everything, the continuity bundle lives as an Actions artifact,
and the page is served from GitHub Pages. The analyst is Opus 5.5 through OpenRouter, pinned to Anthropic's own
endpoints, with one fallback model used only when the primary has no endpoint available.

Continuity is a single chain: previous close → premarket → opening structure → refreshes → close handoff. What carries
forward is structured state (watches with their criteria, relationships, the closing character of the session), never
the old prose. A watch says what would confirm it and what would change it, and a later synthesis has to rule on it.

The [architecture overview](docs/ARCHITECTURE.md) goes through all of this properly.

## What I'm experimenting with

I'd like this to need less of GitHub at runtime over time, while keeping GitHub for the code and its history. The
experiment right now is a Cloudflare coordinator: one Durable Object that would hold execution claims, payment
authorization, accepted continuity and the reader page in one place, instead of spreading them across workflow
artifacts and a concurrency group. It's built and tested locally and on a staging Worker, and I'm replaying real
production runs against it to see whether it would have made exactly the same decisions. It has no production role,
and it won't get one until that replay has held for five straight sessions and I've decided to move anything over.

There are also three separate projects nearby: Market Lab (hypothesis testing against its own observations), Options
Observatory (option chains, volatility and liquidity) and [Cuttingboard](https://github.com/dwats250/cuttingboard)
(lightweight market-structure observations). They run on their own. None of them depends on this one, and this one
doesn't depend on them. The [charter](docs/PROJECT_CHARTER.md) describes how they fit together.

## Setup

Python 3.11 or later.

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python3 -m pytest -q
.venv/bin/ruff check src tests
```

The quickest way to see a brief is a replay, which uses fictional fixture data, no network and no model:

```bash
python -m market_brief premarket --replay                     # render a fictional premarket brief
python -m market_brief premarket --replay --continuity PATH   # replay against another continuity bundle
python -m market_brief open                                   # open the latest render
```

Replay pages are labelled SAMPLE and can't be published as live. Live runs need Alpaca and OpenRouter keys, plus
`BLS_CONTACT` for BLS requests (their firewall turns away anonymous clients). In production these are GitHub Actions
secrets. Other commands:

```bash
python -m market_brief schedule --checkpoint PREMARKET            # what the workflow runs
python -m market_brief continuity-restore --from-file bundle.json
python -m market_brief publish                                    # copy the latest page to publish/index.html
```

## What a run leaves behind

The latest render goes to `output/latest.html`, and `publish` copies only that page to `publish/index.html`.

Each run writes a folder under `runs/<session>/<mode>-<checkpoint>-<time>-<id>/` with the evidence and metadata needed
to explain what happened. A synthesis also saves the exact `analyst_context.json` the model read and the
`narrative.json` it returned; a refresh records which analysis it carried. Accepted continuity is
`runs/continuity/bundle.json`. A production synthesis writes `runs/attempts/<session>-<checkpoint>.json` before it
sends the request, which is how a second runner knows not to pay again.

Rendered fictional examples are in `examples/editions/`. They replay the fixture at each checkpoint's clock (13:00,
13:31, 14:01, 17:01 and 20:03 UTC on 2026-09-08), each in a fresh run root, with SPY −0.53%, QQQ −0.61%, XLI +0.40%
and GLD −0.20% prints injected at the checkpoint's clock (19:59:58 for the close) on every edition but the premarket,
and `--full-packet` for the opening structure. Only the run-id suffix changes between regenerations.

## More reading

- [Project charter](docs/PROJECT_CHARTER.md): what I'm aiming for, the principles, the nearby projects
- [Architecture](docs/ARCHITECTURE.md): how the current system works
- [Project state](PROJECT_STATE.md): what's running now and what's next, with dated history
- [Decisions](DECISIONS.md): each engineering decision and why it was made
- [Brief schema](docs/BRIEF_SCHEMA.md) · [Visual language](docs/VISUAL_LANGUAGE.md) · [Product](docs/PRODUCT.md)
- [Data sources](docs/DATA_SOURCES.md) · [Cuttingboard boundary](docs/CUTTINGBOARD_BOUNDARY.md) ·
  [Cloudflare scheduler](cloudflare/README.md)
- [History](docs/history/2026-09-07-v0-architecture.md): the original September architecture document

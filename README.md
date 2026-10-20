# Market Brief

Market Brief is an evidence-first market briefing desk: deterministic market data first, model-assisted interpretation
second. It publishes one rich premarket brief and one interpretive update after the open each trading day, then keeps
the page fresh with deterministic updates that never call a model or rewrite the accepted analysis.

It is also the base of a small personal market research program. The [project charter](docs/PROJECT_CHARTER.md) sets
out its principles, its sibling components and the targets it is maturing toward.

## The problem it solves

Following a market session well means assembling indices, sectors, important names, rates, metals and scheduled
releases, then asking what changed and what it means. Collection is repetitive and error-prone; interpretation needs
attention. A generic AI market summary does both badly: it mixes facts with invented numbers, cannot say when a value
was observed, and rewrites yesterday's story without saying whether yesterday's reading held up.

Market Brief automates collection, keeps every fact traceable to a timestamped source, and spends model judgment only
where it adds something. It is a reader's note meant to be read in a few minutes, not a trading signal.

## How a trading day runs

| Checkpoint | Market timing | Role | Analyst calls |
|---|---|---|---:|
| `PREMARKET` | NYSE open −30m | Flagship rich brief: prior close, overnight/premarket context, continuity, watches | 1 |
| `OPEN_1M` | Open +1m | Deterministic opening-print refresh | 0 |
| `OPEN_30M` | Open +30m | Opening-structure interpretation over the full evidence record; final routine synthesis of the day | 1 |
| `HOURLY_1100` … `HOURLY_1500` | Exchange-clock hours inside the session | Deterministic numbers/state refresh under the frozen interpretation | 0 |
| `CLOSE_1M` | Close +1m | Deterministic close snapshot and continuity handoff | 0 |

Checkpoint timing is anchored to the NYSE session, including holidays and early closes. Pacific time is presentation
only.

**Why only two paid syntheses.** Interpretation is valuable at two moments: before the open, when the overnight
picture is set, and thirty minutes in, once the open has shown its structure. After that, most intraday movement
updates numbers rather than meaning. Refreshing the numbers deterministically keeps the page current at no model cost,
keeps the day's reading stable instead of drifting with every print, and leaves one clear record of what was believed
and when. A second paid call for the same checkpoint is refused unless the owner explicitly asks for it.

Every page names three clocks: **Prices** (the latest current equity print in its tables, or `prior close <date>`),
**Analysis** (when the interpretation was made, and by which edition) and **Next** (the next scheduled update, which
reads `Update due … has not published` once it is overdue). Each row keeps its own observation clock. Treasury rates
carry their own date: they are the official daily par curve from the previous business day, never an intraday quote.

## Evidence → synthesis → continuity → publication

```text
  Cloudflare Worker (cron) ── wakes ──▶ GitHub Actions: schedule.yml (one run at a time)
                                              │
     restore the last accepted continuity bundle (Actions artifact, hash-checked)
                                              │
     collect: Alpaca IEX prints and daily bars · Treasury nominal and real curves ·
              BLS calendar and release values · Federal Reserve releases
                                              │
     deterministic evidence: normalized rows, returns, spreads, curve moves, comparisons
                                              │
          ┌───────────── synthesis checkpoint? ─────────────┐
          │ yes                                             │ no
  bounded analyst context → one model call            carry the frozen
  → fail-closed validation                            accepted interpretation
          └──────────────────────┬──────────────────────────┘
                                 │
     deterministic renderer → upload continuity → commit publish/index.html → GitHub Pages
                                 │
     receipt: each stage's outcome and any paid call, recorded whatever happened
```

The analyst (Opus 5.5 through OpenRouter, pinned to Anthropic) receives a bounded, versioned context: the admitted
evidence, prior structured state and the output schema. It never fetches anything. Its answer is accepted only if every
reference it cites exists in admitted evidence and in the context it was shown. A rejected answer never replaces the
last accepted page; a rejected opening update publishes as a refresh under the premarket analysis and says so.

Continuity is one chain: previous close → premarket → opening structure → intraday refreshes → close handoff. Prior
state is carried as structured records (watches with their criteria, relationships, closing character), never as old
prose. See [Architecture](docs/ARCHITECTURE.md) for the full contract.

## What makes it different from a generated market summary

- **Deterministic facts.** Prices, returns, spreads, yields, windows and comparisons are calculated by code from
  named sources. The model may cite them through placeholders; it cannot write a number of its own.
- **Visible provenance.** Every claim opens to the rows behind it, with their source and observation clock.
- **Honest absence.** A missing value reads `no print`, `—` or `not collected` with a reason, never zero or silence.
- **Fail closed.** Missing continuity, invalid evidence or a rejected synthesis does not overwrite the last accepted
  brief.
- **Frozen analysis.** A refresh cannot quietly rewrite the interpretation that was paid for and accepted.
- **Testable watches.** Each watch states what would confirm it and what would change it, at the values its author
  saw, and a later synthesis judges it.
- **No trading authority.** The brief explains and tracks market structure. It never grants permission to trade.

## The wider research program

Market Brief is one of four independent components. Each has its own repository or directory, schedule, storage and
development lifecycle; none runs inside another.

- **Market Brief** (here): ongoing interpretation of the session and structured watches.
- **Market Lab** (separate, private): experimentation and hypothesis testing against its own observations.
- **Options Observatory** (local, unpublished): option prices, volatility, Greeks, liquidity and extended-hours
  market structure.
- **[Cuttingboard](https://github.com/dwats250/cuttingboard)**: lightweight, independent market-structure
  observations. Market Brief can quote its published contract as optional, read-only context; scheduled production
  does not request it ([boundary](docs/CUTTINGBOARD_BOUNDARY.md)).

**Direction.** The longer-term aim is a self-hostable research workspace that needs little maintenance: GitHub kept
for the code and its history, and runtime duties moved off it only where evidence shows that is safer. One step is
being explored now: a Cloudflare coordinator that would hold execution claims, payment authorization, accepted
continuity and the reader page in one place. It is built and tested on staging only; production still runs entirely
on GitHub Actions and Pages, with Cloudflare only waking the workflow. A unified dashboard across the components is an
idea, not a plan: if built, it would read their published outputs and decide nothing. See the
[charter](docs/PROJECT_CHARTER.md) for status and graduation criteria.

## Setup and development

Python 3.11+.

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python3 -m pytest -q
.venv/bin/ruff check src tests
```

Commands:

```bash
python -m market_brief premarket --replay                     # fictional replay evidence, no network, no model
python -m market_brief premarket --replay --continuity PATH   # replay against another bundle
python -m market_brief schedule --checkpoint PREMARKET
python -m market_brief continuity-restore --from-file bundle.json
python -m market_brief publish
python -m market_brief open
```

Replay output is explicitly labeled and cannot masquerade as live production. Live runs need Alpaca and OpenRouter
keys (and `BLS_CONTACT` for BLS requests); scheduled production supplies them as GitHub Actions secrets.

## Output and evidence

The latest rendered brief is written to `output/latest.html`. `python -m market_brief publish` copies only the
human-facing HTML to `publish/index.html` for static hosting.

Each run under `runs/<session>/<mode>-<checkpoint>-<time>-<id>/` records the evidence and metadata needed to explain
what happened. Synthesis runs also save `analyst_context.json` and `narrative.json`; deterministic updates record the
interpretation they carried instead of generating another. Accepted production continuity lives in
`runs/continuity/bundle.json` and is restored from and uploaded to GitHub Actions artifacts. A production synthesis
writes `runs/attempts/<session>-<checkpoint>.json` before its provider request is sent, so the scheduler never pays
twice for a checkpoint.

Rendered fictional examples live in `examples/editions/`. They are replays of the fixture at each checkpoint's clock
(13:00, 13:31, 14:01, 17:01 and 20:03 UTC on 2026-09-08), each in a fresh run root, with SPY −0.53%, QQQ −0.61%,
XLI +0.40% and GLD −0.20% prints injected at the checkpoint's clock (19:59:58 for the close) on every edition but the
premarket, and `--full-packet` for the opening structure; the run-id suffix is the only thing that changes between
regenerations.

## Documentation

- [Project charter](docs/PROJECT_CHARTER.md): purpose, principles, ecosystem, graduation criteria
- [Project state](PROJECT_STATE.md): what is running now and what comes next
- [Decisions](DECISIONS.md): the engineering decisions and the reasons for them
- [Architecture](docs/ARCHITECTURE.md): evidence, freshness, continuity, cadence and synthesis contracts
- [Product](docs/PRODUCT.md) · [Brief schema](docs/BRIEF_SCHEMA.md) · [Visual language](docs/VISUAL_LANGUAGE.md)
- [Data sources](docs/DATA_SOURCES.md) · [Cuttingboard boundary](docs/CUTTINGBOARD_BOUNDARY.md)
- [Cloudflare scheduler](cloudflare/README.md) · [Synthesis cost containment](docs/SYNTHESIS_COST_CONTAINMENT.md)

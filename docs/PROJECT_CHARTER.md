# Project charter

This is the short version of what I'm trying to do with Market Brief and the projects around it, and the rules I've
been following while building them. It isn't a status report. What's actually running is in
[PROJECT_STATE.md](../PROJECT_STATE.md), and the reasoning behind specific decisions is in
[DECISIONS.md](../DECISIONS.md). If this file and those two disagree about what's running, trust them.

## Why this exists

Market Brief started on 7 September 2026 as a scheduled report: collect some data, have a model write it up, publish
a page. In about six weeks it turned into something closer to a research notebook. It now keeps a record of what the
analysis said about each session at the time, when it said it, and whether that held up. A few separate experiments
have grown up beside it.

What I'm after is understanding, not signals. I want to get better at reading a session: what moved, what it was tied
to, and what would show my read was wrong. None of this decides a trade.

The end goal is simple to say and harder to build: I want to spend more of my time understanding markets than
maintaining the systems I use to study them. Every tool here is meant to earn its keep by giving me back attention,
not by asking for more of it.

## The question I keep coming back to

*Can the system stay useful, trustworthy and recoverable while I'm busy doing something else?*

A feature that only works because I'm watching it isn't finished, however clever it is. A lot of the recent work has
really been about that question: never paying twice for the same analysis, a receipt for every run, recovering
cleanly from a failed publish, and the coordinator experiment.

## How I'm building it

**Evidence comes before interpretation.** Everything is collected and checked before a model sees any of it. The model
reads a prepared, bounded record. It doesn't fetch, browse or calculate, which keeps what it says tied to what was
actually observed.

**Code owns the calculations; sources own the observations.** Prices, yields and release values come from external
sources, and they're only as good as those sources. Once a value is admitted, though, everything built on it (returns,
spreads, curve moves, comparisons, windows, the clocks) is computed by versioned code. The model can choose what
matters and explain it. It can't produce a number, and code validates every citation it makes.

**Time and provenance are explicit.** Every value says when it was observed, which session it belongs to, what it's
measured against and where it came from. A missing value is named as missing, with a reason. It's never a zero and
never quietly dropped. I learned this one the hard way: for at least a week, the busiest tickers were losing their current
prints without a word because of a clock bug.

**Components stay independent.** Each project owns its own collection, storage, schedule and release cycle. If they
ever share anything, it'll be through files one of them has published, not a shared database or reaching into each
other's internals.

**There's one accepted history.** The brief carries forward exactly one chain of accepted state from the previous
close through the day to the next handoff. A refresh never rewrites an accepted analysis, and old prose is never fed
back in as evidence. Only structured state carries forward.

**The model interprets but has no authority.** It writes readings, rival explanations and testable watches. Nothing in
any of these projects grants permission to trade, sizes a position or places an order.

**Research has to be able to fail.** A reading says what would contradict it. Competing explanations stay on the
table, and a null or disconfirming result gets recorded with the same care as a confirming one.

**Learning stays tied to real evidence.** The options and market-mechanics side of this is grounded in things I've
actually observed: a real chain, a real curve, a real session. Generic theory is easy to find elsewhere.

**Infrastructure stays small.** No service, database, queue or framework unless a specific failure shows one is
needed. Fewer moving parts beats more capability, because every part is something I'll have to maintain.

**GitHub stays for the code, less so for the runtime.** The repository, reviews and history stay on GitHub. Runtime
jobs (scheduling, continuity, publication) move elsewhere only when there's evidence the move is safer, and only after
the replacement has shadowed production and I've decided to switch.

## Where things stand

| Capability | Status |
|---|---|
| Two daily syntheses (PREMARKET, OPEN_30M) with deterministic refreshes and a close snapshot | In production since 2026-09-14 |
| Validation by code: every cited reference must be in the admitted evidence and in what the model was shown | In production |
| A rejected opening update publishes fresh numbers under the premarket analysis and says so | In production since W43 (merged 2026-10-17) |
| Hashed continuity bundle carried between runs as a GitHub Actions artifact | In production |
| At most one paid attempt per checkpoint across runners; a repeat needs an explicit `repay` | In production |
| A receipt for every checkpoint run, recording each stage and any paid call | In production since W43 |
| Cloudflare Worker that wakes the GitHub workflow on the exchange clock | In production (it's only a clock) |
| Page published to GitHub Pages from `publish/index.html` | In production |
| Cloudflare coordinator: one Durable Object for claims, payment, continuity and the reader page | Built and tested locally and on staging, on an unmerged branch; no production role |
| Coordinator shadow replay (G3): five straight sessions with identical decisions, continuity and pages | Being prepared; one session has replayed cleanly; the five-session window hasn't started |
| Weekly continuity and a Week Ahead page | Planned ([plan](2026-09-27-market-memory-week-ahead.md)); only the first slice (moving the analyst to Opus 5.5) is built |
| A single dashboard across the projects | Just an idea |

## The projects around it

There are four, related by purpose rather than by runtime. None needs another to run, and each has its own
repository or directory, schedule, storage and development cycle.

| Project | What it's for | Connection to Market Brief |
|---|---|---|
| **Market Brief** (this repository) | Reading each session as it happens, and keeping watches that later analysis has to rule on | (this one) |
| **Market Lab** (private) | Testing hypotheses against its own deterministic observations, taken at fixed market times | None; neither reads the other |
| **Options Observatory** (local, unpublished) | Option prices, implied volatility, Greeks, liquidity, and how extended-hours trading is starting to change market structure | None |
| **[Cuttingboard](https://github.com/dwats250/cuttingboard)** | Lightweight, independent market-structure observations | Market Brief can quote its published contract as optional, read-only context; scheduled runs don't ask for it ([boundary](CUTTINGBOARD_BOUNDARY.md)) |

The threads running through all of them are practical options education, rates and cross-asset relationships (the
Treasury curve, real yields, metals, the dollar), and data quality: knowing exactly what a source can and can't tell
you, and when.

At some point I might want one page that pulls these together. If I build it, it'll be a presentation layer: it would
read what each project already publishes and show market conditions, freshness, research progress and anything that
needs my attention. It wouldn't collect anything, hold authoritative state, or decide any facts. Nothing like that
exists yet, and there's no plan to build it.

## What graduating would look like

These are the targets I'd use to say the system has grown up. They aren't a scorecard for its own sake, and they
aren't things it has already done. Each one comes from the question above, and each can be judged from records the
system already keeps (run receipts, workflow history, continuity artifacts, the published page). None of them should
need a new monitoring service.

1. **It runs without me.** Twenty consecutive trading sessions where I didn't have to step in: no hand re-dispatch, no
   hand repair of state, no hand publishing. Looking at the page doesn't count as stepping in.
2. **Every checkpoint is accounted for.** At least 99% of required checkpoints either published or ended in a state
   that says what happened (skipped with a reason, refused for stale data, published as degraded), and none are just
   missing. A source outage that the page reports correctly is the system working. Alpaca being down for an hour is
   their problem; it only becomes mine if the page hides it.
3. **No wrong reading goes out unqualified.** No known material interpretation error reaches the page without a
   caveat, a degraded label or a stated coverage limit next to it.
4. **Recovery has actually been tested.** Restoring continuity, recovering from a failed run and rolling back a bad
   release have each been done for real, and none of them caused an unintended second paid call.
5. **There's a daily page a busy person can scan.** One place that shows market conditions, how fresh the data is,
   research progress and anything outstanding, in a minute or two.

## Changing this file

Edit it when a principle changes or a target is met, missed or replaced, and put the reason in
[DECISIONS.md](../DECISIONS.md). It's not a status log. That's what [PROJECT_STATE.md](../PROJECT_STATE.md) is for.

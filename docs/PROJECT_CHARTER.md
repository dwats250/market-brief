# Project charter

*Written 2026-10-19. The principles here are standing guidance. The graduation criteria are proposed targets, not
achievements. Current operation is recorded in [PROJECT_STATE.md](../PROJECT_STATE.md) and the decisions behind it in
[DECISIONS.md](../DECISIONS.md); where this charter and those records disagree about what is running, they win.*

## Purpose

Market Brief began as a scheduled market report. It is now the base of a personal market research program: a place
where observed market evidence, a small amount of model-assisted interpretation, and an explicit record of what was
believed and when accumulate into something one person can learn from.

The goal is understanding, not signals. The brief explains what the market did, what changed, and what would show the
current reading wrong. It never decides a trade.

## The maturity question

> Can the system stay useful, trustworthy and recoverable while its owner is busy doing something else?

Each piece of work should make that answer more clearly "yes". A feature that needs the owner watching it to be safe
has not matured, however much it does.

## Principles

1. **Evidence before interpretation.** Collection and validation finish before any model is called. A model reads a
   prepared, bounded record; it never fetches, browses or computes.
2. **Deterministic code owns financial facts.** Prices, returns, spreads, yields, windows, comparisons, triggers and
   clocks come from admitted sources and versioned calculations. A model may select and explain them. It may not
   produce them.
3. **Explicit time and provenance.** Every value carries when it was observed, which session it belongs to, which
   reference it is measured against and where it came from. A missing value is named as missing, never a zero or a
   silent drop.
4. **Independent components, lightweight interfaces.** Each research component owns its own collection, storage,
   schedule and lifecycle. Components exchange published files, never shared databases, writable mounts or each
   other's internals.
5. **One authoritative accepted continuity history.** The brief carries forward exactly one chain of accepted state:
   prior close, premarket, opening interpretation, refreshes, close handoff. A refresh never rewrites an accepted
   interpretation, and old prose is never read back as evidence.
6. **Model-assisted interpretation, no trading authority.** The analyst writes readings, rival explanations and
   testable watches. Nothing in the system grants permission to trade, sizes a position or places an order.
7. **Falsifiable research.** A reading states what would contradict it. Competing explanations are kept, and a null
   or disconfirming result is recorded with the same care as a confirming one.
8. **Learning connected to evidence.** Options and market-mechanics education is grounded in what was actually
   observed: a real chain, a real curve, a real session, not generic theory.
9. **Minimal infrastructure, low maintenance.** No service, database, queue or framework is added unless a specific
   failure shows it is needed. Fewer moving parts beats more capability.
10. **GitHub for version control; runtime dependence reduced when justified.** The repository, review and history
    stay on GitHub. Runtime duties (scheduling, continuity, publication) move elsewhere only when evidence shows the
    move is safer, and only after the replacement has shadowed production.

## What exists today

| Capability | Status |
|---|---|
| Two daily syntheses (PREMARKET, OPEN_30M) with deterministic refreshes and close snapshot | In production since 2026-09-14 |
| Fail-closed validation: every cited reference must exist in admitted evidence and in what the analyst was shown | In production |
| Hashed continuity bundle carried between runs as a GitHub Actions artifact | In production |
| One paid attempt per checkpoint across runners; manual repeats need an explicit `repay` | In production |
| Per-run receipts recording each stage's outcome and the paid call | In production since W43 (merged 2026-10-17; first live session 2026-10-19) |
| Cloudflare Worker that wakes the GitHub workflow on the exchange clock | In production (wake-up only) |
| Static page published to GitHub Pages from `publish/index.html` | In production |
| Cloudflare coordinator: one Durable Object holding claims, payment, continuity and the reader page | Built and tested locally and on a staging Worker; on an unmerged branch; no production role |
| Coordinator shadow equivalence (G3): five consecutive sessions replayed with identical decisions | In preparation; one session replayed offline with a PASS; the five-session window has not started |
| Weekly continuity and a Week Ahead page | Approved plan ([docs/2026-09-27-market-memory-week-ahead.md](2026-09-27-market-memory-week-ahead.md)); only its first slice (the analyst model) is built |
| A unified research dashboard | Exploratory idea only |

## The research ecosystem

Market Brief is one of four independent components. They are related by purpose, not by runtime: none depends on
another to run, and each keeps its own repository or directory, schedule, storage and release cycle.

| Component | Role | Relationship to Market Brief |
|---|---|---|
| **Market Brief** (this repository) | Ongoing interpretation of the session and structured watches carried across it | — |
| **Market Lab** (separate private repository) | Experimentation and hypothesis testing: its own deterministic observations at fixed market times, kept as evidence a hypothesis can be checked against | Reads nothing from the brief; the brief reads nothing from it |
| **Options Observatory** (local, unpublished) | Option prices, implied volatility, Greeks, liquidity and the emerging extended-hours market structure | Independent; nothing flows between them |
| **[Cuttingboard](https://github.com/dwats250/cuttingboard)** | Lightweight, independent market-structure observations | Optional, read-only, off in scheduled production; see [CUTTINGBOARD_BOUNDARY.md](CUTTINGBOARD_BOUNDARY.md) |

Research interests that cut across them: practical options education, rates and cross-asset relationships (the
Treasury curve, real yields, metals, the dollar), and observational data quality: knowing exactly what a source can
and cannot say, and when.

**A possible unified dashboard.** If one is built, it would be a presentation layer only: it would read what each
component already publishes and show market conditions, freshness, research progress and outstanding exceptions in
one place. It would not collect data, hold authoritative state, or become the place any component's facts are decided.
No such dashboard exists or is scheduled.

## Proposed graduation criteria

These are maturation targets to judge the system against, not a record of what it has done. They are read from
records the system already keeps (run receipts, workflow history, continuity artifacts, the published page). Meeting
them must not require a new monitoring service.

1. **Unattended operation.** Twenty consecutive trading sessions in which no routine owner intervention was needed:
   no hand re-dispatch, hand repair of state or hand publication.
2. **Checkpoint completeness.** At least 99% of required collection checkpoints in a period either published or are
   visibly classified (skipped with a stated reason, refused for stale data, published degraded), with none
   unaccounted for.
3. **No unqualified interpretation errors.** No known material interpretation error reached a reader without a
   qualification on the page: a caveat, a degraded label, or a stated coverage limit.
4. **Tested recovery.** Continuity restoration, recovery from a failed run and rollback of a bad release have each
   been exercised, and none produced an unintended duplicate paid call.
5. **A lightweight daily reader.** One page a busy reader can scan that shows market conditions, data freshness,
   research progress and anything outstanding that needs attention.

## How this charter changes

Edit it when a principle changes or a criterion is met, missed or replaced, and record the reason in
[DECISIONS.md](../DECISIONS.md). Do not use it as a status log; that is [PROJECT_STATE.md](../PROJECT_STATE.md).

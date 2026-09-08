# Market Brief

An AI-assisted briefing desk for one curious market operator: collect the facts,
assemble the day's puzzle pieces, and make the result worth reading.

**Status: DESIGN ONLY.** No market collector, CLI, model integration, or scheduled
agent exists yet. All commands in this repository are proposed interfaces.

The first build should produce one pre-market brief: a concise, source-aware
Markdown note and a self-contained HTML edition, backed by a small deterministic
evidence packet. Observations, interpretation, and conditional watches remain
visibly distinct.

Start with the [fictional example](examples/example-brief.md). Then read the
[product](docs/PRODUCT.md), [architecture](docs/ARCHITECTURE.md), and
[first build plan](docs/V0_PLAN.md).

## Design package

| Document | Question it answers |
|---|---|
| [Product](docs/PRODUCT.md) | Who is this for, and what makes it useful? |
| [Architecture](docs/ARCHITECTURE.md) | What do scripts calculate, and what does the model do? |
| [Brief schema](docs/BRIEF_SCHEMA.md) | What does a reader see, and how are claims supported? |
| [Data sources](docs/DATA_SOURCES.md) | Which inputs are feasible, fresh, optional, or unverified? |
| [Cuttingboard boundary](docs/CUTTINGBOARD_BOUNDARY.md) | How can context be reused without shared authority? |
| [V0 plan](docs/V0_PLAN.md) | What is the exact smallest implementation charge? |

## Relationship to Cuttingboard

Cuttingboard is the deterministic instrument panel. Market Brief is a separate
AI-assisted analyst's note. It may read a published Cuttingboard snapshot and
quote its state, but can never change its regime, qualification, candidates,
permission, execution, or outputs. It must work with Cuttingboard unavailable.

No Python imports from Cuttingboard, submodule, shared writable storage, runtime
reuse, or write-back endpoint is permitted. This repository has its own history
and no remote. The Market Observer / Coverage Audit remains a separate experiment.

## Proposed first run

`market-brief premarket` will collect a bounded set of permitted sources, normalize
facts and freshness, request one model synthesis, validate references, and write
local reports. An already configured model and permitted quote access are setup
prerequisites, not capabilities provided by this design seed. A sourced local
input file is the bounded fallback when a quote feed is unavailable; the report
must disclose that fallback and its coverage.

No stock picking, trade permission, orders, hosting, subscriptions, or continuous
automation is included in the first build. Generated market data and briefs are
local and ignored by Git by default. The example contains fictional data only.

Owner: Dustin / HELM. Design research date: 2026-09-07.

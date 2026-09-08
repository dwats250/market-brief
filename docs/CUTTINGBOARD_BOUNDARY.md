# Cuttingboard boundary and read-only reuse

## Absolute separation

Market Brief is an independent repository at
`<market-brief-repo>`. It is not a Cuttingboard
worktree, extension, runtime consumer, or governance lane. Its output is analyst
context, not Cuttingboard authority.

No Cuttingboard Python imports, copied runtime, Git submodule, shared writable
database, writeable mount, workflow dispatch, repository write, notification,
order, or callback is permitted. Never modify regime, qualification, candidate
selection, TRADE / NO TRADE / HALT, execution, provider acquisition, or dashboard
content. Do not retrieve credentials from Cuttingboard or inherit its environment.

Market Brief may disagree narratively with a Cuttingboard state, but must say
"Market Brief interpretation differs" and preserve the exact source state and
timestamp. An absent, stale, or invalid source means unavailable context; it does
not mean NO TRADE, HALT, or an inferred replacement state.

## Verified reconnaissance, 2026-09-07

Canonical source main was `62e2f2dca6505e17de8ff3d3c3afbb8536585d6b`.
The observed publish branch was `1b940642e4820a402d62ffd2fcab64e48650515a`.
These are distinct identities, not a claim that the latter was generated from
the former. Source merge is not deployment/freshness proof.

| Surface | What was verified | Planned treatment |
|---|---|---|
| [Published dashboard](https://dwats250.github.io/cuttingboard/) | Deployment workflow publishes `ui/` from branch `publish` | Human context; not the preferred machine API |
| [Public contract](https://dwats250.github.io/cuttingboard/contract.json) | Unauthenticated GET returned JSON with schema `v2`, generation/session fields, system_state, regime, candidates, and macro_drivers | Optional allowlisted v0 reader after field validation |
| `publish:ui/index.html` and `ui/dashboard.html` | Both existed with the same Git blob ID `3611ca4b27f31e8a77ab0c840fd154119f5687ad` | Publication artifact identity only; no claim of served HTML byte parity tonight |
| `publish:logs/latest_hourly_contract.json`, `macro_drivers_snapshot.json`, `latest_payload.json`, `latest_run.json`, `market_map.json` | Present in the GitHub tree queried with authenticated access | Not implied public Pages endpoints; no v0 dependence |
| `logs/watchlist_snapshot.json` | Not present in the inspected publish tree | Do not invent a `/watchlist_snapshot.json` endpoint |
| Leadership / breadth | PRD-337 is a universe/movement precursor; no confirmed public fields for these future features | Optional future adapter only after an actual published schema exists |

The Pages API's legacy source descriptor named `main`, while the inspected
[Actions workflow](https://github.com/dwats250/cuttingboard/blob/62e2f2dca6505e17de8ff3d3c3afbb8536585d6b/.github/workflows/pages.yml)
explicitly checks out `publish` and uploads `ui/`. Use workflow and actual HTTP
evidence, not that descriptor alone. No real market payload was committed here.

Schema authority was checked in
[contract_types.py](https://github.com/dwats250/cuttingboard/blob/62e2f2dca6505e17de8ff3d3c3afbb8536585d6b/cuttingboard/contract_types.py),
with field semantics from
[SCHEMA_MAP.md](https://github.com/dwats250/cuttingboard/blob/62e2f2dca6505e17de8ff3d3c3afbb8536585d6b/docs/SCHEMA_MAP.md).
The served contract's presence of macro drivers is not a guarantee every optional
driver is available. In particular, daily 2Y/5Y series may be absent and their
observation dates cannot be replaced by generation time.

## Recommended pattern: optional public contract plus local universe mirror

First build: HTTPS GET of `/contract.json`, timeout and size limit, parse JSON,
validate the locally supported `v2` subset. Admit only generation ID/time,
session date/mode, schema version, literal outcome/permission, and source regime
description when its structure is validated. Candidate symbols may be included
as attributed source context; no entry, stop, target, sizing, or qualification
recalculation. Missing fields remain missing. Preserve the raw `outcome` literal
(`TRADES` may differ from the human-facing label TRADE); never conflate it with
the separate `permission` field.

Report "Cuttingboard, generated [time]" and the exact source state in its own
section. Keep Market Brief's banner explicitly INTERPRETATION. Unknown schema,
wrong field types, future generation time, cross-session data, or conflicting
generation IDs suppress the affected source section and record a reason.
Premarket previous-session context may be shown only under that dated heading;
it cannot be labeled current permission. During a session, use a proposed
90-minute generation-age ceiling for optional contextual display, with exact age
visible. That ceiling is not a claim about the age of underlying quotes.

Do not scrape HTML for decision-authoritative fields as a fallback. No automatic
read of arbitrary `artifacts` paths, no URL following from the payload, and no
synthetic GEX reference admitted as a current market observation. Future movement
support requires a real versioned public artifact or a separately supplied,
provenanced snapshot. Do not request Cuttingboard changes to make v0 work.

## MIRROR v0: membership and relationships only

This design table is a mirror of
[universe_registry.py at the pinned source revision](https://github.com/dwats250/cuttingboard/blob/62e2f2dca6505e17de8ff3d3c3afbb8536585d6b/cuttingboard/universe_registry.py).
It is not executable config yet. A future `config/universe.json` may transcribe
these facts with `mirror_version`, source repository/path/commit, and copied date.
Do not copy Python code or the inert `trade_eligible` field.

| Group | Symbols in source order | Personal members | Benchmark relationship |
|---|---|---|---|
| MARKET | SPY, QQQ | SPY, QQQ | SPY anchor; QQQ → SPY |
| SECTORS | XLK, XLF, XLE, XLI, XLY, XLP, XLV, XLU, XLB, XLRE, XLC | XLE | All → SPY |
| METALS | GLD, SLV, GDX | GLD, SLV, GDX | GLD anchor; SLV/GDX → GLD |
| MEGACAPS | AAPL, MSFT, NVDA, META, AMZN, GOOG | NVDA, META, AMZN, GOOG | All → QQQ |
| Personal only | UCO | UCO | None; not a measurement member |
| Disabled tombstone | TSLA | None | None; neither active measurement nor personal |

There are 22 measurement members and 11 personal members. Source order is not
rank or conviction. Benchmarks are relationships, not computed leadership.
MEGACAPS is an owner-selected basket; META/GOOG are Communication Services and
AMZN is Consumer Discretionary, not all Technology. A constituent-versus-QQQ
spread partly compares the name with itself. UCO is a leveraged crude proxy;
do not relabel it crude or use it as a multi-day unlevered oil return.

Market Brief independently calculates any future relative spreads from its own
dated inputs. Those are Market Brief metrics, never "Cuttingboard Leadership."
Local preferences can diverge from the mirror without altering Cuttingboard.

## Drift and offline behavior

No import-time remote check. At an explicit later `check-mirror` operation or
manual maintenance review, compare source revision and the projected tuples
`symbol/group/enabled/personal/market_structure/benchmark`. Record added, removed,
or changed rows. Warn on drift; do not synchronize automatically or block normal
brief production. New source code is inspected as data, never executed.

The contract adapter has its own version and narrow fixture tests. Additive
unknown fields are ignored; unsupported schema/type changes mark Cuttingboard
unavailable. Run the brief with the network endpoint removed as an acceptance
test. Primary price/calendar collectors and local universe remain usable.

No Market Brief outputs enter the Market Observer experiment's blind contexts.
Any future comparison must disclose whether a brief consumed Cuttingboard;
otherwise it would measure circular agreement rather than independent coverage.

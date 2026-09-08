# Pre-market Brief v0 Implementation Plan

**Status:** design specification only; this document authorizes no build tonight.
This bounded plan adapts the writing-plans workflow to the owner's DESIGN ONLY
charge: exact file responsibilities, acceptance cases, and execution sequence,
without implementing code or requiring additional orchestration skills.

**Goal:** produce one useful real pre-market note from a small source-aware
evidence packet and one model synthesis, with matching Markdown and attractive
standalone HTML outputs.

**Architecture:** one Python CLI, ordinary collectors, deterministic metrics,
local JSON/files, one model adapter, templates. Cuttingboard is optional read-only
context and all output stays in this independent repository's ignored run area.

**Tech stack recommendation:** Python 3.11+, argparse/urllib/json/zoneinfo,
Jinja2 for escaped HTML, exchange_calendars for session boundaries, pytest for
behavioral tests. Pin actual dependency versions during implementation; no dependencies or
application code are installed in this seed.

Implementation references:
[Jinja escaping](https://jinja.palletsprojects.com/en/stable/templates/#html-escaping)
and [exchange_calendars](https://github.com/gerrymanoim/exchange_calendars).
Explicitly enable HTML autoescaping; validate calendar results against NYSE.

## Smallest shippable scope

`market-brief premarket` gathers prior-close SPY/QQQ and the available bounded
universe, official calendar/context, optional delayed premarket observations,
daily Treasury background, and optional Cuttingboard state. It computes the
defined history metrics, requests one model synthesis, and writes evidence.json,
narrative.json, metadata.json, brief.md, and brief.html.

Normal run: configured data access plus one existing authenticated model route.
Permitted sourced input files are supported when acquisition is unavailable;
manual contribution must be disclosed. The first build is not a promise of free
comprehensive live futures, yields, FX, breadth, or geopolitical news. A run with
only prior-close market data must explicitly leave current direction unknown.

## Proposed implementation file fence

All paths below are relative to this new repository only. No Cuttingboard path
is authorized. The tree is a future file map, not files supplied in this seed.

```text
pyproject.toml                         package, CLI entry, dependency/test config
src/market_brief/__init__.py           version only
src/market_brief/cli.py                command, config, sequence, exit result
src/market_brief/evidence.py           records, validation, freshness/coverage
src/market_brief/collect.py            bounded HTTP/file collectors + optional CB
src/market_brief/metrics.py            daily returns, spreads, SMA50, attention
src/market_brief/synthesize.py         one model route, prompt, output validation
src/market_brief/render.py             same content to Markdown and HTML
config/universe.json                  independent mirror and benchmark provenance
config/sources.json                   source URLs/coverage/rights; no secrets
prompts/synthesis.md                  one bounded evidence-to-narrative prompt
templates/brief.html.j2               standalone escaped editorial layout
tests/test_evidence.py                clocks, units, source/CB failures
tests/test_metrics.py                 numeric truth, history and horizons
tests/test_pipeline.py                isolation, failed synthesis, offline CB
tests/test_render.py                  factual parity, escaping, visible labels
tests/fixtures/evidence.sample.json   fictional replay input
tests/fixtures/narrative.sample.json  fictional model output
tests/fixtures/cuttingboard.sample.json fictional minimal external contract
README.md, docs/V0_PLAN.md, .gitignore setup, limitations, evidence of completion
```

Ignored local `inputs/`, `.cache/`, and `runs/` are allowed runtime paths for the
future charge. Real input files, local auth, and live generated reports are not
committable test fixtures. Use pyproject.toml with exact direct dependency pins
for this first build; no new package-manager workflow or lockfile is required.

## Session 1: establish the evidence-to-report path

### Task 1 — verify setup and the narrow data route

- [ ] Confirm the independent Git root and clean design baseline.
- [ ] Inspect one existing model route for structured noninteractive execution,
      timeouts, tools-off isolation, and actual authentication. Do not search
      Cuttingboard's secrets or assume an API account exists.
- [ ] Probe the selected permitted equity route for SPY/QQQ previous-session
      closes and delayed premarket bars, including feed, observation timestamps,
      corporate-action basis, and extended-hours coverage. Check source rights
      for local display, retention, and model processing. Stop that integration
      on a restriction; continue with permitted sourced inputs, explicitly labeled.
- [ ] Check official calendar and Treasury retrieval; record unavailable sources.
      Do not acquire credentials or subscriptions without separate user action.

Acceptance: actual capabilities and gaps are recorded; no "working integration"
claim rests on documentation alone. This probe is bounded to roughly 30 minutes.
If no model route works, implement deterministic replay but report the first-real-
brief milestone blocked; do not call the replay a shipped live agent.

### Task 2 — records, local mirror, and temporal truth

- [ ] Create package/CLI configuration and the explicit universe mirror from the
      pinned design table; no Python import or source execution from Cuttingboard.
- [ ] Implement evidence records and validation before source-specific prose.
- [ ] Add tests for timezone-naive/future observations, unknown-age quotes,
      missing/null versus zero, NaN, units, mismatched horizons, and partial source
      failures. Verify each newly added behavior test fails before its fix.
- [ ] Test a holiday, an early close, and a DST boundary against exchange-calendar
      truth. A weekday-only calendar must fail the cases.

Acceptance: a newly retrieved old daily observation cannot become a current quote;
an empty/failed event source cannot become "no events." Run
`python -m pytest tests/test_evidence.py -q` and require all cases pass.

### Task 3 — collection and minimal history

- [ ] Implement only the selected equity REST route plus sourced-file input,
      Treasury daily XML, BLS/BEA schedules, Fed calendar and context retrieval needed for v0.
      Keep optional source adapters shallow; do not add universal scraping.
- [ ] Enforce explicit feed/adjustment/session choices, pagination bounds,
      timeout/retry limits, deadline, and source failure records.
- [ ] Implement the formulas in ARCHITECTURE.md and only its two attention
      triggers. Derive source references all the way back to input observations.
- [ ] Numeric tests: 100→101 equals +1%; 4.00%→3.93% equals −7 bp;
      +8% versus +3% equals +5 percentage points. Missing 21st close disables a
      20-session return; missing 51st close disables an SMA50 transition.
- [ ] Test that a split/baseline mismatch suppresses the comparison; seven up
      sector ETFs must remain labeled 7/11 sector ETF participation, not breadth.

Acceptance: replay packet arithmetic reconciles; no unread symbol disappears
silently after a paginated request. Run `python -m pytest tests/test_metrics.py -q`.

## Session 2: make the brief useful and pleasant

### Task 4 — isolated synthesis and optional Cuttingboard quotation

- [ ] Implement one configured model route with sanitized evidence on stdin,
      no shell-interpolated text, tools disabled, and a hard timeout. Record actual
      model ID/version and prompt hash; never persist credentials or private thought.
- [ ] Require the narrative schema, existing evidence references, distinct claim
      classes, and at most three conditional watches. Never let model prose replace
      literal factual rows or source permission state.
- [ ] Add optional GET-only public Cuttingboard contract parsing for the bounded
      supported subset. No HTML fallback, local repository access, or mutations.
- [ ] Test invalid JSON, invented IDs/numbers, prompt-injection text in a source,
      unavailable model, and stale/invalid Cuttingboard contracts. Test that a
      constructive Market Brief interpretation leaves source HALT unchanged.
- [ ] Test successful production with Cuttingboard unavailable and with its
      repository not mounted. No network write verbs or order routes are allowed
      to the Cuttingboard or market-data surfaces; model transport is separate.

Acceptance: one normal synthesis call, truthful failure behavior, no update of
latest-success on invalid narrative. Run
`python -m pytest tests/test_pipeline.py -q`.

### Task 5 — one document, two local formats

- [ ] Render the schema hierarchy using deterministic tables and source links;
      clearly label the banner as interpretation and preserve per-row dates.
- [ ] Produce Markdown and inline-CSS HTML from the same accepted records.
      Escape HTML; no remote script, font, image, or tracking dependency.
- [ ] Test factual parity, literal Cuttingboard state preservation, source links,
      sample/live labeling, escaping of injected HTML, and missing coverage.
- [ ] Inspect the real rendered page at desktop and 390px widths: no clipped
      signs/units, no horizontal page overflow, readable source details. Review
      print rendering without adding a PDF generator.

Acceptance: facts are readable before the prose, and the result resembles a
morning note rather than a log. Run `python -m pytest tests/test_render.py -q`.

### Task 6 — first real pre-market run and handoff

- [ ] Run `python -m pytest -q`; require all behavioral checks pass.
- [ ] On an actual trading morning, run the configured command. Inspect source
      observation times, event times, five load-bearing factual claims, and every
      causal sentence against the packet. A sample fixture is not this test.
- [ ] Require SPY/QQQ prior-session history and checked event coverage for full
      acceptance. Report overnight/live gaps prominently; do not claim they were
      solved by the model. Record any permitted manual-input step.
- [ ] Confirm the first note has at most three attention names and three
      testable watches, with no entries, targets, orders, or implied permission.
- [ ] Inspect Git status; keep real evidence and reports ignored. Commit only the
      authorized implementation files and fictional tests. Do not push or merge.
- [ ] Hand Dustin the local HTML path, coverage limitations, validation result,
      and the concrete evidence behind the first-live acceptance claim.

The one-to-two-session estimate assumes a usable permitted data route and existing
model authentication. External entitlement work is not hidden inside that estimate.
An overnight build can finish replay validation and leave only the explicitly
reported live-market acceptance for the next trading morning.

## Deferred until this is worth reading

No four-command cadence implementation, scheduler, multi-model comparison, agent
framework, database, full news search platform, exchange breadth engine, futures
roll engine, advanced technical scanner, 200-day/ATR metrics, web app, hosting,
sharing automation, or PDF generation. No modifications to Cuttingboard or its
separate Market Observer experiment.

Next extension after a useful premarket: add post-open only, with explicit previous
brief/evidence IDs and deterministic delta records. Open and close follow if that
incremental report improves on a fresh essay.

## Exact recommended next implementation charge

> IMPLEMENT — Standalone Market Brief PRE-MARKET v0.
>
> Working directory: <market-brief-repo>.
> Basis: the committed README and docs/PRODUCT.md, ARCHITECTURE.md,
> BRIEF_SCHEMA.md, DATA_SOURCES.md, CUTTINGBOARD_BOUNDARY.md, and V0_PLAN.md.
> Objective: implement the bounded premarket evidence-to-brief path, one existing
> model integration, and matching local Markdown/standalone HTML. Complete the
> behavioral and visual checks and, when a trading-session source is available,
> one real premarket acceptance run. Distinguish replay from live validation.
> Files: only the implementation file fence above and ignored runtime folders
> inside this independent repository. Do not read Cuttingboard credentials,
> import its modules, write its files, dispatch its workflows, or alter decisions.
> Public Cuttingboard GET is optional and failure must not block the brief.
> Use permitted existing source/model access; acquire no credentials, paid plan,
> external account, remote repository, or hosting. Missing access gets a bounded
> sourced-file fallback or a concrete blocked milestone, never invented evidence.
> No broader cadences, orders, framework, database, hosting, push, or merge.
> Commit the bounded implementation locally and return the report path, exact
> commit, tests, source freshness/coverage, manual steps, and remaining limitations.

No owner product decision is needed to accept this design. Actual provider/model
access is a setup fact to verify in the implementation charge, not a fabricated
product-direction gate tonight.

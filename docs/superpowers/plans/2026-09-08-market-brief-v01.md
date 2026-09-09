# Market Brief v0.1 Implementation Plan

**For Fable 5.1:** Planning handoff only. Implement the bounded slice below in a separately authorized implementation session; no orchestration framework or agent team is required.

**Goal:** Give the brief structured session continuity, economical analysis, and clearer reading without changing its visual identity.

**Architecture:** Extend the existing Python pipeline with a persisted analyst-context projection and a small continuity module. One analyst response supplies both reader analysis and bounded continuity records; deterministic code validates, packages, and persists them.

**Tech stack:** Existing Python, JSON, jsonschema, Jinja, exchange calendar, model transports, GitHub Actions and Pages. No database, service, queue, new model SDK, or chart library.

## Grounding

Inspected clean checkout `93f4b263bab29d640b93135f1178d2cee8bb0d9e`, pipeline modules, prompt, renderer/template, tests, docs, and workflows. Read the [public product](https://dwats250.github.io/market-brief/) and rendered desktop/mobile views on September 8, 2026. The served edition was the 1:50 PM PT commissioning brief, headed “Energy leads a flat tape while cyclicals and discretionary lag.” This was product inspection, not an adversarial review or production test run.

The essential architecture already exists: deterministic evidence, source admission, compact `synthesis_packet()`, isolated synthesis, deterministic validation, and dual Markdown/HTML rendering. `PROJECT_STATE.md` records the accepted projection at 25,280 versus 80,189 bytes and 14,059 prompt tokens; those are recorded experiment results, not a newly measured cost benchmark.

The missing continuity is concrete: `previous_brief()` returns a path/hash pointer; narrative `changes` must be empty; the compact projection does not carry previous session state. Normal scheduled jobs persist public HTML but do not restore prior `runs/` data. Local continuity alone would not reach production.

The public page retains a strong editorial identity. Repeated commissioning/status lines, workflow labels, lengthy opening caveats, raw evidence rows, and tables before interpretation delay the useful read. Its caveat also says the just-completed session is missing: that edition cannot establish that session's closing character.

## 1. Architecture and boundaries

```text
Sources / APIs
  -> normalize + deterministic metrics + admission
  -> evidence.json (full canonical record)
  -> analyst_context.json (bounded projection + comparable changes + prior state)
  -> one configured frontier analyst
  -> deterministic validation
  -> narrative.json + structured continuity records
  -> renderer + accepted-state persistence
```

Optional preprocessing can contribute annotated source items before context construction. It never replaces original facts or admission decisions. Keep the full evidence immutable after finalization. The renderer reads the full admitted evidence; the analyst reads only its saved projection.

Create only two focused Python modules: `context.py` for projection/profile selection, and `continuity.py` for state admission, comparisons, packaging and validation. Extend existing modules for the remaining work. Keep existing transports; configure model identity and limits through one small edition/model configuration rather than hardcoded vendor choices. Do not build a provider registry.

## 2. Artifact contracts

All new artifacts have explicit schema versions. Caller-owned metadata includes run ID, exchange session, actual checkpoint, cutoff, mode, producer/code version, and content hashes. References across runs use `(run_id, evidence_id)`; IDs such as `SPY-daily` alone are not globally unique. Bump the narrative schema when adding fields; the current prompt's v0.2 label is not its JSON schema version.

| Artifact | Contents and authority |
|---|---|
| `evidence.json` | Preserve observations, history, derived calculations, events, context items, source permissions/provenance, coverage and optional upstream context. Each metric keeps value/unit, observation time, baseline, adjustment/feed, formula version and input lineage. Add stable metric identity for comparisons: instrument, metric, window, benchmark, basis. Missing remains null with a reason. This is factual authority. |
| `analyst_context.json` | Evidence hash; edition/profile; one compact copy of each selected admitted fact; small metric/baseline legend; ranked sector context; admitted attention triggers; events and bounded source extracts; coverage; prior structured state; deterministic comparable changes. Preserve clocks and source association directly or through a compact group legend. Save the exact artifact used in the request. No historical bar arrays, renderer structures, old brief text or duplicated catalogs. |
| `narrative.json` | Existing headline, character, executive read, section interpretation and attention, plus bounded relationships, watch updates and changes. Model-authored assessments stay identified as interpretation. Exact numeric claims continue to use evidence placeholders. |
| `session_handoff.json` | Closing session and evidence cutoff; closing-character assessment or unavailable reason; up to three relationships/tensions; up to three carried watches and their latest assessments; next-session event references; scoped supporting evidence snapshots; origin run/context/narrative hashes. Generated deterministically from validated records in the same analyst response, with no second summarizer call. |
| `metadata.json` | Exact input/output hashes, prompt/schema/profile/model identities, transport attempts, input/output tokens, reported cost, latency, validation outcome and continuity availability. Record absent usage as unknown. Do not retain private reasoning. |

Persist a small `edition_state.json` in the same shape as the continuity portion of the handoff for each accepted edition. This supports premarket-to-close comparisons without maintaining a second analysis system. `session_handoff.json` is the close-designated state, not another model output. The persisted bundle also includes the premarket anchor and latest accepted edition anchor.

Context selection must retain anchors, all cited dependencies of carried watches, enough sector coverage to see counterexamples, and explicit gaps. For rich editions, keep the small full sector summary. For light editions, emphasize changed facts but retain current anchors, material opposing evidence and unchanged watch dependencies. Deterministic ranking chooses what fits; the analyst chooses significance. Never silently truncate JSON or remove units/baselines to meet a budget. If required context cannot fit, use a bounded larger profile or fail that synthesis with diagnostics.

Validator: retain current numeric, source-permission, SAMPLE/LIVE and reference checks. Require references to both exist in canonical admitted evidence and have been supplied in the context. Prior evidence is separately namespaced and cannot ground a claim of current conditions. Validate state IDs, allowed transitions, bounds, horizon/event references, hashes and session eligibility. Any invalid new model output prevents acceptance and state advancement. These checks establish mechanical grounding, not semantic truth or causal entailment.

## 3. Premarket ↔ post-close continuity

**Premarket:** Admit only the previous exchange session's accepted close handoff, using the exchange calendar across holidays/weekends. Combine its compact state with newly collected evidence; refresh event timing and eligibility. Require the analyst to describe current conditions from current evidence before assessing prior relationships. No previous headlines, executive summaries or section prose enter the prompt. Short structured hypotheses remain hypotheses and are not evidence merely because a prior model wrote them.

**During the session:** Compare current evidence with the premarket anchor and latest accepted edition where useful. Carry stable watch IDs. Derive only mathematically valid changes from compatible metric identities, baselines, sessions, feeds and adjustments. Missing data, repeated old observations and incompatible bases produce explicit `unavailable`, `no_new_observation`, or `not_comparable`, never zero or a fabricated transition.

**Post-close:** Compare against the premarket anchor and latest available intraday state. Record what defined the session, which relationships persisted or changed, and which questions belong in tomorrow's context. If intermediate editions are missing, name the actual comparison point. Archive each edition; do not rewrite its original observations after seeing the outcome.

Use this conceptual watch/relationship record:

- Caller-assigned stable ID; first-mentioned run/time; related instruments; relationship type and metric keys.
- Short original hypothesis; supporting evidence refs; confirmation and contradiction criteria; structured horizon with resolved expiry session/time.
- Append-only assessment: `new`, `strengthened`, `weakened`, `reversed`, `unresolved`; assessment run/time, current refs, previous refs and brief reason.
- Separate lifecycle (`active`, `expired`, `retired`) and evaluability (`assessable`, `missing_evidence`, `not_comparable`). Missing data does not mean an unresolved hypothesis was tested and survived.
- Assessment origin (`analyst` initially). Preserve observable criteria text now; permit a future typed rule/version without inventing a general rule language today. Changed criteria start a new version with a link to the prior record.

Do not label subjective watch assessments as quantitatively confirmed outcomes. Preserve point-in-time values, benchmarks, criteria versions, selected observations, and model/context identity so later research can join subsequent returns or structure states without hindsight rewriting. Archive the full evidence, not just highlighted winners. Long-term retention remains subject to source permissions; a short archive is not yet a historical evaluation dataset.

**Closing-data admission:** `CLOSE_1M` is an execution checkpoint, not evidence of a final close. A post-close artifact must identify whether it has completed-session prices, provisional near-close observations, or only earlier history. With no usable observations from that session, closing character is unavailable and no valid close handoff advances. A partial brief can still publish under existing coverage rules; the next morning states that close continuity is unavailable. Provisional observations must remain labeled, and never masquerade as an official closing bar.

**Persistence:** Use one versioned, bounded continuity bundle in GitHub Actions artifacts, restored before collection and uploaded only after local acceptance. Add `actions: read` where required. Select from the expected workflow/main branch and successful runs, then verify bundle version, hashes, LIVE mode, non-experiment/non-commissioning origin and session eligibility. Never infer validity from artifact name alone. Restore a compact self-contained evidence subset for continuity, not only paths into an absent workspace. Keep close and edition pointers separate so premarket cannot overwrite the previous close.

Archive each normal run's permitted evidence/context/narrative/metadata alongside the small bundle, outside the Pages payload and Git history; use a documented retention window, initially at most thirty days or shorter source limits. Expired/missing/corrupt prior artifacts mean explicit cold-start continuity and a current-only brief. Invalid new output leaves prior state untouched. Persist accepted analytical state independently of Pages delivery; record deployment separately. No dedicated state service or repository of market data.

## 4. Optional lightweight work and Cuttingboard

**Ship no lightweight agent initially.** Current structured prices and narrow official feeds do not justify another inference call. If heterogeneous news is added, one bounded preprocessing role may cluster duplicates, extract source-backed event fields, or classify topics. Store original source IDs/spans, transformation version/model and uncertain/conflicting results. Deterministic checks reject invented entities, dates and values; discarded annotations fall back to normalized source items. Classification is an annotation, never authority. Require demonstrated input/cost reduction before enabling it; it has no market-analysis or continuity-writing role.

**Cuttingboard:** Document a future optional `market_structure` envelope rather than implementing acquisition. Include producer/schema version, generation ID, session, generated and underlying observation times, scope/universe, field definitions, benchmark, units/windows, coverage, provenance and permitted-use flags. Allow bounded market-map state, sector leadership, benchmark-relative structure, explicitly defined breadth, higher-timeframe context and attributed setup/context markers. Preserve an upstream grade such as A literally with its definition/version and observation time; Market Brief does not generate or reinterpret it as trade permission.

The existing optional contract quotation is a different interface and remains separate. A future allowlisted file/GET adapter validates the new envelope; absent, stale, future-dated or incompatible input removes only that optional context. No imports, shared writable storage, scraping fallback or Cuttingboard changes. Nothing depends on this source being present. Record whether it was consumed for later evaluation of circular agreement. This slice adds a short contract example to `docs/CUTTINGBOARD_BOUNDARY.md`, not a new runtime collector.

## 5. Presentation simplification

Preserve existing palette, serif headline, body typography, themes, rules and page width. Use one status/date/as-of line, with sample or commissioning truth retained once. Keep material freshness gaps visible in plain language; move feed mechanics and the Basis ledger into collapsed Sources & Coverage.

Order: header → headline/character/short executive read → compact snapshot → **What matters next** → equity interpretation/support → macro interpretation/rates → sector view → compact cross-asset structure → collapsed Sources & Coverage. Relevant events belong with What matters next. Merge attention explanations and conditional watches there without repeating the same observation; up to three editorial items can reference both trigger and watch IDs.

- Render horizons as natural phrases from checkpoint plus resolved expiry: “Into the close…”, “At the next update…”, “Into the next session…”. `NEXT_BRIEF` must not automatically mean tomorrow. Preserve structured confirmation/contradiction fields; make visible prose continuous. No repeated OBSERVED/INTERPRETATION badges when section hierarchy suffices.
- Put equity interpretation before the mega-cap table. Compact SPY/QQQ facts live in the snapshot; GLD/SLV/GDX structure uses one row per instrument, with named benchmarks and horizons, rather than raw metric lists.
- Sector names first, tickers smaller and muted. Rank by explicitly labeled twenty-session spread versus SPY for this slice, strongest to weakest; daily change is a separate dated column. Missing ranks last; stable ties. Do not silently switch the ranking horizon when current quotes disappear. Make leaders/laggards clear through order, signs and restrained existing colors.
- Replace absolute SMA prices with deterministic `100 × (last completed close / SMA50 − 1)`, labeled distance from 50DMA at that close, using the same admitted history/adjustment. Preserve the average and inputs in evidence. No current price mixed silently with an old average; missing history stays unavailable.
- Treasury table: maturity, yield, daily change in bp; pair only compatible dates/sources/baselines. Mismatches keep separate dates or unavailable changes. One shared as-of when justified.
- Add no charts or charting abstraction. Typed tenor, yield, date and provenance are enough to support a future curve chart.

Keep Markdown and HTML substantively aligned. Check mobile widths 360 and 390, desktop, both themes, long headlines, absent current prints, lagged history and missing optional sections. Reduce columns or disclose secondary detail on narrow screens; never clip facts/headlines to simulate brevity.

## 6. Cadence and compute

Use one common contract and small checkpoint profiles, not five prompt systems. Word ranges below are editorial guidance, not minimum fill requirements; frontier inference remains at most one normal analytical call per edition.

| Edition | Job | Suggested visible analysis |
|---|---|---|
| PREMARKET | Prior close, overnight/current evidence, events, open questions | 350–500 words; up to three attention items |
| OPEN +1M | Immediate changes versus available premarket evidence; distinguish opening noise | 80–140 words; one or two items |
| OPENING STRUCTURE | What persisted after the opening noise; actual comparable evidence only | 140–220 words; up to two items |
| AFTERNOON | What is holding, fading or reversing | 120–200 words; up to two items |
| POST-CLOSE | Session character, changed relationships, tomorrow's handoff | 350–500 words; up to three items |

Initial engineering targets, to tune on saved contexts: total input including prompt/schema around 6–8k tokens for rich editions and 3–4k for light editions; cap structured output around 3k and 1.5k respectively, measured against actual complete responses. These are targets, not claims that current payloads fit. Preserve analytical room before chasing the last token. Reduce repeated schema/legends and redundant fields first. No recursive repair model; bounded transport retries retain explicit cost accounting. A truncated or invalid response remains rejected.

Daily model cost is approximately `2 × rich-call cost + 3 × light-call cost`, plus actual retries and any future measured preprocessing. Calculate each using input/output usage and configured dated prices; do not assume fewer calls or quote unverified vendor rates. Existing usage reporting is the starting point. Cache reusable provider history only when its license, clock and refresh behavior are clear; defer new cache infrastructure here.

Keep Fable 5.1 as the reference analyst for every edition initially, with profile-specific budgets. Make model ID, route and limits configurable; record resolved identity. A future Astra or cheaper analyst uses the same artifact/output contract and must pass deterministic validation plus a small human comparison on saved evidence. Do not include multiple live analyst runs in normal production.

## 7. Minimal Claude Code guidance

Add only a short root `CLAUDE.md`, approximately this content:

> Read PROJECT_STATE.md and the current task's plan. Keep changes inside Market Brief and the requested slice. Deterministic admitted sources and calculations own facts; analysts consume prepared artifacts without fetching. Validate fail-closed before accepting output or advancing continuity. Carry structured prior state, never old brief prose as evidence. Cuttingboard is optional read-only upstream context; do not import or modify it. Preserve visual identity and use plain reader language. Keep SAMPLE/experiment state isolated from production. Verify changed behavior with relevant tests and inspect rendered presentation changes. No services, orchestration or unsolicited scope expansion.

No `.claude/agents/` set now: these responsibilities are clearer as ordinary module boundaries. Update outdated visible-label and previous-summary instructions in existing docs so there is one consistent direction.

## 8. Small coherent commits and acceptance

| Commit | Files and bounded work | Evidence needed |
|---|---|---|
| 1. Persist the analyst boundary | Add `src/market_brief/context.py`; modify `evidence.py`, `synthesize.py`, `cli.py`; extend `tests/test_projection.py`. Move existing projection, add version/hash/run identity and save exact context. Preserve initial admitted facts and current output behavior. | Deterministic projection; unchanged full evidence; one copy per fact; denied sources excluded; supplied-context references enforced; fixture context/hash replay. |
| 2. Carry structured session state | Add `src/market_brief/continuity.py`, `tests/test_continuity.py`, small continuity fixtures; modify `synthesize.py`, `cli.py`, `evidence.py`, `collect.py`, prompt and contract tests. Add bounded state output, state validation, valid comparisons and close handoff. Extend the existing calendar parser/admission only to current plus next exchange session, retaining existing freshness and source coverage rules. | Fixture chain close → next premarket → close; holiday and early-close handling; no prior prose; missing/stale/corrupt state cold start; invalid new state rejection; incompatible/repeated observations do not imply change; missing closing facts do not advance valid close state; tomorrow's admitted event retains its date and is rechecked. |
| 3. Make continuity survive scheduled runners | Modify `.github/workflows/scheduled.yml`, `cli.py`, `continuity.py`; extend `tests/test_pipeline.py` and continuity tests. Restore/upload bounded state and permitted run artifacts; add resolved-checkpoint run-directory naming while touching persistence. | Fresh temporary workspace restores a valid prior bundle; wrong workflow/mode/session/hash is excluded; SAMPLE, experiment and commissioning cannot advance production state; failed analysis preserves pointers; Pages contains only the human brief. |
| 4. Apply edition budgets and plain presentation | Add `config/editions.json`; modify context/synthesis/prompt, `metrics.py`, `render.py`, `templates/brief.html.j2`; extend `test_metrics.py`, `test_projection.py`, `test_contract.py`, `test_render.py`, `test_presentation.py`, `test_openrouter.py`. Implement profiles, model configuration, merged attention view, ordering and compact tables. | Five fixture editions with complete valid responses; numeric-placeholder checks intact; deterministic DMA distance/ranking/paired yields; no visible workflow enums; mobile/desktop visual inspection; record payload and output sizes. Update obsolete label/absolute-average assertions intentionally. |
| 5. Document and hand off | Add `CLAUDE.md`; update `README.md`, `PROJECT_STATE.md`, `DECISIONS.md`, `docs/ARCHITECTURE.md`, `docs/BRIEF_SCHEMA.md`, `docs/CUTTINGBOARD_BOUNDARY.md`; reconcile coverage declarations in `config/sources.json` with wired providers. | Full `python -m pytest` and `ruff check src tests`; saved rendered fixtures; report actual counts, budget measurements and remaining source gaps. No provider acquisition, chart code, scoring or deployment hidden in this commit. |

Use injected transports and temporary roots for tests; replay does not require paid inference or change tracked public HTML. Production workflow restore/upload needs one bounded commissioning check in the implementation session using isolated state: prove it can cross runner boundaries without advancing production pointers. A paid same-evidence quality comparison, if authorized there, should inspect lost relationships, repetitive language, watch specificity and cost; it is not a multi-agent evaluation project.

## 9. Adjustments and final slice

The direction is sound. Three adjustments keep it honest and small: formalize the existing compact projection instead of rebuilding collection; ship no cheap agent until there is unstructured work worth delegating; and distinguish a post-close run from a substantiated closing assessment. Rich premarket coverage still depends on actual overnight/event sources. This slice uses what is admitted and exposes gaps; it cannot manufacture richer market coverage through prompting.

**Proposed Fable slice:** implement commits 1–5 as one bounded feature: saved analyst context, structured previous-close and same-session continuity that survives Actions runners, edition-specific budgets with configurable analyst identity, and the requested presentation simplification. Limit new runtime modules to context and continuity. The deliverable is a brief that can explain what changed, what remains open, and why it matters at materially different edition lengths. Defer lightweight agents, new market providers, Cuttingboard acquisition, charts, quantitative scoring and long-term research storage. Stop at tested implementation and rendered evidence; this plan itself authorizes no implementation or production deployment.

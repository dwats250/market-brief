# Architecture: scripts with a thin CLI

## Recommendation

Build a small Python package with an argparse entry point, ordinary HTTP
collectors, explicit JSON records, one configured model adapter, and Markdown /
HTML templates. This combines script simplicity with a repeatable command. No
service, database, queue, framework, or long-running agent is needed.

Proposed flow:

```text
permitted feeds + local sourced inputs + optional published Cuttingboard
                            |
                 collect and normalize
                            |
           deterministic metrics + coverage checks
                            |
   evidence.json (full record, stable metric identity, deterministic comparisons)
                            |
   analyst_context.json (bounded projection + prior structured state + comparisons)
                            |
              one configured analyst synthesis (edition budget profile)
                            |
   validate: references exist in evidence AND were supplied; continuity records
                            |
   narrative.json + edition_state.json (+ session_handoff.json after a substantiated close)
                            |
                  brief.md + brief.html · continuity bundle
```

Two modules carry the v0.1 additions: `context.py` (projection and edition profiles) and
`continuity.py` (admission, comparisons, packaging, state validation, bundle persistence). Everything
else extends the existing modules. There is still no database, service, queue, or provider registry.

## Deterministic responsibility

The program owns session dates, clocks, provenance, prices, units, returns,
benchmark spreads, history windows, trigger conditions, deltas, source failure
states, literal Cuttingboard quotations, and all factual display rows. The model
owns selection, synthesis, readable organization, competing explanations, and
conditional watches. It does not calculate historical significance from memory.

Source selection occurs before synthesis. The model receives a bounded packet,
not API credentials, arbitrary filesystem access, a browser, or trading tools.
Source text is untrusted quoted material; instructions inside an article cannot
change the task, request credentials, or trigger a tool call.

Use one existing, locally configured model route whose documented noninteractive
JSON input/output path is verified during implementation. This may be an
authenticated local CLI or an API adapter; implement only one. The initial plan
prefers a local CLI already available to Dustin, with an explicit argv array and
stdin, no shell interpolation, isolated working directory, tools disabled, and a
timeout. Do not assume a subscription implies API access. If that route lacks
required isolation/structured output, report the concrete integration limitation;
do not build a provider abstraction platform. No model setup was tested tonight.

## Evidence packet v0

Use schema version `market-brief.evidence.v0`. Required top-level records:

| Record | Required contents |
|---|---|
| run | ID, checkpoint, target time, exchange session/date, collection start/end, code/config versions, mode LIVE or SAMPLE |
| sources | Source ID, kind, canonical URL, retrieval time, observation/publication date, coverage, delay, usage/retention/LLM-sharing status |
| observations | ID, instrument/series/event ID, metric, typed value or null, unit, observation time/date, source ID, status, baseline and session where relevant |
| derived | ID, value/unit, formula version, input observation IDs, window, adjustment basis, coverage denominator |
| events | ID, title, scheduled time with timezone or date/time-unknown, status, source ID, checked time |
| context_items | ID, short factual abstract, publication time, event time if known, source ID, affected topic; no full articles |
| attention | Symbol, horizon, trigger ID, supporting metric IDs, benchmark, effective date, expires after session |
| cuttingboard | Available/absent/stale/invalid, source URL, capture time, generation ID/time, literal allowed fields, adapter version |
| previous | Previous accepted brief ID or null; no fabricated baseline |
| coverage | Required/optional domains, availability, reason, usable horizon; report status READY, PARTIAL, or INSUFFICIENT |

Numbers must be finite; missing is null with a reason, never zero. Datetimes are
timezone-aware UTC; display ET using America/New_York. Daily series use an
observation date and frequency, never a fabricated midnight intraday quote.
Preserve actual feed, instrument type, currency, contract month when applicable,
and adjustment basis. No merging of different instruments under a convenient
label: DXY is not a broad trade-weighted dollar series; futures are not spot;
UCO is a leveraged fund, not crude oil.

## Freshness and source failures

Set one `target_time` at run start. Collect within five minutes. Intraday
observations after that cutoff are excluded or require a new target/run. For
premarket v0, accept timestamped delayed prices up to 20 minutes old, visibly
labeled DELAYED; this is a design tolerance, not an assertion of real-time access.
Quotes of unknown age are UNKNOWN, not current. Baseline spreads need compatible
sessions/adjustments and observation times within five minutes of one another.
Do not compare evidence from opposite sides of a material release as simultaneous.

Previous-close data must match the last completed exchange session. Daily yields
use their own business/release calendar and stated date. A value can be valid
background while ineligible for the current banner. Check calendars during the
run; a cached calendar checked within 24 hours may be used with that age shown.
Expired calendar coverage is unknown, never "no events." Check earnings for the
attention names only. Report confirmed timing, tentative timing, or unknown.

Each collector gets a bounded timeout, at most one retry for a transient error,
and explicit rate-limit handling within the run deadline. No infinite retries,
feed substitution, or hidden use of stale cache. Distinguish 401/403, rate limit,
malformed response, unavailable, stale, and conflicting sources. Never log auth
headers or credential-bearing URLs. A failed optional source degrades its section.

READY for the initial pre-market brief requires SPY/QQQ valid previous-session
history, event-calendar coverage checked for the session, and valid narrative
references. Current premarket direction is a separate coverage flag, never
implied by READY. Missing one equity anchor or part of the configured calendar
coverage produces PARTIAL with a prominent limitation. Missing both equity anchors
or all usable calendar coverage produces INSUFFICIENT: save diagnostics/evidence,
return nonzero, do not replace the last accepted brief. READY requires successful
checks of the configured BLS and BEA schedules and Fed calendar for the session;
this is still a bounded calendar, not proof of every possible catalyst. A partial
report cannot pass full first-live acceptance. Model failure or invalid narrative
also returns nonzero and never updates the latest-success pointer.

## Minimal history and deterministic calculations

Keep up to 65 completed daily OHLC bars per selected equity/ETF locally, with
split adjustment and price-return semantics explicitly fixed. Require a source
whose daily bar session definition is known; do not call an extended-hours daily
aggregate the regular-session close. If needed, derive the regular close from a
known regular-session bar and record that basis. Cross-source or corporate-action
basis conflicts invalidate the affected metric until reconciled.

- Daily price return: `100 * (C[t] / C[t-1] - 1)`; retain the last five.
- 20-session price return: `100 * (C[t] / C[t-20] - 1)`; needs 21 closes.
- Relative spread: asset 20-session return minus benchmark return in percentage
  points, with matching dates and basis. It is not a ratio or an independent
  factor estimate when constituents overlap their benchmark.
- SMA50: arithmetic mean of the last 50 completed closes. A transition needs
  both today's and yesterday's close-minus-SMA50 signs, requiring 51 closes.
  Trigger only on opposite nonzero signs; equality alone is not a cross.
- Yield change: `(yield_percent_now - yield_percent_base) * 100` basis points.

No 200-day state, ATR normalization, support/resistance inference, or "largest
move in two weeks" claim in v0. Those require separately defined history and
metrics; the model cannot supply them. No actual index contribution without
dated weights. Counted sector ETF participation is labeled as such, not breadth
of index constituents or exchanges.

## Synthesis and rendering contract

Ask the model for `market-brief.narrative.v0` JSON: banner interpretation,
executive summary, section paragraphs, selected attention IDs, and up to three
watches. Each paragraph has evidence IDs, a claim class, uncertainty, and an
alternative explanation where causality is proposed. A watch has condition,
observable confirmation, and contradiction; it is never an order instruction.

Observation rows and Cuttingboard status are rendered directly from validated
evidence. The model may reference values but cannot replace these rows. Reject
unknown IDs, invalid JSON, nonfinite numbers, unexpected fields, and unsupported
numeric references. Citation existence does not prove entailment: human spot
checking remains necessary, especially for causal language. Do not claim an
automatic validator can establish narrative truth.

One synthesis call is the normal path. On invalid output, save a short error and
an explicitly labeled evidence-only diagnostic; do not publish it as a successful
AI brief or recursively ask models to repair one another. HTML escapes all source
and model text, uses inline CSS/system fonts, and contains no active remote
scripts, tracking, or remote images. Markdown and HTML share the same facts and
source references. Preserve readable sign, unit, source, and age at narrow widths.

## Local persistence and later increments

Use ignored `runs/<session>/<checkpoint>-<UTC timestamp>/` folders holding
evidence.json, narrative.json, metadata.json, brief.md, and brief.html. Metadata
includes model/version, prompt hash, source/config IDs, hashes, validation result,
elapsed time, and previous brief ID. Secrets and private reasoning traces are
never retained. Update the local latest-success pointer atomically only after
validation; retain partial diagnostics separately. Retries get new IDs.

Cache permitted daily bars, normalized observations, and brief summaries locally;
do not commit real data by default. Initial retention: 65 daily bars per symbol
and 30 days of normalized run evidence/reports, or the source's shorter permitted
retention. Raw responses are ephemeral. Do not prune automatically in v0; document
a later manual cleanup command rather than introducing a background job.

## Continuity (v0.1)

Every observation and derived row carries a stable `identity` (instrument, metric, window,
benchmark, basis). Comparisons across runs use that identity, never the run-local evidence ID:
the same measurement observed again is `no_new_observation`, an absent measurement is
`unavailable`, incompatible units or intraday prints from different sessions are
`not_comparable`, and only a genuinely new observation of the same identity is `changed`.

Premarket admits only the previous exchange session's accepted `session_handoff.json`
(calendar-aware across holidays). Intraday and close editions admit this session's premarket
anchor and the latest accepted edition. Prior state reaches the analyst as structured hypotheses
(watch criteria, relationship statements, closing character) namespaced `anchor:evidence-id`;
no prior headline or prose is ever in the prompt, and prior refs can ground only the
`changes`, `relationships`, and `watch_updates` records.

Deterministic code assigns every watch and relationship ID (`watch-<run_id>-<n>`), resolves
horizons from the exchange calendar (NEXT_BRIEF is the next scheduled checkpoint, only tomorrow
after the close), computes evaluability (`assessable`, `missing_evidence`, `not_comparable`) from
the comparisons, and appends the analyst's assessment. A watch without comparable current
evidence can only be `unresolved`; `reversed` retires it; a passed horizon expires it. At most
three watches and three relationships carry forward.

A post-close edition classifies its closing data: `COMPLETED_SESSION`, `PROVISIONAL_NEAR_CLOSE`
(labeled), `EARLIER_HISTORY_ONLY`, or `NONE`. Because the provider's completed daily bar is
admitted only from the next day and the scheduler fires 30–40 minutes late, a CLOSE_1M run may
admit one labeled `PROVISIONAL` print per instrument: an intraday trade from the final fifteen
minutes before the exchange close, collected within ninety minutes after it. It is rendered,
cited, and carried with that status and never presented as an official closing bar. Only the
first two classifications produce a close handoff; the
bundle's close pointer and edition pointers are separate, so a premarket never overwrites the
previous close and a run without session observations leaves the last close untouched.

The bundle (`runs/continuity/bundle.json`, schema `market-brief.continuity-bundle.v1`, every
record content-hashed) is written only by accepted LIVE, non-experiment, non-commissioning runs.
On Actions it is restored before collection from the newest unexpired artifact of a successful
main-branch run of the schedule workflow and uploaded after acceptance; each run's evidence,
context, narrative, and metadata are archived for thirty days outside Pages and Git. Missing,
stale, corrupt, or foreign state is an explicit cold start with its reason in the brief.

No shared storage or writable mount with Cuttingboard. New report writes must
resolve under this project's local run root, with symlink escapes rejected. The
optional Cuttingboard collector uses an allowlisted HTTPS GET or an explicitly
provided snapshot file; no repository path traversal or callback can write back.


## Structured synthesis output budgets

`config/editions.json` retains total generation ceilings of 5,524 rich / 3,524 light
tokens and requests `reasoning={effort: "low", exclude: true}`. Fable 5.1 uses
adaptive thinking: the old 1,024-token request was not a guaranteed reservation.
[Anthropic's model-specific effort guidance](https://platform.claude.com/docs/en/build-with-claude/effort)
identifies effort as a behavioral control; only total `max_tokens` is a hard cap.
Reported reasoning tokens remain billable even when their content is excluded.
Do not describe a fixed portion of either ceiling as reserved for final JSON.

The schema bounds prose by role (80–360 characters, with a 160-character
headline), limits citations to four sufficient references per record, and bounds
identifier lengths. The already-forbidden Cuttingboard narrative array is empty by
schema as well as semantic validation. The rich word target is still 350–500 across
ALL prose, including watches and continuity; light targets remain edition-specific.
Schema character bounds are backstops, not targets or a global word-count constraint.
No source, evidence row, timestamp, coverage caveat, comparison or prior state is
removed. Repeated schema definitions use `$defs` in both the context and transport;
compact wire JSON removes whitespace without changing saved records or their hashes.

`tests/test_synthesis_budget.py` fills all arrays and prose fields with representative
identifiers: rich is 10,414 compact bytes, light 7,921. Byte/3 estimates occupy about
63% / 75% of their total ceilings before adaptive reasoning. This is a deterministic
size regression check, not a native tokenizer or a guarantee for adversarial strings,
maximum-length identifiers, JSON whitespace, or unbounded adaptive reasoning.
Normal 350–500-word prose should use substantially less than this stress shape.

A `finish_reason=length` response fails as an output-budget exhaustion before JSON
parsing or semantic validation, even if response healing produced parseable JSON.
Sanitized diagnostics retain response ID, resolved model, native finish reason,
nested numeric token/cost accounting, provider and content bytes. The documented
`X-OpenRouter-Metadata: enabled` header requests routing/healing telemetry; only
allowlisted numeric/boolean healing details are retained. Missing accounting is
unknown, never zero. Neither reasoning text nor encrypted reasoning is persisted.
Response-healing stays enabled; there is no evidence it caused the production cap.

Per request, maximum requested paid output is 5,524 tokens (rich) or 3,524 (light),
including hidden reasoning. At an output rate of R dollars per million tokens,
output exposure is 0.005524 * R or 0.003524 * R dollars, plus input charges. There is
exactly ONE client request, including on timeout, HTTP failure, malformed transport,
truncation or validation failure; a timeout can follow a billable generation.
Provider fallback is disabled and parameter support is required. No second model,
repair call or paid fallback is added. Live verification requires owner authorization;
see [the investigation and one-call criteria](SYNTHESIS_COST_CONTAINMENT.md).

# Brief schema and presentation

## Reader contract

Every brief has a session/date, generated time, evidence cutoff, and coverage
line. A fresh document does not imply fresh market observations. The banner's
qualitative state is labeled INTERPRETATION; underlying measurements are OBSERVED.
WATCH means a conditional observation to revisit, never permission to trade.

The proposed Markdown and HTML editions contain the same substantive content.
Target 600–900 words, no more than six banner facts, three attention names, three
watches, and a short source strip. Missing sections get a compact limitation,
not filler. Avoid repeated summaries of the same moves in multiple sections.

## Human-facing hierarchy

| Order | Section | Content and limits |
|---|---|---|
| A | Header | Masthead with the date; a status line only when not LIVE (`SAMPLE · Premarket edition`); three clocks: `Prices` (latest table print or `prior close <date>`), `Analysis` (`<time> · premarket` / `opening structure`), `Next` (`<time> · price refresh` / `analysis update` / `close snapshot`, or `<date> · <time> · premarket analysis`), or one `Prices & analysis` line when a synthesis's two clocks coincide; sample or commissioning truth stated once |
| B | Headline, character, executive read | One headline claim (the condition); the session character (the latest tape) with no visible label — the qualitative state is recorded, not displayed; one or two short paragraphs (the mechanism); then "The take:" in one line (the stance and what would break it) when the analyst committed to one; missing domains in plain language. No figure strip: the tables and their captions are the current-print surfaces |
| C | What changed | A section captioned by its anchors ("vs the previous close · date", "vs premarket and the 6:31 AM PT refresh"), ending on a carried page at its analysis ("· through the 7:01 AM PT analysis"). Adjudication of the prior accepted state, once: verdicts on carried watches (strengthened / weakened / reversed), then verdicts on carried relationships, then analyst-interpreted `changed` comparisons; a lower-priority record that cites no current evidence beyond what an earlier bullet cited is the same conclusion twice and is not shown; "unresolved" never renders here; a plain note when nothing comparable changed or continuity is unavailable |
| D | What matters next | The watches continuity will carry forward, at most three: new watches with natural horizons ("Into the close…", "By the 7:00 AM PT update") and their confirm / changes-it criteria, then still-active carried watches as questions with their horizon and confirm / changes-it criteria (never their verdict, which What changed holds); every other carried watch (expired or set aside without a verdict, reassessed or not) in one collapsed "Earlier watches" drawer; attention items only for instruments the lead, What changed and the live watches do not already cover (empty is normal); today's and next-session events |
| E | Macro & rates | The rates module first: "U.S. Treasury par curve · <date> · official daily observation" (or "latest official daily observation"), 2Y/5Y/10Y/30Y with yield (`5.18%`) and paired daily change (whole bp, neutral colour), 2s10s and 5s30s lines ("31 bp · 5 bp steeper"), the named curve move in bold with its sentence, an inline chart of the observed tenors with the prior entry dashed, notes (release-after-curve, stale, missing spread); then the analyst's paragraph (why the move matters; gold and miners when they move the story); then the proof; then the Metals table under its own caption and its own clock (one row per metal instrument with its named benchmark, spread emphasised) |
| F | Sector view | Sector names first, tickers muted, ranked by the labeled 20-session spread vs SPY strongest to weakest; a separate dated change column; missing ranks last |
| G | Equity structure | Interpretation first, then the mega-cap table (dated change, 20D, vs QQQ · 20s, vs 50DMA) |
| H | Cuttingboard context | Optional literal quotation; omitted when absent |
| Guide | How to read this brief (collapsed, HTML only) | The latest curve move, 2s10s, 5s30s, bull and bear, the par curve, the three clocks, § evidence, all curve moves |
| Footer | Sources & coverage (collapsed) | Basis, limitations, source ledger, evidence ledger, technical details including continuity status |

Editions share one contract; `config/editions.json` sets the budget profile and word guidance
for the two synthesis checkpoints (rich premarket, light opening structure). Deterministic
checkpoints (open +1M, hourly refreshes, close snapshot) render the last accepted synthesis from
its frozen interpretation record (`market-brief.continuity.v1`, kind `interpretation`: the
narrative, every cited row at the values the analyst saw (the take's included), the prior state it assessed (each
carried watch with its question and criteria), resolved horizons, selected triggers) under this run's observed
record. The header therefore names each clock for
what it measures: "Prices" (the latest table print), "Analysis" (the interpretation and its edition) and
"Next" (the scheduler's next update, which the page marks "Update due … has not published" once it is
overdue in the reader's browser). Each row keeps its own observation clock. The "What changed" heading
carries a sub-caption naming the anchors and, on a carried page, the analysis it runs through.

One idea has one home. The lead claims it (headline: condition; character: latest tape; summary: mechanism;
take: stance and breaker), one evidence section proves it, one watch tests it, and What changed carries every
verdict on an earlier hypothesis exactly once. The order is fixed and top-down: the macro backdrop with metals,
then breadth, then the names. Numbers share one style everywhere: `+0.53%`, `−5.30 pp`, `−6 bp`, `90.02 USD`
(no space before a percent sign, a true minus for every negative); on phones numeric cells never wrap and the
tables flex to the width.

## Narrative record

Schema name: `market-brief.narrative.v2` (v1 plus `take`). Besides `schema_version` and `mode`, the narrative
holds only analyst content; run identity, evidence hash, model identity and route, prompt hash and schema hash
are caller-owned provenance in `metadata.json`. Fields:

- `banner`: `label`, `class=INTERPRETATION`, `evidence_ids`, `limitation`.
  Allowed qualitative labels: RISK-ON, RISK-OFF, MIXED, INDETERMINATE, recorded in every
  continuity record and never displayed on the page. Optional improving/deteriorating
  modifier only with comparable prior evidence.
- `summary`: up to two paragraph records.
- `take`: the one interpretation the analyst could turn out to be wrong about: `text` (one short sentence,
  160-character target), class INTERPRETATION, up to four current evidence IDs. Empty (no text, no evidence)
  when the evidence is too thin; never text without evidence or evidence without text. Current supplied
  evidence only, grounded placeholders, no literal numbers, no trade language (the market noun "sell-off"
  is allowed). A narrative frozen under v1 has no take and renders without one.
- `sections`: keyed macro/equities/attention/cuttingboard/events; each paragraph
  has text, class OBSERVED or INTERPRETATION, evidence IDs, uncertainty, and an
  alternative if it proposes a causal relationship. Factual tables come from
  the evidence renderer, not freeform model-authored numbers.
- `attention`: up to three items, each an admitted attention trigger `id` with a short `why`;
  the selected IDs are derived from these items, and no invented triggers.
- `watches`: condition, observable confirmation, contradiction, horizon,
  evidence IDs. Class is always WATCH. The criteria must be observable by the horizon (from
  premarket, NEXT_BRIEF is the opening-structure update thirty minutes after the open).
- `character`: one short paragraph on the session's character with current evidence IDs;
  after a substantiated close it becomes the closing character.
- `relationships`: up to three; `carried_id` null with assessment `new`, or an exact carried
  ID with strengthened / weakened / reversed / unresolved.
- `watch_updates`: assessments of carried watches by exact ID; only `unresolved` is allowed
  when the watch is not `assessable` on current comparisons. A verdict is the change: the same
  move is not written again as a `changes` or `relationships` record.
- `changes`: up to three, each naming a deterministic `changed` comparison ID and citing only that comparison's
  `current_ref` and `prior_ref`.

Prior facts are cited as `anchor:evidence-id` (`previous_close:`, `premarket:`, `latest:`) and
only inside the three continuity records above.

## Artifact contracts per run

| Artifact | Schema | Authority |
|---|---|---|
| `evidence.json` | `market-brief.evidence.v0` (+ `identity` per row, `continuity.comparisons`) | Factual record |
| `analyst_context.json` | `market-brief.analyst-context.v1` | Exact model input; `evidence_hash`, `edition`, `selection`, `prior_state`, `comparisons` |
| `narrative.json` | `market-brief.narrative.v2` | Model output, validated |
| `edition_state.json` | `market-brief.continuity.v1`, kind `edition_state` | `observed` (deterministic) + `assessment` (interpretation), content-hashed |
| `session_handoff.json` | `market-brief.continuity.v1`, kind `session_handoff` | Close-designated state, only after COMPLETED_SESSION or PROVISIONAL_NEAR_CLOSE |
| `metadata.json` | — | Hashes (prompt provenance is the prompt hash), model identity/route/profile/usage, validation, continuity outcome; on synthesis editions, `editorial` overshoots and advisory `style` telemetry, never a gate; a rejected generation keeps its call, route and (when a response arrived) usage and cost |
| `runs/attempts/<session>-<checkpoint>.json` | `market-brief.synthesis-attempt.v1` | Attempt accounting, never continuity: written before a production synthesis sends its provider request, uploaded as `market-brief-attempt-<session>-<checkpoint>`; the scheduler never pays twice for a checkpoint that has one |

Absent source evidence cannot be recovered through model confidence. "No major
news" is forbidden when collection only checked Fed releases. "No matching
items in the checked Fed feed" is a bounded observed statement. Economic releases
may precede price moves without proving causation. Compare actual versus survey
consensus only when both observations have sources.

## Visual direction

Use an editorial note layout: warm off-white paper, dark charcoal body text, one
muted teal accent, amber for limitations, fine rules, and generous line-height
within a compact page. Serif headline, system sans-serif body, tabular numerals
for measurements. Keep the main reading column roughly 70–85 characters wide;
use small tables for facts and prose for relationships. This product has its own
identity and need not reproduce Cuttingboard's technical cockpit.

The top third should answer "What is the day setting up to be, and what evidence
supports that?" Avoid a grid of cards, huge colored scores, ticker crawls, dense
terminal typography, badges on every sentence, or a chat bubble layout. Source
links should sit near claims with fuller detail in a compact end section.

In the first implementation, HTML uses inline CSS, system fonts, no external
assets or JavaScript, semantic headings/tables, print styling, and labels that do
not rely on color. Check desktop and 390, 360 and 320 px widths for clipped or wrapped numbers and
wide tables. The seed's Markdown example demonstrates tone and hierarchy; no HTML
renderer or visual implementation is included tonight.

## Sharing later

Local-first reports may include personal attention and Cuttingboard quotations.
Future sharing defaults to omitting both, plus any source material without
verified sharing rights. Do not embed hidden local metadata in exported HTML.
Revalidate that interpretation remains supported after omissions; do not retain
a conclusion whose supporting evidence was removed. Optional PDF can come after
the HTML is worth reading. No hosting or publishing mechanism is in v0.

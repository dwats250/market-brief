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
| A | Header | One status / edition / date / as-of line; sample or commissioning truth stated once |
| B | Headline, character, executive read | One headline claim; INTERPRETATION label with the qualitative state and the session character; one or two short paragraphs |
| C | Compact snapshot | Up to six exact fact chips with their clocks; missing domains in plain language |
| D | What changed | "Since the previous close · date" or "Since the premarket edition": analyst-interpreted `changed` comparisons and carried relationship assessments; a plain note when nothing comparable changed or continuity is unavailable |
| E | What matters next | Watches with natural horizons ("Into the close…", "At the next update…"), carried watches with their latest assessment, up to three attention items, today's and next-session events |
| F | Equity structure | Interpretation first, then the mega-cap table (dated change, 20D, vs QQQ · 20s, vs 50DMA) |
| G | Macro & rates | Interpretation, then Treasury maturities with yield and paired daily change in bp |
| H | Sector view | Sector names first, tickers muted, ranked by the labeled 20-session spread vs SPY strongest to weakest; a separate dated change column; missing ranks last |
| I | Cross-asset structure | One row per metal instrument with its named benchmark |
| J | Cuttingboard context | Optional literal quotation; omitted when absent |
| Footer | Sources & coverage (collapsed) | Basis, limitations, source ledger, evidence ledger, technical details including continuity status |

Editions share one contract; `config/editions.json` sets the budget profile and word guidance
per checkpoint (rich premarket/close, light open/opening-structure/afternoon).

## Narrative record

Schema name: `market-brief.narrative.v1`. Fields:

- `run_id`, `evidence_packet_hash`, `model_id`, `prompt_version`.
- `banner`: `label`, `class=INTERPRETATION`, `evidence_ids`, `limitation`.
  Allowed qualitative labels: RISK-ON, RISK-OFF, MIXED, INDETERMINATE. Optional
  improving/deteriorating modifier only with comparable prior evidence.
- `summary`: up to two paragraph records.
- `sections`: keyed macro/equities/attention/cuttingboard/events; each paragraph
  has text, class OBSERVED or INTERPRETATION, evidence IDs, uncertainty, and an
  alternative if it proposes a causal relationship. Factual tables come from
  the evidence renderer, not freeform model-authored numbers.
- `attention_ids`: up to three admitted attention records; no invented symbols.
- `watches`: condition, observable confirmation, contradiction, horizon,
  evidence IDs. Class is always WATCH.
- `character`: one short paragraph on the session's character with current evidence IDs;
  after a substantiated close it becomes the closing character.
- `relationships`: up to three; `carried_id` null with assessment `new`, or an exact carried
  ID with strengthened / weakened / reversed / unresolved.
- `watch_updates`: assessments of carried watches by exact ID; only `unresolved` is allowed
  when the watch is not `assessable` on current comparisons.
- `changes`: up to three, each naming a deterministic `changed` comparison ID.

Prior facts are cited as `anchor:evidence-id` (`previous_close:`, `premarket:`, `latest:`) and
only inside the three continuity records above.

## Artifact contracts per run

| Artifact | Schema | Authority |
|---|---|---|
| `evidence.json` | `market-brief.evidence.v0` (+ `identity` per row, `continuity.comparisons`) | Factual record |
| `analyst_context.json` | `market-brief.analyst-context.v1` | Exact model input; `evidence_hash`, `edition`, `selection`, `prior_state`, `comparisons` |
| `narrative.json` | `market-brief.narrative.v1` | Model output, validated |
| `edition_state.json` | `market-brief.continuity.v1`, kind `edition_state` | `observed` (deterministic) + `assessment` (interpretation), content-hashed |
| `session_handoff.json` | `market-brief.continuity.v1`, kind `session_handoff` | Close-designated state, only after COMPLETED_SESSION or PROVISIONAL_NEAR_CLOSE |
| `metadata.json` | — | Hashes, model identity/route/profile/usage, validation, continuity outcome |

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
not rely on color. Check desktop and 390px width for clipped numbers and wide
tables. The seed's Markdown example demonstrates tone and hierarchy; no HTML
renderer or visual implementation is included tonight.

## Sharing later

Local-first reports may include personal attention and Cuttingboard quotations.
Future sharing defaults to omitting both, plus any source material without
verified sharing rights. Do not embed hidden local metadata in exported HTML.
Revalidate that interpretation remains supported after omissions; do not retain
a conclusion whose supporting evidence was removed. Optional PDF can come after
the HTML is worth reading. No hosting or publishing mechanism is in v0.

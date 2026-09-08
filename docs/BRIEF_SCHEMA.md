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
| A | Banner / market state | Title, session, cutoff; a qualitative interpretation such as MIXED, with up to six exact fact chips and their horizons |
| B | Executive summary | Two short paragraphs: dominant relationship, meaningful tension, and biggest unknown |
| C | Macro / cross-asset | Compact observed table; one interpretation paragraph linking rates, FX, commodities, and volatility only as evidence allows |
| D | Equity structure | SPY/QQQ, sector spread, concentration proxy or actual breadth with its definition; previous-close and current observations separated |
| E | Attention list | Up to three names with dated trigger, horizon, evidence, and reason for attention; no target or entry |
| F | Cuttingboard context | Optional, clearly attributed literal state/time and brief explanation; unavailable is acceptable |
| G | Event risk | Up to four material scheduled items, event times in ET, confirmed/estimated status, source; calendar coverage limitation |
| H | What to watch | Up to three condition/confirmation/contradiction statements tied to evidence; no forecasts disguised as certainty |
| Footer | Sources and coverage | Compact source links with dates/delays, missing domains, and model-assisted interpretation label |

For later checkpoints, insert "Changed since [previous time]" immediately after
the banner. List changed, persisted, and invalidated items. Each delta references
both previous and current evidence IDs. The prose must explain why the change
matters, not just subtract numbers. Missing prior data means baseline, not zero.

## Planned narrative record

Schema name: `market-brief.narrative.v0`. Fields:

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
- `changes`: empty for initial v0, later records with previous/current IDs and
  type changed/persisted/invalidated/not-comparable.

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

# Project State

## Now

Scheduled and commissioning briefs collect deterministically, save the exact analyst context, synthesize
once through OpenRouter under an edition budget profile, validate fail-closed (references must exist in
admitted evidence *and* in the supplied context), render, and deploy to Pages. Market Brief v0.1
(2026-09-09, branch `feature/market-brief-v01`) added:

- `analyst_context.json`: versioned, hashed projection per run (`context.py`); light editions receive a
  deterministic "changed" selection that names what it omitted.
- `continuity.py`: previous-close and same-session anchors admitted by exchange session, comparisons keyed by
  stable metric identity, code-owned watch/relationship IDs with append-only analyst assessments,
  `edition_state.json` per accepted edition and `session_handoff.json` after a substantiated close,
  a hashed bundle restored from and uploaded to Actions artifacts (thirty-day horizon).
- `config/editions.json`: one configured analyst, rich/light budgets, per-checkpoint guidance.
- Presentation: status and clocks, character, what changed, what matters next, ranked sectors,
  50DMA distance, the Treasury curve module, collapsed sources.

Measured on the fixture (2026-09-09): rich context 8.6–8.9 KB (~2.2k tokens) and light 8.1 KB before the
7.2 KB output schema; fixture responses 3.9–4.3 KB. Generation ceilings are 7,000 rich / 4,500 light tokens (hard
exposure limits, not usage targets); the wire schema is inlined and bounds are enforced locally.

The hold of 2026-09-10 was lifted on 2026-09-11 (`docs/SYNTHESIS_COST_CONTAINMENT.md`); the first full
production Monday was 2026-09-14, when PREMARKET, OPEN_1M, OPEN_30M and AFTERNOON published and the rich
CLOSE_1M failed closed at 68,093 bytes against its 64,000-byte budget.

**Cadence (2026-09-14, merged to `main` 2026-09-15 as PR #26).** Rich interpretation is scarce: PREMARKET (NYSE
open −30 minutes, rich) and OPEN_30M (open +30 minutes, light) are the day's only analyst calls. OPEN_1M
(open +1 minute), HOURLY_1100 … HOURLY_1500 (exchange-clock hours) and CLOSE_1M (close +1 minute) are
deterministic: they refresh the observed record under the last accepted synthesis, frozen as the bundle's
hashed `interpretation` record, and never call the analyst or rewrite the record. Checkpoints are anchored to
the exchange session and displayed in Pacific time (British Columbia keeps UTC−7 from 2026, so the opening
structure update reads 7:00 AM PT in New York daylight time and 8:00 AM PT in standard time). The close is a snapshot that hands the session off from deterministic state (its own closing
snapshots, the carried assessment labeled with the checkpoint that interpreted it). Every page states its
clocks and the scheduler's next update (since the Rates & Reading Pass: Prices, Analysis, Next). The standalone rich close
edition and the AFTERNOON synthesis are gone; `schedule.CHECKPOINT_KINDS` is the one statement of the day
and `config/editions.json` profiles only the two synthesis checkpoints. Presentation polish landed with it:
one table clock with per-row exceptions, the three-word absence vocabulary (`no print`, `—`, `not
collected`), unsigned neutral zero, `vs SPY` / `vs QQQ` headers with the window in the caption, the Basis
line moved to Technical details, a readable `--faint`, sentence-case watch metadata, "What changed" with an
anchor sub-caption, verdict-first relationship bullets, trigger tags on flagged names, figure clocks only
when they differ from the data clock, and a soft eight-to-ten-word headline target recorded as an
editorial note. PR #24 (quiet provenance) was incorporated in the same branch, so #24 was closed as
superseded.

**Rates & Reading Pass (2026-09-25, branch `feat/rates-reading-pass`, local; PRD
`docs/2026-09-25-rates-reading-pass.md`).** Treasury collection adds the 30Y, bridges the year boundary and
rounds changes to whole bp; `curve.py` derives the 2s10s/5s30s spreads, judges the curve's freshness against
weekdays, names the curve move with a deterministic classifier and notes releases the curve predates. Macro &
rates leads with a dated par-curve module (four tenors, spread lines, the named move, an inline chart, notes),
rates are neutral in colour and shown as `5.18%` / whole bp. The header states three clocks (Prices, Analysis,
Next) with a silent LIVE and a dated masthead, and an overdue page says its update has not published. Section
hierarchy, a retuned light palette with no opacity-dimmed text, a labelled `§ evidence` marker, "Metals", and a
collapsed "How to read this brief" guide complete the reading pass. The analyst reads the curve rows and a
read-only curve record and may write `2s10s`/`5s30s`; the narrative schema is unchanged.

## Next

Rates & Reading Pass: owner review of the branch, then merge; after deploy, watch the first live pages for the
curve module, the Prices clock tracking the tables' latest print, and the first analyst use of the curve label.
First live bond-holiday check: Tue Oct 13, 2026 reads Friday's curve as the latest official observation.

The merge (2026-09-15) redeployed the Cloudflare Worker with the hourly wake candidates.
Observe the first full day: two analyst calls in the run logs, refreshes publishing on the hour with
the opening-structure analysis clock, and the close snapshot handing off (PROVISIONAL
near-close prints; an extended-hours-only print means no session print, no page, and a cold-start close
continuity the next morning, which is the honest outcome). Then decide on the watch-resolution question the
deterministic close leaves open: the opening-structure watches are judged only by the next premarket.
Bounded follow-up, held: a failed first synthesis could still be attempted again if two fresh runners both
start inside one twenty-minute synthesis window after an unusual queue delay; a durable attempt record
across runners would close it.

## Direction after v0.1

v0.1 freezes after merge except for defects found in live use. Future work favors reduction, clarity,
and signal amplification over feature growth.

- PREMARKET is the rich analytical edition and the opening-structure update (open +30 minutes) is the one
  interpretive update of the day. Everything later is a deterministic refresh under that interpretation.
- Prompt refinement should strengthen dominant supported drivers, relationships, contradictions, and
  changes from prior state, not manufacture more signals.
- Weekly continuity, later: Friday post-close weekly handoff → Sunday Week Ahead → Monday premarket.
- Compute experiment, later: intermediate editions may use deterministic delta artifacts and lightweight
  polishing, escalating to the frontier analyst only when warranted.

None of the future items is authorized for implementation now.

## Later

Provider caching under clear licenses, additional providers, charts from the typed tenor/yield rows,
quantitative evaluation of archived point-in-time states, Cuttingboard market_structure envelope adapter.

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
- Presentation: one status line, character, what changed, what matters next, ranked sectors,
  50DMA distance, paired Treasury table, collapsed sources.

Measured on the fixture (2026-09-09): rich context 8.6–8.9 KB (~2.2k tokens) and light 8.1 KB before the
7.2 KB output schema; fixture responses 3.9–4.3 KB. Output caps are 4,500 / 2,500 tokens because the last
recorded live response used 2,794 completion tokens under the smaller v0 contract; tune on saved contexts.

## Next

Run one bounded commissioning check of the runner-boundary restore (`gh workflow run schedule.yml
-f continuity_check=true` after merge) and then observe scheduled editions. Confirm on real runs that a
cron-delayed CLOSE_1M admits PROVISIONAL session-ending prints (IEX latest trade inside the final fifteen
minutes before the close) and hands off; if IEX's latest trade after the close is an extended-hours print,
the run has no session print and the next premarket cold-starts, which is the honest outcome.

## Later

Provider caching under clear licenses, additional providers, charts from the typed tenor/yield rows,
quantitative evaluation of archived point-in-time states, Cuttingboard market_structure envelope adapter.

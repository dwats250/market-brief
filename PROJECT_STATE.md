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
7.2 KB output schema; fixture responses 3.9–4.3 KB. Generation ceilings are 7,000 rich / 4,500 light tokens (hard
exposure limits, not usage targets); the wire schema is inlined and bounds are enforced locally.

The hold of 2026-09-10 was lifted on 2026-09-11 (`docs/SYNTHESIS_COST_CONTAINMENT.md`); the first full
production Monday was 2026-09-14, when PREMARKET, OPEN_1M, OPEN_30M and AFTERNOON published and the rich
CLOSE_1M failed closed at 68,093 bytes against its 64,000-byte budget.

**Cadence (2026-09-14, branch `feat/cadence-two-clock`).** Rich interpretation is scarce: PREMARKET (6:00 PT,
rich) and OPEN_30M (7:00 PT, light) are the day's only analyst calls. OPEN_1M (6:31 PT), HOURLY_0800 …
HOURLY_1200 and CLOSE_1M are deterministic: they refresh the observed record under the last accepted
synthesis, frozen as the bundle's hashed `interpretation` record, and never call the analyst or rewrite the
record. The close is a snapshot that hands the session off from deterministic state (its own closing
snapshots, the carried assessment labeled with the checkpoint that interpreted it). Every page states two
clocks ("Interpretation as of", "Data as of") and the scheduler's next update. The standalone rich close
edition and the AFTERNOON synthesis are gone; `schedule.CHECKPOINT_KINDS` is the one statement of the day
and `config/editions.json` profiles only the two synthesis checkpoints. Presentation polish landed with it:
one table clock with per-row exceptions, the three-word absence vocabulary (`no print`, `—`, `not
collected`), unsigned neutral zero, `vs SPY` / `vs QQQ` headers with the window in the caption, the Basis
line moved to Technical details, a readable `--faint`, sentence-case watch metadata, "What changed" with an
anchor sub-caption, verdict-first relationship bullets, trigger tags on flagged names, figure clocks only
when they differ from the data clock, and a soft eight-to-ten-word headline target recorded as an
editorial note. PR #24 (quiet provenance) is incorporated in the same branch.

## Next

Merge `feat/cadence-two-clock`; the push redeploys the Cloudflare Worker with the hourly wake candidates.
Observe the first full day: two analyst calls in the run logs, refreshes publishing on the hour with
"Interpretation as of 7:00 AM PT", and the close snapshot handing off (PROVISIONAL near-close prints; an
extended-hours-only print means no session print, no page, and a cold-start close continuity the next
morning, which is the honest outcome). Then decide on the watch-resolution question the deterministic
close leaves open: the 7:00 watches are judged only by the next premarket.

## Direction after v0.1

v0.1 freezes after merge except for defects found in live use. Future work favors reduction, clarity,
and signal amplification over feature growth.

- PREMARKET is the rich analytical edition and the 7:00 PT opening-structure update is the one
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

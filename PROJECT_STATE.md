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
- Presentation: status and clocks, character, what changed, what matters next, the Treasury curve module
  with the metals table, ranked sectors, 50DMA distance, collapsed sources.

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

**Editorial compression pass (2026-09-28, branch `feat/editorial-compression`; plan and rulings in the Market
Brief project: `plan-editorial-compression-2026-09-27`, `monday-review-2026-09-28`).** One idea has one home.
Render: the figure strip and the label pill are gone (the label is recorded, never displayed); What changed is
adjudication once (watch verdicts, then relationship verdicts, then changes, with a lower-priority record that
repeats a verdict's evidence not shown, and "unresolved" never shown); What matters next carries exactly the
watches continuity carries (new first, then live carried ones not reversed, at most three) as questions with
horizons, and every other carried watch in one collapsed "Earlier watches" drawer; a flag renders only when
its instrument is not already in the story; Metals fold into Macro & rates under their own caption and clock;
sections run Macro & rates → Sector view → Equity structure; numbers share one style (`+0.53%`, `−5.30 pp`)
and never wrap on phones (checked at 390, 360 and 320 px). Prompt (+829 bytes under an 11,000-byte ceiling):
the lead roles, the summary as the mechanism now, the take as the stance and its breaker, the macro paragraph
as why (with gold and miners when they matter), criteria observable by the horizon, a verdict as the change,
attention only when uncovered. No schema, continuity, context, validator, cadence or data change.

**Carried-watch criteria and push CI (2026-09-29, follow-up to PR #38).** F2: the frozen interpretation record
keeps each carried watch's confirm / changes-it criteria beside its question, so a live carried watch renders
them, at the values its author saw, on the synthesis page and every refresh under it. Additive under
`market-brief.continuity.v1`: no new state, identity or lifecycle; the live set, ordering, verdicts and horizons
are unchanged; the analyst context is byte for byte unchanged (it already read the criteria from the carried
state); an interpretation frozen earlier renders its carried watches as questions alone. CI: `tests.yml` also
runs on pushes to `main`, except a push whose only change is `publish/index.html`, the scheduler's whole
publication write surface (its pushes use the workflow token and start no workflow in any case).

**Accepted continuity survives a failed publication (R1, 2026-09-29).** Restore took only artifacts of runs
that concluded `success`, but the continuity upload comes before the push and the Pages deployment, so a push
rejected after a concurrent merge, or a failed deployment, made an accepted bundle ineligible and the next
runner rolled back to an older bundle or cold-started. `select_artifact` now also admits a run that concluded
`failure` and checks the artifact's own name: the upload runs only when every earlier step succeeded, so the
artifact proves acceptance (a workflow test pins that gate). A rejected synthesis still uploads nothing;
cancelled and unfinished runs stay ineligible; admission and the publication, cadence and model-call rules
are unchanged. One consequence follows from the bundle's completion proof: a queue-delayed wake of the same
checkpoint after such a failure now skips where it used to run the checkpoint again (for a synthesis, a
second paid call), so a page that failed to push waits for the next checkpoint's publish.

**One paid synthesis per checkpoint, across runners (R2, 2026-09-29).** Duplicate suppression proved only
acceptance (marker, checked-out page, restored bundle), so a rejected paid generation left nothing a fresh
runner could see, and a second runner inside the same twenty-minute window paid again; the rejected run's
metadata also said `calls: 0` / `model_route: none`. A production synthesis now writes
`runs/attempts/<session>-<checkpoint>.json` immediately before its provider request (no record, no request);
the workflow's last, `always()` steps upload it as `market-brief-attempt-<session>-<checkpoint>`; `scheduled()`
skips a synthesis checkpoint with a local record or that artifact from a `schedule.yml` run on `main`, and
fails closed (no request) when it cannot list them. The concurrency group makes that upload finish before
another run starts. Attempt records are accounting only; continuity, validation, publication, cadence, the
fallback and deterministic refreshes are unchanged. A rejected generation now records `calls: 1`, its route,
requested model and, when a response arrived, usage and cost.

**Continuity restores only this repository's own runs (S1, 2026-09-29).** `select_artifact` checked the artifact
name, branch name, workflow path and conclusion, all of which a fork's pull_request run can satisfy: it executes
the pull request's own edit of `schedule.yml`, from a branch it can name `main`, and can upload an artifact of any
name. The bundle's hashes are public content digests and its origin fields are self-declared, so a forged bundle
passed every content check and its interpretation would have been rendered by the next refresh. Eligibility now
also requires the run lookup's `head_repository.id` to equal `repository.id` (integers; schema-required, though a
deleted fork's head comes back null); anything missing, null or malformed is not eligible, so a foreign artifact
never outranks an older valid one. R2's attempt lookup now decides origin the same way, on its run details,
instead of the listing's optional ids (which let two absent ids compare equal).
Existing artifacts from this repository's own runs stay eligible; no workflow, bundle or admission change.

**Evidence integrity (E1, 2026-09-29).** Two authority gaps closed without a new clock, source or comparison
system. (1) A stale Treasury curve (`curve_freshness`: more than five calendar days old) already rendered levels
only, but its daily tenor and spread change rows still passed the authority filter and could be cited, or offered
through a `changed` comparison, as current movement; `curve.stale_movement` now withholds them from `model_packet`
(the analyst context and the validator) and from `_current_by_key` (comparisons), exactly when the page blanks them.
Dated levels stay citable; every row stays in the evidence record. (2) A `change` named a changed comparison but
could cite any admitted evidence; it now cites only that comparison's `current_ref` and `prior_ref`. The prompt's
comparison sentence says so and shrinks (10,997 → 10,990 bytes under the 11,000 ceiling).

**Pre-freeze cleanup (2026-09-29).** Four fixes from the final sign-off, nothing else. (F1) The run archive is
diagnostic and now follows the continuity upload and publication (still `always()`), so its own failure gates
neither. (F2) A comparison is `changed` only for a later entry whose value moved: the same value at a later entry
is `no_new_observation`, an entry dated before the prior state is `not_comparable`, and `validate_state` re-derives
the move before it accepts a change. When nothing moved, the page's "What changed" note now says so where the old
rule counted a re-stamped value as a change. (F3) A body cut off mid-read or an unparseable status line takes the
transport path (no automatic paid retry), and a 200 whose response, choice or message is not an object is a
diagnosed rejection that keeps the paid attempt's accounting, never a traceback. (F4) Only the absence of an
eligible continuity bundle is a cold start; a restore that cannot finish (the GitHub API, a run lookup, the
download, a timeout, installing the bundle) exits non-zero, so the run stops before collection and uploads no cold
bundle over accepted state.

## Next

Editorial compression: after merge, verify Slice A against the Monday 2026-09-28 PREMARKET and OPEN_30M run
artifacts (re-render, screenshots at phone width), then judge the prompt from the next normal day's two
scheduled calls (no paid test call).

Carried-watch criteria and push CI: after merge, the next synthesis freezes the criteria (a page carried under
an interpretation frozen before the merge shows questions alone until then); confirm the merge push starts a
`push` Tests run on `main` and the next `Publish … brief` commit starts none.

Accepted continuity survives a failed publication (R1): after merge, the next red scheduler run whose failure
is in publication (a push rejected by a merge, a Pages failure) should be followed by a run whose log reads
`Continuity: restored artifact … from run <that run>`.

One paid synthesis per checkpoint (R2): after merge, each PREMARKET and OPEN_30M run's artifacts should include
`market-brief-attempt-<session>-<checkpoint>`, and any second wake of the same checkpoint should log
`SKIP / <checkpoint> / paid synthesis already attempted` (or `already completed`) with no provider request.

Evidence integrity (E1): after merge, a synthesis on a stale curve should cite Treasury levels only, and any
rejection reading `change cites evidence outside its comparison` names the stray reference.

Continuity restores only this repository's own runs (S1): after merge, the next scheduled run should still log
`Continuity: restored artifact … from run <the previous run>`.

Pre-freeze cleanup: after merge, each run's `market-brief-run-*` archive is uploaded after Pages; a red run whose
log reads `Continuity: restore failed (…); stopping before collection.` has no continuity artifact, and the run
after it restores the one before it.

Market Memory, Week Ahead, market clocks and Opus 5.5 (approved plan, revision 2 with the source-lineage
amendment: `docs/2026-09-27-market-memory-week-ahead.md`). Slice order D1 → C1 → A1 → one full week of session
records → B. D1 (branch `feat/opus-5-5-analyst`): the configured primary analyst becomes Opus 5.5 and a workflow
experiment runs the checkpoint it was dispatched with. Both non-publishing gates passed on 2026-09-27 (PREMARKET rich,
OPEN_30M light: HTTP 200, PASS, Opus 5.5 via Anthropic, $0.154 / $0.087); the owner merges.

Rates & Reading Pass (merged as PR #36): after deploy, watch the first live pages for the
curve module, the Prices clock tracking the tables' latest print, and the first analyst use of the curve label.
First live bond-holiday check: Tue Oct 13, 2026 reads Friday's curve as the latest official observation.

The merge (2026-09-15) redeployed the Cloudflare Worker with the hourly wake candidates.
Observe the first full day: two analyst calls in the run logs, refreshes publishing on the hour with
the opening-structure analysis clock, and the close snapshot handing off (PROVISIONAL
near-close prints; an extended-hours-only print means no session print, no page, and a cold-start close
continuity the next morning, which is the honest outcome). Then decide on the watch-resolution question the
deterministic close leaves open: the opening-structure watches are judged only by the next premarket.

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

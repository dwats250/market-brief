# PRD: Rates & Reading Pass

**Status:** Approved for implementation by the owner, 2026-09-25
**Base:** `main` @ 14670b0
**Branch:** `feat/rates-reading-pass`
**Authority:** This file. It folds in the 2026-09-25 UX/rates review and the owner's rulings on it. Where the two differ, this file wins.

---

## 0. How to work on this

You are implementing a bounded presentation-and-semantics pass on Market Brief. Nearly all of the added value comes from better semantics, deterministic transforms, and better presentation of data the system already collects. Nothing here needs a new data source or a new model call.

1. **Read first.** Read `CLAUDE.md`, `PROJECT_STATE.md`, `DECISIONS.md`, and this file. Then read the code each requirement touches before changing it. Read independent files in parallel.

2. **Plan in writing, then build in slices.** Before editing, write a short slice plan in the **Progress** section at the bottom of this file: one line per slice, mapping requirement IDs to files and tests. Then implement one slice at a time:
   - Where the behavior is new (classifier, spreads, year boundary, freshness), write the tests first.
   - Write the code.
   - Run the full suite.
   - Commit.

3. **Code owns facts; this file owns intent and scope.** §2 records facts verified at 14670b0. If the code disagrees with one of them, don't force the plan onto it:
   - Record the discrepancy in Progress.
   - Choose the implementation that best serves the intent stated in §1 and the requirement.
   - Stop and ask only if the discrepancy makes a requirement unsafe or self-contradictory.

4. **Implement exactly this scope.** If you notice something worth doing that is not listed, put it under "Deferred ideas" in your final report rather than building it. §4 lists ideas that were considered and deliberately deferred.

5. **Write general solutions.** Don't special-case fixtures or tests. When an existing test pins behavior this PRD deliberately changes (header strings, the style hash, yield formatting, rates colour), update that assertion and name it in the commit message. Don't loosen unrelated assertions.

6. **Stay local.** No paid model calls, no pushes, no PRs, no workflow dispatch, and no Cloudflare or Pages changes unless the owner asks in-session. Local commits on the branch only.

7. **Keep Progress current.** It is how this work survives a context compaction: what's done, what's next, discrepancies found, measurements taken.

---

## 1. Why

Market Brief should be quick to scan, use proper institutional rates language, and be truthful about time.

**Three findings drive this pass:**

- **The page mixes three clocks and the header blurs them.**
  - Equity prices refresh hourly.
  - The analysis is written twice a day.
  - Treasury yields are an official daily observation from the previous business day.
- **Rates lack shape.** "10Y +7 bp" says nothing about what the curve did.
- **Small defects erode trust.**
  - A failed run leaves "LIVE" on the page.
  - Rising yields render green.
  - The light theme is too bright.
  - The § evidence marker is hard to discover.
  - The analyst can narrate yesterday's curve as the cause of today's intraday moves.

**Editorial rule for rates:** show the shape, show the change, name the move, say why it matters when the evidence supports it, then stop.

**Posture:** sophistication underneath, a calm surface, professional terminology, and optional explanation for newer readers. This is not a bond terminal.

---

## 2. Verified facts (at 14670b0)

Re-verify the ones your slice depends on.

### Treasury collection
- `collect.treasury_rows` parses Treasury's daily par-yield XML, requested as `field_tdr_date_value={now.year}`.
- It keeps only entries dated before today (ET).
- It emits level and change rows for 2Y, 5Y and 10Y. The change is the latest entry minus the previous entry.
- **30Y is not parsed.** `render.treasury_rows` already loops over 30Y.

### Treasury methodology
- The curve is built from FRBNY indicative bid-side quotes taken at or near 3:30 PM ET, fitted into a par curve, and usually published by 6 PM ET.
- **So every page, all day, shows the previous business day's curve.**
- No intraday yields exist anywhere in the pipeline; "live yields" is on the deferred list in `config/sources.json`.

### Header
- `render.presentation` builds `status_line` ("LIVE · Hourly refresh · Friday, Sep 25") and `clocks`.
- **"Analysis anchored"** is the frozen interpretation's `target_time`.
- **"Observed record refreshed"** is this run's `actual_started_at`. That is when collection started, not a price timestamp.
- **"Next update"** comes from `schedule.next_checkpoint` via `next_update_label`.
- The masthead's right side repeats the edition label.

### Stale pages
- `LAST GOOD BRIEF` is in `DISPLAY_STATUSES`, but nothing sets it.
- A run that fails, or legitimately publishes nothing, leaves the previous page saying LIVE with a "Next update" time in the past.
- Publishes currently land 2–3 minutes after their checkpoint (git log, 2026-09-25).

### Rates formatting and colour
- `direction()` colours `daily yield change` green/red because it is in `SIGNED_METRICS`.
- `formatted()` renders "+7.00 bp" and "5.18 % yield". The raw change value is 7.000000000000028.

### Typography
- h1 is 52 px desktop / 38 px mobile, Georgia 500, which renders as 400.
- h2 is 24 px desktop / 22 px mobile, Georgia 600, which renders as 700. Georgia ships only 400 and 700, and Android falls back to other serifs.
- "What changed" is a `div.since` with a 20 px h2 and no section rule.

### Light palette and its tests
- Current light tokens (PR #35): paper #f7f2e7, muted #665f55, faint #746d62, `--teal` #6f5b3e (actually brown).
- `tests/test_cadence.py` gates light contrast: ink ≥ 7; muted ≥ 4.5; faint ≥ 3.9; teal, positive, negative and amber ≥ 4.5.
- `tests/test_voice_take.py` pins the style block's SHA-256.

### § marker
- `details.cite summary` renders at `opacity:.7`, 14 px, with padding `0 3px`.
- Its effective contrast in the light theme is 3.1:1.
- `td.secondary` uses `opacity:.85`.

### CSP
- The policy is `default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'`.
- Inline SVG and inline script are allowed. Any `<img>`, including data: URIs, is blocked.

### Analyst contract
- Prose may not contain digits except the labels matched by `synthesize.ALLOWED_LABELS` (tenors, windows, index names).
- **"2s10s" and "5s30s" would be rejected today as literal numbers, and the synthesis would fail closed.**
- The trade-language check covers attention reasons and the take; "sold off" passes.

### Budgets
- The provider-enforced narrative schema is near the size Anthropic accepts (DECISIONS.md, 2026-09-25).
- The light analyst context is limited to 40,000 bytes. The real 2026-09-24 opening-structure context had about 3.2 KB of headroom after schema factoring.

### Threshold
- `config/magnitude.json` sets bp SMALL = 3.0.

---

## 3. Requirements

### R1: Header and freshness

**Goal:** each clock says exactly what it measures, and the freshest one anchors the eye.

Replace `status_line` and `clocks` with a masthead plus a three-line clock block:

```
MARKET BRIEF                                   FRIDAY, SEP 25
Prices     10:01 AM PT
Analysis    7:02 AM PT · opening structure
Next       11:00 AM PT · price refresh
```

**Prices line**
- It shows the latest current equity print across the page's tables: the maximum of the per-table `as of` clocks that `change_column` already computes.
- With no current prints, it reads `Prices · prior close Thu, Sep 24`.
- The run's collection time stays in Technical details only.

**Analysis line**
- It shows the interpretation clock (same source as today) plus the interpreting edition: `premarket` or `opening structure`.
- When the two clocks coincide (synthesis editions), use one line: `Prices & analysis 6:02 AM PT · premarket`.

**Next line**
- It keeps `next_update_label`'s kinds, in reader words:
  - `price refresh`
  - `analysis update`
  - `close snapshot`
  - `Mon, Sep 28 · 6:00 AM PT · premarket analysis`

**Presentation**
- The Prices line is the visual anchor: ink colour, slightly larger.
- Analysis and Next are muted.
- Use tabular numerals in a two-column label/time grid that fits 390 px.

**Status**
- LIVE renders nothing.
- SAMPLE and LIVE COMMISSIONING stay visible and loud, as today, alongside the existing `truth` notice. So does LAST GOOD BRIEF if anything ever sets it.
- The masthead's right side shows the date. The edition label no longer appears twice.

**Elsewhere**
- **"What changed"** on a carried page describes the window up to the analysis, not up to now. Keep the anchors in its caption and add the end, for example: `vs premarket and the 6:32 AM PT refresh · through the 7:02 AM PT analysis`.
- **The Markdown rendition** carries the same clock lines.

### R2: Overdue page

**Goal:** a page never claims freshness it no longer has.

**Data**
- Render the next scheduled checkpoint as an absolute ISO timestamp in a data attribute.
- Render its PT display text alongside it.

**Script** (extend the existing inline IIFE)
- Compare `Date.now()` with that timestamp plus a 15-minute grace period. The grace is a named constant in render.py.
- Once past it, the Next line reads `Update due 11:00 AM PT has not published`.
  - Use the page's own PT text; don't recompute it in the browser's time zone.
  - The wording is neutral because the cause may be a failure or a legitimate no-publish.
- Evaluate on load, on `visibilitychange`, and once a minute, because phones keep tabs open.

**Limits**
- No server-side change, no new status value, no network call.
- Without JavaScript, the page renders exactly as before.

### R3: Hierarchy and typography

**Sizes and spacing**
- h2: 24 → 28 px on desktop, 22 → 25 px on mobile.
- Section `margin-top`: 40 → 52 px on desktop, 32 → 40 px on mobile.
- Let size and space do the work. Don't tune serif weights.

**Section rules**
- Change the section rule from 1 px `--line` to 2 px in a darker but still quiet tone, e.g. a new `--rule-strong` token in both themes.
- It should stop the eye while flick-scrolling without looking like a dashboard.

**Structure and names**
- Promote "What changed" to a real `<section>` with the standard h2 and rule.
- Rename "Cross-asset structure" to **Metals**, and drop the "Metals structure" prefix from its caption.
- Don't add an Energy section.

**Unchanged**
- h1 stays dominant.
- Body sizes and weights stay as they are.
- Bold remains reserved for inline labels: The take, Strengthened, Unresolved, and the curve move label.

### R4: Light palette

Retune the light theme as one set. The dark theme is unchanged apart from any shared tokens R3 or R5 add.

| token | value | contrast vs paper |
|---|---|---|
| paper | #E6DFCC | — |
| ink | #2F2B24 | 10.6 |
| muted | #5E594E | 5.2 |
| faint | #655F53 | 4.8 |
| accent (`--teal`) | #3F6660 | 4.8 |
| amber | #7A5424 | 5.1 |
| notice | #EDE6D4 | faint on it 5.1, amber 5.4 |
| positive | #2F6446 | 5.2 |
| negative | #963F37 | 5.2 |
| neutral | #5E5B53 | 5.1 |
| line | #C4BAA2 | decorative |
| rule | #D6CDB8 | decorative |

**Constraints**
- These are starting values. Adjust them if rendering shows a problem, but:
  - every text token must stay ≥ 4.5:1 on both paper and notice;
  - faint must stay quieter than muted.
- The accent deliberately returns to a muted teal; PR #35 had made it brown.

**Remove opacity dimming from text**
- `details.cite summary` (opacity .7) and `td.secondary` (opacity .85, which comes out at 3.9:1).
- Use the colour tokens instead.

**Tests**
- Raise the light faint floor from 3.9 to 4.5.
- Assert the text-on-notice pairs.
- Re-pin `STYLE_SHA256`.

### R5: § evidence

- **One label.** The first § in document order renders as `§ evidence`; every later one stays a bare `§`.
  - Compute the flag somewhere that guarantees document order: the presentation model or a template namespace.
  - The marker after the character line under the headline is not always present, so don't assume it is first.
- **Accessibility.** Keep `aria-label="Supporting evidence"`.
- **Colour.** The marker renders at full contrast in the accent colour, with no opacity.
- **Hit area.** At least 32×32 CSS px, using padding with a matching negative margin so nothing visibly moves.
- **Test.** Each rendered page has exactly one labelled marker, including a fixture where the character line has no refs.

### R6: Treasury data

**30Y**
- Parse `BC_30YEAR` alongside 2Y, 5Y and 10Y from the same entry.
- Emit `treasury-30y` and `treasury-30y-change` in the existing row shape.

**Year boundary**
- When the current-year feed yields fewer than two entries dated before today, also fetch the previous year's feed and merge by date, deduplicating.
- Test with January fixtures:
  - First session of the year: December's last entry is the level.
  - Second session: the change is measured against December.

**Formatting** (everywhere rates render: tables, module, proof lines, ledger, and prose placeholders)
- Yields: `5.18%` — two decimals, no "yield" suffix.
- Basis points: integers, e.g. `+7 bp`. Round at derivation.
- Classify on the same integers the reader sees.
- A value that rounds to zero keeps the existing unsigned neutral zero.

**Colour**
- Yield levels, yield changes, spread levels and spread changes all render in neutral ink everywhere.
- The sign carries direction and the curve move label carries meaning. Rising yields are neither good nor bad.

**Freshness**
- Judge freshness against weekdays, not NYSE sessions. The bond market closes on Columbus Day and Veterans Day while NYSE trades.
- The expected curve date is the previous weekday before the run date (ET).
- **Older than expected but no more than 5 calendar days old:** display normally; the caption says `latest official daily observation`.
- **More than 5 calendar days old:** treat as stale.
  - Show the levels with their date and a quiet stale note.
  - Suppress the spread changes, the curve move and the ghost curve.
- Include Mon Oct 12, 2026 as a fixture; it is the first live bond-holiday case.

### R7: Curve spreads

**Derived rows**
- `treasury-2s10s` = 10Y − 2Y.
- `treasury-5s30s` = 30Y − 5Y.
- `treasury-2s10s-change` and `treasury-5s30s-change` = the latest entry's spread minus the prior entry's spread.
- All values are integer bp.
- Each row is deterministic: its `input_ids` are its legs, and it records a formula version.

**Validity**
- A spread exists only when both legs come from the same entry.
- Its change exists only when the prior entry also has both legs.

**A spread level is a level**
- No plus sign.
- A minus sign when inverted.
- Neutral colour, everywhere it renders.

**Reader phrasing in the module**
- `2s10s  31 bp · 5 bp steeper`
- `5s30s  47 bp · 3 bp flatter`
- `unchanged` when the change is zero.
- On an inverted curve: `−35 bp · 5 bp steeper (less inverted)`.
- When the sign flips, add a note: `2s10s turned positive` or `2s10s inverted`.

**Terminology rules**
- Never "today", "wider" or "narrower".
- Steepening means the signed spread (long − short) rose, whatever the sign of the level. On an inverted curve, that means less inverted.
- The module caption carries the date once; the spread lines don't repeat it.

### R8: Curve move classification

**Where it lives:** a pure deterministic function (suggested: `src/market_brief/curve.py`), with no model call.

**Inputs**
- Δ2 and Δ10, in integer bp, from the latest entry versus the immediately prior entry.
- The 5s30s change, when available.
- Threshold `t` = `config/magnitude.json` bp SMALL (3). Read it from config; don't duplicate it.

**Definitions**
- Δs = Δ10 − Δ2.
- The **leading leg** is the one with the larger |Δ|.
- All threshold comparisons are inclusive (≥ t).

**Rules, applied in order:**

1. **Unavailable.** Any of these conditions → `Curve move unavailable`, plus the reason:
   - the 2Y or 10Y level is missing, or its prior is missing;
   - the legs come from different entries;
   - the observation is stale (R6).
   - Levels still render.
2. **Twist.** Δ2 and Δ10 have opposite signs and both |Δ| ≥ t → `Twist steepener` if Δs > 0, otherwise `Twist flattener`.
3. **Steepening or flattening.** |Δs| ≥ t → a steepener if Δs > 0, a flattener if Δs < 0.
   - If the leading leg's |Δ| ≥ t: `Bear …` if it rose, `Bull …` if it fell.
   - If the leading leg's |Δ| < t: `Slight steepening` or `Slight flattening`, with no bull/bear.
4. **Parallel or quiet.** |Δs| < t → `Parallel shift higher/lower` in the leading leg's direction if its |Δ| ≥ t; otherwise `Little changed`.
5. **Long end.** Only when 5s30s is available:
   - **Mixed curve move.** If 2s10s steepened or flattened (rule 2 or 3) and the 5s30s change is ≥ t in the opposite direction, the label becomes `Mixed curve move`. The sentence names both, e.g. `2s10s bear-steepened while 5s30s flattened.`
   - **Long-end note.** If 2s10s had no steepening/flattening outcome (rule 4) but 5s30s moved ≥ t, keep the label and add one sentence, e.g. `5s30s steepened 7 bp.`
   - If 5s30s is unavailable, classify from 2s10s alone and claim nothing about the long end.

**Reader copy** (one sentence per label):

| Label | Sentence |
|---|---|
| Bear steepener | Long-end yields rose more than the front end. |
| Bear flattener | Front-end yields rose more than the long end. |
| Bull steepener | Front-end yields fell more than the long end. |
| Bull flattener | Long-end yields fell more than the front end. |
| Twist steepener | The front end fell while the long end rose. |
| Twist flattener | The front end rose while the long end fell. |
| Parallel shift higher | Yields rose by similar amounts across the curve. |
| Parallel shift lower | Yields fell by similar amounts across the curve. |
| Slight steepening | The curve steepened slightly; neither end moved much. |
| Slight flattening | The curve flattened slightly; neither end moved much. |
| Little changed | Yields were little changed. |
| Mixed curve move | Names each pair's direction. |
| Curve move unavailable | States the reason: missing tenor, stale observation, or no prior entry. |

Use "more than", not "faster": this is a change between two observations, not a rate of change.

**Worked check:** the Sep 24 entry has 2Y +2 and 10Y +7. Δs = +5, which is ≥ 3. The leading leg is the 10Y at +7, and it rose → **Bear steepener**.

**Record**
- Store the result as a typed, deterministic packet record saved with the evidence record. It contains:
  - label and sentence;
  - pair;
  - inputs, by evidence ID;
  - threshold and rule version;
  - reason, when unavailable;
  - release notes from R10.
- It is not a numeric evidence row and is never citable. The analyst cites the spread rows.
- Prefer additive changes that need no artifact or contract version bump. If a bump is unavoidable, make it explicit and record why.

**Tests** — cover every row of the table, plus:
- each boundary at exactly t and at t − 1;
- lead-leg ties (|Δ2| = |Δ10|);
- inverted levels and zero crossings;
- missing 30Y, missing 2Y, no prior entry, stale observation;
- the mixed curve move and the long-end note.

### R9: Rates module

This module replaces the current "Treasury par yields" table in the Macro & rates section.

**Order inside the section:**

1. **Caption:** `U.S. Treasury par curve · Thu, Sep 24 · official daily observation`.
   - Never "close"; never a clock time.
   - Adjust per R6 when the curve is older or stale.
2. **Table:** Maturity | Yield | Daily change, with rows 2Y, 5Y, 10Y, 30Y. A missing tenor shows `no print`.
3. **Spread lines** (R7).
4. **Curve move:** a bold label followed by its sentence (R8).
5. **SVG** (spec below).
6. **Notes:** release-after-curve (R10), plus any stale or missing-data notes.
7. **The analyst's macro/rates paragraphs**, as the "why it matters".
   - Elsewhere, interpretation sits above the rows; this section deliberately puts it after, per the editorial rule.
   - Render nothing when the analyst wrote nothing.
8. **The existing proof disclosure**, extended to 30Y and the spread rows.

**SVG spec**

- **Rendering**
  - Inline `<svg>`, rendered server-side from precomputed numbers.
  - No JavaScript, no `<img>`, no external fonts.
  - Theme-aware via CSS variables.
- **Points and lines**
  - Plot only the four observed tenors, each marked with a small dot.
  - Draw straight segments between adjacent observed tenors. No smoothing or interpolation: a smooth curve would imply yields at maturities that were never observed.
  - A missing tenor breaks the line; nothing bridges the gap.
- **x-axis:** log-maturity spacing. The 2Y, 5Y, 10Y and 30Y sit at 0, .34, .59 and 1.0 of the plot width, inset so the dots aren't clipped.
- **y-axis**
  - Higher yields plot higher.
  - The domain covers the current and ghost points with padding.
  - It spans at least 100 bp, centred on the data, so a single-day move looks proportionate.
  - No gridlines and no y-axis labels; the values live in the table.
- **Ghost curve**
  - The prior entry — the same one the changes use — drawn dashed and fainter.
  - Drawn only when it has every tenor the current curve has.
  - The dash makes it distinguishable without colour, including in print.
- **Legend:** a tiny legend or direct labels, e.g. solid = Thu, Sep 24; dashed = Wed, Sep 23.
- **Tenor labels:** at least 11 CSS px at both 390 px and 1000 px widths. Use HTML labels if SVG text would scale below that.
- **Accessibility and sizing**
  - `vector-effect: non-scaling-stroke`.
  - `aria-hidden="true"` and `focusable="false"`; the table carries the values.
  - Height roughly 110–140 px.
  - No horizontal overflow at 390 px; check with the existing headless Chrome harness.
- **When not to draw:** when fewer than two adjacent tenors exist, or when the curve is stale.

### R10: Release-after-curve note

**Reference time:** 3:30 PM ET on the curve's observation date.

**BLS releases**
- A collected BLS event triggers the note when its `scheduled_at` is after the reference time and at or before the run's time. The note reads, for example: `Curve predates the 5:30 AM PT Consumer Price Index release.`
- Show up to two titles, then `and N more`.
- Show times in PT, like the rest of the page, and titles exactly as the calendar gives them.

**Fed**
- Include only FOMC policy statements from the Fed feed, using `published_at` against the same window.
- Match on the feed's title for those releases, and verify the pattern against a real feed item or an existing fixture.
- If you can't verify it, ship BLS-only and record that in Progress.

**Where it goes**
- It is deterministic.
- It renders in the module's notes.
- It goes into the curve record, so the analyst sees it.

### R11: Analyst contract (no new pass, no schema change)

**Schema**
- Do not change `market-brief.narrative.v2`, its fields, or its bounds. The provider's grammar envelope is tight.

**Allowed labels**
- Add `2s10s` and `5s30s` — exactly those forms — to `ALLOWED_LABELS` and to the prompt's bounded-label list.
- Test that both pass and that other digit strings still reject.

**Context**
- Add the 30Y rows and the spread rows as anchors, and the curve record as a small read-only object.
- Measure the serialized context size before and after, for both profiles, on the fixtures and on any real `analyst_context.json` available locally.
- If the largest light context would keep less than 1,500 bytes of headroom under 40,000:
  - leave the new rows and the curve record out of the light selection (rates don't change between premarket and the open);
  - record the decision and the numbers in Progress.

**Prompt** (`prompts/synthesis.md`): keep additions terse, because the prompt costs tokens on every call.
- **Bond convention**
  - Write "Treasuries sold off; yields rose" or "Treasuries rallied; yields fell".
  - Changes are in bp.
  - Use front end, long end, 2s10s, 5s30s, steepener/flattener, and bull/bear as bond-market terms.
  - When naming the curve's move, use the supplied curve move label exactly; never invent one.
  - Don't characterize the belly; nothing measures it.
- **Timing**
  - Treasury rows are the official daily par curve from the previous business day. They can frame the backdrop.
  - Never present them as the cause of, reaction to, or explanation for current-session prints, and never say yields are moving now.
  - When the curve predates a release, say the curve doesn't reflect it rather than inferring a reaction.
- **Language**
  - Words like "consistent with", "sensitive to" and "alongside" describe exposure or co-movement.
  - They don't license tying the prior-day curve to a same-session move.
- **Provenance:** prompt provenance stays the prompt hash.

### R12: "How to read this brief"

**Placement and form**
- One `<details>` near the bottom, above Sources & coverage, collapsed by default.
- Summary label: `How to read this brief`.
- Static, deterministic text with no model involvement.
- HTML only; omit it from the Markdown rendition.
- Each entry is one or two sentences. No tutorial copy anywhere in the main reading path.

**Entries, in order:**

1. **Today's curve move**, with its definition. Skip it when unavailable.
2. **2s10s:** "One of the most widely watched Treasury curve slopes, comparing the policy-sensitive shorter end with the longer-duration 10-year yield. Watching it rise and fall shows that part of the curve steepening or flattening."
3. **5s30s:** "The 30-year yield minus the 5-year yield. It shows what the long end is doing on its own."
4. **Bull and bear in bonds:**
   - Bull means prices up, yields down.
   - Bear means prices down, yields up.
   - Steepening means the signed spread rose; on an inverted curve, that means less inverted.
5. **The par curve:** "Treasury's official daily curve, fitted from indicative market quotes taken near 3:30 PM ET. It updates once a business day, not with the hourly price refreshes."
6. **The three clocks:**
   - Prices refresh hourly.
   - The analysis is written before the open and once after it.
   - The curve carries its own date.
7. **§ evidence:** tap § to see the observations behind a line. The full evidence ledger is in Sources & coverage.
8. **A nested `<details>`, "See all curve moves":** lists each R8 label with its sentence.

### R13: Docs, examples, state

**DECISIONS.md**
- Replace the "two clocks" bullet, which mandates the old phrases.
- Add bullets for:
  - rates semantics: T-1 par curve, no intraday yields, dated caption;
  - the classifier and its threshold source;
  - signed-spread steepening;
  - neutral rates colour;
  - overdue-page detection;
  - the Metals rename.

**Other docs**
- `PROJECT_STATE.md`: update Now/Next for this pass.
- `docs/BRIEF_SCHEMA.md` and `README.md`: update the clock strings they quote.

**Examples**
- Regenerate `examples/editions/*` the same way they were produced before; check git history.
- `test_reader_truth` asserts them.

---

## 4. Non-goals (considered and deferred)

**Deferred rates features**
- Plotting every Treasury maturity. The four tenors carry the structure this module is about.
- Move-magnitude percentiles and "large vs recent sessions" labels.
- Term premium, breakevens, real yields, SOFR, swap spreads, MOVE, auction data.

**Presentation**
- A large glossary; interactive charts.
- Renaming "Macro & rates"; adding an Energy section.
- Dark-palette changes beyond new shared tokens.

**Data and model**
- New data sources or vendors, or intraday yields.
- Any additional model call, or any narrative schema change.

**Infrastructure**
- Server-side LAST GOOD BRIEF logic.
- Scheduler, workflow, Worker or Pages changes.

---

## 5. Acceptance criteria

### Header
- [ ] Every page shows Prices / Analysis / Next (or the combined synthesis line) with truthful sources.
- [ ] LIVE is silent; SAMPLE and COMMISSIONING are loud; the edition label appears once at most.
- [ ] "What changed" on carried pages names the analysis clock as its end.

### Overdue script
- [ ] With the next-update time plus 15 minutes in the past, the page shows `Update due … has not published`.
- [ ] With it in the future, the page is unchanged.
- [ ] Verified in headless Chrome if available; the data attribute is always unit-tested.

### Typography
- [ ] h2 sizes, section spacing and the heavier rule are applied.
- [ ] "What changed" is a real section.
- [ ] The Metals rename is done.

### Palette
- [ ] Light palette passes the raised contrast gate, including text on notice.
- [ ] No text uses opacity dimming.
- [ ] Style hash re-pinned.

### § evidence
- [ ] Exactly one `§ evidence` label per page.
- [ ] Hit area ≥ 32×32 px with no visual shift.

### Treasury data
- [ ] 30Y is collected.
- [ ] January fixtures pass.
- [ ] The Oct 12 bond-holiday fixture shows the dated latest official observation.
- [ ] A stale curve suppresses the changes, the curve move and the ghost.

### Rates formatting
- [ ] Rates render as `5.18%` and integer bp everywhere, in neutral colour.

### Spreads and classifier
- [ ] Spreads and changes are correct and signed per R7, including inverted fixtures.
- [ ] The classifier passes the full rule table and edge cases.
- [ ] The Sep 24 fixture yields Bear steepener.

### Module and SVG
- [ ] The module renders in R9 order.
- [ ] The SVG meets the geometry spec.
- [ ] No overflow at 390 px.

### Release note
- [ ] A CPI-morning fixture shows the release-after-curve note.
- [ ] The FOMC path is either verified or explicitly deferred.

### Analyst contract
- [ ] `2s10s` and `5s30s` pass the digit rule.
- [ ] Prompt additions are present.
- [ ] Narrative schema is byte-identical.
- [ ] Context sizes are measured and reported, with the light-headroom decision recorded.

### Guide and docs
- [ ] The guide is collapsed by default, leads with today's move, and appears in HTML only.
- [ ] DECISIONS, PROJECT_STATE, BRIEF_SCHEMA and README are updated; examples regenerated.

### Checks
- [ ] `.venv/bin/python3 -m pytest -q`, `.venv/bin/ruff check src tests` and `git diff --check` all pass.

---

## 6. Verification

**Pages to render from fixtures:**
- premarket (synthesis);
- a carried hourly refresh;
- the close snapshot;
- Monday Oct 12 (bond holiday);
- a stale curve;
- an inverted curve with a twist;
- the first session of January;
- a CPI morning.

**Look at them.** If headless Chrome is available, screenshot each at 390 px and 1000 px, in light and dark, and inspect the images: hierarchy, SVG legibility, the § label, and the overdue state. Say in the report which ones you looked at.

**Report a context-size table:** bytes before and after, per profile, per context measured.

---

## 7. Final report

Reply with:

1. Changes by requirement ID, with commit hashes.
2. Tests added, and existing assertions updated (each named, with the reason).
3. The context-size table and the light-headroom decision.
4. Screenshots inspected.
5. Discrepancies between §2 and the code, and how you resolved each.
6. Deferred ideas, not built.
7. Anything the owner must decide.

---

## Progress

_Claude Code maintains this section: the slice plan, ticked slices, discrepancies, measurements._

### Slice plan

Baseline at 14670b0: 474 passed, ruff clean. Tests-first for S1–S2 (new behaviour).

- [x] **S1 · R6 data.** `collect.py`: parse `BC_30YEAR` → `treasury-30y`/`-change`; merge the previous-year feed when the
  current year has fewer than two entries before today (dedupe by date); bp changes rounded to integers at derivation.
  Tests: `tests/test_curve.py` (30Y rows, January first/second session, merge dedupe, prior-year failure).
- [x] **S2 · R6 freshness, R7 spreads, R8 classifier, R10 notes.** New `src/market_brief/curve.py` (pure functions):
  spread rows `treasury-2s10s`/`-5s30s` (+`-change`) with `input_ids`/`formula_version`, weekday freshness, the ordered
  rule table, long-end note / mixed move, release-after-curve notes (BLS + FOMC title pattern if verifiable), and the typed
  `packet["curve"]` record; called from `metrics.derive` so every packet path gets it; `evidence.WINDOWS` for the new
  metrics. Tests: `tests/test_curve.py` (every label, t and t−1 boundaries, ties, inverted/zero-crossing, missing 30Y/2Y,
  no prior, stale, mixed, long-end note, Sep 24 Bear steepener, Oct 12/13 bond holiday, CPI morning, FOMC).
- [ ] **S3 · R6 formatting and colour.** `render.formatted`/`direction`: `5.18%`, integer bp, unsigned spread levels,
  neutral colour for every rates row. Update the pinned `-4.00 bp` / `3.86 % yield` / `0.00 bp` assertions.
- [x] **S4 · R9 module, R7 reader phrasing, SVG.** `render.py` rates view model (caption per freshness, four-tenor table,
  spread lines, curve move, notes, inline SVG with ghost), template + Markdown; proof extended to 30Y and spreads.
  Tests: `tests/test_rates_module.py` (order, phrasing incl. inverted/flip, stale suppression, SVG geometry).
- [x] **S5 · R1 header, R2 overdue.** Masthead date, Prices/Analysis/Next block (combined synthesis line), non-LIVE
  status line only, "What changed … through the … analysis", Markdown clock lines; `data-next-at` + grace constant +
  IIFE check. Update pinned header strings (`test_cadence`, `test_render`, `test_reader_truth`).
- [ ] **S6 · R3 typography, R4 palette, R5 § evidence.** CSS sizes/spacing/`--rule-strong`, "What changed" section,
  Metals rename, light palette, no text opacity, first-marker label via template namespace, 32×32 hit area. Raise faint
  floor, assert text-on-notice, re-pin `STYLE_SHA256`.
- [ ] **S7 · R12 guide.** Collapsed `How to read this brief` details above Sources & coverage; HTML only.
- [ ] **S8 · R11 analyst contract.** `ALLOWED_LABELS` + prompt label list; prompt bond/timing/language rules; 30Y and
  spread rows as context anchors plus the compact curve record; measure fixture and archived production contexts
  before/after; decide light headroom; prove the narrative schema byte-identical.
- [ ] **S9 · R13.** DECISIONS, PROJECT_STATE, BRIEF_SCHEMA, README; regenerate `examples/editions/*` with the PR #33
  replay recipe.
- [ ] **S10 · Verification.** Render the eight §6 pages; headless-Chrome screenshots at 390/1000 × light/dark; overdue
  state, § hit area, no 390 px overflow; independent review of the whole diff against this file.

### Discrepancies (§2 vs code)

- **Base.** 14670b0 is `origin/main` ("Publish HOURLY_1400 brief"); the local `main` was at 30d4846. The difference is
  `publish/index.html` only, so the branch starts at 14670b0 as specified.
- **Examples predate PR #35.** The PR #33 replay recipe reproduces every committed example byte-for-byte except the
  light-palette CSS line: `examples/editions/*.html` still carry the pre-#35 light tokens. S9 regeneration resolves it.
- **Oct 12 is not itself the stale-looking day.** On Mon Oct 12 the expected curve (the previous weekday) is Fri Oct 9,
  which Treasury did publish, so Monday's pages read "official daily observation". The holiday shows on Tue Oct 13, whose
  expected curve (Mon Oct 12) never exists: Tuesday reads Friday's curve as "latest official daily observation". Both
  days are fixtures (`tests/test_curve.py`, synthetic October entries). The same rule makes Mon Jan 4, 2027 expect
  Fri Jan 1 (a weekday), so Dec 31 reads as the latest official observation.
- **Year of the Treasury request.** `field_tdr_date_value` now uses the ET date's year (the same clock that decides
  "before today"), not the UTC year; the two differ only between 7 PM ET and midnight on December 31.
- **The prior entry's date** is only named in a change row's baseline (`daily observation YYYY-MM-DD`, the collector's
  form). Spread changes and the curve record read it from there; the fictional replay fixture's baseline names no date,
  so its prior date is unknown and nothing claims one.
- **Release window inputs.** Collected calendar events cover only today and the next session, so a release on an
  earlier day that a stale-by-holiday curve also predates (for example Monday releases seen on Tuesday) cannot be named.
  Every collected calendar event counts, not only BLS: the calendar is the only automated one; sourced calendar input
  (BEA/Fed calendar) is the same kind of scheduled release.

### Measurements and verifications

- **FOMC title verified.** The Fed press feed item "Federal Reserve issues FOMC statement" (published 2026-09-16 18:00Z)
  is in the archived production evidence of 2026-09-17/18; the sibling item "…release economic projections from the
  September 15-16 FOMC meeting" must not match. Pattern `\bFOMC statement\b`; the FOMC path ships.
- **Independent verification of S1–S2** (background workflow, 9 agents): a blind oracle written from the R8 text alone
  agreed with `curve.classify` on all 65,000 grid cases (Δ2, Δ10 in −12…12, 5s30s None or −12…12, t in 1, 2, 3, 5) and
  184,512 more at fractional thresholds, 0 disagreements. Code review found two real low-severity defects, each confirmed
  by two refuters and fixed with regression tests: a Treasury-only close was recorded as `EARLIER_HISTORY_ONLY` (spread
  rows counted as price history in `continuity.closing_data`), and a curve older than the seven-day admission window read
  as "no 2Y or 10Y yield" instead of stale. A third finding (spread rows reach the analyst before S8 allows `2s10s` in
  prose) was refuted as slice ordering on an undeployed branch; S8 closes it and nothing merges before it.
- **Real curve.** Treasury's 2026 feed (fetched 2026-09-25) gives Sep 24: 2Y 4.87 / 5Y 5.03 / 10Y 5.18 / 30Y 5.47 (+2 / +4 /
  +7 / +7 bp vs Sep 23): 2s10s 31 bp, 5 bp steeper; 5s30s 44 bp, 3 bp steeper → Bear steepener. Real Dec 2025 / Jan 2026
  entries back the January fixtures (`tests/fixtures/treasury.*.xml`, trimmed from Treasury's own XML).

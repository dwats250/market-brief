# Week Ahead (Sunday) prototype

A standalone Sunday edition for Oct 12–16, 2026, built from a one-time evidence snapshot.

- **Not published.** Nothing here is wired into the scheduler, the workflow, `config/editions.json`, continuity or `publish/`.
- **No paid model call.** The editorial was written in a working session (Opus 5.5) and is labelled as such on the page.

```text
experiments/sunday/
  build.py              deterministic build: snapshot + editorial → index.html, evidence.json
  sunday.html.j2        page template; reuses the production stylesheet read from templates/brief.html.j2
  2026-10-11/
    inputs/             one-time CSVs as read 2026-10-11/12, kept separate from canonical observations
    facts.json          hand-entered sourced facts: calendar, consensus, secondary prices, lab, hypotheses
    edition.json        the editorial; figures only through {evidence-id} placeholders
    evidence.json       generated: every row with source, observation time and kind
    index.html          generated: the edition
```

Rebuild: `.venv/bin/python3 experiments/sunday/build.py experiments/sunday/2026-10-11`. Test: `tests/test_sunday_prototype.py`.

## What it reuses

| Production piece | Use here |
|---|---|
| `templates/brief.html.j2` stylesheet | Read at build time; identical tokens, type, tables, drawers, ledger and themes |
| `collect.yield_rows` | Treasury rows for Oct 9, with the change measured against Oct 2 (week start) |
| `curve.spread_rows`, `curve.classify` | 2s10s and 5s30s with their week changes, and the named curve move |
| `render.curve_chart` | The nominal curve, with Oct 2 dashed |
| Evidence-placeholder rule | No figure in prose without a citable row; unknown IDs fail the build |
| Watch component (Confirm / Changes it) | "What to watch" |

**New and small:**
- the real (TIPS) par curve and par-curve breakevens;
- a since-Sep-1 chart of the real yield and the breakeven, on one bp axis;
- calendar, spotlight, lab and scenario blocks;
- about 20 lines of CSS.

## Evidence kinds (shown in the ledger)

| Kind | Meaning |
|---|---|
| `official` | Treasury, BLS, DOL, UMich, Fiscal Data, Fed |
| `close` | Daily closes (stockanalysis; FRED for the VIX) |
| `settle` | Futures settles, from a dated headline in the lab file |
| `last_quote` | Friday's last FX quote, not a close |
| `secondary` | Macroglide, Trading Economics, Investrade, Tapeboard |
| `consensus` | Aggregator forecasts, labelled FF or TE |
| `lab`, `hypothesis` | market-review lab files and `hypotheses.md` |
| `derived` | Computed here from the rows above |

**Not collected:**
- futures and FX after the Sunday reopen: no page exposed a timestamped print;
- weekend news (Reuters, AP and NHC were not read);
- BoJ, ECB and BoE meeting dates;
- credit spreads beyond HYG and LQD.

## Scheduling: the smallest change for a recurring edition (not made)

1. **Schedule.** Add `WEEK_AHEAD` to `schedule.CHECKPOINT_KINDS` as a synthesis checkpoint. It runs Sundays at **23:00 UTC**, which is 4:00 PM Vancouver all year, since BC stays on UTC−7.
   - It applies when the date is a Sunday. It still runs before a Monday holiday, and the page names the next session.
   - `cli.scheduled` gates on `info["applicable"]`, not `trading_day`. Weekday behaviour is identical.
2. **Wake.** Add a Cloudflare cron `"1 23 * * SUN"`. The existing `resolve-scheduled` stays the only authority.
3. **Budget.** A `weekly` profile in `config/editions.json`: 128,000 B input and 14,000 output tokens (twice `rich`), mapped only to `WEEK_AHEAD`. Weekday profiles are unchanged.
   - R2 attempt records key on `<date>-WEEK_AHEAD`, so the two-a-day invariant is untouched: Sunday is not a trading day.
   - Add a test that `WEEK_AHEAD` never resolves on a weekday.
4. **Isolation.** No continuity restore or advance, as the approved Week Ahead plan already decided.
5. **Futures.** CME reopens at 6:00 PM ET, which is 3:00 PM PT in daylight time and 4:00 PM PT from Nov 1. At 23:00 UTC the edition is one hour after the open until Nov 1, then lands on it.
   - Nothing in the pipeline reads futures today, so publication does not wait for them, and the page says "not collected".
   - If a futures source is ever admitted, move the wake to 23:30 UTC. Never substitute Friday's close.

**Decision needed (not made here):** merging the schedule change activates a paid weekly call. The owner charges the first weekend experiment.

## What works now vs the wishlist

| | Now (this prototype) | Still wishlist |
|---|---|---|
| Nominal curve, spreads, move label | From the production code paths | Already in the weekday brief |
| Real yields, breakevens | Built from Treasury's real par curve (one-time input) | Collector fetch in production (R1) |
| Curve history | Week-start ghost; since-Sep-1 decomposition | Production uses 2 of the ~190 entries it already downloads |
| Auction tiles | Last week's results vs September, as a table | Graphic; needs an official comparable source. The tail needs a when-issued yield, which isn't published officially. No coupon auctions this week |
| Event rates reaction | Not attempted | Needs intraday yields; none admitted |
| Calendar | Verified by hand, consensus labelled | BLS automated; Census and the Fed calendar page are next; earnings need owner input |
| Futures and FX after the reopen | Not collected | Needs an admitted, timestamped source |
| Cross-market spotlights | Two, chosen from the evidence; Japan and FX considered and not featured | Selection rule for the analyst |
| Lab | Three hypotheses with status, measure and source | Read `hypotheses.md` as a dated context item; scoring stays in market-review |
| Model delegation | None needed: preparation is deterministic code | Not recommended |

## Recommended next slice

**R1, weekday brief, no model change.** In `collect.py` and `curve.py`:
- fetch Treasury's real par curve (same host);
- derive 5Y and 10Y par-curve breakevens;
- keep the week-start entry from the year's feed the collector already downloads.

Render a real/breakeven line under the existing curve table, and the week-start ghost.

It pays off on Thursday Oct 15, when Wednesday's curve shows whether the CPI move was real yield or breakeven. This prototype's derivations and test fixtures carry over directly.

**After R1:**
- **S1:** the deterministic `WEEK_AHEAD` checkpoint that renders this page from the collector, with no model.
- **S2:** the weekly profile and the paid experiment.

PR #50 (the evidence cutoff fix) should merge before either.

# Planning packet (revision 2, after Codex Astra's review): Market Memory, Week Ahead, market clocks, Opus 5.5

Status: **Approved by the owner, 2026-09-27** (revision 2 plus the source-lineage amendment). Base: `main` @ 282726a.
Authority: this file is the plan of record for these slices; where code and this file differ, record the discrepancy in Progress rather than forcing the plan.

## Context

The owner uses Market Brief during sessions to understand market mechanics, test whether the brief is useful, keep point-in-time state that memory would lose, and watch setups develop before they trigger. This packet adds small complements, not a new project:

- **A. A daily session record.** Deterministic, zero model calls, kept in the close run's artifact.
- **B. A Sunday Week Ahead.** One weekly synthesis over the week's **archived, citable** facts. It is isolated from weekday continuity.
- **C. A collapsed "Market clocks & data" disclosure.** Labels say exactly what each clock measures.
- **D. Opus 5.5 as the configured primary analyst.** The fallback is unchanged.

Durable or public storage is **deferred** until 20–30 live sessions show what is worth keeping.

**Invariants:**
- Weekday operation stays at exactly **two** analyst calls.
- The CLOSE record adds **zero** calls.
- Sunday adds exactly **one** call per week.
- Fail-closed validation is preserved.
- SAMPLE/experiment isolation is preserved.
- Monday's PREMARKET admits Friday's CLOSE_1M exactly as it does today.

**How the facts were checked:**
- Read-only against the code, real production artifacts (the 09-21..09-25 close runs with their same-run bundles, and the manual 09-26 01:33Z close) and OpenRouter's public API.
- Four internal adversarial critics.
- Codex Astra (gpt-6-astra, high reasoning, read-only).
- Re-verified for this revision: the artifacts API lists newest first; source records need a plain-HTTPS `url` (`evidence.source_record`); derived rows are citable through `evidence_catalog`/`model_packet` when their `source_id` is an `llm_allowed` source; placeholder IDs match `[a-zA-Z][\w-]*` (`evidence.py:23`).

---

## 1. Current reuse map

| Need | Existing machinery | How it is used |
|---|---|---|
| Daily record | The accepted CLOSE_1M run already holds, in memory and in its folder: the validated `packet` (21 IEX near-close prints, D−1 bar-derived rows, `sector_leadership`, Treasury rows, `curve`), `session_handoff`, the admitted `interpretation` local (`cli.py:242`), and the bundle's `premarket` slot | A pure projection written as `session_memory.json` **in the close run folder**, uploaded by the existing always-run archive step (`schedule.yml:173-183`, 30 days). No new storage. |
| Session-D facts | A scheduled close (20:01Z) has only last-trade prints for D. Bars, `sector_leadership` and the curve are D−1 (`collect.py:246-247`, `:98`). A post-midnight close has the settled `-daily` bar and zero prints. | Each value is stored with its own observation date or time and a `basis`. |
| Leadership / relative strength | `packet["sector_leadership"]` (top/bottom 3 by 20-session spread vs SPY), `QQQ-spread20` | Store that selection as is, with its observation date. |
| Rates / curve | `treasury-{2,5,10,30}y(-change)`, `treasury-2s10s/5s30s(-change)`, `packet["curve"]` (move label, `curve-move.v1`) | Store as is, dated by the curve's `observed_at`. |
| Radar | The watch contract (condition, confirmation, contradiction, horizon, code-owned IDs, frozen `values`, append-only analyst assessments, ≤3 carried), multi-day relationships, deterministic attention triggers | **No new radar schema.** The record keeps each watch with its frozen facts and verdicts, so hypotheses and outcomes survive the day. |
| Citable evidence | `packet["derived"]` rows are cataloged and placeholder-resolvable when their source is `llm_allowed` (`evidence.py:331-374`). The validator's allowed set is `evidence_catalog ∩ supplied_ids`. | Sunday admits archived observations as **dated derived rows** that keep their original source lineage. **No validator change.** |
| Artifact restore | `cli.restore_continuity`: `_gh_json` listing, `select_artifact` checks (branch main, workflow `schedule.yml`), `gh run download`, and "never fails the job; explicit cold start" | Reused in a bounded `memory-restore` command for Sunday. |
| Events | `collect.calendar_events` and `normalize_packet` admit {today, next session} only (`collect.py:140`, `evidence.py:215-226`). BLS returns 403 on every run (issue #23). | The same path gets a bounded next-week window on WEEK_AHEAD. No new pipeline. |
| Scheduling | `CHECKPOINT_KINDS`, `checkpoint_session`, `next_checkpoint`, `TOLERANCE_MINUTES`, `cli.scheduled` idempotency. The Worker only dispatches. | One Sunday checkpoint with explicit gates. |
| Clocks | The header's Prices/Analysis/Next, `pacific_time`/`short_date`/`compact_clock`/`next_update`, Technical details, row `observed_at`/`retrieved_at`, `curve.observed_at`, `history.dates[-1]` | Reused. Labels say exactly what each value is. |
| Synthesis | The OpenRouter transport, Anthropic-only pin, fallback, provenance, and `narrative.v2` (5,197 B, inside the 5,295 B accepted envelope) | Reused unchanged. The weekly edition adds one profile entry whose wire schema is byte-identical. |
| Tests | `test_cadence.Day`, `freeze_clock`, the injected `gh` runner pattern (`test_pipeline.py:370-397`), and the conftest `urlopen` block | These prove the call-count and archive invariants. |

---

## 2. Smallest proposed architecture

```
weekday CLOSE_1M (0 calls) ─► runs/<utc>/live-close_1m-…/session_memory.json ─► run artifact (30 days)
                                                   │ bounded restore (existing gh machinery)
Sunday WEEK_AHEAD (1 call; isolated: no bundle restore, admit, or advance)
   factual input = archived session records → dated, CITABLE derived rows (original source lineage kept)
                 + one standard current collection → settled/current SUPPLEMENT ({SYM}-week, Friday-or-last-session rows, curve)
   context note  = the week's hypotheses and verdicts, labeled INTERPRETATION, not evidence
   output        = publish/index.html (Week Ahead page) + run artifact (narrative, metadata.week provenance)
Monday PREMARKET: unchanged (admits Friday CLOSE_1M's handoff)
```

**New concepts (the whole budget):**
- **`src/market_brief/memory.py`:** `session_memory()` (the pure projection), `select_records()` (restore selection), `admit_archived(packet, records)` (archived rows), and `week_note(records)` (the labeled hypotheses).
- **Schema:** `market-brief.memory.v1`, one kind, `session_memory`.
- **Checkpoint:** `WEEK_AHEAD`, kind `synthesis`.
- **Profile:** `weekly` in `editions.json`, with rich budgets and a `context: "week"` selection.
- **Metrics:** `"week return"` (fresh supplement) and `"session return"` (archived role).
- **Source listing:** one `session-memory` entry in Sources naming the restore channel; no row is attributed to it.
- **CLI:** the `memory-restore` command.

**Not built:**
- Durable or public storage (a later owner decision).
- A weekly record kind, `previous_week`, recursive weekly context, a weekly NEXT_BRIEF branch, or formal cross-week radar evaluation.
- A regime field, radar taxonomy or scoring.
- A news or calendar pipeline, or a second model or router.
- Notes or journaling machinery.
- A close synthesis.

---

## 3. Slices

**Order:** D1 → C1 (any time) → A1 → (at least one full week of A1 records in artifacts) → B. Each slice has its own PRD Progress section and goes tests first, then the full suite, `ruff`, and `git diff --check`.

| Slice | What it changes | Calls |
|---|---|---|
| **D1** Opus 5.5 plus the experiment workflow fix | config, test pins, docs, one workflow branch | none added. Gate: 1 paid experiment (rich); the light gate is an owner decision. |
| **C1** Market clocks & data | renderer only | none |
| **A1** Session record at CLOSE_1M | deterministic, one hook | **0** |
| **B** Week Ahead (one PR, commits B1–B4; the SUN cron is in the PR, so **merge = activation**) | scheduler, collect event window, memory restore/admission, context selection, renderer, workflow | **+1 per week** |

### D1: model, and an experiment path that honors the checkpoint

- **`schedule.yml` manual step.** When `experiment` is true and `commissioning` is false, run `premarket --checkpoint "$checkpoint" --experiment`. The legacy `--commissioning --experiment` path runs only when both are true.
  - The "Resolve checkpoint" step already outputs the selected input.
  - Metadata must show `checkpoint == PREMARKET` (or `WEEK_AHEAD`) and `experiment == true`.
- **Model change.** Only `analyst.model` → `anthropic/claude-opus-5.5` (§7). The fallback is unchanged.
- **Gate.** Dispatch on the PR branch before merge: `gh workflow run schedule.yml --ref <branch> -f checkpoint=PREMARKET -f experiment=true` (≈ $0.23). It proves three things:
  - Opus 5.5 accepts the 5,197 B schema;
  - no `finish_reason=length`;
  - the provenance fields are filled.
- **Light profile.** A rich call does not prove the light one (owner decision 2).
- **No automatic budget change.** Truncation or rejection is diagnosed first, and budgets move only by owner ruling.

### C1: see §8

### A1: `session_memory.json` at an accepted production CLOSE_1M

**Gate.** Runs only when:
- `kind == "close"`;
- a handoff was written;
- the run is production (`LIVE`, not experiment, not commissioning — the bundle's own gate).

Replay, SAMPLE, commissioning and experiment runs never write a record. Records live only under `RUN_ROOT/runs/…`, which is gitignored and patched in tests, so no flag is needed.

**Order in `cli.run`:**
1. `state`
2. `handoff`
3. **derive the record** (pure, in memory)
4. render/publish
5. **write `session_memory.json`**
6. `validation = PASS`
7. bundle advance

**Failure handling:**
- **Observed part:** projected only from the already-validated packet. Missing rows become `null` plus a named gap. It can raise only on a code defect, and then the close **fails closed** (no PASS, no advance; house rule). Unexpected exceptions are converted to `ValueError` so the metadata stays truthful.
- **Optional parts** (interpretation, premarket verdicts, watches, relationships, frozen facts) are each guarded. A failure becomes a named entry in `gaps`, and **never** prevents the observed-only record or the close.

**Recovery.** The derived bytes sit in the run folder that the always-run archive uploads, so a later publish or push failure cannot lose them. Nothing is recollected or re-synthesized.

### B1: scheduler and isolation

**Checkpoint:**
- `CHECKPOINTS += "WEEK_AHEAD"`, kind `synthesis`, with a `CHECKPOINT_TITLES` entry.
- `checkpoint_session` branch:
  - `scheduled` = the ET Sunday on or after `now`, at **16:00 ET** (CME Globex reopen minus 2 h);
  - `applicable` = today's ET date is that Sunday;
  - `session_date` = that Sunday (the page marker key).
- A year-long sweep test shows the branch never raises, because weekday `resolve-scheduled` iterates it.

**Scheduler and dates:**
- `cli.scheduled` gates on `info["applicable"]` instead of `trading_day`. That is identical for weekday checkpoints.
- **Allowed dates**, enforced in `cli.run`:
  - production WEEK_AHEAD only on an ET Sunday;
  - experiments on an ET Saturday or Sunday;
  - refused on trading days and weekday holidays.
- **The completed week** = XNYS sessions in [Monday of the run's ET week, run date). Holiday weeks end wherever the calendar says (Good Friday: Thursday).

**Isolation:**
- For WEEK_AHEAD, `cli.run` does not load or admit the bundle. The prior is an explicit cold start with the reason "week ahead is isolated from session continuity".
- No `compare_all`, no `advance_bundle`, no `latest-success` pointer.
- `schedule.yml` skips `continuity-restore` and the continuity upload when the checkpoint is WEEK_AHEAD.
- Idempotency: the existing page marker plus the checkpoint marker. `completed_in_bundle` never sees WEEK_AHEAD.

**Next clock:**
- `next_checkpoint` adds the upcoming WEEK_AHEAD as a candidate **only when `kinds is None`**. So `next_synthesis` still points Friday's NEXT_BRIEF watches at Monday.
- `render.next_update`'s cross-date branch labels it "week ahead".
- **Regression tests** (Astra's missing pin, `test_cadence.py:164-166`):
  - Friday's close page reads "Next · Sun … · 1:00 PM PT · week ahead";
  - the Thursday before Good Friday reads the same;
  - the Wednesday before Thanksgiving still reads Friday's premarket;
  - the Sunday page reads Monday's premarket (Tuesday after a Monday holiday).
- **Pin updates:** `SYNTHESIS_CHECKPOINTS` and the editions set (`test_cadence.py:144-151`); `cadence_note`; the workflow choice list; the test_pipeline workflow-text pins; one separate SUN-cron assertion in `test_cloudflare_scheduler` (the weekday `CRONS` tuple is unchanged).

### B2: archived facts in, current collection as supplement

**`memory-restore`.** A new CLI command beside `continuity-restore`; the workflow step runs only for WEEK_AHEAD.
- **Listing:**
  - `_gh_json("repos/{repo}/actions/artifacts?per_page=100&page=N")`, newest first;
  - stop when `created_at` precedes the week's first session, or after 5 pages;
  - keep names starting `market-brief-run-CLOSE_1M-` that are unexpired and whose `workflow_run.head_branch` is `main`;
  - run lookup requires `path == schedule.yml`;
  - at most 2 candidates per week session.
- **Download:** `gh run download <run> -n <name>` into `runs/week/<run_id>/`.
- **Acceptance.** Each `session_memory.json` must pass:
  - the record itself: schema, kind, `content_hash`, `source.mode` equal to the run's mode, both flags false, `session.date` in the completed week;
  - the **sibling `metadata.json`** in the same folder: `validation == "PASS"`, `continuity.advanced == true`, checkpoint `CLOSE_1M`.
- **Selection.** One record per session, the **earliest** accepted (the contemporaneous record). Others are listed as ignored.
- **Failure handling.** It never fails the job. Missing sessions are named. Replay reads `tests/fixtures/memory/` and never calls `gh`.

**Archived rows.** After `normalize_packet`, `memory.admit_archived` appends each record's observations to `packet["derived"]` as **dated rows**. This happens before `derive`/`annotate_magnitude`/`finalize_coverage` and before `evidence.json` is written, so it all lands in the hashed packet.

**Source lineage (owner amendment, 2026-09-27).** Every archived row keeps its **original** attribution; the archive is only the carrier:
- `source_id` stays the original source (e.g. `alpaca-iex`, `alpaca-daily`, `treasury`);
- `observed_at` and `retrieved_at` stay the originals;
- derived-origin rows also keep their `formula_version` and `input_ids`;
- a `lineage` block records `{original_id, session_date, run_id, record_hash, restored_from: {artifact_id, workflow_run_id}}`.

The analyst therefore sees the original `source_id`, and the evidence ledger shows the original source with "session record <date>". If a referenced source is missing from Sunday's source list, the record's archived source descriptor (same id, original URL, flagged `archived: true`) is admitted; otherwise the current record for that same source is used. The restore channel appears once in Sources as "Session records · N of M sessions · restored from run artifacts", and **no row is attributed to it**.

| Record part | Row ID | Metric | Dating |
|---|---|---|---|
| Returns | `{SYM}-session-YYYYMMDD` | `"session return"` | baseline states basis `iex_last_trade` or `iex_daily_bar` |
| Leadership | `{SYM}-spread20-YYYYMMDD` | as recorded | by observed date (D−1) |
| Rates | `treasury-10y-YYYYMMDD` etc. | as recorded | by observed date (D−1). The week's records hold the curve path from the prior Friday through Thursday; Sunday's collection holds Friday's. |
| Frozen watch/relationship facts | `<original-id>-<YYYYMMDD or YYYYMMDDTHHMM>` | as recorded | identical observations dedupe; a conflict becomes a gap |

- Every archived row has status `BACKGROUND`, its **original `source_id`**, its original `observed_at`, and the `lineage` block. So each row is **citable, placeholder-resolvable, dated and attributable**, with **no validator change**.
- Renderer tables and the rates module select rows by current ID, so archived rows appear only in citations and the evidence ledger (a test pins this).

**Minimum evidence.**
- **Zero** valid archived records: the run fails closed before the call. No page is published, and Friday's page shows its honest overdue line.
- Otherwise it proceeds. Missing sessions become coverage limitations stated on the page. Grounding ensures every claim cites a supplied dated row.

**Current supplement (the one standard collection).**
- `metrics.derive` emits `{SYM}-week` rows ("week return": last settled close over the last close before the week's first session) on WEEK_AHEAD only, plus the usual settled daily rows and the current curve.
- The collector is given the checkpoint so events use the **next-week window**: XNYS sessions of the following week, keeping the 24 h `checked_at` rule, with relation `NEXT WEEK`. The collector's three test stubs are updated.
- It adds or replaces **no** Mon–Thu facts; those come only from records.

**Context.**
- **Profile `weekly`:** rich budgets (64,000 / 7,000 / low effort / 2 summary paragraphs / 3 attention items / 3 watches) with `context: "week"`.
  - The selection **keeps all archived rows, all `-week` rows, current anchor topics, the leadership rows, curve inputs and trigger evidence**.
  - Other background is omitted and named, the same selection mechanism the light edition uses.
- **`week_note`:** per session, `closing_data`, `read {checkpoint, label, character}`, watches `{id, from, hypothesis, confirmation, contradiction, facts:[archived IDs], verdicts, end}`, `judged`, relationships `{id, statement, facts, statuses}`, and `missing`.
  - Labeled INTERPRETATION, "earlier hypotheses, not evidence".
  - Old placeholders are rewritten to the archived frozen-fact IDs, or "(value unavailable)" with a gap.
  - It carries **no** per-day headline or take.
- **Guidance** (in `editions.json`):
  - tell the story that survived, organized by hypothesis, not by day;
  - cite dated rows;
  - use SESSION/NEXT_CLOSE/EVENT horizons;
  - no trade language.
- **Measured before any paid call.** Rebuild the Sunday context offline from the real 09-21..09-25 close artifacts. The estimate is ≈ 25 KB archived rows + 3 KB week rows + ≈ 14 KB selection and note ≈ 42 KB, against a context-only budget of 58,786 B. Record the table in Progress.

### B3: acceptance and weekly retention

- The standard synthesis path: validate, `edition_state` and `narrative` written to the run folder, render, publish.
- **No bundle advance and no handoff.**
- The weekly "lossy layer" is the run artifact itself: `narrative.json` plus `metadata.week = {sessions, records: {date: {run_id, content_hash}}, missing, ignored}`. There is no new record kind.

### B4: Week Ahead page

Same template:
- **Labels:** `EDITION_LABELS` / `EDITION_WORDS` "Week ahead".
- **Tables:** the change column and figures use `{SYM}-week`.
- **What the week taught us:** renders the week's relationships.
- **Carry forward:** watches and attention, with the caption "Conditions to watch next week; the daily briefs do not carry them forward".
- **Next week:** from XNYS (sessions, holidays, early closes) plus admitted events. If the calendar source is unavailable, the page says so from its source status.

**Closed-market wording is derived from dates, never assumed:**
- "Markets closed since <day of the latest settled close>" replaces the "current prints unavailable" domain on WEEK_AHEAD.
- Prices reads "prior close <date>" as today.
- A Good Friday week reads Thursday.

"What changed" hides itself (no anchors).

### B activation

1. After at least one full week of A1 records exists, run one paid WEEK_AHEAD experiment on a Saturday or Sunday from the PR branch: `-f checkpoint=WEEK_AHEAD -f experiment=true`.
2. Confirm from `metadata.json`: `checkpoint == WEEK_AHEAD`, `experiment == true`, the archived record hashes, the context bytes, usage and `finish_reason`.
3. Merge. The PR's `"1 20-21 * * SUN"` cron deploys through `cloudflare-scheduler.yml`.
4. Verify with the schedules GET and the invocation GraphQL.

---

## 4. Data contract: `session_memory.json`

Values below are real, from the 09-25 20:01 close (pre-#36 code, so its curve fields are null). Projected size is ≈ 8–10 KB. There is **no hard size rejection**: optional lists are bounded by the synthesis caps, and a regression test holds the real-week fixtures under ~16 KB.

```json
{
  "schema_version": "market-brief.memory.v1", "kind": "session_memory",
  "session": {"date": "2026-09-25", "open": "2026-09-25T13:30:00Z", "close": "2026-09-25T20:00:00Z"},
  "source": {"mode": "LIVE", "commissioning": false, "experiment": false, "checkpoint": "CLOSE_1M",
             "run_id": "live-close_1m-200158-faacf708", "target_time": "2026-09-25T20:01:58Z",
             "code_revision": "d6b00d8", "evidence_hash": "ac4d0fef…", "handoff_hash": "27563eef…",
             "closing_data": "PROVISIONAL_NEAR_CLOSE"},
  "sources": {"alpaca-iex": {"name": "…", "kind": "quote", "url": "https://…", "provider": "alpaca", "feed": "iex"},
              "alpaca-daily": {"…": "…"}, "treasury": {"…": "…"}},
  "observed": {
    "returns": {"SPY":  {"value": 0.51, "unit": "%", "basis": "iex_last_trade", "observed_at": "2026-09-25T20:00:07Z", "status": "DELAYED",
                         "id": "SPY-intraday", "source_id": "alpaca-iex", "retrieved_at": "2026-09-25T20:01:5…Z"},
                "MSFT": {"value": 3.73, "unit": "%", "basis": "iex_last_trade", "observed_at": "2026-09-25T19:59:55Z", "status": "DELAYED",
                         "id": "MSFT-intraday", "source_id": "alpaca-iex", "retrieved_at": "…"},
                "…": "21 symbols; basis iex_daily_bar with a date when the run holds D's settled bar"},
    "leadership": {"observed": "2026-09-24", "benchmark": "SPY", "window": "20 sessions",
                   "top": [{"topic": "XLK", "value": 6.29, "unit": "pp"}, {"topic": "XLC", "value": 1.03}, {"topic": "XLE", "value": 0.11}],
                   "bottom": [{"topic": "XLU", "value": -9.65, "unit": "pp"}, {"topic": "XLRE", "value": -7.78}, {"topic": "XLB", "value": -7.56}],
                   "QQQ_vs_SPY": {"value": 4.01, "unit": "pp"}},
    "rates": {"observed": "2026-09-24", "yields": {"2Y": 4.87, "5Y": 5.03, "10Y": 5.18, "30Y": null},
              "change_bp": {"2Y": 2, "5Y": 4, "10Y": 7, "30Y": null},
              "spreads_bp": {"2s10s": null, "5s30s": null}, "spread_change_bp": {"2s10s": null, "5s30s": null},
              "move": null, "rule": null}
  },
  "retained": {
    "class": "INTERPRETATION",
    "read": {"checkpoint": "OPEN_30M", "run_id": "live-open_30m-140214-1d6d50bd", "hash": "7274b739…",
             "evidence_cutoff": "2026-09-25T14:02:14Z", "label": "MIXED",
             "character": "Half an hour in, tech is the lone sector bid; …"},
    "watches": [{"id": "watch-live-premarket-130156-89f85b48-2", "from": "PREMARKET", "hypothesis": "…",
                 "confirmation": "…", "contradiction": "…", "horizon": "Into the close",
                 "facts": ["SPY-dma50"], "verdicts": [{"status": "weakened", "by": "OPEN_30M"}], "end": "dropped"}],
    "judged": [{"id": "watch-live-open_30m-140156-82b4d2e3-2", "status": "strengthened", "by": "PREMARKET"}],
    "relationships": [{"id": "rel-live-premarket-130153-f64d0f2e-1", "statement": "…", "facts": ["treasury-10y"],
                       "statuses": ["new", "unresolved", "strengthened", "strengthened"]}],
    "facts": {"SPY-dma50": {"value": 0.81, "unit": "%", "metric": "distance from 50DMA", "observed_at": "2026-09-24"},
              "treasury-10y": {"value": 5.18, "unit": "% yield", "metric": "daily par yield", "observed_at": "2026-09-24"}}
  },
  "gaps": [],
  "content_hash": "…"
}
```

**Deterministic (always present for an accepted close):**
- `session` and `source`.
- **Lineage on every observed value and frozen fact:** the original evidence `id`, `source_id` and `retrieved_at`, plus `formula_version`/`input_ids` for derived-origin rows. A `sources` map holds descriptors for every referenced source (name, kind, URL, provider, feed, data delay), so attribution survives the close run.
- `observed.returns` (21 symbols). The basis is `iex_daily_bar` when `{SYM}-daily.observed_at == D`; otherwise it is the run's own `-intraday` row (`iex_last_trade`, which may fall up to about 2 minutes after the bell, so it is labeled a trade, not a close). If neither exists: `null` plus a gap.
- `observed.leadership`: `packet["sector_leadership"]` plus `QQQ-spread20`.
- `observed.rates`: Treasury rows plus `curve`. Pre-#36 fields are null, and changes are rounded at derivation.
- `retained.facts`: the frozen values of rows cited by retained watches and relationships, in `render.expand` order (`watch.values`, then `interpretation.evidence`, then premarket snapshots), never from the current catalog.
- All IDs and `end` (`expired` / `reversed` / `dropped` / `open`). `gaps` and `content_hash`.

**Model-authored (INTERPRETATION, frozen):**
- `retained.read.{label,character}`.
- The watch and relationship text.
- Verdict statuses. Only `origin == "analyst"` assessments whose `run_id` is a same-session synthesis run (the admitted interpretation, and the premarket slot admitted with the same-session checks) are counted. A stale slot becomes a gap.

**Human-authored:** none.

**Omitted:** comparisons, full snapshots, background r20/dma50 for all symbols, attention selections, headlines and takes, events, and a regime field.

**Weekly output.** `narrative.v2`, unchanged.

| Reader part | Field(s) | Grounding |
|---|---|---|
| 1. The week | `banner.title`, `summary[≤2]`, `take` | archived dated rows plus `-week` and settled rows |
| 2. Regime / state change | `banner.label` + `character` | same rows; label is INTERPRETATION |
| 3. What the week taught us | `relationships[≤3]` (new) | rows cited in statements |
| 4. Carry forward | `watches[1..3]`, `attention[≤3]` | triggers deterministic |
| 5. Next week | XNYS plus admitted next-week events | deterministic |

---

## 5. Persistence

**v1.** `session_memory.json` lives **only** in the existing close-run artifact (30-day retention). Sunday needs at most 7 days.

- No `memory/` directory, commit, staging or public-repo change.
- Exposure is unchanged: the same artifact already holds `evidence.json` and `session_handoff.json`, from which every field is projected.
- The Week Ahead page publishes as every page does today.

**Later owner decision.** Durable storage is decided after 20–30 live sessions show which fields are actually used. The options then are committed JSON, a longer artifact retention (repo maximum 90 days), or a private store. Human notes, if ever kept, stay private and optional, keyed to the public record IDs (session date, `watch-…`/`rel-…`).

---

## 6. Scheduling

**Anchor.** Sunday 16:00 ET, which is Globex reopen minus 2 h. It displays as 1:00 PM PT in New York daylight time and 2:00 PM PT in standard time, because Vancouver stays UTC−7.

**Timing tradeoff:**
- Every archived input exists by Friday evening.
- Friday's settled bar exists from Saturday 00:00Z, and Friday's curve from Saturday 00:00 ET plus Treasury's publication.
- The Fed-release window moves, so Saturday and Sunday content can differ slightly.
- 16:00 ET leaves about 2 h to re-dispatch manually before futures trade.

**Wakes.**
- Cloudflare gains `"1 20-21 * * SUN"`. That gives exactly one due wake every Sunday across 52 weeks, including both New York DST changes (verified by both reviews). The other wake SKIPs.
- `resolve-scheduled` stays the only authority, with the 20-minute single-attempt tolerance (kind `synthesis`).

---

## 7. Model change: primary only

**Live (2026-09-27):**
- `anthropic/claude-opus-5.5` (canonical `-20260921`): $4/$20 per MTok vs Fable 5.1 $10/$50, 1M context, 128K output.
- The Anthropic endpoint lists `structured_outputs`, `response_format` and `reasoning`.
- The pin `{"order":["anthropic"],"allow_fallbacks":false,"require_parameters":true}` stays correct; a base slug does not match the `/fast` tier.
- No tools, no `tool_choice`, no thinking or sampling parameters are sent. `reasoning.effort: "low"` is passed through.

**Files:**
- `config/editions.json:5`: `model`.
- `synthesize.py:59`: the `OPENROUTER_MODEL` constant, plus the stale Fable comments at `:714-736`.
- `tests/test_openrouter.py`: pins only. The constants-vs-config test already exists at `:128`.
- `tests/test_voice_take.py:347`: the literal.
- `DECISIONS.md`: a dated ruling bullet.
- `docs/ARCHITECTURE.md:263`.

**Unchanged:** `fallback_model` (`anthropic/claude-fable-5`) and its semantics, profiles, budgets, the prompt, the schema, provenance, and `cli_model`.

**Economics.** At identical token counts, cost is 0.4×: 09-25 PREMARKET ≈ $0.23, OPEN_30M ≈ $0.14. A week with Sunday ≈ $2.1, vs ≈ $4.65 today. Token equivalence is unverified.

**Sunday.** It uses the same single analyst.

**Noted, unchanged.** The "no endpoints found" regex may also match OpenRouter's parameter-routing 404s.

---

## 8. Rendering: "Market clocks & data"

**Placement.** One collapsed `<details class="drawer">`, **last inside "Sources & coverage", after Technical details**. That position is safe for every slicer. HTML only; the Markdown is unchanged. It uses existing table markup; if a CSS rule is needed, `STYLE_SHA256` is re-pinned deliberately.

**Market sessions** (usual weekday hours; the date is used only for DST conversion; "exchange holidays not shown (NYSE's are)"):

| Market | Hours |
|---|---|
| CME equity futures | Sun 18:00 ET open; daily halt 17:00–18:00 ET |
| Tokyo | 9:00–11:30, 12:30–15:30 JST |
| Seoul | 9:00–15:30 KST |
| Hong Kong | 9:30–12:00, 13:00–16:00 HKT |
| Shanghai | 9:30–11:30, 13:00–15:00 CST |
| London | 8:00–16:30 (BST or GMT per date) |
| New York | 9:30–16:00 ET |

- Each row shows local time and PT (with the PT weekday).
- Hours are a static constant converted with `zoneinfo`.
- A test cross-checks them against `exchange_calendars`. At runtime the calendars are avoided: `XSHG` raises after 2026-12-31, and `CMES` has no halt.

**Data clocks.** Labels say exactly what each value is:

| Label | Source |
|---|---|
| Prices observed | latest print `observed_at` |
| Prices fetched | rows' `retrieved_at` |
| Daily bars through | `history.dates[-1]` |
| Treasury curve observed | `curve.observed_at` (a date) |
| Collection cutoff | `run.target_time` |
| Analysis evidence cutoff | `interpretation.origin.target_time`, plus edition. It is **not** a generation time. |
| Next update scheduled | plain text, **no** `data-next-at`; the header stays the single overdue-aware line |

**Must not emit:** `data-session-date`/`data-checkpoint` substrings, or a second `<dl class="clocks">`.

**Timezone test.** A strict test asserts the expected Vancouver conversions (e.g. 2026-11-02 at −07:00) rather than tolerating either offset. If CI's system tzdata fails it, set `PYTHONTZPATH=` in the workflows with a `tzdata>=2026.3` dependency, so `zoneinfo` uses the pinned package.

---

## 9. Tests and acceptance

**Invariant (asserted):**
- `Day` weekday: `calls == ["PREMARKET","OPEN_30M"]`.
- CLOSE_1M: `synthesis == {"kind":"close","calls":0}`, with `synthesize` rigged to raise at the close.
- Sunday: `calls == ["WEEK_AHEAD"]`.
- The WEEK_AHEAD wire schema is byte-identical to PREMARKET's and inside the envelope. Daily analyst contexts are byte-identical before and after (archived rebuild).

**A1:**
- A production close writes `session_memory.json` in its run folder, with a verified hash and zero human input.
- An interpretation, premarket or watch fault leaves an **observed-only record** with named `gaps`, and the close still passes.
- A projection code defect fails the close closed: no PASS, no advance.
- Page-less close: `retained.read` is a gap.
- A close with no session observation writes nothing.
- Replay, commissioning and experiment runs never write.
- Returns: 21 × `iex_last_trade` at 20:01Z; 21 × `iex_daily_bar` after midnight.
- Verdicts count only same-session analyst assessments; "dropped" is an `end`, not a verdict.
- Frozen facts never come from the current catalog.

**B, archived evidence:**
- **Materiality:** changing one archived value in a restored record changes exactly that row in the Sunday context and evidence, with the current collection fixed.
- **Minimum:** removing all records means no call and no page.
- A missing or corrupt day is a named limitation.
- Archived rows are citable (a placeholder resolves to the archived value and date), and a `week_note` value is never citable.
- **Lineage:**
  - each archived row's `source_id`, `observed_at`, `retrieved_at` (and `formula_version`/`input_ids`) equal the original row in that close's `evidence.json`;
  - `lineage.run_id`/`record_hash` match the restored record and its artifact;
  - no row carries a `session-memory` source;
  - a source absent from Sunday's collection is admitted from the archived descriptor;
  - the ledger shows the original source name.
- There is no per-day headline or take in the context.
- Exactly one standard `collect_live` call, and no Mon–Thu facts from it.

**B, restore:**
- An injected `gh` runner covers: newest-first paging stops at the window; non-main or non-`schedule.yml` runs are ignored; a record whose sibling metadata is not PASS and advanced is rejected; the earliest record wins; a SAMPLE or replay run never calls `gh`.

**B, isolation:**
- The bundle bytes are identical before and after a Sunday run, and after two consecutive Sundays.
- Monday PREMARKET still admits Friday's handoff (`anchors.previous_close.checkpoint == "CLOSE_1M"`).
- The workflow text skips continuity restore and upload for WEEK_AHEAD.

**B, dates and scheduling:**
- Due at Sun 16:00 ET +20 min in EDT and EST.
- `cli.main(["schedule","--checkpoint","WEEK_AHEAD"])` runs on Sunday and is idempotent.
- Refused on trading days and weekday holidays; experiments allowed on Saturday.
- Completed-week and next-week windows are right for Thanksgiving, Christmas, New Year, Good Friday and Monday holidays.
- The Friday / Thursday-before-Good-Friday / Wednesday-before-Thanksgiving / Sunday Next-label regressions.

**B, events:** the next-week window admits a Tuesday event on WEEK_AHEAD and not on weekdays.

**B, page:**
- Closed-market wording is derived from dates (a Good Friday fixture reads Thursday).
- Archived rows never appear in tables or the rates module.
- The Carry forward caption is present.
- No trade language in the weekly copy.

**C1:**
- One `data-next-at` and no markers.
- Strict Vancouver conversions.
- The static-hours cross-check.
- Phone width with the drawer **open**.
- Deterministic render.

**D1:**
- Pins updated.
- The experiment branch honors `-f checkpoint` (a workflow-text test).
- Gate evidence recorded in Progress.

**Every slice:** `.venv/bin/python3 -m pytest -q`, `.venv/bin/ruff check src tests`, `git diff --check <base>..HEAD`; CDP screenshots at 390/1000 px in light and dark (C1, B4); examples regenerated (they are already stale since 282726a).

---

## 10. Complexity budget

| Item | Count |
|---|---|
| New production modules | **1** (`memory.py`, ~250 lines) |
| New schema kinds | **1** (`session_memory`) |
| New config | 1 checkpoint (kind `synthesis`), 1 profile (`weekly`), 1 edition |
| New metrics | 2 (`week return`, `session return`) |
| New source listing | `session-memory` (restore channel only) |
| New CLI command | `memory-restore` |
| Edited modules | schedule, cli (close hook, WEEK_AHEAD isolation and dates, `scheduled` gate, restore command), collect (event window parameter), evidence (event window, `WINDOWS`), metrics (week rows), context (`week` selection and note), render + template (drawer, weekly labels, blocks, Next label). **continuity.py, synthesize logic and the validator are unchanged.** |
| Workflows | `schedule.yml`: experiment branch, WEEK_AHEAD restore and skip conditions, choice option. `wrangler.toml`: one SUN cron. |
| Storage | none new (the run artifact) |
| Calls | weekday 2 (unchanged) · close 0 · Sunday +1 |

**Deferred:**
- Durable or public storage; human notes.
- A weekly record kind, `previous_week`, formal cross-week radar evaluation, a weekly NEXT_BRIEF branch.
- A Treasury week-change row (the week path is already archived plus current).
- Background metrics for all symbols; attention selections; a per-day table on the Week Ahead page.
- `TRADE_LANGUAGE` coverage of watch text; `git pull --rebase` for the existing push race.
- Foreign holidays; a JS "open now" clock; a regime classifier; a WEEK horizon enum; a new calendar source (issue #23); a stock-vs-sector map.

---

## 11. Owner decisions

**Decided by the owner (2026-09-27):**
- Isolate WEEK_AHEAD from weekday continuity. This reverses the "→ Monday premarket" arrow in `PROJECT_STATE.md:83`; record it in `DECISIONS.md` at implementation.
- Artifact-only v1 storage.
- Fallback unchanged.
- One weekly call; the budget text reads "two per trading day plus one weekly".

**Open:**
1. **Sunday time and activation.** 16:00 ET (Globex −2 h) is the default. Merging the B PR deploys the cron.
2. **Paid gates.** One D1 rich PREMARKET experiment (required). The light profile is proven by either a second OPEN_30M experiment (≈ $0.14) or the first live OPEN_30M, with "revert on `length` or rejection". Plus one B WEEK_AHEAD weekend experiment before merge.
3. **Catalysts.** Accept that "Next week" lists only XNYS facts while BLS returns 403 (issue #23). The bounded window is built and fills automatically if the source recovers; a new calendar source stays a separate lane.

**Later:** durable storage after 20–30 live sessions.

---

## Approval

- **Revision 2 approved by the owner on 2026-09-27, with one amendment:** preserve original source lineage on archived factual observations. It is incorporated above (§3 B2, §4, §9).
- **Implementation starts with D1 (Opus 5.5).** Paid gates run only on an explicit owner charge; there is no push, PR or dispatch without one.

## Delta: what changed because of Astra (and the owner's directives)

**Removed:**
- Public `memory/` commits, publish-step staging, the `--record-memory` flag, and the redistribution/licensing decision. Storage is artifact-only.
- The `week_memory` kind, `previous_week`, recursive weekly context, the special weekly NEXT_BRIEF branch, and formal cross-week radar evaluation.
- The **numbers-free** week projection. Replaced by citable archived rows plus a separately labeled hypothesis note.
- The majority-of-files rule. Replaced by: zero records means no call; otherwise grounding plus named gaps.
- The hard 12 KB rejection. Now a size target plus a regression bound.
- The fallback change to Opus 5 (the fallback is unchanged), and the new constants-vs-config test (it exists at `test_openrouter.py:128`).
- The "raise budget below 15% headroom" rule.
- The annotation timestamp policy, reduced to one line.
- The recomputability and "Sunday content invariant" rationales.

**Changed:**
- **Sunday's factual input is the archive.** Session records are restored by bounded reuse of the artifact machinery, accepted only with sibling PASS-and-advanced metadata, and admitted as dated citable rows. The current collection is only a supplement (`-week`, settled, curve).
- **WEEK_AHEAD is isolated.** No bundle restore, admit or advance; the workflow skips continuity for it; allowed dates are explicit; Monday is unchanged.
- **The session record gained** the leadership selection, full rates state and frozen facts for retained watches. It is split into an always-present `observed` part and a `retained` part whose failures become named gaps. It is written before PASS in the run folder, so its bytes survive any later push failure.
- **The event path gained** a bounded next-week window. There is still no new pipeline.
- **The experiment workflow** honors `-f checkpoint` (PREMARKET or WEEK_AHEAD). The gates confirm the checkpoint from metadata; rich and light evidence are separated.
- **Clock labels are precise.** "Analysis evidence cutoff", not generation time. There is a strict Vancouver tz test.
- **Closed-market wording** comes from observation dates, and holiday weeks are handled.
- **Added:** the Friday (and Good-Friday-Thursday) Next-label regression that Astra flagged, plus the `test_cadence.py:164` pin update.

---

## Verification (end to end)

1. **D1:** the PR-branch experiment. Check `metadata.json` for checkpoint PREMARKET, experiment true, `model.resolved_model`, `provider_route`, `usage.reasoning_tokens`, `finish_reason` and cost. Nothing is published.
2. **A1:** the `Day` suite. Then the first live close after merge: the run artifact contains `session_memory.json` with the contract, and a page-less or faulted day shows `gaps`.
3. **B:**
   - offline rebuild of the Sunday context from the real week's artifacts (size table);
   - the `Day`-style Sunday test against `tests/fixtures/memory/`;
   - the weekend experiment;
   - after merge, the first Sunday: Cloudflare schedules GET plus the invocation GraphQL, the Pages page, `metadata.week` hashes, the bundle unchanged, and Monday premarket's previous-close anchor still CLOSE_1M.
4. **C1:** fixture pages with the drawer closed and open; CDP screenshots at 390/1000 px in light and dark.


---

## Progress

### D1 · Opus 5.5 (branch `feat/opus-5-5-analyst`, base 282726a)

- [x] Tests first: `test_configured_primary_analyst_is_opus_5_5_and_the_fallback_is_unchanged` (config and the
  test constants both pin `anthropic/claude-opus-5.5` with the Fable 5 fallback unchanged); the one-paid-call
  literal in `test_voice_take`; `test_manual_dispatch_runs_the_requested_checkpoint` executes the workflow's manual
  step with a stub `python` and proves an experiment runs its dispatched checkpoint (PREMARKET, OPEN_30M) while only
  a commissioning experiment resolves the phase from the clock. All failed before the change.
- [x] `config/editions.json` primary model; `synthesize.OPENROUTER_MODEL`; the manual step reads the checkpoint
  from `env` (no template expansion inside the script); stale Fable-only capability comments refreshed;
  `DECISIONS.md` ruling bullet; `ARCHITECTURE.md` budget note; `PROJECT_STATE.md` Next.
- [ ] Gate (owner charge required; one paid call, nothing published): push the branch, then
  `gh workflow run schedule.yml --ref feat/opus-5-5-analyst -f checkpoint=PREMARKET -f experiment=true` inside a
  weekday premarket window, and confirm from the run's `metadata.json`: `checkpoint == "PREMARKET"`,
  `experiment == true`, `validation == "PASS"`, `model.requested_model`/`resolved_model` Opus 5.5,
  `provider_route` Anthropic, `finish_reason` not `length`, usage and cost recorded.
- [ ] Light-profile evidence: owner decision 2 (a second OPEN_30M experiment, or the first live OPEN_30M with
  "revert on `length` or rejection").

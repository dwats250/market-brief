# PRD: Scheduler liveness after a stuck wake (defect P0)

**Status:** Phase 1 (investigate and design) approved 2026-09-30 and reported 2026-10-01. **Phase 2 (implement) approved by the owner, 2026-10-01**, with the rulings recorded in Progress.
**Base:** `main` @ c226967 (later `main` commits are publication-only; re-verify)
**Branch:** `ccr-840c4634-k9bz7y` (the session branch, accepted by the owner in place of `fix/scheduler-liveness`)
**Save as:** `docs/2026-09-30-scheduler-liveness.md`, and keep the Progress section at the bottom current.
**Out of scope here:** release actuals and RSP. They get their own PRD after this one is repaired.

---

## 0. How to work on this

1. **Read first.** Read `CLAUDE.md`, `PROJECT_STATE.md`, `DECISIONS.md`, this file, `.github/workflows/schedule.yml`, `.github/workflows/pages.yml`, `cloudflare/src/index.js`, `cloudflare/wrangler.toml`, `src/market_brief/schedule.py`, and `cli.scheduled` with the helpers it calls. Then read the tests that pin them: `tests/test_schedule.py`, `tests/test_cloudflare_scheduler.py`, and any test of the workflow.
2. **Phase 1 is read-only.** Investigate, design, and write the Phase 1 report (§6) into Progress and as your reply. Then **stop**. No code changes, no commits other than saving this PRD with its Progress section, and no workflow dispatches.
3. **Evidence labels.** Mark every claim in the report **observed** (you saw it in run metadata, logs or code; cite it) or **inferred** (your reasoning from observations). Do not infer a root cause from the cancellation cascade; the cascade is a consequence, not a cause. If the cause cannot be observed, say exactly "root cause not observable" and say what evidence would settle it.
4. **Use `gh` for GitHub facts.** The run pages are public, but job-level timing, runner assignment and logs need the API. Examples: `gh api repos/dwats250/market-brief/actions/runs/36748511695`, `.../runs/36748511695/jobs`, `.../runs/36748511695/timing`, and `gh run list --workflow schedule.yml --limit 40 --json databaseId,number,status,conclusion,createdAt,updatedAt`.
5. **Stay safe.** No paid model calls. No pushes to `main`. No workflow dispatches, no run cancellations and no Cloudflare deploys unless the owner asks in this session. Local commits on the branch only, in Phase 2.
6. **Code owns facts; this file owns intent.** §2 was verified from public pages and the code at c226967. If anything there is wrong, record the discrepancy in Progress and work from what is true.

---

## 1. Why

On Sep 30, the HOURLY_1300 wake (run #203) took the workflow's concurrency group, and its `brief` job then never
received a runner. Because the workflow serializes on one concurrency group, every later wake waited behind it
as the pending run and was cancelled by the next one to arrive. HOURLY_1300, HOURLY_1400, HOURLY_1500 and
CLOSE_1M never published, and Sep 30 has no close snapshot or handoff.

The serialization is right. It protects continuity, publication order and paid-synthesis dedupe, and stays. The defect is that **one wake that never starts can hold the group indefinitely**, so a single platform hiccup silently costs the rest of the day, and possibly the next one.

**Goal:** if one dispatch stalls before it starts, later still-valid checkpoints and the close publish anyway, without a second paid synthesis, without two publishers racing, and with a log line saying what was recovered and why.

---

## 2. Facts (GitHub API and code, verified 2026-10-01)

The incident identifiers are kept here for a possible GitHub Support ticket. Phase 1 (Progress) holds the full
timeline, its evidence labels and the corrections to the first reading of the run pages.

### Runs
- **#202** (`36741213779`, deployment `6764055004`): the HOURLY_1200 wake, success; the last good run. Its
  github-pages deployment went `waiting` → `queued` → `in_progress` within 4 s, as every sampled deployment did.
- **#203** (`36748511695`, check suite `99528538652`, `brief` job `110000782238`, deployment `6765318215`): the
  HOURLY_1300 wake, created 17:02:03 UTC on c226967. It **was processed**: at 17:02:05 it took the group, its
  three jobs were created, the two jobs whose `if:` was false completed `skipped`, and a github-pages deployment
  was created for `brief`. Only `brief` never received a runner or ran a step. The deployment received **no
  status at all for about 9 h 05 m**, not even `waiting`. #203 was cancelled at 02:07:36 UTC on Oct 1; who
  cancelled it, and by which endpoint, is not observable through the API.
- **#204**, **#205**, **#206** (`36755617810`, `36762759547`, `36769731557`): the HOURLY_1400, HOURLY_1500 and
  CLOSE_1M wakes. Each waited as the pending run, never got a job, and was **cancelled** 2–3 s after the next wake
  was created ("Canceling since a higher priority waiting request for market-brief-pages exists" on #204's page).
  None succeeded.
- **#207** (`36776686174`, deployment `6774440203`): the 21:01 wake. It waited as the pending run, started at
  02:07:46 once #203 was cancelled, and **resolved SKIP**, as that slot always does in New York summer time. No
  line in its log said why.
- **Root cause not observable.** The observed failure boundary is GitHub-side environment and deployment
  processing for the `brief` job, before runner assignment. Application, workflow, Cloudflare dispatch, the
  concurrency group itself and any repository or deployment lock are excluded by evidence (Phase 1 §2).

### Configuration
- **`schedule.yml`:** workflow-level `concurrency: { group: market-brief-pages, cancel-in-progress: false }`; its only trigger is `workflow_dispatch`, and a run from any branch joins the group. The `brief` job runs on `ubuntu-latest` with `environment: github-pages`. Its last two steps record the paid-attempt artifact under `always()`. Its checkout is the commit `main` held at dispatch.
- **`pages.yml`:** the same group and the same `github-pages` environment. It runs on pushes to `publish/**` or to itself, and on `workflow_dispatch`; publish commits are pushed with the default token, which does not trigger workflows, so the scheduler never starts it.
- **Cloudflare Worker** (`cloudflare/src/index.js`, 43 lines at c226967): dispatches `schedule.yml` with `cloudflare_wakeup=true` at the `wrangler.toml` crons, `1 13-21 * * MON-FRI` and `31 13-14 * * MON-FRI` (UTC). At c226967 it does not read run state. Merging a change under `cloudflare/` to `main` redeploys it.

### Scheduler semantics
- **Tolerance:** `TOLERANCE_MINUTES = {"synthesis": 20, "refresh": 45, "close": 45}`. A wake resolves its checkpoint from the clock when it starts (the nearest one due); it carries no checkpoint of its own. At c226967 a Cloudflare wake that resolves nothing prints a bare `SKIP` into the step output and nothing to the log; `SKIP / … / outside checkpoint window` appears only if the window closes between the resolve and `schedule` steps.
- **Idempotency:** across runners, only the restored continuity bundle prevents re-running a completed checkpoint. The checkpoint marker is runner-local, and the published page's `data-session-date` / `data-checkpoint` are read from the dispatch-time checkout.
- **Paid dedupe:** before a synthesis, `earlier_attempt` checks the durable attempt record. If it can't check, it refuses to call the provider.

### Platform
- GitHub's concurrency allows one running and one pending run per group; a newer pending run cancels the older pending one. A run can hold the group with no job running (#203). GitHub does not expose the group's holder on the run or job endpoints (a documented concurrency-groups endpoint could not be verified from the investigation environment); with a workflow-level group, jobs are created only once a run holds it.
- `timeout-minutes` limits a job only after it starts. Nothing in the workflow bounds how long a run may sit queued.
- `run_started_at` and a job's `started_at` are set even on runs and jobs that never ran; only runner assignment and steps show that a job started.

---

## 3. Phase 1: investigate and design (report, then stop)

**P1. Timeline.** Reconstruct HOURLY_1200 (success) → HOURLY_1300 dispatched → stuck, then every later wake through #207, from the API: created, started and updated times, job states, runner assignment, the concurrency annotations, and who or what cancelled each run.

**P2. Earliest unsupported transition and its class.** Name the first state change that should not have happened, and classify the cause with evidence:
- application or workflow defect;
- GitHub concurrency or environment behavior, including the `github-pages` environment and any deployment queue or protection rule;
- GitHub hosted-runner or platform queue (check GitHub's status history for Sep 30, 17:00–18:00 UTC);
- Cloudflare dispatch;
- a repository or deployment lock;
- other.

If none is evidenced, write "root cause not observable" and list what would settle it.

**P3. Smallest liveness repair.** Propose one bounded, deterministic recovery so that a run which **never started** cannot hold the group past the point where it could still do useful work. Requirements:
- **Threshold.** Derive any staleness threshold from the existing tolerance semantics (`TOLERANCE_MINUTES`, check-in windows), not a new unrelated clock. For example: a wake that has not started within the largest tolerance window can no longer serve the checkpoint it was dispatched for.
- **Safety predicate.** Recovery may act only on a run that has **not started any step**. Such a run cannot have sent a provider request, uploaded continuity or published. A run that has started anything is never touched.
- **Location.** Recovery must work even when the stall is in GitHub's queue, so prefer a place outside the stalled group. One candidate is the Cloudflare Worker before it dispatches: it already holds a GitHub token. Verify that token's scopes and say whether it needs more. Compare the alternatives you considered and why you rejected them.
- **Not allowed:** `cancel-in-progress: true` on the existing group, removing the group, or making checkpoints concurrent. If evidence shows the existing architecture cannot recover safely without a new service, say so with that evidence; don't add one by default.

**P4. Invariants.** Show, item by item, why the proposal preserves each invariant in §4.

**P5. Tests.** Map each scenario in §5 to a named test and say where it lives: Python with mocked GitHub state, and/or the Worker's tests. Mock all GitHub responses. No paid calls.

**P6. Blast radius.** List the files that would change, the token or permission changes, the deploy steps (Worker redeploy), and the rollback.

---

## 4. Invariants (all must hold after Phase 2)

1. At most two paid synthesis checkpoints per normal trading day: PREMARKET and OPEN_30M.
2. No automatic paid retry after a provider request.
3. Paid-attempt authority and dedupe stay durable across runners.
4. Continuity stays fail-closed.
5. Foreign or fork artifacts stay ineligible.
6. Accepted continuity cannot be rolled back by a publication failure.
7. Deterministic refreshes never call the analyst.
8. Publication and state ordering stay deterministic.
9. No two publishers race or silently overwrite each other.
10. Cancellation does not become a routine state-management mechanism. It applies only to never-started runs past their window, and every instance is logged.
11. A missing page stays missing. Nothing reconstructs or backfills a past edition.

---

## 5. Required scenarios (Phase 2 tests, designed in Phase 1)

| | Scenario | Must show |
|---|---|---|
| A | Normal trading day: PREMARKET → OPEN_1M → OPEN_30M → hourlies → CLOSE_1M | Unchanged behavior; recovery never fires |
| B | One deterministic wake never starts | Later valid checkpoints and CLOSE_1M run; the stuck run is cleared once past threshold |
| C | A synthesis run is executing | Recovery never cancels or re-runs it, however many wakes arrive |
| D | A paid synthesis was attempted and rejected | Recovery respects the durable attempt record; no second provider request |
| E | Accepted continuity exists, then a publication fails | The next run restores the accepted state |
| F | Two recovery candidates arrive close together | Exactly one owns publication and state advancement; the other skips with a log line |
| G | Any recovery | The Worker log and the next run's log say what was cleared, when it was created, and why it qualified. Nothing is skipped silently |

---

## 6. Phase 1 report (your reply, and the same text in Progress)

1. The reconstructed incident timeline, with each line labeled observed or inferred.
2. The evidenced root cause, or "root cause not observable" plus what would settle it.
3. The smallest proposed liveness fix.
4. Why it preserves every invariant in §4.
5. The exact tests, mapped to scenarios A–G.
6. The blast radius: files, permissions, deploys, rollback.
7. Anything the owner must decide.

Then stop and wait for the owner.

## 7. Phase 2 (after owner review)

Implement the approved design tests first, in small slices. Run `.venv/bin/python3 -m pytest -q`, `.venv/bin/ruff check src tests`, any Worker tests, and `git diff --check`. Update `DECISIONS.md` (one bullet: the recovery rule and why it is safe) and `PROJECT_STATE.md`. Commit locally. Pushing, opening a PR and redeploying the Worker are owner steps.

---

## Progress

_Claude Code keeps this current: Phase 1 findings, the owner's ruling, the slice plan, and what's done._

**2026-10-01: Phase 1 complete (read-only).** (The PRD body above was corrected on 2026-10-01 after the
owner's review; this report is kept as written.)
Base re-verified: `main` is still c226967 ("Publish HOURLY_1200 brief"). Baseline: `.venv/bin/python3 -m pytest -q`
775 passed, 12 skipped (headless Chrome); `ruff check src tests` clean. No code, workflow or Worker change; no
dispatch, cancel or deploy.

### Phase 1 report

**Evidence and labels.** GitHub facts come from the REST API via curl; this session's `gh` token was invalid, and
the proxy authenticates plain API calls. Job logs come from the GitHub MCP server. `github.com` HTML pages,
`/environments`, `/pages` and githubstatus.com were blocked here. **[O]** means observed, with the source cited;
**[I]** means inferred. The raw snapshots and simulation scripts lived in the session scratchpad and are not
committed.

#### 1. Timeline (UTC)

| When | What happened | Label |
|---|---|---|
| 09-30 16:01:58 | #202 (36741213779) created. Brief job created 16:01:59. Deployment 6764055004 went `waiting` 16:02:01 → `queued` :02 → `in_progress` :04 (runner 1000005325) | [O] run, jobs, deployment statuses |
| 16:02:21–:26 | #202 resolved and ran HOURLY_1200 (`schedule --checkpoint "HOURLY_1200"`, `LIVE / PARTIAL / refreshed`) and pushed c226967 | [O] job log 109975791839 |
| 16:03:11 | #202 complete; the group is free | [O] run updated_at; deployment `success` |
| 17:00 | HOURLY_1300 due; its window closes at 17:45 | [O] computed with `scheduled_checkpoint` |
| 17:02:03 | #203 (36748511695) created by `workflow_dispatch`, 63 s after the 17:01 cron. Over 7 days this lag ranged 33–63 s | [O] run; [I] it is the Worker's dispatch (actor dwats250) |
| 17:02:05 | #203 took the group. All three jobs were created. `continuity-check` and `cloudflare-smoke` completed `skipped` at 17:02:05. `brief` (110000782238) was created, and so was github-pages deployment 6765318215 | [O] jobs, check runs, deployments |
| 17:02:05 → 02:07:36 | `brief` never got a runner and never ran a step (`runner_id` 0, `runner_name` '', `steps` []; snapshot taken after the cancel). Deployment 6765318215 got **no status at all**, not even `waiting`, for 9 h 05 m | [O] |
| 18:01:44 | #204 (HOURLY_1400 wake) created; it never got a job (pending in the group) | [O] jobs total_count 0 |
| 19:01:37 / :40 | #205 (HOURLY_1500 wake) created; #204 cancelled 3 s later, after 59 m 56 s | [O]. The "higher priority waiting request" annotation was seen on #204's page and can't be read through the API. That this is GitHub's replace-pending rule is [I] |
| 20:01:37 / :40 | #206 (CLOSE_1M wake) created; #205 cancelled 3 s later | [O] |
| 21:01:37 / :39 | #207 (the 21:01 wake, a SKIP slot in EDT) created; #206 cancelled 2 s later | [O] |
| 10-01 02:07:36 | #203's `brief` completed `cancelled`; the run updated at 02:07:37 (9 h 05 m 34 s in all). Its deployment then got `waiting` at 02:07:37 and `error` at 02:07:46 | [O] |
| 02:07:36 | Who cancelled #203, and with which endpoint, is not observable: there is no canceller field, no annotation, and the deployment-status creator is always dwats250. Nothing in the repo cancels runs. This fits the manual cancel the owner was asked to make | [O] absence; [I] owner |
| 02:07:36–:46 | #207 promoted: jobs created 02:07:36–37, deployment `waiting`/`queued` 02:07:38, runner 02:07:46 | [O] |
| 02:08:03–:04 | #207 resolved SKIP, steps 6–19 skipped, and **no log line says SKIP or why**. `scheduled_checkpoint` returns None both at 02:08 and at 21:01, so the delay changed nothing for this slot | [O] job log 110185281804, steps |
| ~02:23 | No unfinished run in any workflow; `main` still at c226967 | [O] |

- **Normal start latency** (53 github-pages deployments, Sep 24–30):
  - deployment created → `waiting`: 1–6 s
  - deployment created → `in_progress`: 4–40 s
  - run created → job started: 5–41 s
- **History:**
  - Seven days of `schedule.yml`: 55 success, 3 failure, 4 cancelled. The cancelled runs are #203–#206 only.
  - Across all 207 runs, only #203–#207 took more than 15 minutes from creation to their last update. [O]
- **Consequences:**
  - HOURLY_1300, HOURLY_1400, HOURLY_1500 and CLOSE_1M did not publish.
  - There is no Sep 30 close handoff. The newest continuity artifact is #202's, so the Oct 1 PREMARKET will cold-start its close continuity. [O] artifacts; [I] consequence.

#### 2. Root cause

**Root cause not observable.**

**Earliest unsupported transition [O].** Deployment 6765318215 (github-pages, created 17:02:05Z with job
110000782238) posted no status for 9 h 05 m. Its first status, `waiting`, came 1 s after the job was cancelled.
Every other deployment sampled posted `waiting` within 1–6 s. That includes #157's branch-policy rejection on
09-25: it posted `waiting` and then `failure` within 2 s, annotated "Branch … is not allowed to deploy to
github-pages due to environment protection rules".

**Class: GitHub-side environment or deployment processing for the `brief` job, before the hosted-runner queue
(PRD category 2, platform side).** The stage is observed; the mechanism is not.

- **Excluded, application or workflow defect:** no step ran, and the same workflow at the same SHA ran normally as
  #207 [O].
- **Excluded, Cloudflare dispatch:** the run was created within the normal lag, and its jobs expanded [O].
- **Excluded as a cause, the concurrency group:** the group had been free since 16:03:11 and #203 took it. The
  cascade is the consequence [O].
- **Excluded, repository or deployment lock:** no other run in any workflow between 15:00Z and 03:00Z, and no other
  github-pages deployment between 16:03:11 and 02:07:37 [O].
- **Not supported, a configured protection rule:** rules act after `waiting`, which never posted, and `main` is
  admitted [I; `/environments` returns 403 here].
- **Not supported, the hosted-runner queue:** the deployment never reached `queued`, which in every normal run comes
  before a runner is assigned [I].
- **Unknown, GitHub status history** for Sep 30, 17:00–18:00Z: githubstatus.com is blocked here.

**What would settle it:**
1. A GitHub Support ticket. Cite run 36748511695, check suite 99528538652, job 110000782238, deployment 6765318215,
   SHA c226967 and the window 17:02:05Z–02:07:37Z. Give #202 (36741213779 / 6764055004) and #207 (36776686174 /
   6774440203) as normal comparisons. Ask for:
   - the job's internal state history;
   - why no deployment status was posted;
   - any incident in that window;
   - the actor and method of the 02:07:36 cancel.
2. githubstatus.com history for that hour.
3. The github-pages environment settings and their change history.
4. The owner's security log, or a direct confirmation, for the #203 cancel.

#### 3. Proposed fix

One bounded, deterministic recovery step in the Cloudflare Worker (`cloudflare/src/index.js`), run before every
dispatch, plus logging in the workflow.

**Rule.** At a tick, normal-cancel a run that holds the group only when all four conditions hold.
1. **It is a wake.** That means:
   - workflow `schedule.yml`, event `workflow_dispatch`, branch `main`, `run_attempt` 1;
   - `display_title` "Cloudflare wake-up" (set by a new `run-name`, below).

   Manual dispatches, `pages.yml` runs and runs from other branches are never cleared. When one of them is at least
   T old and holds the group, the Worker logs it (`left`) on every tick.
2. **It never started.** Every job is either `skipped`, or `queued`/`waiting`/`pending`/`requested` with:
   - `runner_id` null or 0;
   - `runner_name` null or '';
   - no steps.

   A run with zero jobs counts only when the run itself is not `in_progress`. `run_started_at` and job `started_at`
   are not used, because both are set on runs that never ran [O].
3. **It is past its window.** Its age (the tick's `scheduledTime` minus `created_at`) is at least T, where T = 20 min
   = `TOLERANCE_MINUTES["synthesis"]`.
4. **The queue is live.** The wake created just before it got a runner within T of its job being created. This
   stops recovery from cancelling runs in a slow but working queue. It also means recovery never clears twice in a
   row.

**Why T = 20.**
- **Wake spacing is the real constraint.** The Worker acts only at ticks, which are at least 30 min apart. A wake's
  age at the next tick is at least 28.95 min [O].
  - Any T from about 1 to 28.9 min clears a stuck wake at exactly the next tick.
  - T ≥ 30 skips that tick. T = 30 never clears at a 30-min tick, because ages there are 28.95–29.45 min.
  - 20 is the narrowest existing due window. It falls inside that range without adding a clock, and it doubles as
    the promptness bound in condition 4.
- **By the next tick, the stuck wake cannot do its own work any more.** It can no longer resolve the checkpoint its
  own tick would have resolved. Two independent scans found 0 violations: 438 sessions (2026-01-02..2027-09-30), every
  consecutive tick pair, start lags of 0–120 s [O].
- **A fresh wake is an exact substitute.** A wake carries no checkpoint and resolves by the clock when it starts
  [O `cli.py:592-593`, `index.js:22-25`], so it does exactly what the stuck one would do if it started at that
  moment.
- **What "past their window" means in invariant 10:** the wake can no longer resolve the checkpoint it was
  dispatched for, because that checkpoint is no longer nearest-due. One case is cleared inside its nominal
  tolerance: the OPEN_1M wake (13:31 UTC in EDT, 14:31 UTC in EST) is cleared at the next tick, 15 min before its
  45-min tolerance ends, because OPEN_30M is nearest-due by then. This is owner decision 2.
- **Single-stall simulation with the real scheduler** (44 scenarios: EDT, EST, early closes) [O]:

  | Threshold | Checkpoints lost beyond the stalled wake's own |
  |---|---|
  | T = 20 | none |
  | T = 45 (the PRD's example) | a later valid checkpoint in 7 cases, including the PREMARKET and OPEN_30M syntheses |
  | T = 60 | up to 2, including CLOSE_1M |
  | no recovery | the rest of the day and the next session |

  Replaying the incident with T = 20: #203 is cleared at 18:01:42, the 18:01 wake resolves HOURLY_1400, and only
  HOURLY_1300 is lost.
- **Slow-but-live queue simulation** (every run waits D min for a runner) [O]:
  - Without condition 4, T = 20 cancels each run just before it would start. With D = 45–75 nothing publishes, with
    4–10 cancels a day. With D ~ U(40,80), the mean is 0.6 published a day, against 5.2 with no recovery.
  - With condition 4, the single-stall result is unchanged, a slow day gets at most one cancel, and the mean is
    4.2–4.9 published a day (against 5.2–5.7 with no recovery).

**What the Worker does on each tick.**
1. **List unfinished runs** with small status-filtered requests (`/actions/runs?status=` each of queued,
   in_progress, waiting, pending and requested). Keep the group's members: `schedule.yml` and `pages.yml` runs from
   any branch. The holder is the oldest.
   - Why not one 50-run list: it is about 736 KB and costs about 4–8 ms of CPU, against the Free plan's 10 ms
     [O measured].
2. **If the holder meets conditions 1–4:**
   1. Look up the previous wake for condition 4.
   2. Cancel every clearable member, newest first, so a pending wake goes before the holder and nothing stale is
      promoted. Re-read each run and its jobs immediately before its `POST …/cancel`. Never force-cancel.
   3. Poll the run for up to about 10 s. Log `cleared` only once it is `completed`; otherwise log
      `cancel_not_effective`.
3. **Dispatch as today.** If something was cleared, add the input `liveness_recovery` holding a one-line note: the
   run id and number, `created_at`, age and reason. If GitHub answers 422 to that, re-dispatch once without the
   note.
4. **Fail safe.** The whole sweep is wrapped, and every request has `AbortSignal.timeout(10 s)`. Any error,
   malformed answer or timeout logs `unknown`, cancels nothing, and still dispatches.
   - A normal tick costs 5 small GETs plus the dispatch. The worst case is about 20 subrequests, against a limit
     of 50.

Worker log lines are JSON:
`{"liveness": "idle"|"left"|"cleared"|"cancel_not_effective"|"unknown", run_id, run_number, created_at, age_minutes, threshold_minutes, reason}`.

**Workflow and Python changes.**
- **`schedule.yml` run name:**
  `run-name: ${{ inputs.cloudflare_wakeup == true && 'Cloudflare wake-up' || 'Scheduled Market Brief' }}`.
  - The `== true` comparison is string-safe, as elsewhere in the file. Nothing reads the run name today [O grep].
- **`schedule.yml` recovery note:** a new input `liveness_recovery` (string, default '').
  - The Resolve checkpoint step receives it through `env:` and prints it as one sanitized line (`Liveness: …`, with
    CR/LF stripped and the length capped). It never goes into `$GITHUB_OUTPUT`.
- **`cli.py` `resolve-scheduled`:** when it resolves SKIP, print `SKIP / - / no checkpoint due at <utc>` to stderr.
  - stdout stays exactly the token the workflow captures. #207's SKIP was silent [O].
- **`schedule.yml` deploy guard (scenario F, invariant 9):**
  - **The step:** after Publish, fetch `main` (depth 1). Configure, Upload and Deploy Pages run only when
    `git diff --quiet HEAD FETCH_HEAD -- publish` holds, i.e. the page this run would deploy is `main`'s page.
    Otherwise log `NOT DEPLOYED / <label> / this checkout's page is behind main; the newer page stays live`.
  - **Why it is needed:** a wake that SKIPs inside `schedule` (already completed or already attempted) still
    redeploys Pages from its dispatch-time checkout [O `schedule.yml:198-220`, `cli.py:534-548`]. A second wake for
    the same checkpoint therefore overwrites a newer page.
  - **History:** this predates the incident. One tick produced two dispatches on Sep 11 (#54/#55) and on Sep 22
    (#133/#134) [O]. Recovery itself does not create such a pair, but F as written needs this closed.
- **`wrangler.toml`:** `[observability] enabled = true`. Without the block, the deploy's wrangler 3.90.0 sends
  `observability: {enabled: false}`, so the Worker's log is not persisted today [O wrangler source].

**Alternatives considered and rejected.**
- **`cancel-in-progress: true`, removing the group, job-level or per-checkpoint groups:** forbidden by the PRD.
- **`timeout-minutes`:** it bounds a job only after it starts, and #203 never started [O docs, #203].
- **`concurrency.queue: max`** (a new GitHub option): it keeps the stuck holder and turns the cascade into a 9-hour
  backlog of late wakes.
- **A watchdog workflow on GitHub:**
  - it needs a runner on a platform that may be the one stalling;
  - GitHub cron fired late and in duplicates, which is why the Worker exists;
  - `GITHUB_TOKEN` would need `actions: write`.
- **Recovery inside the next run:** impossible, because the next run can't start while the holder holds the group.
  #204–#207 never got jobs [O].
- **A separate sweep cron:** no benefit; clearing only matters immediately before a dispatch.
- **A lock service or Durable Object:** not needed. GitHub's own cancel freed the group within about 1 s on Oct 1
  [O timing], although who cancelled and how is not observable.
- **Detect-and-alert only:** recovery would wait on a human, which took 9 h here.
- **Checkout `ref: ${{ github.ref }}` instead of the deploy guard:**
  - One line, and it also fixes the push rejection listed under residuals.
  - But a run would then mix the dispatch-time workflow file with the tip's code.
  - Offered as owner decision 5.
- **Moving `environment:` to a separate deploy job:** an architecture change that does nothing for stalls
  elsewhere.

**Residuals (not fixed by this proposal).**
- A stalled run that is not a wake (a manual dispatch, `pages.yml`, another branch) still blocks the group. It is
  logged on every tick, not cleared.
- Recovery stops at the first clear when two stalls come in a row (condition 4); the second is logged.
- A runner could be assigned in the roughly 100 ms between the last re-read and the cancel. The first side effect
  comes at least 14 s after job start [O step timings], and a normal cancel still uploads the attempt record.
- If an owner's manual run is pending behind a stalled wake, clearing the wake promotes it. The fresh wake queued
  behind it then pushes from a stale checkout and is rejected. Its edition is accepted into continuity but not
  published; nothing is overwritten.
- It is not verified that a normal cancel frees a run stalled like #203 *while* the stall lasts; the only cancel
  came 9 h in. `cancel_not_effective` makes that case visible.
- That `run-name` is evaluated when the run is created is inferred, from its documented contexts and from
  `display_title` being present on never-started runs. One check after merge confirms it. If it turns out false,
  nothing is ever cleared: fail-safe, and logged as `left`.

#### 4. Invariants

1. **At most two paid syntheses a day.**
   - A cleared run ran no step, so it sent nothing.
   - The replacement resolves a synthesis only if one is due at its own start, and `scheduled()` still checks
     `completed_in_bundle` and `earlier_attempt` [O].
   - Ticks are at least 30 min apart and the synthesis window is 20 min, so no next tick resolves the same synthesis
     [O scan].
2. **No automatic paid retry after a provider request.**
   - A run that sent a request has started steps, so it fails condition 2 and is never touched.
   - The re-read narrows the race to about 100 ms, against at least 14 s before the first side effect.
   - A normal cancel keeps the `always()` attempt upload [O docs], and a cancelled run's record still counts
     [O `tests/test_synthesis_attempts.py:432,460`].
3. **Paid-attempt dedupe stays durable.** Unchanged: nothing deletes artifacts, and the Worker holds no state.
4. **Continuity stays fail-closed.** Unchanged: a cleared run uploaded nothing, and the restore rules are the same
   [O `continuity.py:178`].
5. **Foreign and fork artifacts stay ineligible.** Unchanged: the Worker clears only this repository's `main` wakes.
6. **A publication failure can't roll back accepted continuity.**
   - The deploy guard comes after the continuity upload and never fails the run. `NOT DEPLOYED` concludes success,
     so the run's bundle stays restorable.
   - A SKIP run re-uploads the bundle it restored, unchanged. [I; a test pins it]
7. **Refreshes never call the analyst.** The kinds and the pipeline are unchanged.
8. **Ordering stays deterministic.** The group still serializes. Clearing goes newest first, and the dispatch waits
   for the cancel to complete, so a stale pending wake is never promoted ahead of a fresh one.
9. **No two publishers race or overwrite each other.**
   - The group serializes publishers.
   - The deploy guard stops a stale checkout from redeploying an older page.
   - A stale push is rejected as non-fast-forward, as today [O `schedule.yml:207`].
   - Residual: an owner `pages.yml` dispatch from an older checkout (this predates the proposal).
10. **Cancellation doesn't become routine.** It applies only to never-started wakes past their window, only while
    the queue is live, at most once in a row, and every instance is logged in the Worker log and the next run's log.
11. **A missing page stays missing.** The replacement carries no checkpoint, the missed checkpoint's window has
    passed, and the note is only printed.

#### 5. Tests (Phase 2; every GitHub response mocked; no paid calls)

A new `tests/test_cloudflare_liveness.py` runs the real `cloudflare/src/index.js` under Node, using an inline
harness: scripted `fetch`, a frozen `scheduledTime`, and captured console output. It skips when Node is absent, as
the headless-Chrome tests do, but fails instead of skipping when `CI` is set. A prototype of five tests passed on
Node 20 and 22 [O]. The Python tests extend existing modules.

| | Tests |
|---|---|
| A | `test_a_quiet_tick_dispatches_exactly_as_before`: the body is byte-identical and nothing is cancelled; the fixture holds a running run, a seconds-old queued run, and tests.yml and pages.yml runs. `test_recovery_never_fires_across_a_green_day`: every tick, EDT and EST. `test_a_hung_run_list_still_dispatches_on_time`. `test_a_malformed_github_answer_still_dispatches`. Existing: `test_a_green_day_restores_each_run_from_the_one_before_it`, `test_one_production_day_synthesizes_twice_and_refreshes_deterministically`, `test_no_two_wakes_resolve_the_same_checkpoint_before_its_publish_can_land` |
| B | `test_a_never_started_wake_past_its_window_is_cleared_before_the_dispatch`: a synthesized #203 state, parametrized over job status (queued, waiting, pending, requested), `runner_id` (null, 0) and `runner_name` (null, ''). `test_a_never_started_wake_inside_twenty_minutes_is_left`. `test_one_stalled_wake_is_cleared_once_and_the_close_publishes`: the real Worker at each Sep 30 tick against a Python fake of the group, with runs resolved by the real `scheduled_checkpoint`; exactly one cancel; HOURLY_1400, HOURLY_1500 and CLOSE_1M complete and HOURLY_1300 does not. `test_the_wake_threshold_is_the_synthesis_window_and_below_the_wake_spacing`: parses the JS constant and the crons. `test_no_wake_can_still_serve_its_checkpoint_at_the_next_wake`: sampled sessions, DST changes, early closes |
| C | `test_a_started_run_is_never_cancelled_however_many_wakes_arrive`: 12 ticks. `test_only_a_never_started_wake_is_clearable`, parametrized: runner assigned, a step present, `in_progress` with zero jobs, `run_attempt` 2, another branch, the manual title, pages.yml. `test_a_wake_that_gets_a_runner_between_reads_is_left`. `test_a_slow_queue_is_cancelled_at_most_once`: every run waits 35 min. `test_the_worker_never_force_cancels_and_calls_only_list_cancel_and_dispatch`. `test_the_brief_job_can_be_cancelled_before_it_starts`: its `if:` holds no status function. Existing: `test_every_run_that_can_pay_waits_for_the_previous_one_to_finish` |
| D | Existing: `test_a_rejected_synthesis_is_never_paid_for_again`, `test_an_unlistable_attempt_record_store_never_pays`. New: `test_a_double_fired_recovery_tick_pays_the_opening_synthesis_once`. At 14:01 EDT the 13:31 wake is cleared and two replacements run; the first is rejected, the second SKIPs on the attempt record, and provider requests == 1 |
| E | Existing: `test_an_accepted_synthesis_survives_a_failed_publication`, `test_a_wake_after_a_failed_publication_skips_the_checkpoint_its_bundle_records`, `test_a_runs_continuity_artifact_is_eligible_whatever_its_publication_did`. New: `test_the_deploy_guard_comes_after_the_continuity_upload_and_cannot_gate_it` |
| F | `test_a_double_fired_tick_clears_once`: the second cancel gets 409 or fails its re-read, and is logged. `test_a_pending_wake_is_cleared_before_the_holder`. `test_only_mains_page_is_deployed`: runs the guard's shell against a bare origin and a pinned depth-1 clone; the run that pushed deploys; a stale SKIP gets `deploy=false` and the NOT DEPLOYED line; an owner commit outside `publish/` still deploys; a failed fetch fails the step. `test_a_skipping_run_reuploads_the_bundle_it_restored_unchanged`. Existing: `test_a_queue_delayed_earlier_wake_cannot_repeat_a_completed_checkpoint_on_a_fresh_runner` |
| G | `test_a_cleared_run_is_logged_with_its_id_creation_age_and_reason`. `test_a_cancel_that_does_not_complete_is_not_reported_as_cleared`. `test_the_next_wake_carries_and_prints_the_recovery_note`: the dispatch body, plus workflow text showing the input is declared, passed via `env:`, printed as one line in Resolve checkpoint, and read nowhere else. `test_a_rejected_note_falls_back_to_a_plain_wakeup`. `test_a_left_holder_is_logged_every_tick`. `test_the_token_never_reaches_a_log_or_the_dispatch_body`. `test_a_skipped_wake_says_why`: the stderr reason, with stdout exactly `SKIP`. `test_both_run_titles_are_pinned` |

#### 6. Blast radius

**Files that change:**
- **Worker:** `cloudflare/src/index.js`; `cloudflare/wrangler.toml` (`[observability]`).
- **Workflow:** `.github/workflows/schedule.yml` (`run-name`, the new input, the Resolve-step print, the deploy
  guard).
- **Python:** `src/market_brief/cli.py` (the `resolve-scheduled` stderr reason).
- **Tests:**
  - `tests/test_cloudflare_liveness.py` (new).
  - `tests/test_cloudflare_scheduler.py`: threshold and supersession pins. `test_cloudflare_worker_dispatches_only_a_wakeup`
    is renamed or reworded; the literal strings it pins stay.
  - `tests/test_schedule.py`, `tests/test_continuity_restore.py`, `tests/test_synthesis_attempts.py`.
- **Docs:**
  - `cloudflare/README.md`: the Worker is no longer "only a wake-up layer", and the token wording changes.
  - `docs/ARCHITECTURE.md`: lines 231–248, plus the stale "hold" paragraph at 338–345.
  - `docs/SYNTHESIS_COST_CONTAINMENT.md`: the stale "in force" header.
  - `DECISIONS.md` (one bullet), `PROJECT_STATE.md`, and this PRD.

**Permissions: none new (inferred).**
- Dispatch already needs Actions: write (fine-grained token) or `repo` (classic), and cancel needs the same.
- Listing runs and jobs needs Actions: read, which write implies and a public repo allows anyway [O REST docs
  source].
- The token is dwats250's (the run actor) [O]. Its type and expiry are not observable from here.
- A fine-grained token can't be limited to "dispatch only", so the README wording changes.

**Deploy:**
- Merging to `main` deploys the Worker automatically (`cloudflare-scheduler.yml` on `cloudflare/**`) [O], so it is
  not a separate owner step.
- The new input and `run-name` land in the same commit, so they are on `main` before the new Worker's first
  dispatch. The 422 fallback covers any mismatch.
- `main` has no required checks [O]. Merge only after the PR's Tests run is green with the Node tests actually
  executed, and deploy only from `main`.

**Rollback:**
- **Fast path:** `wrangler rollback <version-id>` (or the dashboard), back to the version recorded before the merge
  with `wrangler versions list --config cloudflare/wrangler.toml`.
  - A bare `wrangler rollback` does nothing useful here, because each deploy creates two versions (deploy, then
    `secret put`) [O docs].
  - Crons are not versioned, so they stay as they are.
- **Durable path:** revert the merge commit, which redeploys the old Worker.
- **Partial reverts are safe:** the old Worker never sends the note, and without `run-name` nothing is clearable.
  Workers Logs stay on after a code rollback.

**Post-merge checks** (these go into PROJECT_STATE "Next"):
- the first wake's `display_title` reads "Cloudflare wake-up";
- Workers Logs show `{"liveness":"idle"}` and the invocation's `cpuTime`;
- the first late or off-season wake prints `SKIP / - / no checkpoint due at …`.

#### 7. Owner decisions

1. **Threshold.** Approve T = 20 (`TOLERANCE_MINUTES["synthesis"]`) with the live-queue condition, over the PRD's
   example of 45, which loses a later valid checkpoint in 7 of 44 single-stall cases.
2. **"Past their window."** Accept it as "can no longer resolve the checkpoint it was dispatched for", including the
   OPEN_1M case cleared 15 min before its nominal tolerance ends.
3. **What gets cleared.** Clear only Cloudflare wakes (identified by `run-name`), and log but never clear stalled
   manual, `pages.yml` and other-branch runs. Or widen this.
4. **Force-cancel fallback.** Whether to add one for a wake whose normal cancel did not take effect. It would be
   safe for a run with no steps; it is not proposed now.
5. **Stale-checkout fix.** The `publish/` deploy guard (proposed) or checkout `ref: ${{ github.ref }}`. Also,
   whether it belongs in this PRD (F and invariant 9 as written need it) or in its own.
6. **Worker role change.** Approve that the Worker now reads run state and may cancel, and approve Workers Logs
   persistence.
7. **`GH_DISPATCH_TOKEN`.** Check it at github.com/settings/personal-access-tokens:
   - It should be one fine-grained token, scoped to `market-brief` only, with Actions read and write and Metadata
     read.
   - Note its expiry and its "last used" time. Fine-grained tokens default to 30 days, and the secret was last
     replaced around 2026-09-09. If the default was taken, dispatches fail from around Oct 9.
   - If it is a classic or `gh` token, rotate it. A rotated value reaches the Worker only through a deploy run.
8. **Cloudflare plan.** Confirm it. The Free plan allows 10 ms of CPU per cron run, which sets the listing budget.
9. **The #203 cancel.** Confirm who cancelled it and how, and consider the GitHub Support ticket from section 2.
10. **Merge = deploy.** Accept it, record the current Worker version id before merging, and deploy only from `main`.
11. **Branch.** This session works on `ccr-840c4634-k9bz7y`, the branch the session was given, not
    `fix/scheduler-liveness`.

#### Discrepancies with §1/§2 (recorded per §0.6)

1. **#203 was picked up.** Two jobs were skipped at 17:02:05 and a deployment was created; only `brief` never got a
   runner. §2 inferred the opposite.
2. **#205 and #206** concluded `cancelled`, not Success.
3. **#207 was not cancelled.** It ran at 02:07:46 after #203's cancel and resolved SKIP, the normal outcome of the
   21:01 slot. §1's "each later wake … cancelled" holds for #204–#206; the CLOSE_1M wake was #206.
4. **#203 was cancelled** at 02:07:36Z (the run updated at 02:07:37). Who cancelled it is not observable.
5. **A Cloudflare wake outside its window does not log `SKIP / … / outside checkpoint window`.** `resolve-scheduled`
   puts a bare SKIP into the step output and writes nothing to the log. That line appears only if the window closes
   between the resolve step and the `schedule` step.
6. **Idempotency across runners rests on the continuity bundle alone.** The checkpoint marker is runner-local, and
   the published-page check reads the dispatch-time checkout.
7. **`pages.yml` runs more often than "never".** It also runs on changes to itself and on `workflow_dispatch`, and
   last ran 2026-09-26 from an owner push. `schedule.yml` runs from any branch also join the group.
8. **A run can hold the group with no job running** (#203). Also, `concurrency.queue: max` now exists.
9. **#204's concurrency annotation** can't be read through the API (no check runs, HTML blocked). Its 59 m 56 s is
   confirmed.
10. **§7:** merging redeploys the Worker automatically.
11. **Stale docs:** ARCHITECTURE and SYNTHESIS_COST_CONTAINMENT still describe the 2026-09-10 hold as "in force",
    which PROJECT_STATE records as lifted.

### Owner ruling, 2026-10-01: Phase 2 approved

Clarification for the record: the Oct 1 OPEN_30M failure is a separate validator incident, not caused by this
branch (it is not on `main`). PREMARKET and OPEN_1M succeeded; OPEN_30M collected, sent one Opus synthesis
request and failed closed ("SMALL observations cannot anchor a watch"); its paid-attempt artifact was uploaded.
This PR does not change the validator, retry that synthesis, or touch that issue.

Rulings:
1. The 20-minute threshold with the live-queue condition, tied to the 20-minute synthesis window: approved.
2. Clearing the prior OPEN_1M wake at the next tick: approved. Terminology: the wake has been **superseded by a
   later checkpoint and, if started now, can no longer resolve the checkpoint associated with its original wake**.
   Tests prove this across normal sessions, DST transitions and early closes.
3. Clear only verified Cloudflare wakes. Manual dispatches, `pages.yml`, other branches and other workflows are
   logged and left alone.
4. No force-cancel. A normal cancel that is not effective within the bounded wait logs `cancel_not_effective`;
   the Worker dispatches as designed and leaves the uncertainty visible.
5. The stale-page deploy guard (publish content versus `main`) is in this slice. Checking out the newest `main`
   in a running job is rejected: it mixes dispatch-time workflow and code with a later repository state.
6. The Cloudflare Worker is the recovery location, stateless: inspect, qualify, normal-cancel, verify, dispatch,
   log. No Durable Object, database, lock service or watchdog workflow.
7. Persisted Worker observability: approved; narrow structured logs, never the token, authorization headers or
   unneeded GitHub payloads.
8. `GH_DISPATCH_TOKEN`: the Actions write permission used for dispatch is the class cancellation needs; verify the
   configured credential before deployment without exposing it. No token-lifecycle machinery.
9. Stay inside the Free-plan CPU and subrequest budget; if measurements violate it, stop and report.
10. GitHub Support is optional. The root cause stays "root cause not observable"; the incident IDs are kept in §2.
11. The session branch `ccr-840c4634-k9bz7y` is accepted.

Safety predicate, as ruled: a run is cleared only when all required evidence is present; unknown, malformed or
missing GitHub state is not clearable; the candidate and its jobs are re-read before the cancel; any runner
assignment, started step, completed work, other branch, manual identity, `run_attempt` > 1 or ambiguous
ownership leaves it alone. The holder is not assumed to be the oldest unfinished run: the Worker establishes it
from observed group, run and job state, and when it cannot, it logs `unknown` or `left` and cancels nothing.
Pending members are cleared before the holder only when evidence shows they are Cloudflare wakes in this same
lane and are themselves safe to clear.

### Phase 2 slice plan

Tests first in each slice; `.venv/bin/python3 -m pytest -q`, `.venv/bin/ruff check src tests`, the Worker's Node
tests and `git diff --check` after each.

1. **Worker (A, C, F):** a Node harness with a fake GitHub, the Worker tests and the Sep 30, EST, early-close,
   green-day and slow-queue replays; then `cloudflare/src/index.js` and `[observability]` in `wrangler.toml`.
2. **Workflow and CLI (B, C, D, E):** `run-name`, the `liveness_recovery` input and its one-line log, the
   `resolve-scheduled` SKIP reason, the publish-versus-`main` deploy guard, and the supersession tests.
3. **Docs (G):** `cloudflare/README.md`, the ARCHITECTURE scheduler paragraph, one DECISIONS bullet, PROJECT_STATE.
4. Measure the Worker's request and CPU budget; independent adversarial review (false-positive cancellation,
   paid-synthesis duplication, continuity rollback, stale-page deployment, the re-read/cancel race); stop for
   the owner before merge or deploy.

### Phase 2 report, 2026-10-01: implemented; waiting for the owner before merge or deploy

**Commits** on `ccr-840c4634-k9bz7y` (pushed to that branch only; nothing merged, deployed or dispatched):

| Commit | What it does |
|---|---|
| 442cb84 | docs: the PRD body corrected; Phase 2 approved; owner rulings and slice plan |
| ff7bdb1 | The Worker's liveness step, `[observability]`, and the Node-harness tests |
| 15b2967 | `run-name`, the `liveness_recovery` note, the `resolve-scheduled` SKIP reason, the deploy guard |
| 42f4d27 | docs: README, ARCHITECTURE, one DECISIONS bullet, PROJECT_STATE |
| bb2e6fa | Caps the group members the Worker inspects, which bounds its subrequests |
| 8f488a5 | Fixes from the adversarial review (below) |

**Files changed** (`git diff --stat c226967..HEAD`; 14 files):
- **Worker:** `cloudflare/src/index.js`, `cloudflare/wrangler.toml`, `cloudflare/README.md`.
- **Workflow and CLI:** `.github/workflows/schedule.yml`, `src/market_brief/cli.py`.
- **Tests:**
  - `tests/test_cloudflare_liveness.py` (new);
  - `tests/test_cloudflare_scheduler.py`, `tests/test_continuity_restore.py`, `tests/test_schedule.py`,
    `tests/test_synthesis_attempts.py`.
- **Docs:** `DECISIONS.md`, `PROJECT_STATE.md`, `docs/ARCHITECTURE.md`, this PRD.

Untouched: `pages.yml`, `continuity.py`, `schedule.py`, the validator, prompts and synthesis.

**Tests:**
- `.venv/bin/python3 -m pytest -q`: 904 passed, 12 skipped. The baseline was 775 passed; the skips are the same
  headless-Chrome checks.
- `ruff check src tests`: clean. `git diff --check`: clean.
- The Worker's Node tests (84, in `tests/test_cloudflare_liveness.py`) ran here on Node 22. They have not run in CI
  yet, because Tests runs only on pull requests and pushes to `main`. Under CI they fail rather than skip if Node is
  missing.
- Every new Worker test was checked to fail against the previous Worker:
  - 65 of 75 fail against the c226967 Worker;
  - the 7 review tests fail against bb2e6fa;
  - the 3 new guard tests fail against the previous guard.

**Replays** (the real Worker against a fake GitHub lane, with runs resolved by the real `scheduled_checkpoint`):

| Case | Result |
|---|---|
| Sep 30 (#203 stalled before runner assignment) | Cleared at the 18:01 tick; HOURLY_1300 stays missing; HOURLY_1400, HOURLY_1500 and CLOSE_1M publish; exactly one cancel; nothing backfilled |
| OPEN_1M wake stalled, EDT, EST and both DST-change Mondays | Superseded and cleared at the next tick; OPEN_30M runs |
| Early close (2026-11-27), the H12 wake stalled | CLOSE_1M runs |
| Green days | No cancel |
| Slow but live queue | At most one cancel; cost pinned (see residual risk 1) |
| Executing synthesis | Never cancelled across 12 ticks |
| Rejected paid synthesis; double-fired tick; a runner arriving before the cancel | Exactly one OPEN_30M provider request |
| Stale checkout; tag named `main`; untracked file in `publish/` | No deploy |
| Failed, refused, gateway-error and ignored cancels | Reported, never as `cleared` |
| Malformed, hung or unreadable GitHub answers | Nothing cancelled; the wake is still dispatched |

**Worker budget.** Measured with Node 22 on the first invocation, with GitHub answers prepared before timing (5 runs
each):

| Tick | Requests | Cold CPU | Warm CPU |
|---|---|---|---|
| Quiet | 6 | 2.5–3.0 ms | 0.2 ms |
| Stall | 14 | 6.2–7.7 ms | 1.2 ms |
| Worst (holder + 2 pending) | 23 | 7.7–8.9 ms | 1.9 ms |

- For comparison, the original Worker costs 0.7–2.2 ms, and a control making the same requests with no logic costs
  about 4.2 ms. That control cost is Node's own `fetch`/`Response`, which Workers implements natively.
- The theoretical maximum is 41 subrequests (limit 50). All measured cases are inside the Free plan's 10 ms CPU and
  50 subrequests, but the stall tick's margin is not wide in Node. Confirm with Workers Logs' `cpuTime` after deploy.

**Adversarial review.** Five lenses, 19 agents, each finding re-checked by a skeptical verifier. **No blocker or
major finding.**

| Lens | Confirmed findings, and what was done |
|---|---|
| False-positive cancellation | **Fixed:** a manual smoke test, continuity check, commissioning run or experiment with the wake box ticked got the wake name and could be cleared. `run-name` now requires exactly the Worker's inputs. **Fixed:** a pending wake was cancelled before the holder was re-checked; an inverted runner wait counted as a live queue. **Pinned and reported:** the slow-but-live-queue cost (residual risk 1). |
| Paid-synthesis duplication | No duplication found. **Fixed:** the scenario-D test did not exercise recovery. It is replaced by two tests that drive the real Worker (a double-fired tick; a runner arriving before the cancel lands) into the paid-attempt model; exactly one OPEN_30M provider request either way. |
| Continuity rollback | No rollback by the change. **Fixed:** the guard's fetch had no step timeout; it now has two minutes, so a hang fails the run (restorable) instead of reaching the job timeout's cancel (ineligible). **Pre-existing, residual:** re-running a `schedule.yml` run can make its bundle ineligible (residual risk 5). |
| Stale-page deployment | **Fixed:** the guard now fetches `refs/heads/main`, so a tag named `main` cannot stand in for the branch. It compares the `publish/` tree that is actually uploaded, untracked files included. **Pre-existing, residual:** `pages.yml` is unguarded; an owner page push can be replaced by a SKIP wake (residual risks 3–4). |
| Re-read and cancel race | **Fixed:** a runner arriving after the re-read was reported as "never started". The Worker now reads the jobs after the cancel and reports `cancelled_after_start` (or `cancelled_unverified`). **Fixed:** a cancel that lands but answers 5xx is now verified. **Fixed:** verification reads are no longer refused by the sweep budget. **Fixed:** a re-read slower than 2 s is not acted on. The PRD's "~100 ms" was a typical figure, not a bound; the bound is now the cancel request itself (≤ 10 s), plus GitHub's asynchronous handling after a 202, which nothing can bound. |

**Residual risks that changed from Phase 1:**
1. **The slow-but-live-queue cost is larger than Phase 1 reported.**
   - Phase 1 said "a slow day gets at most one cancel" and reported only mean costs. One cancel still holds. But the
     first wake still waiting at the next tick after a prompt run is cleared, and in a long, steady slowdown that
     one cancel delays every later run by a tick.
   - Pinned against no recovery:
     - 2026-09-30 with a 61-minute runner wait: 4 checkpoints publish against 7 (OPEN_30M and CLOSE_1M lost).
     - 2026-12-01 at 61 minutes: 4 against 8 (both syntheses and CLOSE_1M lost).
     - At 35 minutes: one checkpoint fewer.
   - With mixed latencies, cancels can recur within a day (the review measured up to 5 a day with alternating
     10 s / 70 min waits, while publishing more than no recovery).
   - This is ruling 1's trade, now measured. No invariant is weakened: nothing is paid twice, rolled back or
     overwritten.
2. **The re-read/cancel window.** It is bounded by the cancel request (≤ 10 s) and by GitHub's processing of an
   accepted cancel. When a runner wins the race, the outcome is visible (`cancelled_after_start`), and the attempt
   record the run uploaded still prevents a second payment.
3. **`pages.yml` is unguarded (pre-existing).** An owner dispatch or re-run of it, or a late push event, can deploy
   an older page until the next run that deploys. A fix would add the same guard to `pages.yml`; that is out of
   this slice.
4. **An owner page push can be replaced (pre-existing).** A wake's dispatch replaces a pending `pages.yml` run, and a
   wake that resolves SKIP deploys nothing, so the page can stay behind `main` until the next checkpoint publishes.
   Re-dispatch `pages.yml` by hand if needed.
5. **Re-runs and continuity (pre-existing).** Re-running a `schedule.yml` run makes its earlier attempt's continuity
   artifact ineligible once that run is cancelled or still running. Dispatch a fresh run instead.
6. **The `Liveness:` line in the run log is best effort.** A later dispatch can replace the run that carries it.
   Workers Logs is the authoritative record.
7. **`run-name` is set at run creation (inferred).** If it is not, nothing is ever cleared, which is fail-safe and
   logged as `left`. Check it once after merge.

**Before merge or deploy (owner steps):**
1. Record the deployed Worker version (`wrangler versions list --config cloudflare/wrangler.toml`) for
   `wrangler rollback <id>`. A bare rollback is a no-op, because each deploy makes two versions.
2. Confirm `GH_DISPATCH_TOKEN` can still dispatch (Actions read and write on this repository), without exposing it.
3. Open the PR and let Tests run, with the Node tests executed. Merging to `main` deploys the Worker.
4. After merge:
   - the first wake's run is named `Cloudflare wake-up`;
   - Workers Logs show `{"liveness":"idle"}` and the tick's `cpuTime`;
   - the 21:01 UTC summer wake logs `SKIP / - / no checkpoint due at …`.

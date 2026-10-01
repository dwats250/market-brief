# PRD: Scheduler liveness after a stuck wake (defect P0)

**Status:** Phase 1 (investigate and design) approved by the owner, 2026-09-30. Phase 2 (implement) waits for the owner's review of your Phase 1 report.
**Base:** `main` @ c226967 (or later; re-verify)
**Branch:** `fix/scheduler-liveness`
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

On Sep 30, the HOURLY_1300 wake (run #203) went to `queued` and never started. Because the workflow serializes on one concurrency group, every later wake waited behind it and was cancelled by the next one to arrive. HOURLY_1300, HOURLY_1400, HOURLY_1500 and CLOSE_1M never published, and Sep 30 has no close snapshot or handoff.

The serialization is right. It protects continuity, publication order and paid-synthesis dedupe, and stays. The defect is that **one wake that never starts can hold the group indefinitely**, so a single platform hiccup silently costs the rest of the day, and possibly the next one.

**Goal:** if one dispatch stalls before it starts, later still-valid checkpoints and the close publish anyway, without a second paid synthesis, without two publishers racing, and with a log line saying what was recovered and why.

---

## 2. Verified facts (public run pages and code, read 2026-10-01 ~01:40 UTC)

### Runs
- **#203** (`36748511695`), the HOURLY_1300 wake: created 17:02 UTC Sep 30 by `workflow_dispatch` on c226967. Still **queued** about 8.5 hours later. All three jobs (`continuity-check`, `cloudflare-smoke`, `brief`) show queued and 0s, including the two whose `if:` should have evaluated false. That suggests the run itself was never picked up, not that a runner was missing for `brief`. This is **inferred**; confirm it with the jobs API.
- **#204** (`36755617810`): created 18:01 UTC, **cancelled** after 59m56s with the annotation "Canceling since a higher priority waiting request for market-brief-pages exists".
- **#205** and **#206**: about 1h0m each. The workflow list page showed them as "Success", but #204 showed the same and was actually cancelled. Verify both with the API.
- **#207** (`36776686174`): pending at read time.
- The owner has been asked to cancel #203 manually so the next session isn't blocked. Record whether it was cancelled, by whom, and when.

### Configuration
- **`schedule.yml`:** workflow-level `concurrency: { group: market-brief-pages, cancel-in-progress: false }`. The `brief` job runs on `ubuntu-latest` with `environment: github-pages`. Its last two steps record the paid-attempt artifact under `always()`.
- **`pages.yml`:** the same group and the same `github-pages` environment. It triggers on pushes to `publish/**`. Publish commits are pushed with the default token, which does not trigger workflows, so this normally never runs.
- **Cloudflare Worker** (`cloudflare/src/index.js`, 43 lines): dispatches `schedule.yml` with `cloudflare_wakeup=true` at the `wrangler.toml` crons, `1 13-21 * * MON-FRI` and `31 13-14 * * MON-FRI` (UTC). It does not read run state.

### Scheduler semantics
- **Tolerance:** `TOLERANCE_MINUTES = {"synthesis": 20, "refresh": 45, "close": 45}`. A wake outside its checkpoint's window logs `SKIP / … / outside checkpoint window` and exits 0.
- **Idempotency:** a checkpoint marker, the published page's `data-session-date` / `data-checkpoint`, or the continuity bundle prevents re-running a completed checkpoint.
- **Paid dedupe:** before a synthesis, `earlier_attempt` checks the durable attempt record. If it can't check, it refuses to call the provider.

### Platform
- GitHub's concurrency allows one running and one pending run per group; a newer pending run cancels the older pending one.
- `timeout-minutes` limits a job only after it starts. Nothing in the workflow bounds how long a run may sit queued.

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

**2026-10-01 — Phase 1 in progress (read-only).** Evidence is being collected from the GitHub API; the full
Phase 1 report replaces this note when it is verified.

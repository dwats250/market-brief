# Cloudflare scheduler

This Worker is a wake-up layer. Cloudflare Cron sends a
`workflow_dispatch` request to the existing `schedule.yml` workflow; the
Python scheduler remains authoritative for exchange sessions, holidays, early
closes, checkpoint resolution and kind (synthesis, refresh or close), and
idempotency. Checkpoints are anchored to the NYSE session (open −30m, open +1m,
open +30m, exchange-clock hours, close +1m) and displayed in Pacific time. The
crons in `wrangler.toml` are wake candidates for both New York seasons: one
minute past each hour 13:00–21:00 UTC (premarket, the opening structure update,
the hourly refreshes, and the close +1M snapshot after regular and early closes)
and :31 for the open +1M refresh. No two wakes fall inside one minute of each
other, so a checkpoint is never resolved twice before its publish has landed. A
wake with nothing due is a SKIP; in particular the :31 candidate that belongs to
the other New York season (10:31 ET or 8:31 ET) is a SKIP, never a second attempt
at the opening-structure synthesis, because a synthesis checkpoint is due only
within twenty minutes of its scheduled minute. The deploy workflow redeploys the
Worker whenever `cloudflare/**` changes on `main`, so merging a Worker change
deploys it; deploy only from `main`.

Before each dispatch the Worker also keeps the scheduler live. The workflow's
one concurrency group (`market-brief-pages`) runs one run at a time, so a run
that holds the group without ever starting holds it indefinitely (2026-09-30,
run #203). The Worker reads the group's unfinished runs and normal-cancels the
run holding it only when every piece of evidence is present: it is this
repository's own `schedule.yml` dispatch on `main`, first attempt, named
"Cloudflare wake-up" by the workflow's `run-name`; no job ever had a runner or
a step; it is at least twenty minutes old (`TOLERANCE_MINUTES["synthesis"]`),
which it first is at the next wake, when a later checkpoint has superseded it;
and the run before it got a runner within those twenty minutes. Clearing
therefore never repeats in a row, so a slow but live queue is never starved,
though its first wake still waiting at the next tick after a prompt run is
cleared like a stalled one. The holder is the one unfinished run of the group
with jobs. Anything unknown, malformed or ambiguous cancels nothing; manual
dispatches (smoke tests, continuity checks, commissioning and experiments
included), `pages.yml`, other branches and started runs are never cancelled;
there is no force-cancel. The candidate is re-read just before the cancel, and
the cancel is verified, including whether a runner reached the run first.
Every decision is one JSON log line (`{"liveness": "idle" | "left" | "unknown"
| "cleared" | "cancelled_after_start" | "cancelled_unverified" |
"already_completed" | "cancel_failed" | "cancel_not_effective" |
"note_rejected", …}`), persisted by Workers Logs, which is the authoritative
record. An outcome is also passed to the dispatched run as the
`liveness_recovery` input, which its log prints as `Liveness: …`; that copy is
best effort (a later dispatch can replace the run that carries it). The wake is
always dispatched.

The Worker requires the Cloudflare deployment credentials already configured
in GitHub Actions and a separate `GH_DISPATCH_TOKEN` repository secret.
That token is installed as a Cloudflare Worker secret by the deployment
workflow. It needs this repository's Actions permission (read and write, which
dispatching already requires and which also covers reading and cancelling runs)
and nothing else. No market data or synthesis work runs in the Worker.

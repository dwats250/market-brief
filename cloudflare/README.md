# Cloudflare scheduler

This Worker is only a wake-up layer. Cloudflare Cron sends a
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
Worker whenever `cloudflare/**` changes on `main`.

The Worker requires the Cloudflare deployment credentials already configured
in GitHub Actions and a separate `GH_DISPATCH_TOKEN` repository secret.
That token is installed as a Cloudflare Worker secret by the deployment
workflow and must be limited to dispatching this repository's Actions
workflow. No market data or synthesis work runs in the Worker.

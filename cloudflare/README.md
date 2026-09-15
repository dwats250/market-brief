# Cloudflare scheduler

This Worker is only a wake-up layer. Cloudflare Cron sends a
`workflow_dispatch` request to the existing `schedule.yml` workflow; the
Python scheduler remains authoritative for Pacific time, exchange sessions,
holidays, early closes, checkpoint resolution and kind (synthesis, refresh or
close), and idempotency. The crons in `wrangler.toml` are wake candidates for
both Pacific seasons: one minute past each hour 13:00–21:00 UTC (premarket, the
opening structure update, the hourly refreshes, and the close +1M snapshot after
regular and early closes) and :31 for the open +1M refresh. No two wakes fall
inside one minute of each other, so a checkpoint is never resolved twice before
its publish has landed. A wake with nothing due is a SKIP. The deploy workflow
redeploys the Worker whenever `cloudflare/**` changes on `main`.

The Worker requires the Cloudflare deployment credentials already configured
in GitHub Actions and a separate `GH_DISPATCH_TOKEN` repository secret.
That token is installed as a Cloudflare Worker secret by the deployment
workflow and must be limited to dispatching this repository's Actions
workflow. No market data or synthesis work runs in the Worker.

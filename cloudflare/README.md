# Cloudflare scheduler

This Worker is only a wake-up layer. Cloudflare Cron sends a
`workflow_dispatch` request to the existing `schedule.yml` workflow; the
Python scheduler remains authoritative for Pacific time, exchange sessions,
holidays, early closes, checkpoint resolution, and idempotency.

The Worker requires the Cloudflare deployment credentials already configured
in GitHub Actions and a separate `GH_DISPATCH_TOKEN` repository secret.
That token is installed as a Cloudflare Worker secret by the deployment
workflow and must be limited to dispatching this repository's Actions
workflow. No market data or synthesis work runs in the Worker.

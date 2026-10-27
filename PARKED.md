# Parked: intraday Cboe yield block (`feat/intraday-yields`, 3b7a026)

Parked 2026-10-27 (Claude, owner work permit WP2). Do not merge or rebase this branch as is.

- **Why parked.** The Lab already records the same four Cboe yield indices (^IRX/^FVX/^TNX/^TYX) at every checkpoint, so this lane would duplicate an observation the project already has, on the publishing side. No owner decision asks for it on the Brief page.
- **Stale base.** The branch forks at `f705635`, before the prior-close fix `60a0f91` (#62). Its own diff touches `collect.py` but not the prior-close code. A three-way merge into `main` at `f246a1e` was simulated on 2026-10-27: no conflicts, the fix kept, 1,286 tests passed. A rebase or a file-level checkout from this branch could still revert the fix; `tests/test_prior_close_baseline.py` on `main` is the guard.
- **Cboe prior close.** `prev_day_close` on the yield indices was the previous session's final value at the 09:00 ET Lab checkpoint on Oct 23 and on Monday Oct 26 (Lab checkpoint files), so this lane does not repeat the Alpaca `prevDailyBar` defect. Two observations only.
- **To revive.** Owner decision first; then rebase onto current `main` and re-run the full suite and the prior-close tests.

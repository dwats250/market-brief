from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_cloudflare_wakeup_preserves_python_scheduler_authority():
    workflow = (ROOT / ".github/workflows/schedule.yml").read_text()
    assert "cloudflare_wakeup" in workflow
    assert "python -m market_brief resolve-scheduled" in workflow
    assert "python -m market_brief schedule --checkpoint" in workflow
    assert "inputs.cloudflare_wakeup != true" in workflow


def test_cloudflare_worker_dispatches_only_a_wakeup():
    worker = (ROOT / "cloudflare/src/index.js").read_text()
    assert 'cloudflare_wakeup: "true"' in worker
    assert 'ref: "main"' in worker
    assert "status: response.status" in worker
    assert "GH_DISPATCH_TOKEN" in worker


CRONS = ('"1 13-21 * * MON-FRI"',  # premarket, opening structure, hourly refreshes, close +1M; PDT and PST
         '"31 13-14 * * MON-FRI"')  # open +1M refresh


def test_cloudflare_crons_include_both_dst_candidates_hourly_refreshes_and_early_close_candidates():
    config = (ROOT / "cloudflare/wrangler.toml").read_text()
    for cron in CRONS:
        assert cron in config
    assert config.count("* * MON-FRI") == len(CRONS)  # no other wake candidates


def wake_minutes():
    """Every UTC (hour, minute) the configured crons fire, read from the expressions above."""
    minutes = []
    for cron in CRONS:
        minute, hours = cron.strip('"').split(" ")[:2]
        start, end = (int(part) for part in hours.split("-"))
        minutes += [(hour, int(minute)) for hour in range(start, end + 1)]
    return minutes


def test_no_two_wakes_resolve_the_same_checkpoint_before_its_publish_can_land():
    """A wake resolved one minute after another would re-run the same checkpoint from a checkout that
    predates the first run's publish (dispatch pins the commit), so no two wakes may resolve to one
    checkpoint within a few minutes of each other, on a normal day or an early close, in either season."""
    from datetime import datetime, timedelta, timezone

    from market_brief.schedule import scheduled_checkpoint
    for day in ("2026-07-06", "2026-01-12", "2026-11-27", "2026-12-24"):
        resolved = []
        for hour, minute in sorted(wake_minutes()):
            now = datetime(*map(int, day.split("-")), hour, minute, tzinfo=timezone.utc)
            checkpoint = scheduled_checkpoint(now)
            if checkpoint:
                resolved.append((now, checkpoint))
        assert resolved, day
        for (earlier, first), (later, second) in zip(resolved, resolved[1:]):
            assert not (first == second and later - earlier < timedelta(minutes=5)), (day, first, earlier, later)
        # Every synthesis and the close are still reached.
        names = {checkpoint for _, checkpoint in resolved}
        assert {"PREMARKET", "OPEN_1M", "OPEN_30M", "CLOSE_1M"} <= names, (day, names)

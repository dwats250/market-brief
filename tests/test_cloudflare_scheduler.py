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
    assert 'inputs: { cloudflare_wakeup: "true" }' in worker
    assert 'ref: "main"' in worker
    assert "checkpoint" not in worker
    assert "GH_DISPATCH_TOKEN" in worker


def test_cloudflare_crons_include_both_dst_candidates_and_early_close_candidates():
    config = (ROOT / "cloudflare/wrangler.toml").read_text()
    for cron in (
        '"0 13-15 * * MON-FRI"',
        '"31 13-14 * * MON-FRI"',
        '"7 19-20 * * MON-FRI"',
        '"1 17,18,20,21 * * MON-FRI"',
    ):
        assert cron in config

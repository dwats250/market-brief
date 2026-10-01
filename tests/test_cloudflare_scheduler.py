from pathlib import Path

import pytest

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


# --- scheduler liveness: the Worker's threshold, its wake identity and the note it hands the next run ---------

def workflow_text():
    return (ROOT / ".github/workflows/schedule.yml").read_text()


def worker_constant(name):
    import re
    return re.search(rf"^const {name} = (.+);$", (ROOT / "cloudflare/src/index.js").read_text(), re.M).group(1)


def test_a_stalled_wake_is_cleared_at_the_next_wake_and_never_earlier():
    """The Worker clears a never-started wake once it is the synthesis window old. Wakes are at least thirty minutes
    apart and a dispatch lands within about a minute of its cron, so that age is reached first at the next wake."""
    from market_brief.schedule import TOLERANCE_MINUTES
    threshold = int(worker_constant("SUPERSEDED_MINUTES"))
    assert threshold == TOLERANCE_MINUTES["synthesis"] == min(TOLERANCE_MINUTES.values())
    minutes = sorted(hour * 60 + minute for hour, minute in wake_minutes())
    spacing = min(later - earlier for earlier, later in zip(minutes, minutes[1:]))
    assert threshold <= spacing - 2  # a dispatch landing up to two minutes after its cron is still cleared on time


def superseded_sample():
    """One session a month across the calendar, the Mondays after each DST change, and every early close."""
    import exchange_calendars as xcals
    cal = xcals.get_calendar("XNYS")
    sessions = [s.date().isoformat() for s in cal.sessions_in_range("2026-01-02", "2027-09-30")]
    monthly = {}
    for day in sessions:
        monthly.setdefault(day[:7], day)
    early = {d.date().isoformat() for d in cal.early_closes if "2026-01-02" <= d.date().isoformat() <= "2027-09-30"}
    return sorted(set(monthly.values()) | early | {"2026-03-09", "2026-11-02", "2027-03-15", "2026-09-30"})


@pytest.mark.parametrize("day", superseded_sample())
def test_a_stalled_wake_is_superseded_by_a_later_checkpoint_at_the_next_wake(day):
    """At the next wake, a never-started wake started then could no longer resolve the checkpoint associated with
    its original wake: a later checkpoint has superseded it (for OPEN_1M, before its own 45-minute tolerance ends).
    A fresh wake resolves whatever is due when it starts, so clearing the stalled one loses nothing it could do."""
    from datetime import datetime, timedelta, timezone

    from market_brief.schedule import scheduled_checkpoint
    ticks = [datetime(*map(int, day.split("-")), hour, minute, tzinfo=timezone.utc)
             for hour, minute in sorted(wake_minutes())]
    for tick, following in zip(ticks, ticks[1:]):
        for start in (timedelta(0), timedelta(minutes=2)):
            own = scheduled_checkpoint(tick + start)
            if own:
                for late in (timedelta(0), timedelta(minutes=2)):
                    assert scheduled_checkpoint(following + late) != own, (day, tick, own)


def test_the_worker_clears_only_wakes_the_workflow_names_as_its_own():
    workflow = workflow_text()
    assert workflow.startswith("name: Scheduled Market Brief\n")
    assert ("\nrun-name: ${{ inputs.cloudflare_wakeup == true && 'Cloudflare wake-up' || 'Scheduled Market Brief' }}\n"
            in workflow)
    assert worker_constant("WAKE_TITLE") == '"Cloudflare wake-up"'
    assert worker_constant("SCHEDULE_PATH") == '".github/workflows/schedule.yml"'


def test_the_brief_job_can_be_cancelled_before_it_starts():
    """A normal cancel skips a job whose `if:` still holds on cancellation; a status function there would keep a
    never-started brief job, and the group, alive."""
    import re
    brief = workflow_text().split("\n  brief:\n", 1)[1].split("\n    steps:\n", 1)[0]
    condition = re.search(r"^    if: (.+)$", brief, re.M).group(1)
    assert not re.search(r"\b(always|success|failure|cancelled)\s*\(", condition), condition


def test_the_recovery_note_is_declared_and_only_printed():
    workflow = workflow_text()
    assert ("      liveness_recovery:\n"
            "        description: Set by the Cloudflare wake-up when it cleared a stalled run; printed in the log "
            "only. Leave empty\n"
            "        required: false\n"
            '        default: ""\n'
            "        type: string\n") in workflow
    uses = [line.strip() for line in workflow.splitlines() if "liveness_recovery" in line]
    assert uses == ["liveness_recovery:", "LIVENESS_RECOVERY: ${{ inputs.liveness_recovery }}"]
    script = resolve_step_script()
    assert "$LIVENESS_RECOVERY" in script and "${{" not in script.split("python -m market_brief", 1)[0]


def resolve_step_script():
    text = workflow_text().split("      - name: Resolve checkpoint\n", 1)[1]
    script = text.split("        run: |\n", 1)[1]
    lines = []
    for line in script.splitlines():
        if line and not line.startswith("          "):
            break
        lines.append(line[10:])
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize(("note", "resolved"), [("", "HOURLY_1400"), ("cleared run 1 (#203)", "SKIP")])
def test_the_resolve_step_prints_the_note_as_one_line_and_resolves_as_before(tmp_path, note, resolved):
    import os
    import subprocess
    stub = tmp_path / "bin" / "python"
    stub.parent.mkdir()
    stub.write_text(f'#!/bin/sh\necho "SKIP / - / no checkpoint due" >&2\necho "{resolved}"\n')
    stub.chmod(0o755)
    hostile = note and note + "\n::stop-commands::token\r$(touch pwned) `touch pwned`"
    output = tmp_path / "output"
    result = subprocess.run(["bash", "-e", "-c", resolve_step_script()], cwd=tmp_path, capture_output=True,
                            text=True, check=False,
                            env=dict(os.environ, PATH=f"{stub.parent}:{os.environ['PATH']}",
                                     GITHUB_OUTPUT=str(output), INPUT_CLOUDFLARE_WAKEUP="true",
                                     INPUT_COMMISSIONING="false", INPUT_CHECKPOINT="PREMARKET",
                                     EVENT_SCHEDULE="", LIVENESS_RECOVERY=hostile))
    assert result.returncode == 0, result.stderr
    printed = [line for line in result.stdout.splitlines() if line.startswith("Liveness: ")]
    if note:
        assert printed == ["Liveness: cleared run 1 (#203) ::stop-commands::token $(touch pwned) `touch pwned`"]
        assert not any(line.startswith("::") for line in result.stdout.splitlines())
    else:
        assert printed == []
    assert not (tmp_path / "pwned").exists()
    run = "false" if resolved == "SKIP" else "true"
    assert output.read_text() == f"checkpoint={resolved}\nlabel={resolved}\nrun={run}\n"

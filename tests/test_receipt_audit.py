"""Daily receipt audit: missed-wake detection from receipt artifacts and the NYSE calendar. No network: the GitHub API
is a fake keyed by request path."""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from market_brief import audit, cli
from market_brief.schedule import CHECKPOINTS

REPO = "owner/repo"
OWN = dict(repository=dict(id=7), head_repository=dict(id=7))
ALL = list(CHECKPOINTS)
# 2026-11-23 Monday (EST, regular close 16:00 ET = 21:00 UTC); 2026-11-27 Friday after Thanksgiving (13:00 ET close);
# 2026-11-26 Thanksgiving (closed); 2026-11-28 Saturday.
MON, EARLY, HOLIDAY, SATURDAY = "2026-11-23", "2026-11-27", "2026-11-26", "2026-11-28"
EVENING = datetime(2026, 11, 23, 23, 30, tzinfo=timezone.utc)


def utc(text):
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def receipt(session, checkpoint, run_id, branch="main", expired=False, created=None):
    return dict(name=f"market-brief-receipt-{session}-{checkpoint}-{run_id}", expired=expired,
                created_at=created or f"{session}T20:00:00Z", workflow_run=dict(id=run_id, head_branch=branch))


CREATED = {}  # run id -> the time its (fake) run was created; set by full_day / receipt(created_run=...)
SCHEDULE = ".github/workflows/schedule.yml"


def run_details(run_id, **change):
    return dict(dict(status="completed", conclusion="success", path=SCHEDULE, created_at=CREATED.get(run_id), **OWN),
                **change)


class FakeGitHub:
    def __init__(self, artifacts, runs=None, fail=False):
        self.artifacts, self.runs, self.fail, self.requests = artifacts, runs or {}, fail, []

    def __call__(self, request):
        path = request[0]
        self.requests.append(path)
        if self.fail:
            raise ValueError("GitHub API request failed")
        if "/actions/artifacts" in path:
            page = int(path.rsplit("page=", 1)[1])
            return dict(artifacts=self.artifacts[(page - 1) * 100:page * 100])
        run_id = int(path.rsplit("/", 1)[1])
        return self.runs.get(run_id) or run_details(run_id)


def woke(session, checkpoint, run_id, minutes=1):
    """A receipt whose run the wake created `minutes` after the checkpoint's scheduled minute."""
    from datetime import timedelta
    info = next(i for i in audit.expected_checkpoints(session)[0] if i["checkpoint"] == checkpoint)
    CREATED[run_id] = (datetime.fromisoformat(info["scheduled_at"]) + timedelta(minutes=minutes)).isoformat()
    return receipt(session, checkpoint, run_id)


def full_day(session, checkpoints, start=1000):
    return [woke(session, checkpoint, start + i) for i, checkpoint in enumerate(checkpoints)]


# --- calendar ----------------------------------------------------------------------------------------------------


def names(session):
    return [info["checkpoint"] for info in audit.expected_checkpoints(session)[0]]


def test_regular_session_expects_all_nine_checkpoints():
    expected, close = audit.expected_checkpoints(MON)
    assert [i["checkpoint"] for i in expected] == ALL
    assert close == utc("2026-11-23T16:01:00-05:00")


def test_early_close_drops_hourlies_at_or_after_the_close():
    _, close = audit.expected_checkpoints(EARLY)
    assert names(EARLY) == ["PREMARKET", "OPEN_1M", "OPEN_30M", "HOURLY_1100", "HOURLY_1200", "CLOSE_1M"]
    assert close == utc("2026-11-27T13:01:00-05:00")


@pytest.mark.parametrize("day", [HOLIDAY, SATURDAY])
def test_holiday_and_weekend_expect_nothing(day):
    assert audit.expected_checkpoints(day) == ([], None)


def test_summer_close_is_anchored_to_new_york_not_utc():
    expected, close = audit.expected_checkpoints("2026-07-15")
    assert names("2026-07-15") == ALL and close == utc("2026-07-15T16:01:00-04:00")


# --- which session a scheduled run audits ------------------------------------------------------------------------


def test_scheduled_evening_run_audits_todays_session():
    assert audit.audit_target(EVENING) == (MON, None)


def test_run_before_todays_close_plus_grace_audits_the_previous_session():
    assert audit.audit_target(utc("2026-11-24T16:30:00-05:00")) == (MON, None)


@pytest.mark.parametrize("late", ["2026-11-24T02:00:00-05:00", "2026-11-24T09:00:00-05:00"])
def test_delayed_schedule_still_audits_the_session_that_closed(late):
    assert audit.audit_target(utc(late)) == (MON, None)


def test_holiday_run_reaudits_the_session_before_it():
    assert audit.audit_target(utc("2026-11-26T18:30:00-05:00")) == ("2026-11-25", None)


def test_early_close_is_auditable_the_same_evening():
    assert audit.audit_target(utc("2026-11-27T18:30:00-05:00")) == (EARLY, None)


# --- complete / missing / late / holiday --------------------------------------------------------------------------


def test_complete_day_passes():
    code, lines = audit.audit(EVENING, FakeGitHub(full_day(MON, ALL)), REPO)
    assert code == 0 and "all 9 checkpoints" in lines[0]


def test_missing_wake_fails_and_names_the_checkpoint():
    artifacts = full_day(MON, [c for c in ALL if c != "HOURLY_1300"])
    code, lines = audit.audit(EVENING, FakeGitHub(artifacts), REPO)
    assert code == 1
    assert lines[0].startswith(f"MISSED / {MON} / 1 of 9") and lines[0].endswith("HOURLY_1300")
    assert "  HOURLY_1300: NO RECEIPT" in lines


def test_whole_day_without_wakes_fails():
    code, lines = audit.audit(EVENING, FakeGitHub([]), REPO)
    assert code == 1 and "9 of 9" in lines[0]


def test_late_wake_inside_its_window_still_counts():
    # A wake GitHub queued for half an hour, and a receipt artifact uploaded late, still prove the wake happened.
    artifacts = full_day(MON, ALL[:-1]) + [woke(MON, "CLOSE_1M", 2000, minutes=50)]
    artifacts[-1]["created_at"] = "2026-11-23T22:40:00Z"
    assert audit.audit(EVENING, FakeGitHub(artifacts), REPO)[0] == 0


@pytest.mark.parametrize("conclusion", ["failure", "cancelled"])
def test_failed_or_cancelled_run_receipt_counts_the_wake_happened(conclusion):
    artifacts = full_day(MON, ALL)
    assert audit.audit(EVENING, FakeGitHub(artifacts, {1008: run_details(1008, conclusion=conclusion)}), REPO)[0] == 0


@pytest.mark.parametrize("minutes", [-30, 120, 60 * 24 * -2])
def test_manual_run_outside_the_wake_window_never_hides_a_missed_wake(minutes):
    # A morning rerun of the close, a run before the scheduled minute, a weekend test labelled for Monday.
    artifacts = full_day(MON, ALL[:-1]) + [woke(MON, "CLOSE_1M", 2000, minutes=minutes)]
    code, lines = audit.audit(EVENING, FakeGitHub(artifacts), REPO)
    assert code == 1 and lines[0].endswith("CLOSE_1M")
    assert "  CLOSE_1M: NO WAKE (receipt only from run(s) outside the wake window: 2000)" in lines


def test_a_queued_wake_counts_by_its_start_time():
    # Created at the PREMARKET wake, queued behind it, started inside OPEN_1M's window and ran OPEN_1M.
    artifacts = full_day(MON, [c for c in ALL if c != "OPEN_1M"]) + [woke(MON, "OPEN_1M", 2000, minutes=-30)]
    runs = {2000: run_details(2000, run_started_at="2026-11-23T14:32:00Z")}
    assert audit.audit(EVENING, FakeGitHub(artifacts, runs), REPO)[0] == 0


def test_an_in_window_receipt_wins_over_an_outside_one():
    artifacts = [woke(MON, "CLOSE_1M", 2000, minutes=-120)] + full_day(MON, ALL)
    assert audit.audit(EVENING, FakeGitHub(artifacts), REPO)[0] == 0


def test_early_close_day_does_not_expect_late_hourlies():
    artifacts = full_day(EARLY, ["PREMARKET", "OPEN_1M", "OPEN_30M", "HOURLY_1100", "HOURLY_1200", "CLOSE_1M"])
    code, lines = audit.audit(utc("2026-11-27T18:30:00-05:00"), FakeGitHub(artifacts), REPO)
    assert code == 0 and "all 6 checkpoints" in lines[0]


def test_holiday_run_reaudits_the_complete_day_before_without_alarm():
    code, lines = audit.audit(utc("2026-11-26T18:30:00-05:00"), FakeGitHub(full_day("2026-11-25", ALL)), REPO)
    assert code == 0 and lines[0].startswith("AUDIT / 2026-11-25 / all 9")


def test_explicit_holiday_date_passes_without_calling_github():
    github = FakeGitHub([], fail=True)
    code, lines = audit.audit(EVENING, github, REPO, session_date=HOLIDAY)
    assert code == 0 and "not a trading session" in lines[0] and github.requests == []


def test_too_early_audit_never_raises_a_false_alarm():
    github = FakeGitHub([])
    code, lines = audit.audit(utc("2026-11-23T16:30:00-05:00"), github, REPO, session_date=MON)
    assert code == 0 and "not auditable before" in lines[0] and github.requests == []


# --- what counts as a receipt ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("change", [
    dict(branch="feat/x"), dict(expired=True),
])
def test_ineligible_artifacts_do_not_count(change):
    artifacts = full_day(MON, ALL[:-1]) + [dict(woke(MON, "CLOSE_1M", 2000), **change)]
    if "branch" in change:
        artifacts[-1]["workflow_run"] = dict(id=2000, head_branch=change["branch"])
    assert audit.audit(EVENING, FakeGitHub(artifacts), REPO)[0] == 1


@pytest.mark.parametrize("change", [
    dict(status="in_progress"), dict(path=".github/workflows/tests.yml"), dict(head_repository=dict(id=8)),
])
def test_runs_that_prove_nothing_do_not_count(change):
    artifacts = full_day(MON, ALL[:-1]) + [woke(MON, "CLOSE_1M", 2000)]
    assert audit.audit(EVENING, FakeGitHub(artifacts, {2000: run_details(2000, **change)}), REPO)[0] == 1


def test_receipt_of_another_session_or_mismatched_run_does_not_count():
    other = woke("2026-11-20", "CLOSE_1M", 2000)
    forged = woke(MON, "CLOSE_1M", 2001)
    forged["workflow_run"]["id"] = 2002
    assert audit.audit(EVENING, FakeGitHub(full_day(MON, ALL[:-1]) + [other, forged]), REPO)[0] == 1


def test_listing_pages_until_the_session_is_covered():
    filler = [receipt(MON, "PREMARKET", 5000 + i, created="2026-11-23T21:00:00Z") for i in range(100)]
    github = FakeGitHub(filler + full_day(MON, ALL))
    assert audit.audit(EVENING, github, REPO)[0] == 0
    assert sum("/actions/artifacts" in r for r in github.requests) == 2


def test_api_failure_raises_so_the_job_fails_visibly():
    with pytest.raises(ValueError):
        audit.audit(EVENING, FakeGitHub([], fail=True), REPO)


def test_a_listing_that_hits_the_page_cap_is_an_error_not_a_missed_wake():
    filler = [receipt(MON, "PREMARKET", 9000 + i, created="2026-11-23T21:00:00Z")
              for i in range(100 * audit.MAX_PAGES)]
    with pytest.raises(ValueError, match="incomplete"):
        audit.audit(EVENING, FakeGitHub(filler + full_day(MON, ALL)), REPO)


# --- CLI ----------------------------------------------------------------------------------------------------------


def test_cli_returns_one_on_a_missed_wake(monkeypatch, capsys):
    monkeypatch.setattr(audit, "utc_now", lambda: EVENING)
    monkeypatch.setattr(cli, "_gh_json", lambda request, runner=None: FakeGitHub([])(request))
    args = SimpleNamespace(repository=REPO, date=None, branch="main")
    assert cli.audit_receipts(args) == 1
    assert capsys.readouterr().out.startswith(f"MISSED / {MON}")


def test_cli_rejects_a_malformed_date_as_an_audit_error(capsys):
    args = SimpleNamespace(repository=REPO, date="23/11/2026", branch="main")
    assert cli.audit_receipts(args) == 2
    assert capsys.readouterr().out.startswith("AUDIT ERROR")


@pytest.mark.parametrize("failure", [ValueError("GitHub API request failed"),
                                     __import__("subprocess").TimeoutExpired("gh", 60)])
def test_cli_audit_errors_exit_two_never_one(monkeypatch, capsys, failure):
    monkeypatch.setattr(audit, "utc_now", lambda: EVENING)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)

    def broken(request, runner=None):
        raise failure
    monkeypatch.setattr(cli, "_gh_json", broken)
    assert cli.main(["receipt-audit"]) == 2
    assert "AUDIT ERROR" in capsys.readouterr().out


def test_cli_non_object_run_lookup_is_not_counted():
    artifacts = full_day(MON, ALL)
    code, _ = audit.audit(EVENING, FakeGitHub(artifacts, {1008: ["not", "an", "object"]}), REPO)
    assert code == 1


def test_workflow_is_read_only_scheduled_and_cloudflare_free():
    text = (Path(__file__).resolve().parents[1] / ".github/workflows/receipt-audit.yml").read_text()
    assert 'cron: "30 23 * * MON-FRI"' in text
    assert "contents: read" in text and "actions: read" in text and ": write" not in text
    job = text.split("jobs:", 1)[1].lower()
    for forbidden in ("secrets.", "cloudflare", "openrouter", "apca", "premarket", "schedule --", "deploy", "git push",
                      "upload-artifact"):
        assert forbidden not in job
    assert "python -m market_brief receipt-audit" in job

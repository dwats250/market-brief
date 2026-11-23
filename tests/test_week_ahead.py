"""B1–B4: the Sunday Week Ahead edition (WP-MB-WEEK-AHEAD-01).

Plan of record: docs/2026-09-27-market-memory-week-ahead.md §3 B1–B4, §6 and §9 B. The main scenario drives a real
production week through the CLI (two syntheses and a close each day, each close writing its session record), installs
those records as `memory-restore` would, and runs the Sunday edition.
"""

import json
import shutil
from datetime import datetime, timezone

import pytest
from test_cadence import Day

from market_brief import cli, memory
from market_brief.continuity import bundle_path
from market_brief.evidence import PLACEHOLDER, ROOT, evidence_catalog, read_json
from market_brief.memory import MEMORY_FILE, load_week
from market_brief.schedule import (
    CHECKPOINT_KINDS,
    SYNTHESIS_CHECKPOINTS,
    checkpoint_session,
    completed_week,
    due,
    next_checkpoint,
    scheduled_checkpoint,
    week_calendar,
)

WEEK = ("2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18")
PREVIOUS = {"2026-09-14": "2026-09-11", "2026-09-15": "2026-09-14", "2026-09-16": "2026-09-15",
            "2026-09-17": "2026-09-16", "2026-09-18": "2026-09-17"}
SUNDAY = "2026-09-20"
SUNDAY_AT = f"{SUNDAY}T20:01:00+00:00"  # 16:01 ET in daylight time


def utc(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def curve_on(day):
    """The fixture's Treasury curve, re-dated to `day` (the official curve a run that day would hold)."""
    def collect(raw, now):
        for row in raw["observations"]:
            if row.get("source_id", "").startswith(("treasury", "sample-rates")):
                row["observed_at"] = day
                if "baseline" in row and row["baseline"].startswith("daily observation"):
                    row["baseline"] = f"daily observation {PRIOR_CURVE.get(day, day)}"
    return collect


PRIOR_CURVE = {"2026-09-11": "2026-09-10", **{d: p for d, p in PREVIOUS.items()}}


class Week(Day):
    def run(self, now, checkpoint, **kwargs):
        last = kwargs.get("last_history_date")
        if last and "collect" not in kwargs:
            kwargs["collect"] = curve_on(last)
        return super().run(now, checkpoint, **kwargs)

    def session(self, day, prints=(("SPY", -0.5), ("QQQ", -0.7), ("XLI", 0.3)), pm=True):
        prior = PREVIOUS[day]
        if pm:
            assert self.run(f"{day}T13:00:00+00:00", "PREMARKET", intraday=False, last_history_date=prior) == 0
            assert self.run(f"{day}T14:01:00+00:00", "OPEN_30M", last_history_date=prior) == 0
        assert self.run(f"{day}T20:03:00+00:00", "CLOSE_1M", print_at=f"{day}T19:59:58+00:00", prints=prints,
                        last_history_date=prior) == 0

    def restore(self, days=WEEK):
        """What `memory-restore` installs: each close's run folder under runs/week/<workflow run>/."""
        for index, day in enumerate(days):
            close = self.folder("CLOSE_1M", day)
            shutil.copytree(close, self.root / "runs" / "week" / f"9000{index}" / day / close.name)

    def sunday(self, at=SUNDAY_AT, flags=(), **kwargs):
        return self.run(at, "WEEK_AHEAD", intraday=False, last_history_date=WEEK[-1], flags=flags, **kwargs)

    def sunday_folder(self, day=SUNDAY):
        return self.folder("WEEK_AHEAD", day)


@pytest.fixture
def week(monkeypatch, tmp_path):
    return Week(monkeypatch, tmp_path)


@pytest.fixture
def lived(week):
    for day in WEEK:
        week.session(day)
    week.restore()
    return week


# --- B1: the checkpoint, its dates and its clock ------------------------------------------------------

def test_week_ahead_is_the_one_weekly_synthesis():
    assert CHECKPOINT_KINDS["WEEK_AHEAD"] == "synthesis"
    assert SYNTHESIS_CHECKPOINTS == ("PREMARKET", "OPEN_30M", "WEEK_AHEAD")


@pytest.mark.parametrize("wake,expected", [
    ("2026-09-20T20:01:00+00:00", "WEEK_AHEAD"),   # EDT: 16:00 ET is 20:00 UTC
    ("2026-09-20T21:01:00+00:00", None),           # the other season's wake: past the 20-minute window
    ("2026-11-22T20:01:00+00:00", None),           # EST: 16:00 ET is 21:00 UTC, so 20:01 is early
    ("2026-11-22T21:01:00+00:00", "WEEK_AHEAD"),
    ("2026-03-08T20:01:00+00:00", "WEEK_AHEAD"),   # the Sunday New York springs forward
    ("2026-11-01T21:01:00+00:00", "WEEK_AHEAD"),   # the Sunday New York falls back
    ("2026-09-19T20:01:00+00:00", None),           # Saturday
    ("2026-09-21T20:01:00+00:00", None),           # Monday
])
def test_exactly_one_sunday_wake_is_due_in_each_season(wake, expected):
    assert (scheduled_checkpoint(utc(wake)) == "WEEK_AHEAD") == (expected == "WEEK_AHEAD")


def test_a_year_of_wakes_finds_exactly_one_due_week_ahead_per_sunday():
    from datetime import date, timedelta
    day, found = date(2026, 1, 4), []
    while day.year == 2026:
        for hour in (20, 21):
            wake = datetime(day.year, day.month, day.day, hour, 1, tzinfo=timezone.utc)
            if due(wake, "WEEK_AHEAD")[0]:
                found.append(wake)
        day += timedelta(days=7)
    assert len(found) == 52 and len({w.date() for w in found}) == 52


def test_the_weekday_checkpoints_never_resolve_on_sunday_and_week_ahead_never_on_weekdays():
    for hour in range(24):
        for day in ("2026-09-14", "2026-09-18", "2026-09-19"):
            assert not due(utc(f"{day}T{hour:02d}:01:00+00:00"), "WEEK_AHEAD")[0]
    info = checkpoint_session(utc(SUNDAY_AT), "WEEK_AHEAD")
    assert info["session_date"] == SUNDAY and info["trading_day"] is False and info["applicable"]


@pytest.mark.parametrize("day,sessions", [
    ("2026-09-20", list(WEEK)),
    ("2026-11-29", ["2026-11-23", "2026-11-24", "2026-11-25", "2026-11-27"]),   # Thanksgiving
    ("2026-12-27", ["2026-12-21", "2026-12-22", "2026-12-23", "2026-12-24"]),   # Christmas Friday
    ("2027-01-03", ["2026-12-28", "2026-12-29", "2026-12-30", "2026-12-31"]),   # New Year's Day Friday
    ("2027-03-28", ["2027-03-22", "2027-03-23", "2027-03-24", "2027-03-25"]),   # Good Friday
    ("2026-09-13", ["2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11"]),   # Labor Day Monday
])
def test_the_completed_week_follows_the_exchange_calendar(day, sessions):
    assert completed_week(day) == sessions


def test_the_next_week_names_closures_and_early_closes():
    days = {d["date"]: d for d in week_calendar("2026-11-22")}
    assert days["2026-11-26"] == dict(date="2026-11-26", open=False)
    assert days["2026-11-27"]["early_close"] is True and days["2026-11-25"]["early_close"] is False


@pytest.mark.parametrize("now,current,checkpoint,session", [
    ("2026-09-18T20:03:00+00:00", "CLOSE_1M", "WEEK_AHEAD", "2026-09-20"),   # Friday's close reads week ahead
    ("2027-03-25T20:03:00+00:00", "CLOSE_1M", "WEEK_AHEAD", "2027-03-28"),   # Thursday before Good Friday
    ("2026-11-25T21:03:00+00:00", "CLOSE_1M", "PREMARKET", "2026-11-27"),    # Wednesday before Thanksgiving
    ("2026-09-20T20:05:00+00:00", "WEEK_AHEAD", "PREMARKET", "2026-09-21"),  # the Sunday page reads Monday
    ("2026-09-06T20:05:00+00:00", "WEEK_AHEAD", "PREMARKET", "2026-09-08"),  # Labor Day: Tuesday
])
def test_the_next_clock_points_at_the_right_edition(now, current, checkpoint, session):
    info = next_checkpoint(utc(now), current)
    assert (info["checkpoint"], info["session_date"]) == (checkpoint, session)


def test_session_watches_still_point_at_the_premarket_after_friday():
    from market_brief.schedule import next_synthesis
    assert next_synthesis(utc("2026-09-18T20:03:00+00:00"), "CLOSE_1M")["checkpoint"] == "PREMARKET"


def test_friday_close_page_reads_week_ahead_next():
    from market_brief.render import next_update_label
    info = next_checkpoint(utc("2026-09-18T20:03:00+00:00"), "CLOSE_1M")
    assert next_update_label(info, "2026-09-18") == "Sun, Sep 20 · 1:00 PM Vancouver time · week ahead"


# --- the Sunday run ---------------------------------------------------------------------------------

def test_a_sunday_makes_one_call_publishes_and_leaves_weekday_continuity_untouched(lived):
    before = bundle_path(lived.root).read_text()
    calls = list(lived.calls)
    assert lived.sunday() == 0
    assert lived.calls == calls + ["WEEK_AHEAD"]  # exactly one call
    assert lived.published[-1] == "WEEK_AHEAD"
    assert bundle_path(lived.root).read_text() == before  # no restore, admission or advance
    meta = lived.metadata("WEEK_AHEAD", SUNDAY)
    assert meta["synthesis"] == dict(kind="synthesis", calls=1) and meta["validation"] == "PASS"
    assert meta["continuity"]["advanced"] is False and meta["continuity"]["status"] == "cold_start"
    assert "isolated" in meta["continuity"]["reason"]
    assert set(meta["week"]["records"]) == set(WEEK) and meta["week"]["missing"] == []
    for day, item in meta["week"]["records"].items():
        saved = json.loads(next((lived.root / "runs" / "week").rglob(f"{day}/*/{MEMORY_FILE}")).read_text())
        assert item["content_hash"] == saved["content_hash"] and item["written_by"] == "close"
    assert not (lived.sunday_folder() / "session_handoff.json").exists()


def test_two_sundays_never_touch_the_bundle_and_monday_still_reads_fridays_close(lived):
    before = bundle_path(lived.root).read_text()
    assert lived.sunday() == 0
    assert lived.sunday(at=f"{SUNDAY}T20:15:00+00:00", flags=("--repay",)) == 0
    assert bundle_path(lived.root).read_text() == before
    assert lived.run("2026-09-21T13:00:00+00:00", "PREMARKET", intraday=False, last_history_date=WEEK[-1]) == 0
    context = json.loads((lived.folder("PREMARKET", "2026-09-21") / "analyst_context.json").read_text())
    anchor = context["prior_state"]["anchors"]["previous_close"]
    assert anchor["checkpoint"] == "CLOSE_1M" and anchor["session_date"] == WEEK[-1]


def test_archived_rows_are_dated_citable_and_keep_their_original_lineage(lived):
    assert lived.sunday() == 0
    folder = lived.sunday_folder()
    packet = json.loads((folder / "evidence.json").read_text())
    archived = [r for r in packet["derived"] if r.get("archived")]
    assert archived
    for day in WEEK:
        close = json.loads((lived.folder("CLOSE_1M", day) / "evidence.json").read_text())
        originals = {r["id"]: r for r in close["observations"] + close["derived"]}
        rows = [r for r in archived if r["lineage"]["session_date"] == day and not r.get("frozen")]
        assert rows, day
        for row in rows:
            original = originals[row["lineage"]["original_id"]]
            for key in ("source_id", "observed_at", "retrieved_at", "value", "formula_version", "input_ids"):
                assert row.get(key) == original.get(key), (row["id"], key)
            assert row["source_id"] != "session-memory" and row["status"] == "BACKGROUND"
            assert row["lineage"]["run_id"] == close["run"]["run_id"]
    context = json.loads((folder / "analyst_context.json").read_text())
    shown = {r["id"]: r for g in context["catalog"] for r in g["rows"]}
    spy_monday = shown["SPY-session-20260914"]
    assert spy_monday["observed_at"].startswith("2026-09-14") and spy_monday["source_id"] == "sample-prices"
    # Archived rows are citable evidence; the week's earlier hypotheses are not.
    assert context["week"]["hypotheses"]["class"] == "INTERPRETATION"
    note_text = json.dumps(context["week"]["hypotheses"])
    assert not any(ident in shown and not ident.startswith(("SPY", "QQQ")) for ident in ("rel-", "watch-")
                   if ident in note_text)


def test_changing_one_archived_value_changes_exactly_that_row(week):
    for day in WEEK:
        week.session(day)
    week.restore()
    assert week.sunday() == 0
    first = json.loads((week.sunday_folder() / "evidence.json").read_text())
    path = next((week.root / "runs" / "week").rglob(f"2026-09-16/*/{MEMORY_FILE}"))
    record = json.loads(path.read_text())
    record["observed"]["returns"]["SPY"]["value"] = 1.2345
    from market_brief.evidence import digest
    body = {k: v for k, v in record.items() if k != "content_hash"}
    path.write_text(json.dumps(dict(body, content_hash=digest(body))))
    shutil.rmtree(week.root / "runs" / SUNDAY)
    assert week.sunday() == 0
    second = json.loads((week.sunday_folder() / "evidence.json").read_text())
    before = {r["id"]: r for r in first["derived"] if r.get("archived")}
    after = {r["id"]: r for r in second["derived"] if r.get("archived")}
    changed = {i for i in before if {k: v for k, v in before[i].items() if k != "lineage"}
               != {k: v for k, v in after[i].items() if k != "lineage"}}
    assert changed == {"SPY-session-20260916"} and after["SPY-session-20260916"]["value"] == 1.2345


def test_no_valid_record_means_no_call_and_no_page(week):
    for day in WEEK:
        week.session(day)
    calls = list(week.calls)
    assert week.sunday() == 2
    assert week.calls == calls and week.published[-1] != "WEEK_AHEAD"
    meta = week.metadata("WEEK_AHEAD", SUNDAY)
    assert meta["validation"] == "FAILED" and "no paid call" in meta["error"]
    assert not (week.sunday_folder() / "brief.html").exists()


def test_a_missing_or_corrupt_session_is_a_named_limitation(week):
    for day in WEEK:
        week.session(day)
    week.restore(days=WEEK[:2] + WEEK[3:])
    corrupt = next((week.root / "runs" / "week").rglob(f"2026-09-18/*/{MEMORY_FILE}"))
    corrupt.write_text(corrupt.read_text().replace('"LIVE"', '"LIVE "', 1))
    assert week.sunday() == 0
    meta = week.metadata("WEEK_AHEAD", SUNDAY)
    assert meta["week"]["missing"] == ["2026-09-16", "2026-09-18"]
    assert any("does not verify" in item["reason"] for item in meta["week"]["ignored"])
    packet = json.loads((week.sunday_folder() / "evidence.json").read_text())
    assert "No accepted session record for 2026-09-16" in packet["coverage"]["limitations"]
    page = (week.sunday_folder() / "brief.html").read_text()
    assert "No accepted session record for 2026-09-16" in page and "no record" in page


def test_records_from_failed_or_non_production_closes_are_ignored(week):
    for day in WEEK:
        week.session(day)
    week.restore()
    path = next((week.root / "runs" / "week").rglob("2026-09-15/*/metadata.json"))
    meta = json.loads(path.read_text())
    path.write_text(json.dumps(dict(meta, validation="FAILED")))
    records, report = load_week(week.root, list(WEEK))
    assert "2026-09-15" in report["missing"]
    assert any("PASS and advanced" in item["reason"] for item in report["ignored"])


def test_the_earliest_record_of_a_session_wins(week):
    for day in WEEK:
        week.session(day)
    week.restore()
    close = week.folder("CLOSE_1M", "2026-09-17")
    late = week.root / "runs" / "week" / "99999" / "2026-09-17" / (close.name + "-late")
    shutil.copytree(close, late)
    record = json.loads((late / MEMORY_FILE).read_text())
    record["source"]["target_time"] = "2026-09-17T23:59:00+00:00"
    from market_brief.evidence import digest
    body = {k: v for k, v in record.items() if k != "content_hash"}
    (late / MEMORY_FILE).write_text(json.dumps(dict(body, content_hash=digest(body))))
    records, report = load_week(week.root, list(WEEK))
    assert report["records"]["2026-09-17"]["run_id"] == close.name
    assert any(item["reason"] == "a later record for 2026-09-17" for item in report["ignored"])


def test_a_reconstructed_record_is_refused_by_production_and_admitted_by_an_experiment(week):
    for day in WEEK:
        week.session(day)
    week.restore()
    for path in (week.root / "runs" / "week").rglob(MEMORY_FILE):
        record = json.loads(path.read_text())
        record["source"]["written_by"] = "reconstruction"
        from market_brief.evidence import digest
        body = {k: v for k, v in record.items() if k != "content_hash"}
        path.write_text(json.dumps(dict(body, content_hash=digest(body))))
    assert load_week(week.root, list(WEEK))[0] == []
    assert len(load_week(week.root, list(WEEK), allow_reconstructed=True)[0]) == 5


@pytest.mark.parametrize("at,flags,allowed", [
    ("2026-09-19T20:01:00+00:00", ("--experiment",), True),    # Saturday experiment
    ("2026-09-19T20:01:00+00:00", (), False),                  # Saturday production
    ("2026-09-18T20:01:00+00:00", ("--experiment",), False),   # a trading day
    ("2026-11-26T20:01:00+00:00", ("--experiment",), False),   # a weekday holiday
])
def test_week_ahead_runs_only_on_its_days(week, at, flags, allowed):
    from market_brief.cli import week_ahead_allowed
    if allowed:
        week_ahead_allowed(utc(at), "--experiment" in flags)
        return
    with pytest.raises(ValueError, match="only on a Sunday"):
        week_ahead_allowed(utc(at), "--experiment" in flags)
    assert week.run(at, "WEEK_AHEAD", intraday=False, flags=flags) == 2
    assert week.calls == [] and not list((week.root / "runs").rglob("live-week_ahead-*"))


def test_the_scheduled_path_runs_once_and_is_idempotent(lived, capsys):
    assert lived.run(SUNDAY_AT, "WEEK_AHEAD", intraday=False, last_history_date=WEEK[-1], command="schedule") == 0
    assert lived.calls[-1] == "WEEK_AHEAD"
    calls = len(lived.calls)
    assert lived.run(f"{SUNDAY}T20:10:00+00:00", "WEEK_AHEAD", intraday=False, last_history_date=WEEK[-1],
                     command="schedule") == 0
    assert len(lived.calls) == calls and "already" in capsys.readouterr().out


def test_an_experiment_publishes_nothing_and_records_nothing(lived):
    published = list(lived.published)
    assert lived.sunday(flags=("--experiment",)) == 0
    assert lived.published == published
    assert not (lived.root / "runs" / "attempts" / f"{SUNDAY}-WEEK_AHEAD.json").exists()


# --- events: the next-week window ---------------------------------------------------------------------

def test_the_next_week_window_admits_a_tuesday_event_on_week_ahead_only():
    from market_brief.evidence import normalize_packet
    raw = read_json(ROOT / "tests/fixtures/evidence.sample.json")
    raw["mode"] = "LIVE"
    source = raw["sources"][0]
    event = dict(id="tue-event", title="Tuesday release", source_id=source["id"],
                 scheduled_at="2026-09-22T12:30:00+00:00", checked_at=SUNDAY_AT, status="SCHEDULED")
    raw["events"] = [event]
    for s in raw["sources"]:
        s["retrieved_at"] = SUNDAY_AT
    sunday = normalize_packet(raw, utc(SUNDAY_AT), "LIVE", "WEEK_AHEAD")
    assert [e["id"] for e in sunday["events"]] == ["tue-event"]
    assert sunday["events"][0]["session_relation"] == "NEXT WEEK"
    raw["events"][0]["checked_at"] = "2026-09-18T20:00:00+00:00"
    for s in raw["sources"]:
        s["retrieved_at"] = "2026-09-18T20:00:00+00:00"
    friday = normalize_packet(raw, utc("2026-09-18T20:00:00+00:00"), "LIVE", "PREMARKET")
    assert friday["events"] == []


# --- the page -------------------------------------------------------------------------------------------

def test_the_page_answers_the_five_questions_with_dated_evidence(lived):
    assert lived.sunday() == 0
    page = (lived.sunday_folder() / "brief.html").read_text()
    assert 'data-checkpoint="WEEK_AHEAD"' in page and f'data-session-date="{SUNDAY}"' in page
    for heading in ("The week in markets", "What the week tested", "Rates &amp; cross-asset", "Next week",
                    "What to watch next week", "Sources &amp; coverage"):
        assert heading in page, heading
    assert memory.MEMORY_FILE not in page
    assert "Conditions to watch next week; the daily briefs do not carry them forward." in page
    assert "Session records</span><span class=\"source-status\">5 of 5 sessions" in page
    assert "Sectors up on the week" in page and "Day by day" in page
    import re
    body = re.sub(r"<style>.*?</style>", "", page, flags=re.S)
    assert not PLACEHOLDER.search(body) and "{{" not in body


def test_archived_rows_never_enter_the_current_tables_or_the_rates_module(lived):
    assert lived.sunday() == 0
    packet = json.loads((lived.sunday_folder() / "evidence.json").read_text())
    archived = {r["id"] for r in packet["derived"] if r.get("archived")}
    rates = packet.get("rates") or {}
    assert not archived & set(json.dumps(rates).split('"'))
    week_rows = [r for r in packet["derived"] if r["metric"] == "week return"]
    assert week_rows and not any(r.get("archived") for r in week_rows)
    assert all(r["observed_at"] == WEEK[-1] and r["window_start"] == "2026-09-11" for r in week_rows)


def test_no_trade_language_in_the_weekly_guidance():
    from market_brief.context import edition_profile
    from market_brief.synthesize import TRADE_LANGUAGE
    profile = edition_profile("WEEK_AHEAD")
    assert profile["profile"] == "weekly" and profile["context"] == "week"
    assert not TRADE_LANGUAGE.search(profile["guidance"].replace("trade language", ""))


def test_the_weekly_wire_schema_is_the_rich_one():
    from market_brief.context import edition_profile
    from market_brief.synthesize import factored_transport_schema, narrative_schema
    weekly = factored_transport_schema(narrative_schema(edition_profile("WEEK_AHEAD")))
    rich = factored_transport_schema(narrative_schema(edition_profile("PREMARKET")))
    assert json.dumps(weekly, sort_keys=True) == json.dumps(rich, sort_keys=True)


def test_every_placeholder_on_the_page_resolves(lived):
    assert lived.sunday() == 0
    folder = lived.sunday_folder()
    narrative = json.loads((folder / "narrative.json").read_text())
    packet = json.loads((folder / "evidence.json").read_text())
    rows = evidence_catalog(packet)
    for text in json.dumps(narrative).split('"'):
        for ident in PLACEHOLDER.findall(text):
            assert ident in rows


# --- wiring: the wake and the workflow ---------------------------------------------------------------

def test_the_sunday_wake_is_one_cron_with_both_seasons():
    config = (ROOT / "cloudflare/wrangler.toml").read_text()
    assert config.count('"1 20-21 * * SUN"') == 1 and config.count("* * SUN") == 1


def test_the_workflow_isolates_week_ahead_from_continuity_and_restores_records():
    text = (ROOT / ".github/workflows/schedule.yml").read_text()
    assert "          - WEEK_AHEAD\n" in text
    restore = text.split("id: restore", 1)[1].split("- name:", 1)[0]
    assert "steps.checkpoint.outputs.checkpoint != 'WEEK_AHEAD'" in restore
    upload = text.split("Upload accepted continuity bundle", 1)[1].split("- name:", 1)[0]
    assert "steps.checkpoint.outputs.checkpoint != 'WEEK_AHEAD'" in upload
    records = text.split("Restore the completed week's session records", 1)[1].split("- name:", 1)[0]
    assert "steps.checkpoint.outputs.checkpoint == 'WEEK_AHEAD'" in records
    assert "memory-restore" in records and records.count("--reconstruct") == 1
    assert 'if [ "$EXPERIMENT" = "true" ]' in records


def test_memory_restore_lists_newest_first_and_keeps_only_this_repositorys_own_closes(lived, monkeypatch):
    import argparse
    monkeypatch.setattr(cli, "RUN_ROOT", lived.root)
    import subprocess
    week_dir = lived.root / "runs" / "week"
    shutil.rmtree(week_dir)
    own = dict(path=".github/workflows/schedule.yml", repository=dict(id=1), head_repository=dict(id=1))
    artifacts = []
    for index, day in enumerate(reversed(WEEK)):
        artifacts.append(dict(id=100 + index, name=f"market-brief-run-CLOSE_1M-{500 + index}",
                              created_at=f"{day}T20:05:00Z", expired=False,
                              workflow_run=dict(id=500 + index, head_branch="main")))
    artifacts.insert(0, dict(id=99, name="market-brief-run-CLOSE_1M-499", created_at="2026-09-18T21:00:00Z",
                             workflow_run=dict(id=499, head_branch="feature")))  # another branch
    artifacts.append(dict(id=98, name="market-brief-run-CLOSE_1M-498", created_at="2026-09-11T20:05:00Z",
                          workflow_run=dict(id=498, head_branch="main")))  # before the week: stops the listing
    pages, calls = {1: artifacts[:4], 2: artifacts[4:], 3: [dict(id=1, name="x", created_at="2026-01-01T00:00Z")]}, []
    by_run = {500 + i: day for i, day in enumerate(reversed(WEEK))}

    def runner(command, **kwargs):
        calls.append(command)
        if command[:2] == ["gh", "api"]:
            path = command[2]
            if "artifacts?" in path:
                page = int(path.rsplit("page=", 1)[1])
                body = dict(artifacts=pages.get(page, []))
            else:
                run_id = int(path.rsplit("/", 1)[1])
                body = own if run_id != 502 else dict(own, path=".github/workflows/other.yml")
            return subprocess.CompletedProcess(command, 0, json.dumps(body), "")
        run_id, target = int(command[3]), command[command.index("-D") + 1]
        close = lived.folder("CLOSE_1M", by_run[run_id])
        shutil.copytree(close, f"{target}/{by_run[run_id]}/{close.name}")
        return subprocess.CompletedProcess(command, 0, "", "")
    args = argparse.Namespace(week=SUNDAY, repository="owner/repo", branch="main", reconstruct=False)
    assert cli.restore_memory(args, runner) == 0
    listed = [c[2] for c in calls if c[:2] == ["gh", "api"] and "artifacts?" in c[2]]
    assert len(listed) == 2  # the second page reached the week's start
    downloads = {int(c[3]) for c in calls if c[:3] == ["gh", "run", "download"]}
    assert downloads == {500, 501, 503, 504} and 499 not in downloads and 498 not in downloads
    records, report = load_week(lived.root, list(WEEK))
    assert report["missing"] == [by_run[502]]
    assert all(item["workflow_run_id"] for item in report["records"].values())


def test_an_archived_row_without_its_original_source_is_withheld_and_named(lived):
    records, _ = load_week(lived.root, list(WEEK))
    record = json.loads(json.dumps(records[0]["record"]))
    refs = set()
    for watch in record["retained"]["watches"]:  # as a W47 record frozen before snapshots kept lineage
        for ref, fact in watch["facts"].items():
            if fact.pop("source_id", None):
                refs.add(ref)
    packet = dict(sources=[], observations=[], derived=[])
    packet, gaps, withheld = memory.admit_archived(packet, [dict(records[0], record=record)])
    assert not any(r.get("frozen") and not r.get("source_id") for r in packet["derived"])
    gap = next(g for g in gaps if "without an original source, withheld" in g)
    named = gap.split(": ", 1)[1].split(", ")
    # Each withheld row is a stripped fact with no sourced copy elsewhere in the record (that copy would be kept).
    assert named and all(any(ident.startswith(f"{ref}-") for ref in refs) for ident in named)
    assert withheld["no_source"] == len(named) and withheld["collision"] == 0


def test_an_archived_row_whose_source_descriptor_is_missing_is_counted_as_withheld(lived):
    records, _ = load_week(lived.root, list(WEEK))
    record = json.loads(json.dumps(records[0]["record"]))
    watch = next(w for w in record["retained"]["watches"] if w["facts"])
    ref = next(iter(watch["facts"]))
    watch["facts"][ref]["source_id"] = "ghost-source"  # no archived descriptor names it
    packet = dict(sources=[], observations=[], derived=[])
    packet, gaps, withheld = memory.admit_archived(packet, [dict(records[0], record=record)])
    assert withheld["no_descriptor"] == 1
    assert not any(r.get("source_id") == "ghost-source" for r in packet["derived"])


def _strip_sources(week, days=WEEK):
    """Remove the original source from every frozen fact of the sessions' records (as W47's pre-lineage records),
    resealing each so it still verifies. Returns the stripped fact ids."""
    from market_brief.evidence import digest
    stripped = set()
    for day in days:
        path = next((week.root / "runs" / "week").rglob(f"{day}/*/{MEMORY_FILE}"))
        record = json.loads(path.read_text())
        for watch in record["retained"]["watches"]:
            for ref, fact in watch["facts"].items():
                if fact.pop("source_id", None):
                    stripped.add(ref)
        body = {k: v for k, v in record.items() if k != "content_hash"}
        path.write_text(json.dumps(dict(body, content_hash=digest(body))))
    return stripped


def test_the_page_names_how_many_facts_were_withheld_and_why_without_showing_them(lived):
    import html
    import re
    assert _strip_sources(lived)
    assert lived.sunday() == 0
    meta = lived.metadata("WEEK_AHEAD", SUNDAY)
    gap = next(g for g in meta["week"]["gaps"] if g.startswith("archived rows without an original source"))
    withheld_ids = gap.split(": ", 1)[1].split(", ")
    count = meta["week"]["withheld"]["no_source"]
    assert count == len(withheld_ids) > 0
    raw = (lived.sunday_folder() / "brief.html").read_text()
    page = html.unescape(re.sub(r"<style>.*?</style>", "", raw, flags=re.S))
    noun = "fact" if count == 1 else "facts"
    assert (f"{count} archived {noun} from the week's session records withheld: saved without their original "
            "source, so they cannot be cited.") in page
    assert f"· {count} withheld</span>" in page
    # The disclosure sits in the coverage drawer the page opens by default.
    assert re.search(r'<details class="drawer" open><summary>Coverage limitations.*?withheld: saved', raw, re.S)
    # Neither the withheld facts nor their identities reach the page, the evidence or the analyst.
    context = (lived.sunday_folder() / "analyst_context.json").read_text()
    packet = json.loads((lived.sunday_folder() / "evidence.json").read_text())
    admitted = {r["id"] for key in ("observations", "derived") for r in packet[key]}
    for ident in withheld_ids:
        assert ident not in raw, ident
        assert f'"{ident}"' not in context, ident
        assert ident not in admitted, ident


def test_a_sourced_copy_of_a_fact_wins_over_an_unsourced_duplicate(lived):
    records, _ = load_week(lived.root, list(WEEK))
    first = json.loads(json.dumps(records[0]["record"]))
    watch = next(w for w in first["retained"]["watches"] if w["facts"])
    ref = next(iter(watch["facts"]))
    second = json.loads(json.dumps(first))
    del watch["facts"][ref]["source_id"]
    packet = dict(sources=[], observations=[], derived=[])
    packet, gaps, withheld = memory.admit_archived(
        packet, [dict(records[0], record=first), dict(records[0], record=second)])
    assert withheld["no_source"] == 0
    assert any(r.get("lineage", {}).get("original_id") == ref and r.get("source_id") for r in packet["derived"])


def test_an_archived_source_without_a_retrieval_time_is_withheld_not_a_crash(lived):
    records, _ = load_week(lived.root, list(WEEK))
    record = json.loads(json.dumps(records[0]["record"]))
    watch = next(w for w in record["retained"]["watches"] if w["facts"])
    ref = next(iter(watch["facts"]))
    fact = watch["facts"][ref]
    fact["source_id"] = "ghost-source"
    fact.pop("retrieved_at", None)
    record.setdefault("sources", {})["ghost-source"] = dict(id="ghost-source", name="Ghost", url="https://example.org")
    packet = dict(sources=[], observations=[], derived=[])
    packet, gaps, withheld = memory.admit_archived(packet, [dict(records[0], record=record)])
    assert withheld["no_descriptor"] == 1
    assert not any(r.get("source_id") == "ghost-source" for r in packet["derived"])


def test_no_withheld_line_when_every_archived_fact_kept_its_source(lived):
    assert lived.sunday() == 0
    assert lived.metadata("WEEK_AHEAD", SUNDAY)["week"]["withheld"] == {}
    page = (lived.sunday_folder() / "brief.html").read_text()
    assert "withheld:" not in page and " withheld</span>" not in page


def test_withheld_wording_is_a_count_and_a_reason():
    from market_brief.weekpage import withheld_limitation
    assert withheld_limitation("no_source", 1) == ("1 archived fact from the week's session records withheld: saved "
                                                   "without their original source, so they cannot be cited.")
    assert withheld_limitation("no_descriptor", 3).startswith("3 archived facts from the week's session records")
    assert "provenance is incomplete" in withheld_limitation("unknown", 2)


# --- review regressions (independent review of B) ----------------------------------------------------

def test_archived_ids_carry_their_own_observation_date_and_never_collide(lived):
    assert lived.sunday() == 0
    meta = lived.metadata("WEEK_AHEAD", SUNDAY)
    assert not [g for g in meta["week"]["gaps"] if "two records disagree" in g]
    packet = json.loads((lived.sunday_folder() / "evidence.json").read_text())
    for row in (r for r in packet["derived"] if r.get("archived")):
        stamp = row["id"].rsplit("-", 1)[1]
        assert stamp[:8] == memory._fact_suffix(row)[:8], row["id"]
        assert row["lineage"]["original_status"]


def test_old_placeholders_never_reach_the_page_raw(week):
    for day in WEEK:
        week.session(day)
    week.restore()
    from market_brief.evidence import digest
    for path in (week.root / "runs" / "week").rglob(MEMORY_FILE):
        record = json.loads(path.read_text())
        for watch in record["retained"]["watches"]:
            watch["hypothesis"] = "QQQ holds {{QQQ-daily}} and {{not-a-row}}"
        for relationship in record["retained"]["relationships"]:
            relationship["statement"] = "Growth leads at {{QQQ-daily}}"
        body = {k: v for k, v in record.items() if k != "content_hash"}
        path.write_text(json.dumps(dict(body, content_hash=digest(body))))
    assert week.sunday() == 0
    import re
    page = re.sub(r"<style>.*?</style>", "", (week.sunday_folder() / "brief.html").read_text(), flags=re.S)
    assert "{{" not in page and "(value unavailable)" in page


def test_the_event_window_reads_the_new_york_date_late_on_sunday():
    from market_brief.schedule import week_event_dates
    late = utc("2026-09-21T00:30:00+00:00")  # Sunday 8:30 PM in New York
    assert week_event_dates(late) == {d["date"] for d in week_calendar(SUNDAY) if d["open"]}
    assert completed_week(late) == list(WEEK)


def test_archived_daily_returns_never_count_as_current_coverage():
    from market_brief.evidence import finalize_coverage
    packet = dict(run=dict(checkpoint="WEEK_AHEAD", session=dict(date=SUNDAY)), sources=[], observations=[],
                  events=[], context_items=[], derived=[dict(id="SPY-daily-20260911", topic="SPY",
                                                            metric="daily return", value=0.1, archived=True),
                                                       dict(id="QQQ-daily-20260911", topic="QQQ",
                                                            metric="daily return", value=0.1, archived=True)])
    finalize_coverage(packet)
    assert "equity prior-close history" in packet["coverage"]["missing_domains"]


def test_a_week_ahead_replay_needs_its_records(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    assert cli.main(["premarket", "--replay", "--checkpoint", "WEEK_AHEAD"]) == 2


def test_later_artifacts_of_a_session_never_crowd_out_its_contemporaneous_close(lived, monkeypatch):
    import argparse
    import subprocess
    monkeypatch.setattr(cli, "RUN_ROOT", lived.root)
    shutil.rmtree(lived.root / "runs" / "week")
    own = dict(path=".github/workflows/schedule.yml", repository=dict(id=1), head_repository=dict(id=1))
    day = WEEK[-1]
    listing = [dict(id=3, name="market-brief-run-CLOSE_1M-703", created_at=f"{day}T23:50:00Z",
                    workflow_run=dict(id=703, head_branch="main")),
               dict(id=2, name="market-brief-run-CLOSE_1M-702", created_at=f"{day}T23:40:00Z",
                    workflow_run=dict(id=702, head_branch="main")),
               dict(id=1, name="market-brief-run-CLOSE_1M-701", created_at=f"{day}T20:05:00Z",
                    workflow_run=dict(id=701, head_branch="main")),
               dict(id=0, name="x", created_at="2026-09-01T00:00:00Z", workflow_run=dict(id=1, head_branch="main"))]
    downloads = []

    def runner(command, **kwargs):
        if command[:2] == ["gh", "api"]:
            body = dict(artifacts=listing) if "artifacts?" in command[2] and command[2].endswith("page=1") else (
                dict(artifacts=[]) if "artifacts?" in command[2] else own)
            return subprocess.CompletedProcess(command, 0, json.dumps(body), "")
        downloads.append(int(command[3]))
        close = lived.folder("CLOSE_1M", day)
        shutil.copytree(close, f"{command[command.index('-D') + 1]}/{day}/{close.name}")
        return subprocess.CompletedProcess(command, 0, "", "")
    args = argparse.Namespace(week=SUNDAY, repository="owner/repo", branch="main", reconstruct=False)
    assert cli.restore_memory(args, runner) == 0
    assert downloads == [701, 702]


def test_earlier_experiment_dispatches_never_use_up_a_sessions_restore_slots(lived, monkeypatch):
    import argparse
    import subprocess
    monkeypatch.setattr(cli, "RUN_ROOT", lived.root)
    shutil.rmtree(lived.root / "runs" / "week")
    own = dict(path=".github/workflows/schedule.yml", repository=dict(id=1), head_repository=dict(id=1))
    day = WEEK[-1]
    listing = [dict(id=3, name="market-brief-run-CLOSE_1M-803", created_at=f"{day}T20:05:00Z",
                    workflow_run=dict(id=803, head_branch="main")),
               dict(id=2, name="market-brief-run-CLOSE_1M-802", created_at=f"{day}T17:00:00Z",
                    workflow_run=dict(id=802, head_branch="main")),
               dict(id=1, name="market-brief-run-CLOSE_1M-801", created_at=f"{day}T15:00:00Z",
                    workflow_run=dict(id=801, head_branch="main")),
               dict(id=0, name="x", created_at="2026-09-01T00:00:00Z", workflow_run=dict(id=1, head_branch="main"))]

    def runner(command, **kwargs):
        if command[:2] == ["gh", "api"]:
            body = dict(artifacts=listing) if "artifacts?" in command[2] and command[2].endswith("page=1") else (
                dict(artifacts=[]) if "artifacts?" in command[2] else own)
            return subprocess.CompletedProcess(command, 0, json.dumps(body), "")
        run_id = int(command[3])
        close = lived.folder("CLOSE_1M", day)
        target = f"{command[command.index('-D') + 1]}/{day}/{close.name}"
        shutil.copytree(close, target)
        if run_id != 803:  # two earlier experiment dispatches of CLOSE_1M
            meta = json.loads((Path(target) / "metadata.json").read_text())
            (Path(target) / "metadata.json").write_text(json.dumps(dict(meta, experiment=True, run_id=f"x{run_id}")))
            (Path(target) / MEMORY_FILE).unlink()
        return subprocess.CompletedProcess(command, 0, "", "")
    from pathlib import Path
    args = argparse.Namespace(week=SUNDAY, repository="owner/repo", branch="main", reconstruct=False)
    assert cli.restore_memory(args, runner) == 0
    records, report = load_week(lived.root, list(WEEK))
    assert day in report["records"]


def test_the_analyst_never_sees_a_placeholder_it_cannot_cite():
    from market_brief.context import compact_note
    note = {"class": "INTERPRETATION", "note": "", "sessions": [], "relationships": [],
            "watches": [dict(first="2026-09-14", hypothesis="NVDA lags at {{NVDA-spread20-20260914}}", verdicts=[],
                             end="expired")]}
    assert "{{" not in json.dumps(compact_note(note, set()))
    assert "{{NVDA-spread20-20260914}}" in json.dumps(compact_note(note, {"NVDA-spread20-20260914"}))

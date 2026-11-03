import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from market_brief import collect
from market_brief.actuals import (
    FAMILIES,
    JOLTS_REVISION,
    PRODUCTIVITY_REVISION,
    ReleaseError,
    check_release,
    due_releases,
    read_release,
    release_rows,
)
from market_brief.evidence import normalize_observation
from market_brief.render import release_cards

FIXTURES = Path(__file__).parent / "fixtures"
JOLTS = (FIXTURES / "bls-jolts.2026-11-03.htm").read_text()
PRODUCTIVITY = (FIXTURES / "bls-productivity.2026-09-03.htm").read_text()


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def event(title, at):
    return dict(id="release", title=title, source_id="bls", scheduled_at=at, status="SCHEDULED")


def test_jolts_headline_identity_values_and_prior_month_revision():
    release = read_release("jolts", JOLTS)
    assert release["period"] == "2026-09"
    assert release["released_at"] == datetime(2026, 11, 3, 10, tzinfo=ZoneInfo("America/New_York"))
    assert release["values"] == {"openings": 7.5, "openings_rate": 4.5}
    assert release["revisions"] == [dict(period="2026-08", metric=JOLTS_REVISION, value=111000,
                                         revised_to=7.2, unit="openings",
                                         baseline="BLS revision to the previous month's reported job-openings change")]


def test_productivity_quarter_values_and_explicit_revision():
    release = read_release("productivity", PRODUCTIVITY)
    assert release["period"] == "2026-Q2" and release["edition"] == "revised"
    assert release["values"] == {"productivity": 1.4, "unit_labor_costs": 1.2}
    assert release["revisions"] == [dict(period="2026-Q2", metric=PRODUCTIVITY_REVISION, value=-0.1,
                                         previous=1.3, revised=1.2, unit="percent change",
                                         baseline="BLS revised quarterly unit labor costs")]


@pytest.mark.parametrize("family,page,title,at,period", [
    ("jolts", JOLTS, "Job Openings and Labor Turnover Survey for September 2026",
     "2026-11-03T15:00:00Z", "2026-09"),
    ("productivity", PRODUCTIVITY, "Productivity and Costs (R) for Second Quarter 2026",
     "2026-09-03T12:30:00Z", "2026-Q2"),
])
def test_calendar_gates_new_readers_and_provenance_rows(family, page, title, at, period):
    release = read_release(family, page)
    scheduled = utc(at)
    scheduled_event = event(title, scheduled.isoformat())
    assert due_releases([scheduled_event], scheduled - timedelta(seconds=1), enabled=[family]) == []
    assert due_releases([scheduled_event], scheduled, enabled=[family]) == [(family, scheduled_event, period)]
    check_release(release, scheduled_event, period)
    retrieved = scheduled + timedelta(minutes=2)
    rows = release_rows(release, retrieved)
    assert rows and {row["source_id"] for row in rows} == {FAMILIES[family]["source"]}
    assert {row["reference_period"] for row in rows} == {period}
    assert {row["retrieved_at"] for row in rows} == {retrieved.isoformat()}
    normalized = normalize_observation(rows[0], retrieved)
    assert normalized["status"] == "AVAILABLE" and normalized["freshness"] == "DATED"
    card = release_cards(rows, [scheduled_event])
    assert len(card) == 1 and card[0]["events"] == ["release"]
    display = {line["label"]: line["values"] for line in card[0]["lines"]}
    if family == "jolts":
        assert display == {"Job openings": ["7.5m"], "Openings rate": ["4.5%"],
                           "Revisions": ["Aug openings +111,000"]}
    else:
        assert display == {"Labor productivity": ["+1.4%"], "Unit labor costs": ["+1.2%"],
                           "Revisions": ["Q2 2026 +1.3% → +1.2%"]}


def test_new_readers_are_not_enabled_by_calendar_presence_alone():
    jolts = event("Job Openings and Labor Turnover Survey", "2026-11-03T15:00:00Z")
    productivity = event("Productivity and Costs", "2026-11-05T13:30:00Z")
    assert due_releases([jolts], utc("2026-11-03T15:01:00Z")) == []
    assert due_releases([productivity], utc("2026-11-05T13:31:00Z")) == []
    assert collect.read_json(FIXTURES.parent.parent / "config/economic_actuals.json") == {
        "enabled_families": ["empsit", "cpi"]}


@pytest.mark.parametrize("family,page,event_value,period", [
    ("jolts", JOLTS, event("Job Openings and Labor Turnover Survey for October 2026", "2026-11-03T15:00:00Z"),
     "2026-10"),
    ("productivity", PRODUCTIVITY, event("Productivity and Costs (R) for Fourth Quarter 2026",
                                         "2026-09-03T12:30:00Z"), "2026-Q4"),
])
def test_wrong_reference_period_is_refused(family, page, event_value, period):
    with pytest.raises(ReleaseError, match="reports"):
        check_release(read_release(family, page), event_value, period)


def test_page_with_wrong_official_publication_time_is_refused_as_stale():
    release = read_release("jolts", JOLTS)
    changed = event("Job Openings and Labor Turnover Survey for September 2026", "2026-11-04T15:00:00Z")
    with pytest.raises(ReleaseError, match="still shows the November 3, 2026 release"):
        check_release(release, changed, "2026-09")


@pytest.mark.parametrize("family,page", [("jolts", JOLTS), ("productivity", PRODUCTIVITY)])
def test_release_time_and_required_identity_cannot_be_missing(family, page):
    damaged = page.replace("USDL-26-1630", "USDL-00-0000") if family == "jolts" else page.replace(
        "USDL-26-1434", "no source identifier")
    with pytest.raises(ReleaseError):
        read_release(family, damaged)


def test_productivity_source_rows_accept_quarters_but_keep_missing_revision_provenance_explicit():
    release = read_release("productivity", PRODUCTIVITY)
    rows = release_rows(release, utc("2026-09-03T12:32:00Z"))
    revision = next(row for row in rows if row["metric"] == PRODUCTIVITY_REVISION)
    assert revision["revised_period"] == "2026-Q2"
    assert (revision["revised_from"], revision["revised_to"]) == (1.3, 1.2)
    assert normalize_observation(revision, utc("2026-09-03T12:32:00Z"))["status"] == "AVAILABLE"


@pytest.mark.parametrize("family,page,title,at", [
    ("jolts", JOLTS, "Job Openings and Labor Turnover Survey for September 2026", "2026-11-03T15:00:00Z"),
    ("productivity", PRODUCTIVITY, "Productivity and Costs (R) for Second Quarter 2026", "2026-09-03T12:30:00Z"),
])
def test_collector_reads_only_enabled_due_official_page_and_keeps_retrieval_time(
        monkeypatch, family, page, title, at):
    scheduled = utc(at)
    retrieved = scheduled + timedelta(minutes=2)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return retrieved if tz else retrieved.replace(tzinfo=None)

    monkeypatch.setattr(collect, "datetime", FrozenDateTime)
    requests = []

    def fetcher(url, deadline):
        requests.append(url)
        return page

    events = [event(title, scheduled.isoformat())]
    rows, records = collect.release_actuals(events, retrieved, time.monotonic() + 60, fetcher,
                                            enabled=[family])
    assert requests == [FAMILIES[family]["url"]]
    assert records[0]["id"] == FAMILIES[family]["source"]
    assert records[0]["retrieved_at"] == retrieved.isoformat()
    assert rows and all(row["retrieved_at"] == retrieved.isoformat() for row in rows)

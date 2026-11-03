from datetime import datetime, timezone

import pytest

from market_brief import collect
from market_brief.evidence import normalize_observation, normalize_packet
from market_brief.render import release_cards
from market_brief.stats_can import (
    DAILY,
    SCHEDULE,
    StatisticsCanadaError,
    lfs_actual,
    lfs_schedule,
)

SCHEDULE_PAGE = (
    "<h2>Upcoming releases</h2><p>Releases are published in <em>The Daily</em> each working day at "
    '8:30 a.m. Eastern.</p><h3 class="hd1">November&#160;6</h3>'
    '<ol class="upcominglist"><li><div class="rtitle">Labour Force Survey, '
    "<span>October 2026</span></div></li></ol>"
)
DAILY_PAGE = (
    "<html><head><title>The Daily — Labour Force Survey, September 2026</title>"
    '<meta name="dcterms.title" content="The Daily — Labour Force Survey, September 2026" />'
    '<meta name="dcterms.description" content="Employment declined by 68,000 (-0.3%) in September and '
    "the employment rate fell 0.2 percentage points to 60.6%. The unemployment rate increased 0.1 "
    'percentage points to 6.5%." />'
    '<meta name="dcterms.issued" title="W3CDTF" content="2026-10-09" />'
    '<meta name="dcterms.modified" title="W3CDTF" content="2026-10-09" /></head></html>'
)
OCTOBER_PAGE = (
    DAILY_PAGE.replace("September 2026", "October 2026")
    .replace("in September", "in October")
    .replace("2026-10-09", "2026-11-06")
)


def test_official_schedule_maps_lfs_to_dated_eastern_release():
    now = datetime(2026, 11, 3, 15, tzinfo=timezone.utc)
    event = lfs_schedule(SCHEDULE_PAGE, now, datetime(2026, 11, 3, 15, 1, tzinfo=timezone.utc))
    assert {
        key: event[key] for key in ("id", "title", "source_id", "published_at", "checked_at", "scheduled_at", "status")
    } == {
        "id": "statcan-lfs-event-20261106",
        "title": "Statistics Canada Labour Force Survey for October 2026",
        "source_id": "statcan-calendar",
        "published_at": None,
        "checked_at": "2026-11-03T15:01:00+00:00",
        "scheduled_at": "2026-11-06T13:30:00+00:00",
        "status": "SCHEDULED",
    }


def test_daily_release_keeps_canadian_units_and_separate_metric_identity():
    release = lfs_actual(DAILY_PAGE, datetime(2026, 10, 9, 13, 30, tzinfo=timezone.utc), "2026-09")
    assert release["period"] == "2026-09"
    assert release["published_at"] == "2026-10-09T13:30:00+00:00"
    assert release["values"] == {"employment_change": -68_000, "unemployment_rate": 6.5}


@pytest.mark.parametrize(
    "page,period", [(DAILY_PAGE, "2026-08"), (DAILY_PAGE.replace("2026-10-09", "2026-10-10"), "2026-09")]
)
def test_daily_release_rejects_wrong_period_or_release_date(page, period):
    with pytest.raises(StatisticsCanadaError):
        lfs_actual(page, datetime(2026, 10, 9, 13, 30, tzinfo=timezone.utc), period)


def test_daily_release_rejects_unreadable_headlines_and_old_release_date():
    damaged_pages = (
        DAILY_PAGE.replace("Employment declined", "Jobs declined"),
        DAILY_PAGE.replace(
            'dcterms.modified" title="W3CDTF" content="2026-10-09"',
            'dcterms.modified" title="W3CDTF" content="2026-10-10"',
        ),
    )
    for page in damaged_pages:
        with pytest.raises(StatisticsCanadaError):
            lfs_actual(page, datetime(2026, 10, 9, 13, 30, tzinfo=timezone.utc), "2026-09")
    row = dict(
        id="statcan-lfs-employment-change",
        topic="Statistics Canada Labour Force Survey",
        metric="Canadian employment change",
        value=-68_000,
        unit="persons",
        baseline="Canada LFS",
        frequency="release",
        observed_at="2026-10-09T13:30:00+00:00",
        retrieved_at="2026-10-10T13:31:00+00:00",
        source_id="statcan-lfs",
        reference_period="2026-09",
    )
    assert normalize_observation(row, datetime(2026, 10, 10, 13, 31, tzinfo=timezone.utc))["status"] == "STALE"


def test_schedule_fails_closed_without_explicit_lfs_listing_or_release_time():
    now = datetime(2026, 11, 3, 15, tzinfo=timezone.utc)
    with pytest.raises(StatisticsCanadaError):
        lfs_schedule(
            SCHEDULE_PAGE.replace("8:30 a.m. Eastern", "8:00 a.m. Eastern"),
            now,
            datetime(2026, 11, 3, 15, 1, tzinfo=timezone.utc),
        )
    assert (
        lfs_schedule(
            SCHEDULE_PAGE.replace("Labour Force Survey", "Labour survey"),
            now,
            datetime(2026, 11, 3, 15, 1, tzinfo=timezone.utc),
        )
        is None
    )


def test_url_paths_are_fixed_official_pages():
    assert SCHEDULE == "https://www150.statcan.gc.ca/n1/dai-quo/cal2-eng.htm"
    assert DAILY.format(stamp="261106") == "https://www150.statcan.gc.ca/n1/daily-quotidien/261106/dq261106a-eng.htm"
    assert collect.allowed(SCHEDULE)
    assert collect.allowed(DAILY.format(stamp="261106"))
    assert not collect.allowed("https://www150.statcan.gc.ca/n1/daily-quotidien/anything")


def test_adapter_is_inert_until_explicit_source_enablement(tmp_path, monkeypatch):
    config = tmp_path / "sources.json"
    config.write_text('{"statcan_lfs": false}')
    monkeypatch.setattr(collect, "SOURCES_CONFIG", config)
    calls = []

    def forbidden(url, deadline):
        calls.append(url)
        raise AssertionError("disabled source must not make a request")

    assert collect.stats_can_collection(datetime(2026, 11, 3, 15, tzinfo=timezone.utc), 100, forbidden) == ([], [], [])
    assert calls == []


def test_enabled_adapter_reads_only_listed_release_after_official_time(tmp_path, monkeypatch):
    config = tmp_path / "sources.json"
    config.write_text('{"statcan_lfs": true}')
    monkeypatch.setattr(collect, "SOURCES_CONFIG", config)
    fixed_now = datetime(2026, 11, 6, 13, 29, tzinfo=timezone.utc)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(collect, "datetime", FixedDatetime)
    calls = []

    def fetcher(url, deadline):
        calls.append(url)
        return SCHEDULE_PAGE if url == SCHEDULE else OCTOBER_PAGE

    _, scheduled_events, no_rows = collect.stats_can_collection(fixed_now, 100, fetcher)
    assert calls == [SCHEDULE]
    assert scheduled_events[0]["scheduled_at"] == "2026-11-06T13:30:00+00:00" and no_rows == []
    fixed_now = datetime(2026, 11, 6, 14, tzinfo=timezone.utc)
    calls.clear()
    sources, events, rows = collect.stats_can_collection(fixed_now, 100, fetcher)
    assert calls == [SCHEDULE, DAILY.format(stamp="261106")]
    assert [row["id"] for row in rows] == ["statcan-lfs-employment-change", "statcan-lfs-unemployment-rate"]
    assert rows[0]["value"] == -68_000 and rows[0]["unit"] == "persons"
    assert rows[1]["value"] == 6.5 and rows[1]["unit"] == "percent"
    assert rows[0]["reference_period"] == "2026-10"
    assert rows[0]["observed_at"] == "2026-11-06T13:30:00+00:00"
    assert not {"revised_month", "revised_from", "revised_to"}.intersection(rows[0])
    assert rows[0]["retrieved_at"] == "2026-11-06T14:00:00+00:00"
    assert normalize_observation(rows[0], fixed_now)["status"] == "AVAILABLE"
    assert sources[0]["id"] == "statcan-calendar" and sources[1]["id"] == "statcan-lfs"
    assert sources[1]["name"].startswith(
        "Adapted from Statistics Canada, Labour Force Survey (The Daily), October 2026."
    )
    assert events[0]["source_id"] == "statcan-calendar"
    packet = normalize_packet(
        dict(mode="LIVE", sources=sources, observations=rows, history=[], derived=[], events=events, context_items=[]),
        fixed_now,
        "LIVE",
    )
    assert {row["id"]: row["status"] for row in packet["observations"]} == {
        "statcan-lfs-employment-change": "AVAILABLE",
        "statcan-lfs-unemployment-rate": "AVAILABLE",
    }
    card = release_cards(rows, events)
    assert card[0]["title"] == "Statistics Canada Labour Force Survey"
    assert card[0]["events"] == ["statcan-lfs-event-20261106"]
    assert "−68,000 persons" in card[0]["lines"][0]["values"][0]

"""Global Market Glance: one optional overseas line, collected once a day, carried in the published page.

Off in production (`glance.SOURCE is None`: no free source permits public display of all five indices). These tests
drive it with a stand-in source.
"""

import json
from datetime import datetime, timezone

import pytest
from test_cadence import TUE, Day

from market_brief import glance

UTC = timezone.utc


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def bars(last, days=("2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13"),
         closes=(100, 101, 102, 103, 104, 98.5)):
    return [(d, c) for d, c in zip(days, closes) if d <= last]


class Source:
    """A stand-in for the admitted source: counts collections and the codes each one asked for."""

    def __init__(self, sessions=None, fail=False):
        self.calls, self.fail = [], fail
        self.sessions = sessions or {}

    def __call__(self, codes):
        self.calls.append(list(codes))
        if self.fail:
            raise OSError("down")
        return {code: bars(self.sessions.get(code, "2026-10-13")) for code in codes}


def page_for(record):
    return f'<html><details class="proof glance" data-glance="{glance.html.escape(glance.view(record, {})["data"])}">'


# --- percentile ---------------------------------------------------------------------------------------------------

def test_percentile_is_the_share_of_history_strictly_smaller():
    history = [0.1 * n for n in range(1, 101)]  # 0.1 .. 10.0, sorted
    assert glance.percentile(5.05, history) == 50.0
    assert glance.percentile(-5.05, history) == 50.0  # absolute move
    assert glance.percentile(0.1, history) == 0.0  # equal to the smallest: nothing is strictly smaller
    assert glance.percentile(10.5, history) == 100.0
    baseline = {"KOSPI": dict(start="2023-10-13", end="2026-10-12", sessions=100, abs_returns=history)}
    record = dict(day="2026-10-13", as_of="2026-10-13T13:00:00+00:00", attempts=1, status="ok", reason="",
                  markets={"KOSPI": dict(session="2026-10-13", previous="2026-10-12", change_pct=-9.6)})
    item = glance.view(record, baseline)["markets"][0]
    assert item["unusual"] and item["rank"] == "larger than 95% of its daily moves"
    record["markets"]["KOSPI"]["change_pct"] = -9.4
    assert not glance.view(record, baseline)["markets"][0]["unusual"]  # 93.0 < 95


def test_the_baseline_is_built_once_from_closes_and_a_malformed_one_marks_nothing(tmp_path):
    rows = [("KOSPI", f"2023-01-{d:02d}", 100 + d) for d in range(1, 11)] + [("XYZ", "2023-01-01", 1)]
    built = glance.build_baseline(rows)
    assert list(built["markets"]) == ["KOSPI"]
    moves = built["markets"]["KOSPI"]["abs_returns"]
    assert moves == sorted(moves) and len(moves) == 9 and built["markets"]["KOSPI"]["start"] == "2023-01-01"
    good = dict(markets={"DAX": dict(start="a", end="b", abs_returns=[0.5] * glance.MIN_BASELINE)})
    for broken in ("not json", json.dumps(dict(markets={"DAX": dict(abs_returns=[0.5] * 10)})),
                   json.dumps(dict(markets={"DAX": dict(abs_returns=[2.0, 1.0] * 200)})), json.dumps([])):
        (tmp_path / "b.json").write_text(broken)
        assert glance.load_baseline(tmp_path / "b.json") == {}
    (tmp_path / "b.json").write_text(json.dumps(good))
    assert set(glance.load_baseline(tmp_path / "b.json")) == {"DAX"}
    assert glance.load_baseline(tmp_path / "missing.json") == {}


# --- completed-session selection, holidays, stale -----------------------------------------------------------------

@pytest.mark.parametrize("calendar,when,expected", [
    ("XKRX", "2026-10-13T13:00:00", "2026-10-13"),  # Seoul closed 06:30 UTC: today's session is complete
    ("XKRX", "2026-10-13T05:00:00", "2026-10-12"),  # still trading: the previous session
    ("XETR", "2026-10-13T13:00:00", "2026-10-12"),  # Frankfurt mid-session at the US premarket
    ("XETR", "2026-10-13T16:00:00", "2026-10-13"),
    ("XTKS", "2026-10-12T13:00:00", "2026-10-09"),  # Tokyo holiday (Sports Day): Friday's session
    ("XKRX", "2026-10-09T13:00:00", "2026-10-08"),  # Seoul holiday (Hangul Day)
])
def test_expected_session_is_the_latest_completed_one(calendar, when, expected):
    assert glance.expected_session(calendar, at(when)) == expected


def test_a_session_counts_only_when_the_source_has_it_and_the_bar_before_it():
    assert glance.completed(bars("2026-10-13"), "2026-10-13") == dict(
        session="2026-10-13", previous="2026-10-12", change_pct=-5.29)
    assert glance.completed(bars("2026-10-12"), "2026-10-13") is None  # the source has not posted it yet
    assert glance.completed([("2026-10-13", 98.5)], "2026-10-13") is None  # no previous close
    assert glance.completed([("2026-10-12", "x"), ("2026-10-13", float("nan"))], "2026-10-13") is None
    assert glance.completed(None, "2026-10-13") is None


def test_a_market_on_holiday_or_still_trading_shows_its_session_weekday_never_as_today():
    source = Source()
    record = glance.resolve(at("2026-10-12T13:00:00"), "", source)  # US Monday; Tokyo closed
    items = {i["label"]: i for i in glance.view(record, {})["markets"]}
    assert record["markets"]["N225"]["session"] == "2026-10-09" and items["Nikkei"]["day"] == "Fri"
    assert record["markets"]["KOSPI"]["session"] == "2026-10-12" and items["KOSPI"]["day"] == ""
    assert items["DAX"]["day"] == "Fri"  # Frankfurt still trading Monday: Friday's completed session
    assert items["KOSPI"]["dates"] == "Mon, Oct 12 vs Fri, Oct 9"


# --- fetching: once a day, bounded retries ------------------------------------------------------------------------

def test_one_collection_a_day_is_reused_and_the_dax_is_not_fetched_again_after_frankfurt_closes():
    source = Source()
    first = glance.resolve(at("2026-10-13T13:00:00"), "", source)
    assert source.calls == [list(glance.CODES)] and first["attempts"] == 1
    assert first["markets"]["DAX"]["session"] == "2026-10-12"
    later = glance.resolve(at("2026-10-13T17:00:00"), page_for(first), source)  # Frankfurt closed at 15:30 UTC
    assert len(source.calls) == 1 and later == first


def test_only_a_genuinely_missing_session_is_retried_and_never_more_than_three_times():
    source = Source(sessions={"KOSPI": "2026-10-12"})  # the source has not posted Seoul's close
    record = glance.resolve(at("2026-10-13T13:00:00"), "", source)
    assert "KOSPI" not in record["markets"] and len(record["markets"]) == 4
    for _ in range(5):
        record = glance.resolve(at("2026-10-13T14:00:00"), page_for(record), source)
    assert source.calls[1:] == [["KOSPI"], ["KOSPI"]] and record["attempts"] == glance.MAX_ATTEMPTS
    assert glance.view(record, {})["markets"][0]["text"] == "—"


def test_yesterdays_record_is_not_reused():
    source = Source()
    monday = glance.resolve(at("2026-10-12T13:00:00"), "", source)
    tuesday = glance.resolve(at("2026-10-13T13:00:00"), page_for(monday), source)
    assert len(source.calls) == 2 and tuesday["day"] == "2026-10-13"


# --- missing or malformed data, failure ---------------------------------------------------------------------------

@pytest.mark.parametrize("page", [
    "", "<html>", '<details data-glance="{not json">', '<details data-glance="[]">',
    '<details data-glance="{&quot;day&quot;:&quot;x&quot;}">',
])
def test_a_malformed_carried_record_is_ignored(page):
    assert glance.read_carried(page) is None


def test_source_failure_or_garbage_is_a_quiet_unavailable_state():
    record = glance.resolve(at("2026-10-13T13:00:00"), "", Source(fail=True))
    assert record["status"] == "unavailable" and record["markets"] == {}
    assert glance.view(record, {})["status"] == "unavailable"
    record = glance.resolve(at("2026-10-13T13:00:00"), "", lambda codes: "garbage")
    assert record["status"] == "unavailable"


def test_off_without_a_source():
    assert glance.SOURCE is None
    assert glance.resolve(at("2026-10-13T13:00:00"), "") is None
    assert glance.view(None) is None


# --- through the real run: preservation across refreshes, isolation, publication ----------------------------------

@pytest.fixture
def day(monkeypatch, tmp_path):
    return Day(monkeypatch, tmp_path)


def publish(day, checkpoint):
    (day.root / "publish").mkdir(exist_ok=True)
    (day.root / "publish/index.html").write_text(day.page(checkpoint))


def test_the_line_survives_refreshes_without_another_collection_and_stays_out_of_evidence_and_context(day, monkeypatch):
    calls = []

    def source(codes):  # Tuesday Sep 8, 2026: Asia's sessions done; Frankfurt's latest completed is Monday
        calls.append(list(codes))
        return {c: [("2026-09-04", 99.0), ("2026-09-07", 100.0), ("2026-09-08", 101.5)] for c in codes}

    monkeypatch.setattr(glance, "SOURCE", source)
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    premarket = day.page("PREMARKET")
    assert "Overseas · KOSPI +1.50%" in premarket and "DAX +1.01% Mon" in premarket
    publish(day, "PREMARKET")
    assert day.run(f"{TUE}T13:31:00+00:00", "OPEN_1M") == 0
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M") == 0  # reads the 6:02 page: OPEN_1M was not published
    assert len(calls) == 1
    for checkpoint in ("OPEN_1M", "OPEN_30M"):
        assert glance.read_carried(day.page(checkpoint)) == glance.read_carried(premarket)
    for checkpoint in ("PREMARKET", "OPEN_30M"):
        for name in ("evidence.json", "analyst_context.json"):
            path = day.folder(checkpoint) / name
            if path.exists():
                assert "glance" not in path.read_text() and "KOSPI" not in path.read_text()


def test_a_failing_source_never_blocks_publication(day, monkeypatch):
    monkeypatch.setattr(glance, "SOURCE", Source(fail=True))
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    assert "Overseas · unavailable today" in day.page("PREMARKET")


def test_with_the_glance_off_the_page_carries_no_line(day):
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    assert "data-glance" not in day.page("PREMARKET") and "Overseas" not in day.page("PREMARKET")


def test_a_broken_glance_view_renders_the_edition_without_it():
    from test_pipeline import fixture_packet, narrative

    from market_brief.render import render

    _, plain = render(fixture_packet(), narrative())
    _, page = render(fixture_packet(), narrative(), glance=dict(status="ok", markets=None, data="x"))
    assert page == plain

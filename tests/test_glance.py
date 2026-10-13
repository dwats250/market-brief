"""Global Equity Proxies: five US-listed country ETFs on the existing Alpaca integration, display only."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from test_cadence import TUE, Day

from market_brief import glance

UTC = timezone.utc
NOW = datetime(2026, 10, 13, 14, 2, 30, tzinfo=UTC)  # Tue 10:02 ET
SESSION = dict(open="2026-10-13T13:30:00+00:00", close="2026-10-13T20:00:00+00:00")


def snap(price, when=NOW - timedelta(seconds=20), prev=100.0):
    return {"latestTrade": {"t": when.isoformat().replace("+00:00", "Z"), "p": price}, "prevDailyBar": {"c": prev}}


def payload(**overrides):
    base = dict(EWY=snap(94.79), EWJ=snap(98.2), EWT=snap(99.4), EWH=snap(100.21), EWG=snap(99.88))
    return {**base, **overrides}


def test_five_proxies_under_the_existing_freshness_rules():
    record = glance.resolve(NOW, SESSION, "OPEN_30M", clock=lambda: NOW, fetch=lambda codes: payload(
        EWH=snap(100.21, when=NOW - timedelta(minutes=45)),        # older than twenty minutes: stale
        EWG=snap(99.88, when=NOW - timedelta(days=1))))             # yesterday's trade: no print today
    view = glance.view(record, {})
    assert [m["code"] for m in view["markets"]] == ["EWY", "EWJ", "EWT", "EWH", "EWG"]
    assert [m["text"] for m in view["markets"]] == ["−5.21%", "−1.80%", "−0.60%", "—", "—"]
    assert view["markets"][3]["rank"] == "intraday observation older than twenty minutes"
    assert view["markets"][4]["rank"] == "no IEX trade yet this session"
    assert view["session"] == "Tue, Oct 13" and view["markets"][0]["when"] == "10:02 AM ET"


def test_an_after_hours_print_is_not_shown_at_the_close():
    late = NOW.replace(hour=20, minute=0, second=40)
    record = glance.resolve(late + timedelta(seconds=30), SESSION, "CLOSE_1M",
                            lambda codes: payload(EWY=snap(94.79, when=late)), lambda: late + timedelta(seconds=30))
    assert record["markets"]["EWY"]["change_pct"] is None
    assert "after-hours" in record["markets"]["EWY"]["reason"]


def test_percentile_and_highlight():
    history = sorted(0.02 * n for n in range(1, 301))  # 0.02 .. 6.00
    assert glance.percentile(3.0, history) == 49.7 and glance.percentile(-3.0, history) == 49.7
    assert glance.percentile(0.02, history) == 0.0 and glance.percentile(9.0, history) == 100.0
    baseline = {"EWY": dict(start="2023-10-13", end="2026-10-12", sessions=300, abs_returns=history)}
    view = glance.view(glance.resolve(NOW, SESSION, "OPEN_30M", lambda codes: payload(EWY=snap(93.0)),
                                      lambda: NOW), baseline)
    assert view["markets"][0]["unusual"] and view["markets"][0]["rank"] == "largest in its baseline"
    assert not view["markets"][1]["unusual"] and view["window"] == "2023-10-13 to 2026-10-12"


def test_the_baseline_is_built_once_and_a_malformed_one_marks_nothing(tmp_path):
    built = glance.build_baseline([("EWY", f"2023-01-{d:02d}", 100 + d) for d in range(1, 11)] + [("XYZ", "x", 1)])
    assert list(built["markets"]) == ["EWY"] and len(built["markets"]["EWY"]["abs_returns"]) == 9
    for broken in ("not json", json.dumps(dict(markets={"EWY": dict(abs_returns=[0.5] * 10)})),
                   json.dumps(dict(markets={"EWY": dict(abs_returns=[2.0, 1.0] * 200)})), json.dumps([])):
        (tmp_path / "b.json").write_text(broken)
        assert glance.load_baseline(tmp_path / "b.json") == {}
    assert glance.load_baseline(tmp_path / "missing.json") == {}
    view = glance.view(glance.resolve(NOW, SESSION, "OPEN_30M", lambda codes: payload(), lambda: NOW), {})
    assert not any(m["unusual"] for m in view["markets"]) and view["window"] == ""


def raise_down(codes):
    raise OSError("down")


@pytest.mark.parametrize("fetch", [raise_down, lambda codes: "garbage", lambda codes: {"EWY": None}])
def test_any_failure_is_quiet(fetch):
    view = glance.view(glance.resolve(NOW, SESSION, "OPEN_30M", fetch), {})
    assert view["status"] in {"ok", "unavailable"}


def test_without_credentials_there_is_no_line(monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    assert glance.resolve(NOW, SESSION, "OPEN_30M") is None and glance.view(None) is None


def test_one_request_per_run_for_the_five_symbols():
    calls = []
    glance.resolve(NOW, SESSION, "OPEN_30M", lambda codes: calls.append(codes) or payload())
    assert calls == [glance.CODES]


# --- through the real run: isolation and publication --------------------------------------------------------------

@pytest.fixture
def day(monkeypatch, tmp_path):
    return Day(monkeypatch, tmp_path)


def test_the_line_publishes_and_stays_out_of_evidence_and_context(day, monkeypatch):
    when = datetime.fromisoformat(f"{TUE}T14:00:50+00:00")
    monkeypatch.setattr(glance.resolve, "__defaults__", (
        lambda codes: {c: snap(101.0, when=when) for c in codes}, lambda: when + timedelta(seconds=12)))
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M") == 0
    page = day.page("OPEN_30M")
    assert "Global equity proxies<br>EWY +1.00% · EWJ +1.00%" in page and "They are proxies" in page
    assert "EWG +1.00%</summary>" in page and "Prices from the Tue, Sep 8 US session." in page
    for name in ("evidence.json", "analyst_context.json"):
        text = (day.folder("OPEN_30M") / name).read_text()
        assert "EWY" not in text and "glance" not in text


def test_a_failing_source_never_blocks_publication(day, monkeypatch):
    monkeypatch.setattr(glance.resolve, "__defaults__", (raise_down, glance.utc_now))
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    assert "Global equity proxies · unavailable" in day.page("PREMARKET")


def test_without_credentials_the_page_carries_no_line(day, monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    assert "Global equity proxies" not in day.page("PREMARKET")


def test_a_broken_view_renders_the_edition_without_it():
    from test_pipeline import fixture_packet, narrative

    from market_brief.render import render

    _, plain = render(fixture_packet(), narrative())
    _, page = render(fixture_packet(), narrative(), glance=dict(status="ok", markets=None))
    assert page == plain


def test_the_date_shows_on_the_ticker_line_only_when_it_is_not_the_page_date():
    from test_pipeline import fixture_packet, narrative

    from market_brief.render import render

    packet = fixture_packet()
    year, month, day = (int(part) for part in packet["run"]["session"]["date"].split("-"))
    today = datetime(year, month, day)
    markets = [dict(code=c, label=c, text="+1.00%", unusual=False, rank="", when="") for c in glance.CODES]
    for when, on_line in ((today, False), (today - timedelta(days=4), True)):
        stamp = f"{glance.WEEKDAYS[when.weekday()]}, {when:%b} {when.day}"
        _, page = render(packet, narrative(), glance=dict(status="ok", markets=markets, session=stamp,
                                                          threshold="95", window=""))
        assert (f"EWG +1.00% · {stamp}</summary>" in page) is on_line
        assert "EWG +1.00%</summary>" in page or on_line
        assert f"Prices from the {stamp} US session." in page

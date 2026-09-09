"""Daily history admission: strict, with one bounded same-day post-close publication-lag exception."""

from datetime import datetime, timedelta, timezone

import exchange_calendars as xcals
from test_pipeline import narrative

from market_brief.evidence import ROOT, finalize_coverage, normalize_packet, read_json
from market_brief.metrics import derive
from market_brief.render import compact_equity_rows, presentation

UNIVERSE = read_json(ROOT / "config/universe.json")


def utc(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def packet_at(now, last_history_date="2026-09-04", intraday=True, checkpoint="PREMARKET",
              intraday_value=-0.53, events=()):
    raw = read_json(ROOT / "tests/fixtures/evidence.sample.json")
    raw["target_time"] = now.isoformat()
    raw["events"].extend(events)
    for row in raw["events"]:
        row["checked_at"] = now.isoformat()
    for row in raw["history"]:
        row["retrieved_at"] = now.isoformat()
        shift = (datetime.fromisoformat(last_history_date) - datetime.fromisoformat(row["dates"][-1])).days
        if shift:
            # Rebuild the date axis so it still ends on the requested session.
            cal = xcals.get_calendar("XNYS")
            sessions = cal.sessions_in_range("2026-01-01", last_history_date)
            row["dates"] = [s.date().isoformat() for s in sessions[-len(row["closes"]):]]
    for row in raw["observations"]:
        row["retrieved_at"] = now.isoformat()
    if intraday:
        raw["observations"].append(dict(
            id="SPY-intraday", topic="SPY", metric="premarket return", value=intraday_value, unit="%",
            baseline="latest trade versus previous regular close", frequency="intraday",
            observed_at=(now - timedelta(minutes=4)).isoformat(),
            retrieved_at=now.isoformat(), source_id="sample-prices", status="AVAILABLE", reason=""))
    packet = normalize_packet(raw, now, "SAMPLE", checkpoint)
    derive(packet, UNIVERSE)
    return finalize_coverage(packet)


def derived_ids(packet):
    return {row["id"] for row in packet["derived"]}


def test_same_day_post_close_lag_admits_prior_completed_history():
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"))
    assert packet["run"]["session"]["previous_session"] == "2026-09-08"
    assert "SPY-daily" in derived_ids(packet)
    assert "SPY-r20" in derived_ids(packet)
    assert "QQQ-spread20" in derived_ids(packet)
    assert packet["history_errors"] == []
    assert packet["history_lag"] == {"completed_session": "2026-09-08", "history_through": "2026-09-04"}
    assert any("2026-09-04" in item and "2026-09-08" in item for item in packet["coverage"]["limitations"])


def test_lagged_history_rows_keep_their_own_observation_date():
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"))
    daily = next(row for row in packet["derived"] if row["id"] == "SPY-daily")
    assert daily["observed_at"] == "2026-09-04"
    assert daily["status"] == "BACKGROUND"


def test_today_cell_comes_from_intraday_not_lagged_daily_return():
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"))
    facts = [row for row in [*packet["observations"], *packet["derived"]] if row["value"] is not None]
    row = compact_equity_rows(facts, ["SPY"])[0]
    assert row["today"]["id"] == "SPY-intraday"
    assert row["today"]["display"] == "-0.53 %"
    assert row["r20"]["display"] != "n/a"


def test_exception_rejects_history_two_sessions_behind():
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"), last_history_date="2026-09-03")
    assert "SPY-daily" not in derived_ids(packet)
    assert packet["history_errors"]
    assert "history_lag" not in packet or not packet["history_lag"]


def test_exception_does_not_apply_next_morning():
    packet = packet_at(utc("2026-09-09T12:45:00+00:00"), intraday=False)
    assert packet["run"]["session"]["previous_session"] == "2026-09-08"
    assert "SPY-daily" not in derived_ids(packet)
    assert packet["history_errors"]


def test_exception_does_not_apply_during_the_session():
    packet = packet_at(utc("2026-09-08T17:00:00+00:00"))
    assert packet["run"]["session"]["previous_session"] == "2026-09-04"
    assert "SPY-daily" in derived_ids(packet)
    assert not packet.get("history_lag")


def test_completed_bar_when_published_uses_normal_admission():
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"), last_history_date="2026-09-08")
    assert "SPY-daily" in derived_ids(packet)
    assert not packet.get("history_lag")
    assert packet["history_errors"] == []


def test_lagged_daily_return_never_fills_today_without_current_prints():
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"), intraday=False)
    assert packet["history_lag"]
    facts = [row for row in [*packet["observations"], *packet["derived"]] if row["value"] is not None]
    row = compact_equity_rows(facts, ["SPY"], allow_daily_today=False)[0]
    assert row["today"]["display"] == "n/a"
    assert row["r20"]["display"] != "n/a"
    view = presentation(packet, narrative())
    equities = next(s for s in view["sections"] if s["key"] == "equities")
    assert all(r["today"]["display"] == "n/a" for r in equities["mega_rows"] + equities["sector_rows"])


def test_daily_return_still_fills_today_before_the_open():
    packet = packet_at(utc("2026-09-08T12:45:00+00:00"), intraday=False)
    assert not packet.get("history_lag")
    view = presentation(packet, narrative())
    equities = next(s for s in view["sections"] if s["key"] == "equities")
    assert any(r["today"]["display"] != "n/a" for r in equities["mega_rows"])

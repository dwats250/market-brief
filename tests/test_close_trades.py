"""The close finds each symbol's last regular-session trade (WP-MB-TRUTH-01, item 3).

Since Oct 16 (Market Review gaps.md), and on five consecutive sessions to Nov 20, 2026, the 4:01 PM ET close page had no
regular-session print for some symbols (Nov 20: NVDA, META, XLC, XLRE). Alpaca's snapshot reports only the latest
trade; by 4:01 PM that is often an after-hours trade, which admission rightly refuses (PR #50), and nothing then
looked for the trade that ended the session. The collector now asks Alpaca's IEX historical trades for that symbol's
last trade at or before the 4:00 PM ET close. An after-hours value is never substituted: when no such trade comes back
the symbol stays refused, and a failed lookup is named in the source's reason.
"""

from datetime import datetime, timedelta, timezone

import pytest

import market_brief.collect as collect
from market_brief.collect import SourceError, alpaca_collect
from market_brief.evidence import normalize_observation

OPEN = datetime(2026, 11, 20, 14, 30, tzinfo=timezone.utc)       # Fri Nov 20, 9:30 AM ET (EST)
CLOSE = datetime(2026, 11, 20, 21, 0, tzinfo=timezone.utc)       # 4:00 PM ET
START = CLOSE + timedelta(seconds=65)                            # CLOSE_1M collection
SNAPPED = START + timedelta(seconds=2)


def iso(moment):
    return moment.isoformat().replace("+00:00", "Z")


def daily_bars():
    days = ["2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19"]
    return [{"t": f"{day}T05:00:00Z", "c": 100.0} for day in days]


def snapshot(trade_at, price=99.0):
    return {"latestTrade": {"t": iso(trade_at), "p": price},
            "prevDailyBar": {"t": "2026-11-19T05:00:00Z", "c": 100.0}}


class Clock(datetime):
    moments = []

    @classmethod
    def now(cls, tz=None):
        moment = cls.moments.pop(0) if len(cls.moments) > 1 else cls.moments[0]
        return moment if tz else moment.replace(tzinfo=None)


def run(monkeypatch, snapshots, trades, start=START, snapped=SNAPPED):
    """`trades` maps a symbol to the historical-trades response, or to an exception to raise."""
    requests = []

    def fake(path, params, deadline, key_id, secret_key):
        requests.append((path, dict(params)))
        if path == "/v2/stocks/bars":
            return {"bars": {symbol: daily_bars() for symbol in snapshots}}
        if path == "/v2/stocks/snapshots":
            return snapshots
        symbol = path.split("/")[3]
        answer = trades[symbol]
        if isinstance(answer, Exception):
            raise answer
        return answer

    Clock.moments = [start + timedelta(seconds=1), snapped]
    monkeypatch.setattr(collect, "datetime", Clock)
    monkeypatch.setattr(collect, "_alpaca_request", fake)
    result = alpaca_collect(start, tuple(snapshots), "id", "secret")
    return result, requests


def test_an_after_hours_latest_trade_is_replaced_by_the_last_regular_session_trade(monkeypatch):
    result, requests = run(monkeypatch, {
        "SPY": snapshot(CLOSE - timedelta(seconds=0.6), 96.49),                # already a regular-session trade
        "NVDA": snapshot(CLOSE + timedelta(seconds=20), 120.0),                # after-hours
    }, {"NVDA": {"symbol": "NVDA", "trades": [{"t": iso(CLOSE - timedelta(seconds=0.2)), "p": 88.34}],
                 "next_page_token": "x"}})
    rows = {row["topic"]: row for row in result["observations"]}
    nvda = rows["NVDA"]
    assert nvda["observed_at"] == iso(CLOSE - timedelta(seconds=0.2))
    assert nvda["value"] == pytest.approx(-11.66)
    assert nvda["reason"] == ("feed=IEX; last regular-session trade at or before the close, from IEX historical "
                              "trades; prior close 2026-11-19 regular session")
    assert rows["SPY"]["reason"] == "feed=IEX; latest trade timestamp; prior close 2026-11-19 regular session"
    # One lookup, for the after-hours symbol only, bounded by the session and asking for the latest trade.
    lookups = [(path, params) for path, params in requests if path.endswith("/trades")]
    assert lookups == [("/v2/stocks/NVDA/trades", dict(start=iso(OPEN), end=iso(CLOSE), limit=1, feed="iex",
                                                       sort="desc"))]
    assert result["sources"][1]["status"] == "AVAILABLE"
    # Admitted like every close print: a regular-session trade, more than a minute old at collection.
    admitted = normalize_observation(nvda, START, CLOSE, CLOSE)
    assert (admitted["status"], admitted["value"]) == ("DELAYED", pytest.approx(-11.66))


@pytest.mark.parametrize("answer, why", [
    ({"symbol": "META", "trades": []}, "no regular-session trade returned"),
    ({"symbol": "META", "trades": None}, "no regular-session trade returned"),
    ({"symbol": "META", "trades": [{"t": iso(CLOSE + timedelta(seconds=1)), "p": 600.0}]},
     "no regular-session trade returned"),
    ({"symbol": "META", "trades": [{"t": iso(OPEN - timedelta(minutes=1)), "p": 600.0}]},
     "no regular-session trade returned"),
    ({"symbol": "META", "trades": [{"t": iso(CLOSE - timedelta(seconds=1)), "p": "nan"}]},
     "no regular-session trade returned"),
    (SourceError("Alpaca HTTP 403"), "Alpaca HTTP 403"),
])
def test_no_after_hours_value_is_ever_substituted(monkeypatch, answer, why):
    after_hours = CLOSE + timedelta(seconds=30)
    result, _ = run(monkeypatch, {"META": snapshot(after_hours, 590.0)}, {"META": answer})
    (meta,) = result["observations"]
    # The snapshot's own after-hours trade stands unchanged, so admission refuses it as before.
    assert meta["observed_at"] == iso(after_hours) and meta["reason"].startswith("feed=IEX; latest trade timestamp")
    refused = normalize_observation(meta, START, CLOSE, CLOSE)
    assert (refused["status"], refused["value"]) == ("UNAVAILABLE", None)
    source = result["sources"][1]
    assert source["status"] == "DEGRADED"
    assert source["reason"] == f"last regular-session trade not found for META ({why})"


def test_no_lookup_during_the_session_or_for_a_regular_session_trade(monkeypatch):
    during = datetime(2026, 11, 20, 20, 2, 20, tzinfo=timezone.utc)
    _, requests = run(monkeypatch, {"SPY": snapshot(during - timedelta(seconds=1))}, {},
                      start=during, snapped=during + timedelta(seconds=1))
    assert not [path for path, _ in requests if path.endswith("/trades")]
    _, requests = run(monkeypatch, {"SPY": snapshot(CLOSE - timedelta(seconds=1))}, {})
    assert not [path for path, _ in requests if path.endswith("/trades")]


@pytest.mark.parametrize("answer", [
    {"symbol": "META", "trades": [{"t": iso(CLOSE - timedelta(seconds=1)), "p": 590.0}]},   # another instrument
    {"trades": [{"t": iso(CLOSE - timedelta(seconds=1)), "p": 88.0}]},                       # identity missing
    {"symbol": "NVDA", "trades": [{"t": None, "p": 88.0}]},
    {"symbol": "NVDA", "trades": [{"t": 1763672399, "p": 88.0}]},
    {"symbol": "NVDA", "trades": [{"t": "yesterday", "p": 88.0}]},
    ["not", "an", "object"],
])
def test_a_malformed_or_foreign_answer_is_one_failed_lookup(monkeypatch, answer):
    """Review P3/P4: the answer must name the requested symbol and carry a readable trade time; anything else fails that
    one lookup and never stops collection."""
    result, _ = run(monkeypatch, {"NVDA": snapshot(CLOSE + timedelta(seconds=20), 120.0),
                                  "SPY": snapshot(CLOSE - timedelta(seconds=1), 96.49)}, {"NVDA": answer})
    rows = {row["topic"]: row for row in result["observations"]}
    assert rows["NVDA"]["observed_at"] == iso(CLOSE + timedelta(seconds=20))     # the refused after-hours trade
    assert rows["SPY"]["reason"].startswith("feed=IEX; latest trade timestamp")
    assert result["sources"][1]["reason"] == ("last regular-session trade not found for NVDA "
                                              "(no regular-session trade returned)")

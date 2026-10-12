"""The evidence cutoff follows collection, so a trade reported during collection is current, not "future".

W41 (2026-10-05..09): on 35 in-session pages a mean of 14.6 of 21 tickers had a current print; NVDA was absent on 25,
AAPL on 22, SPY on 20, XLRE on 2, and every CLOSE_1M page had 21 of 21. The run's start was the cutoff, the IEX
snapshot is requested seconds later, and a ticker whose latest trade fell in that gap was dropped without a word.
The busiest tickers trade in that gap most often; after the close nothing trades, so nothing was dropped.
"""

import json
from datetime import datetime, timedelta, timezone

import market_brief.collect as collect
from market_brief.collect import alpaca_collect

START = datetime(2026, 10, 9, 15, 3, 42, 486363, tzinfo=timezone.utc)  # HOURLY_1100, Oct 9: run start
SNAPPED = START + timedelta(seconds=2)  # the IEX snapshot response arrives after Treasury, BLS, Fed and the bars


def iso(moment):
    return moment.isoformat().replace("+00:00", "Z")


def bars():
    return [{"t": f"2026-10-0{day}T04:00:00Z", "c": 100 + day} for day in range(1, 9)]


def snapshot(trade_at, price):
    return {"latestTrade": {"t": iso(trade_at), "p": price}, "prevDailyBar": {"c": 100}}


class Clock(datetime):
    """`datetime.now` returns each moment in turn and then keeps the last: run start, then each later reading."""
    moments = []

    @classmethod
    def now(cls, tz=None):
        moment = cls.moments.pop(0) if len(cls.moments) > 1 else cls.moments[0]
        return moment if tz else moment.replace(tzinfo=None)


def collect_with(monkeypatch, payload):
    def fake(path, params, deadline, key_id, secret_key):
        if path.endswith("bars"):
            return {"bars": {symbol: bars() for symbol in payload}}
        return payload

    Clock.moments = [START + timedelta(seconds=1), SNAPPED]  # bars requested, then the snapshot's response
    monkeypatch.setattr(collect, "datetime", Clock)
    monkeypatch.setattr(collect, "_alpaca_request", fake)
    return alpaca_collect(START, tuple(payload), "id", "secret")


def test_a_trade_after_the_run_began_but_before_the_snapshot_is_a_current_print(monkeypatch):
    result = collect_with(monkeypatch, {
        "SPY": snapshot(START + timedelta(seconds=1.2), 101.0),     # busy: traded during collection
        "XLRE": snapshot(START - timedelta(seconds=40), 101.5),     # quiet: last traded before the run
    })
    rows = {row["topic"]: row for row in result["observations"]}
    assert set(rows) == {"SPY", "XLRE"}
    assert rows["SPY"]["observed_at"] == iso(START + timedelta(seconds=1.2))
    # Retrieved when the snapshot came back, never before the trade it reports.
    assert all(row["retrieved_at"] == SNAPPED.isoformat() for row in rows.values())


def test_a_trade_stamped_after_the_snapshot_response_is_still_refused(monkeypatch):
    result = collect_with(monkeypatch, {
        "SPY": snapshot(SNAPPED + timedelta(seconds=30), 101.0),  # a clock fault: later than the response itself
        "QQQ": snapshot(START - timedelta(days=1), 99.0),          # the previous session: no print today
    })
    assert result["observations"] == []


def test_the_run_admits_a_print_reported_during_collection(tmp_path, monkeypatch):
    from test_continuity import live_cli

    start = datetime(2026, 9, 8, 12, 45, tzinfo=timezone.utc)
    done = start + timedelta(seconds=3)
    cli = live_cli(monkeypatch, tmp_path, start.isoformat(), "PREMARKET", intraday=False)
    raw = cli.collect_live(start)
    raw["observations"].append(dict(
        id="SPY-intraday", topic="SPY", metric="premarket return", value=-0.53, unit="%",
        baseline="Alpaca IEX latest trade versus previous regular close", frequency="intraday",
        observed_at=(start + timedelta(seconds=1.2)).isoformat(),
        retrieved_at=(start + timedelta(seconds=2)).isoformat(),
        source_id="sample-prices", status="AVAILABLE", reason=""))
    seen = []
    monkeypatch.setattr(cli, "collect_live", lambda target, include_cuttingboard=False: seen.append(target) or raw)
    Clock.moments = [start, done]  # the run starts, then every later reading is after collection
    monkeypatch.setattr(cli, "datetime", Clock)
    assert cli.main(["premarket", "--checkpoint", "PREMARKET"]) == 0
    assert seen == [start]  # collection still runs on the run's own start (dates, windows, staleness)
    folder = next(p for p in (tmp_path / "runs/2026-09-08").iterdir() if (p / "evidence.json").exists())
    evidence = json.loads((folder / "evidence.json").read_text())
    assert evidence["run"]["collection_started_at"] == start.isoformat()
    assert evidence["run"]["target_time"] == done.isoformat()
    spy = next(row for row in evidence["observations"] if row["id"] == "SPY-intraday")
    assert (spy["status"], spy["reason"]) == ("AVAILABLE", "")

"""The evidence cutoff follows collection, so a trade reported during collection is current, not "future".

W41 (2026-10-05..09): on 35 in-session pages a mean of 14.6 of 21 tickers had a current print; NVDA was absent on 25,
AAPL on 22, SPY on 20, XLRE on 2, and every CLOSE_1M page had 21 of 21. The run's start was the cutoff, the IEX
snapshot is requested seconds later, and a ticker whose latest trade fell in that gap was dropped without a word.
The busiest tickers trade in that gap most often; at 4:01 PM after-hours trading is thin, so the close pages lost
none. That cause is strongly supported by elimination, not yet confirmed against Alpaca's trade record.
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
    assert result["sources"][1]["status"] == "UNAVAILABLE"


def test_the_snapshot_window_closes_when_its_response_arrives(monkeypatch):
    """The trade bound, each row's retrieved_at and the source's checked time are one moment: the response."""
    result = collect_with(monkeypatch, {"SPY": snapshot(SNAPPED, 101.0)})  # traded in the response's own instant
    assert [row["topic"] for row in result["observations"]] == ["SPY"]
    assert result["observations"][0]["retrieved_at"] == SNAPPED.isoformat()
    assert result["sources"][1]["retrieved_at"] == SNAPPED.isoformat()
    assert result["sources"][1]["status"] == "AVAILABLE"


def test_a_symbol_without_a_current_print_is_named_with_its_reason_and_the_source_is_not_available(monkeypatch):
    """No silent drop: a partial snapshot is Degraded, and its reason names each missing symbol and why."""
    def fake(path, params, deadline, key_id, secret_key):
        if path.endswith("bars"):
            return {"bars": {symbol: bars() for symbol in ("SPY", "QQQ", "XLRE", "GDX")}}  # no AAPL history
        return {
            "SPY": snapshot(SNAPPED + timedelta(seconds=30), 101.0),
            "QQQ": snapshot(START - timedelta(days=1), 99.0),
            "XLRE": snapshot(START - timedelta(seconds=40), 101.5),
            "AAPL": snapshot(START - timedelta(seconds=5), 230.0),
            "GDX": {"prevDailyBar": {"c": 50}},
        }                                                                             # NVDA not returned at all

    Clock.moments = [START + timedelta(seconds=1), SNAPPED]
    monkeypatch.setattr(collect, "datetime", Clock)
    monkeypatch.setattr(collect, "_alpaca_request", fake)
    result = alpaca_collect(START, ("SPY", "QQQ", "XLRE", "AAPL", "NVDA", "GDX"), "id", "secret")
    assert [row["topic"] for row in result["observations"]] == ["XLRE"]
    source = result["sources"][1]
    assert source["status"] == "DEGRADED"
    assert source["reason"] == (
        "no current print for 5 of 6 symbols — SPY (trade stamped after the snapshot response); "
        "QQQ (no IEX trade yet this session); AAPL (no prior-close history); "
        "NVDA (not returned by the snapshot); GDX (no trade or prior close in the snapshot)")


def test_an_after_hours_trade_is_never_regular_session_evidence():
    """PR #50 moves the cutoff seconds later, after 4:00 PM at the close snapshot; the session close still bounds
    what a print can stand for. Through the real admission path, at CLOSE_1M and at a late hourly refresh."""
    from market_brief.evidence import finalize_coverage, normalize_packet

    close = datetime(2026, 9, 8, 20, 0, tzinfo=timezone.utc)  # Tue Sep 8, 4:00 PM ET
    for checkpoint, now in (("CLOSE_1M", close + timedelta(seconds=63)), ("HOURLY_1500", close + timedelta(minutes=2))):
        raw = dict(mode="SAMPLE", sources=[dict(id="sample-prices", name="Sample prices", kind="quote",
                   url="https://example.com/prices", retrieved_at=now.isoformat(), status="AVAILABLE",
                   llm_allowed=True, retention_allowed=True)],
                   observations=[dict(id=f"{symbol}-intraday", topic=symbol, metric="premarket return", value=0.5,
                                      unit="%", baseline="latest trade versus previous regular close",
                                      frequency="intraday", observed_at=at.isoformat(), retrieved_at=now.isoformat(),
                                      source_id="sample-prices", status="AVAILABLE", reason="")
                                 for symbol, at in (("SPY", close + timedelta(seconds=30)),
                                                    ("XLRE", close - timedelta(seconds=10)))])
        packet = finalize_coverage(normalize_packet(raw, now, "SAMPLE", checkpoint))
        rows = {row["topic"]: row for row in packet["observations"]}
        assert (rows["SPY"]["status"], rows["SPY"]["value"]) == ("UNAVAILABLE", None), checkpoint
        assert rows["SPY"]["reason"] == "after-hours trade, after the regular-session close"
        assert rows["XLRE"]["status"] in {"AVAILABLE", "DELAYED"} and rows["XLRE"]["value"] == 0.5
        assert "SPY: after-hours trade, after the regular-session close" in packet["coverage"]["limitations"]


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


def test_a_degraded_price_source_reads_degraded_with_its_reason_and_wraps_on_a_phone():
    from test_pipeline import NOW, ROOT, derive, narrative, normalize_packet, read_json

    from market_brief.evidence import finalize_coverage
    from market_brief.render import render

    raw = read_json(ROOT / "tests/fixtures/evidence.sample.json")
    reason = "no current print for 2 of 21 symbols — SPY, AAPL (no IEX trade yet this session)"
    for source in raw["sources"]:
        if source["id"] == "sample-prices":
            source.update(status="DEGRADED", reason=reason)
    packet = finalize_coverage(derive(normalize_packet(raw, NOW, "SAMPLE"), read_json(ROOT / "config/universe.json")))
    assert f"Fictional equity closes: {reason}" in packet["coverage"]["limitations"]
    _, page = render(packet, narrative())
    # A reason this long is a sentence: the degraded status wraps like an unavailable one (390 px measured 0 overflow).
    assert f'<span class="source-status degraded">Degraded · {reason}</span>' in page
    assert ".source-status.degraded{white-space:normal}" in page

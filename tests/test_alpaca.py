from datetime import datetime, timedelta, timezone

import pytest

from market_brief.collect import ALPACA_UNIVERSE, SourceError, alpaca_collect, alpaca_probe

NOW = datetime(2026, 9, 8, 13, 35, tzinfo=timezone.utc)


def bars(symbol):
    return [{"t": f"2026-09-{day:02d}T00:00:00Z", "c": 100 + day}
            for day in range(1, 9)]


def snapshots():
    return {
        "SPY": {"latestTrade": {"t": "2026-09-08T13:34:00Z", "p": 110},
                "prevDailyBar": {"c": 109}},
        "QQQ": {"latestTrade": {"t": "2026-09-08T13:34:00Z", "p": 220},
                "prevDailyBar": {"c": 218}},
    }


def test_alpaca_probe_reports_metadata_without_payload(capsys):
    def fake(path, params, deadline, key_id, secret_key):
        assert key_id == "id" and secret_key == "secret"
        return snapshots() if path.endswith("snapshots") else {"bars": {
            "SPY": bars("SPY"), "QQQ": bars("QQQ")}}

    result = alpaca_probe(NOW, key_id="id", secret_key="secret", fetcher=fake)
    assert result["authenticated"] and result["feed"] == "IEX"
    assert result["historical_symbols"] == ["QQQ", "SPY"]
    assert result["intraday_symbols"] == ["QQQ", "SPY"]
    assert "secret" not in capsys.readouterr().out


def test_alpaca_collect_preserves_feed_and_freshness():
    def fake(path, params, deadline, key_id, secret_key):
        return snapshots() if path.endswith("snapshots") else {"bars": {
            "SPY": bars("SPY"), "QQQ": bars("QQQ")}}

    # Exercise the real request seam without network access.
    import market_brief.collect as collect
    original = collect._alpaca_request
    collect._alpaca_request = fake
    try:
        result = alpaca_collect(NOW, ("SPY", "QQQ"), "id", "secret")
    finally:
        collect._alpaca_request = original
    assert {s["feed"] for s in result["sources"]} == {"IEX"}
    assert {s["expected_freshness"] for s in result["sources"]} == {"LIVE", "PRIOR_CLOSE"}
    assert all(row["reason"].startswith("feed=IEX") for row in result["observations"])
    assert len(result["history"]) == 2


def test_slv_uses_existing_batches_and_retains_validated_proxy_benchmark(monkeypatch):
    import exchange_calendars as xcals

    import market_brief.collect as collect
    from market_brief.evidence import finalize_coverage, normalize_packet, read_json
    from market_brief.metrics import derive
    from market_brief.render import instrument_label

    assert ALPACA_UNIVERSE.count("SLV") == 1 and ALPACA_UNIVERSE.count("SPY") == ALPACA_UNIVERSE.count("QQQ") == 1
    sessions = xcals.get_calendar("XNYS").sessions_in_range("2026-07-20", "2026-09-04")
    dates = [session.strftime("%Y-%m-%d") for session in sessions[-25:]]
    calls = []

    class Clock(datetime):
        calls = 0

        @classmethod
        def now(cls, tz=None):
            cls.calls += 1
            value = NOW + timedelta(seconds=cls.calls)
            return value if tz else value.replace(tzinfo=None)

    def fake(path, params, deadline, key_id, secret_key):
        calls.append((path, params.copy()))
        symbols = params["symbols"].split(",")
        if path.endswith("bars"):
            return {"bars": {symbol: [dict(t=f"{day}T00:00:00Z", c=100 + i + (10 if symbol == "SLV" else 0))
                                   for i, day in enumerate(dates)] for symbol in symbols}}
        return {symbol: {
            "latestTrade": {"t": "2026-09-08T13:34:00Z", "p": 140.0},
            "dailyBar": {"t": "2026-09-08T13:34:00Z", "c": 140.0},
            "prevDailyBar": {"t": f"{dates[-1]}T20:00:00Z", "c": 134.0 if symbol == "SLV" else 124.0},
        } for symbol in symbols}

    monkeypatch.setattr(collect, "datetime", Clock)
    monkeypatch.setattr(collect, "_alpaca_request", fake)
    result = alpaca_collect(NOW, key_id="id", secret_key="secret")
    assert len(calls) == 2
    for path, params in calls:
        assert params["symbols"] == ",".join(ALPACA_UNIVERSE)
        assert params["feed"] == "iex"
    assert calls[0][0].endswith("bars") and calls[0][1]["adjustment"] == "split"
    assert calls[1][0].endswith("snapshots")
    history = next(row for row in result["history"] if row["symbol"] == "SLV")
    assert history["source_id"] == "alpaca-daily" and history["adjustment"] == "split"
    assert history["session"] == "regular_close" and history["dates"][-1] == "2026-09-04"
    observed = {row["topic"]: row for row in result["observations"]}
    assert "SPY" in observed and "QQQ" in observed and "SLV" in observed
    assert observed["SLV"]["reason"].endswith("prior close 2026-09-04 regular session")
    assert observed["SLV"]["status"] == "AVAILABLE"
    assert observed["SLV"]["source_id"] == "alpaca-iex"
    assert result["sources"][0]["coverage_symbols"] == len(ALPACA_UNIVERSE)
    assert result["sources"][0]["status"] == result["sources"][1]["status"] == "AVAILABLE"

    raw = dict(mode="LIVE", sources=result["sources"], observations=result["observations"],
               history=result["history"], events=[], context_items=[])
    packet = normalize_packet(raw, NOW + timedelta(seconds=3), "LIVE")
    derived = derive(packet, read_json(collect.ROOT / "config/universe.json"))
    finalized = finalize_coverage(derived)
    slv_spread = next(row for row in finalized["derived"] if row["id"] == "SLV-spread20")
    slv = next(row for row in finalized["derived"] if row["id"] == "SLV-daily")
    assert slv_spread["metric"] == "relative to GLD"
    gld_history = next(row for row in finalized["history"] if row["symbol"] == "GLD")
    slv_expected_spread = (100 * (history["closes"][-1] / history["closes"][-21] - 1)
                           - 100 * (gld_history["closes"][-1] / gld_history["closes"][-21] - 1))
    assert slv_spread["value"] == pytest.approx(slv_expected_spread)
    assert slv["value"] == pytest.approx(100 * (history["closes"][-1] / history["closes"][-2] - 1))
    assert instrument_label("SLV") == "Silver ETF proxy · SLV"
    assert {row["id"] for row in finalized["derived"] if row["topic"] in {"SPY", "QQQ"}} >= {
        "SPY-daily", "QQQ-daily", "QQQ-spread20"}
    slv_live = next(row for row in finalized["observations"] if row["topic"] == "SLV")
    assert slv_live["expected_freshness"] == "LIVE" and slv_live["status"] == "DELAYED"


def test_slv_missing_history_is_reported_and_does_not_create_a_price(monkeypatch):
    import market_brief.collect as collect

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW + timedelta(seconds=1)

    def fake(path, params, deadline, key_id, secret_key):
        symbols = params["symbols"].split(",")
        if path.endswith("bars"):
            return {"bars": {symbol: bars(symbol) for symbol in symbols if symbol != "SLV"}}
        return {
            symbol: {"latestTrade": {"t": "2026-09-08T13:34:00Z", "p": 140.0},
                     "prevDailyBar": {"t": "2026-09-04T20:00:00Z", "c": 104.0}}
            for symbol in ("SPY", "QQQ", "SLV")
        }

    monkeypatch.setattr(collect, "datetime", Clock)
    monkeypatch.setattr(collect, "_alpaca_request", fake)
    result = alpaca_collect(NOW, symbols=("SPY", "QQQ", "SLV"), key_id="id", secret_key="secret")
    assert not any(row["topic"] == "SLV" for row in result["observations"])
    assert result["sources"][1]["status"] == "DEGRADED"
    assert "SLV (no prior-close history)" in result["sources"][1]["reason"]


def test_slv_snapshot_with_wrong_prior_close_is_refused(monkeypatch):
    import market_brief.collect as collect

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW + timedelta(seconds=1)

    def fake(path, params, deadline, key_id, secret_key):
        if path.endswith("bars"):
            dates = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]
            return {"bars": {symbol: [dict(t=f"{day}T00:00:00Z", c=100 + i)
                                     for i, day in enumerate(dates)] for symbol in ("SPY", "QQQ", "SLV")}}
        return {
            "SPY": {"latestTrade": {"t": "2026-09-08T13:34:00Z", "p": 140.0},
                    "prevDailyBar": {"t": "2026-09-04T20:00:00Z", "c": 103.0}},
            "QQQ": {"latestTrade": {"t": "2026-09-08T13:34:00Z", "p": 140.0},
                    "prevDailyBar": {"t": "2026-09-04T20:00:00Z", "c": 103.0}},
            "SLV": {"latestTrade": {"t": "2026-09-08T13:34:00Z", "p": 140.0},
                    "prevDailyBar": {"t": "2026-09-04T20:00:00Z", "c": 120.0}},
        }

    monkeypatch.setattr(collect, "datetime", Clock)
    monkeypatch.setattr(collect, "_alpaca_request", fake)
    result = alpaca_collect(NOW, symbols=("SPY", "QQQ", "SLV"), key_id="id", secret_key="secret")
    assert not any(row["topic"] == "SLV" for row in result["observations"])
    assert {row["topic"] for row in result["observations"]} == {"SPY", "QQQ"}
    assert result["sources"][1]["status"] == "DEGRADED"
    assert "SLV (prior close basis mismatch" in result["sources"][1]["reason"]


def test_alpaca_probe_requires_both_credentials(monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    with pytest.raises(SourceError, match="credentials"):
        alpaca_probe(NOW)

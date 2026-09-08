from datetime import datetime, timezone

import pytest

from market_brief.collect import SourceError, alpaca_collect, alpaca_probe

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


def test_alpaca_probe_requires_both_credentials(monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    with pytest.raises(SourceError, match="credentials"):
        alpaca_probe(NOW)

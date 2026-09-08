import pytest

from market_brief.metrics import history_metrics, return_pct, yield_bps


def test_units():
    assert return_pct(101, 100) == pytest.approx(1)
    assert yield_bps(3.93, 4) == pytest.approx(-7)
    assert return_pct(108, 100) - return_pct(103, 100) == pytest.approx(5)


def test_windows_and_cross():
    assert history_metrics([100] * 20)["return_20"] is None
    assert history_metrics([100] * 50)["cross_50"] is None
    result = history_metrics([100] * 49 + [99, 101])
    assert result["cross_50"] == "UP"
    assert len(result["daily_returns"]) == 5
    assert history_metrics([100] * 51)["cross_50"] is None


@pytest.mark.parametrize("closes", [[100, 0], [100, float("nan")], [100, True]])
def test_bad_prices_rejected(closes):
    with pytest.raises(ValueError):
        history_metrics(closes)

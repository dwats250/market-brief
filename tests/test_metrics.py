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


def test_dma_distance_and_spread_ranking_are_deterministic():
    from market_brief.metrics import dma_distance, rank_by_spread
    assert dma_distance(102, 100) == pytest.approx(2)
    assert dma_distance(98, 100) == pytest.approx(-2)
    with pytest.raises(ValueError):
        dma_distance(100, 0)
    order = ["XLK", "XLF", "XLE", "XLI"]
    rows = [dict(symbol="XLK", relative=dict(value=1.0)), dict(symbol="XLF", relative=dict(value=None)),
            dict(symbol="XLE", relative=dict(value=4.0)), dict(symbol="XLI", relative=dict(value=1.0))]
    ranked = [row["symbol"] for row in rank_by_spread(rows, order)]
    assert ranked == ["XLE", "XLK", "XLI", "XLF"]  # strongest first, stable tie, missing last
    assert rank_by_spread(rows, order) == rank_by_spread(list(reversed(rows)), order)

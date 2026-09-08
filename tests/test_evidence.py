from copy import deepcopy
from datetime import datetime, timezone

import pytest

from market_brief.evidence import normalize_observation, session_info

NOW = datetime(2026, 9, 8, 12, 45, tzinfo=timezone.utc)


def observation():
    return dict(id="spy", topic="SPY", metric="premarket return", value=0.0,
                unit="%", baseline="previous regular close", frequency="intraday",
                observed_at="2026-09-08T12:30:00+00:00", retrieved_at=NOW.isoformat(),
                source_id="sample", status="AVAILABLE", reason="")


def test_zero_is_not_missing_and_delayed_is_visible():
    result = normalize_observation(observation(), NOW)
    assert result["value"] == 0
    assert result["status"] == "DELAYED"


@pytest.mark.parametrize("timestamp,status", [
    ("2026-09-08T10:00:00+00:00", "STALE"),
    ("2026-09-08T13:00:00+00:00", "INVALID"),
    (None, "UNKNOWN"),
    ("2026-09-08T12:30:00", "INVALID"),
])
def test_time_is_observation_time_not_retrieval(timestamp, status):
    row = observation()
    row["observed_at"] = timestamp
    result = normalize_observation(row, NOW)
    assert result["status"] == status
    assert result["value"] is None


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "0.5"])
def test_numeric_types_fail_closed(value):
    row = observation()
    row["value"] = value
    assert normalize_observation(row, NOW)["status"] == "INVALID"


def test_daily_data_does_not_become_current():
    row = deepcopy(observation())
    row.update(frequency="daily", observed_at="2026-09-04", topic="US 2Y")
    assert normalize_observation(row, NOW)["status"] == "BACKGROUND"


def test_calendar_holiday_early_close_dst():
    assert session_info(datetime(2026, 9, 7, 12, tzinfo=timezone.utc))["trading_day"] is False
    assert session_info(NOW)["previous_session"] == "2026-09-04"
    early = session_info(datetime(2026, 11, 27, 12, tzinfo=timezone.utc))
    assert early["close"].startswith("2026-11-27T18:00")
    before = session_info(datetime(2026, 3, 6, 12, tzinfo=timezone.utc))
    after = session_info(datetime(2026, 3, 9, 12, tzinfo=timezone.utc))
    assert "14:30" in before["open"] and "13:30" in after["open"]

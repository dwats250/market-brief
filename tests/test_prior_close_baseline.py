"""The prior close behind every current print is the last completed regular session's daily bar, with its date.

Incident (2026-10-23): before the open, Alpaca's snapshot `prevDailyBar` is two sessions back, so every premarket
"vs prior close" print carried the previous session's move inside it (META −8.03% published; about −1.1% against
Thursday's close). These tests pin the baseline to the history's last completed session, cross-checked against the
snapshot's own bar for that date and the exchange calendar, and drop a print rather than substitute an older close.
"""

from datetime import datetime, timedelta, timezone

from market_brief.collect import _alpaca_intraday, _snapshot_prior_close, alpaca_collect
from market_brief.schedule import prior_session_date

UTC = timezone.utc


def bar(day, close):
    return {"t": f"{day}T04:00:00Z", "c": close}


def history(symbol, closes):
    """`closes` as {date: close}, oldest first."""
    return dict(symbol=symbol, dates=list(closes), closes=list(closes.values()),
                adjustment="split", session="regular_close", source_id="alpaca-daily",
                retrieved_at="2026-10-23T13:02:00+00:00")


def premarket_snapshot(price, when, last_session, before_last):
    """What Alpaca returns before the open: dailyBar is the last completed session, prevDailyBar the one before."""
    return {"latestTrade": {"t": when, "p": price}, "dailyBar": bar(*last_session), "prevDailyBar": bar(*before_last)}


def session_snapshot(price, when, today, last_session):
    """Once the session trades: dailyBar is today's bar in progress, prevDailyBar the last completed session."""
    return {"latestTrade": {"t": when, "p": price}, "dailyBar": bar(*today), "prevDailyBar": bar(*last_session)}


def prints(payload, histories, now, prior_session):
    missing = {}
    rows = _alpaca_intraday(payload, histories, now, now + timedelta(seconds=5), "alpaca-iex", missing,
                            prior_session=prior_session)
    return {row["topic"]: row for row in rows}, missing


# --- the incident: Friday premarket after a large Thursday decline ------------------------------------------------

def test_friday_premarket_measures_against_thursday_not_wednesday():
    now = datetime(2026, 10, 23, 13, 2, tzinfo=UTC)                      # Fri 9:02 ET, before the open
    meta = history("META", {"2026-10-21": 697.54, "2026-10-22": 648.85})  # Thursday −6.98%
    snapshot = premarket_snapshot(641.53, "2026-10-23T12:46:00Z", ("2026-10-22", 648.85), ("2026-10-21", 697.54))
    rows, missing = prints({"META": snapshot}, [meta], now, "2026-10-22")
    assert not missing
    assert round(rows["META"]["value"], 2) == -1.13           # not −8.03
    assert "prior close 2026-10-22" in rows["META"]["reason"]


def test_the_old_field_would_have_carried_thursdays_move():
    snapshot = premarket_snapshot(641.53, "2026-10-23T12:46:00Z", ("2026-10-22", 648.85), ("2026-10-21", 697.54))
    assert _snapshot_prior_close(snapshot, "2026-10-23") == ("2026-10-22", 648.85)
    assert round(100 * (641.53 / snapshot["prevDailyBar"]["c"] - 1), 2) == -8.03  # the published figure


# --- ordinary weekday, Monday, holiday ---------------------------------------------------------------------------

def test_ordinary_weekday_premarket_uses_yesterdays_bar():
    now = datetime(2026, 10, 22, 13, 2, tzinfo=UTC)
    qqq = history("QQQ", {"2026-10-20": 706.0, "2026-10-21": 713.91})
    snapshot = premarket_snapshot(710.0, "2026-10-22T12:59:00Z", ("2026-10-21", 713.91), ("2026-10-20", 706.0))
    rows, missing = prints({"QQQ": snapshot}, [qqq], now, prior_session_date(now))
    assert prior_session_date(now) == "2026-10-21"
    assert round(rows["QQQ"]["value"], 2) == round(100 * (710.0 / 713.91 - 1), 2) and not missing


def test_monday_premarket_uses_fridays_bar():
    now = datetime(2026, 10, 26, 13, 2, tzinfo=UTC)                      # Mon
    assert prior_session_date(now) == "2026-10-23"
    spy = history("SPY", {"2026-10-22": 762.1, "2026-10-23": 765.0})
    snapshot = premarket_snapshot(766.0, "2026-10-26T12:50:00Z", ("2026-10-23", 765.0), ("2026-10-22", 762.1))
    rows, missing = prints({"SPY": snapshot}, [spy], now, prior_session_date(now))
    assert round(rows["SPY"]["value"], 4) == round(100 * (766.0 / 765.0 - 1), 4) and not missing


def test_exchange_holiday_prior_session_skips_the_closed_day():
    # Thanksgiving 2026 is Thu Nov 26; Friday's prior session is Wednesday.
    assert prior_session_date(datetime(2026, 11, 27, 13, 2, tzinfo=UTC)) == "2026-11-25"
    # A run on the holiday itself has the session before the next one.
    assert prior_session_date(datetime(2026, 11, 26, 15, 0, tzinfo=UTC)) == "2026-11-25"
    # A weekend run too.
    assert prior_session_date(datetime(2026, 10, 25, 15, 0, tzinfo=UTC)) == "2026-10-23"


# --- the open transition and the close --------------------------------------------------------------------------

def test_regular_session_open_keeps_the_same_baseline():
    """At 9:31 ET the snapshot's fields roll (dailyBar becomes today's bar); the prior close must not move."""
    before = datetime(2026, 10, 23, 13, 2, tzinfo=UTC)
    after = datetime(2026, 10, 23, 13, 32, tzinfo=UTC)
    nvda = history("NVDA", {"2026-10-21": 190.0, "2026-10-22": 182.25})
    pre = premarket_snapshot(181.54, "2026-10-23T12:57:00Z", ("2026-10-22", 182.25), ("2026-10-21", 190.0))
    live = session_snapshot(181.59, "2026-10-23T13:32:00Z", ("2026-10-23", 181.59), ("2026-10-22", 182.25))
    a, _ = prints({"NVDA": pre}, [nvda], before, "2026-10-22")
    b, _ = prints({"NVDA": live}, [nvda], after, "2026-10-22")
    assert round(a["NVDA"]["value"], 2) == -0.39 and round(b["NVDA"]["value"], 2) == -0.36
    assert a["NVDA"]["reason"] == b["NVDA"]["reason"]


def test_near_close_print_is_still_against_yesterday():
    now = datetime(2026, 10, 23, 20, 1, tzinfo=UTC)                       # 4:01 PM ET, same date
    assert prior_session_date(now) == "2026-10-22"
    spy = history("SPY", {"2026-10-21": 770.0, "2026-10-22": 762.1})
    live = session_snapshot(765.5, "2026-10-23T19:59:50Z", ("2026-10-23", 765.5), ("2026-10-22", 762.1))
    rows, missing = prints({"SPY": live}, [spy], now, prior_session_date(now))
    assert not missing and round(rows["SPY"]["value"], 4) == round(100 * (765.5 / 762.1 - 1), 4)


# --- missing, stale or inconsistent bars: no print, never an older close ----------------------------------------

def test_stale_daily_bars_drop_the_print_instead_of_substituting():
    now = datetime(2026, 10, 23, 13, 2, tzinfo=UTC)
    stale = history("QQQ", {"2026-10-20": 706.0, "2026-10-21": 713.91})  # Thursday's bar never arrived
    snapshot = premarket_snapshot(692.0, "2026-10-23T12:59:00Z", ("2026-10-22", 694.5), ("2026-10-21", 713.91))
    rows, missing = prints({"QQQ": snapshot}, [stale], now, "2026-10-22")
    assert not rows
    assert missing["QQQ"] == "daily bars stale: history through 2026-10-21, prior session 2026-10-22"


def test_missing_history_closes_without_a_dated_snapshot_bar_drops_the_print():
    now = datetime(2026, 10, 23, 13, 2, tzinfo=UTC)
    snapshot = {"latestTrade": {"t": "2026-10-23T12:59:00Z", "p": 100.0}, "prevDailyBar": {"c": 99.0}}  # undated
    rows, missing = prints({"EWY": snapshot}, [dict(symbol="EWY")], now, None)
    assert not rows and missing["EWY"] == "no trade or prior close in the snapshot"


def test_adjustment_basis_mismatch_drops_the_print():
    """A split-adjusted history bar against a raw snapshot bar for the same date: the bases disagree."""
    now = datetime(2026, 10, 23, 13, 2, tzinfo=UTC)
    adjusted = history("META", {"2026-10-21": 348.77, "2026-10-22": 324.43})      # 2-for-1 applied
    raw = premarket_snapshot(320.0, "2026-10-23T12:46:00Z", ("2026-10-22", 648.85), ("2026-10-21", 697.54))
    rows, missing = prints({"META": raw}, [adjusted], now, "2026-10-22")
    assert not rows
    assert missing["META"].startswith("prior close basis mismatch for 2026-10-22: snapshot 648.85 vs daily bar 324.43")


def test_glance_without_history_uses_the_snapshots_last_completed_bar():
    """The proxies carry no history; the latest bar dated before the trade is the prior close (never prevDailyBar
    by name)."""
    now = datetime(2026, 10, 23, 13, 2, tzinfo=UTC)
    pre = premarket_snapshot(50.5, "2026-10-23T12:58:00Z", ("2026-10-22", 50.0), ("2026-10-21", 52.0))
    rows, missing = prints({"EWY": pre}, [dict(symbol="EWY")], now, None)
    assert not missing and round(rows["EWY"]["value"], 2) == 1.0
    assert "prior close 2026-10-22" in rows["EWY"]["reason"]


# --- end to end through the collector ---------------------------------------------------------------------------

def test_alpaca_collect_applies_the_exchange_prior_session():
    now = datetime(2026, 10, 23, 13, 2, tzinfo=UTC)
    bars = {"SPY": [bar("2026-10-21", 770.0), bar("2026-10-22", 762.1)]}

    def fake(path, params, deadline, key_id, secret_key):
        if path.endswith("snapshots"):
            return {"SPY": premarket_snapshot(758.0, "2026-10-23T12:55:00Z", ("2026-10-22", 762.1),
                                              ("2026-10-21", 770.0))}
        return {"bars": bars}

    import market_brief.collect as collect
    original = collect._alpaca_request
    collect._alpaca_request = fake
    try:
        result = alpaca_collect(now, symbols=("SPY",), key_id="id", secret_key="secret")
    finally:
        collect._alpaca_request = original
    [row] = result["observations"]
    assert round(row["value"], 4) == round(100 * (758.0 / 762.1 - 1), 4)
    assert "prior close 2026-10-22" in row["reason"]

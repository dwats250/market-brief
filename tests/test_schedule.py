from datetime import datetime, timezone
from types import SimpleNamespace

from market_brief import cli
from market_brief.schedule import checkpoint_session, due, scheduled_checkpoint


def utc(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def test_checkpoint_schedule_tracks_dst():
    winter = checkpoint_session(utc("2026-01-12T12:00:00+00:00"), "PREMARKET")
    summer = checkpoint_session(utc("2026-07-06T12:00:00+00:00"), "PREMARKET")
    assert winter["scheduled_at"] == "2026-01-12T14:00:00+00:00"
    assert summer["scheduled_at"] == "2026-07-06T13:00:00+00:00"


def test_holiday_is_not_due():
    ready, info = due(utc("2026-11-26T14:05:00+00:00"), "PREMARKET")
    assert not ready and not info["trading_day"]


def test_close_checkpoint_uses_actual_early_close():
    info = checkpoint_session(utc("2026-11-27T17:00:00+00:00"), "CLOSE_1M")
    assert info["trading_day"]
    assert info["exchange_close"] == "2026-11-27T18:00:00+00:00"
    assert info["scheduled_at"] == "2026-11-27T18:01:00+00:00"


def test_utc_candidates_resolve_each_pacific_checkpoint_in_pdt_and_pst():
    assert scheduled_checkpoint(utc("2026-07-06T13:00:00+00:00")) == "PREMARKET"
    assert scheduled_checkpoint(utc("2026-07-06T13:31:00+00:00")) == "OPEN_1M"
    assert scheduled_checkpoint(utc("2026-07-06T14:00:00+00:00")) == "OPEN_30M"
    assert scheduled_checkpoint(utc("2026-07-06T15:00:00+00:00")) == "HOURLY_1100"
    assert scheduled_checkpoint(utc("2026-07-06T17:01:00+00:00")) == "HOURLY_1300"  # early-close candidate, normal day
    assert scheduled_checkpoint(utc("2026-07-06T19:00:00+00:00")) == "HOURLY_1500"
    assert scheduled_checkpoint(utc("2026-07-06T20:00:00+00:00")) is None  # 1:00 PT: the close is due at 1:01
    assert scheduled_checkpoint(utc("2026-07-06T20:01:00+00:00")) == "CLOSE_1M"
    assert scheduled_checkpoint(utc("2026-01-12T13:00:00+00:00")) is None  # 5:00 PST: nothing due
    assert scheduled_checkpoint(utc("2026-01-12T14:00:00+00:00")) == "PREMARKET"
    assert scheduled_checkpoint(utc("2026-01-12T14:31:00+00:00")) == "OPEN_1M"
    assert scheduled_checkpoint(utc("2026-01-12T15:00:00+00:00")) == "OPEN_30M"
    assert scheduled_checkpoint(utc("2026-01-12T16:00:00+00:00")) == "HOURLY_1100"
    assert scheduled_checkpoint(utc("2026-01-12T20:00:00+00:00")) == "HOURLY_1500"
    assert scheduled_checkpoint(utc("2026-01-12T21:01:00+00:00")) == "CLOSE_1M"


def test_early_close_candidates_resolve_to_close_plus_one():
    from datetime import timedelta

    from market_brief.schedule import next_checkpoint
    close = utc("2026-11-27T18:00:00+00:00")  # 13:00 ET the day after Thanksgiving
    assert scheduled_checkpoint(close + timedelta(minutes=1)) == "CLOSE_1M"
    # Hourly refreshes at or after the early close are not applicable; the close snapshot covers them.
    assert not checkpoint_session(close, "HOURLY_1300")["applicable"]
    assert not checkpoint_session(close, "HOURLY_1500")["applicable"]
    assert checkpoint_session(close, "HOURLY_1200")["applicable"]
    assert checkpoint_session(close, "CLOSE_1M")["applicable"]
    assert scheduled_checkpoint(close) is None
    assert scheduled_checkpoint(close - timedelta(hours=1)) == "HOURLY_1200"
    assert next_checkpoint(close - timedelta(hours=1), "HOURLY_1200")["checkpoint"] == "CLOSE_1M"
    assert scheduled_checkpoint(close - timedelta(minutes=59)) == "HOURLY_1200"  # 17:01 UTC wake, EST
    assert scheduled_checkpoint(close + timedelta(hours=1, minutes=1)) is None  # 19:01 UTC: nothing left


def test_checkpoints_are_anchored_to_the_exchange_session_in_both_seasons():
    """Open −30m, open +1m, open +30m, exchange-clock hours, close +1m: the same offsets in EDT and EST.
    The reader sees the resulting time in Pacific time, so the displayed clock moves with New York."""
    from datetime import timedelta

    from market_brief.render import pacific_time
    from market_brief.schedule import VANCOUVER
    expected = {"PREMARKET": timedelta(minutes=-30), "OPEN_1M": timedelta(minutes=1),
                "OPEN_30M": timedelta(minutes=30), "HOURLY_1100": timedelta(minutes=90),
                "HOURLY_1500": timedelta(hours=5, minutes=30)}
    for day, opening in (("2026-07-06", "13:30"), ("2026-11-02", "14:30"), ("2027-01-12", "14:30")):
        now = utc(f"{day}T12:00:00+00:00")
        infos = {checkpoint: checkpoint_session(now, checkpoint) for checkpoint in expected}
        assert all(info["exchange_open"] == f"{day}T{opening}:00+00:00" for info in infos.values()), day
        for checkpoint, offset in expected.items():
            scheduled = utc(infos[checkpoint]["scheduled_at"])
            assert scheduled - utc(infos[checkpoint]["exchange_open"]) == offset, (day, checkpoint)
        close = checkpoint_session(now, "CLOSE_1M")
        assert utc(close["scheduled_at"]) - utc(close["exchange_close"]) == timedelta(minutes=1)
    summer = {c: utc(checkpoint_session(utc("2026-07-06T12:00:00+00:00"), c)["scheduled_at"])
              for c in ("PREMARKET", "OPEN_1M", "OPEN_30M")}
    winter = {c: utc(checkpoint_session(utc("2027-01-12T12:00:00+00:00"), c)["scheduled_at"])
              for c in ("PREMARKET", "OPEN_1M", "OPEN_30M")}
    assert [pacific_time(t.isoformat()) for t in summer.values()] == ["6:00 AM PT", "6:31 AM PT", "7:00 AM PT"]
    # In winter the exchange time is one UTC hour later; what that reads in Pacific time depends on
    # whether British Columbia still follows New York's fall-back (tzdata 2026b says it does not).
    assert [(w.hour - s.hour, w.minute - s.minute) for w, s in zip(winter.values(), summer.values())] == [(1, 0)] * 3
    displayed = [pacific_time(t.isoformat()) for t in winter.values()]
    assert displayed == [t.astimezone(VANCOUVER).strftime("%-I:%M %p") + " PT" for t in winter.values()]
    if winter["OPEN_30M"].astimezone(VANCOUVER).utcoffset() == timedelta(hours=-7):
        assert displayed == ["7:00 AM PT", "7:31 AM PT", "8:00 AM PT"]


def test_every_session_keeps_the_opening_checkpoints_after_the_open_and_refreshes_inside_it():
    import exchange_calendars as xcals
    cal = xcals.get_calendar("XNYS")
    for session in cal.sessions_in_range("2026-09-15", "2027-06-30"):
        now = utc(session.date().isoformat() + "T12:00:00+00:00")
        opening, closing = cal.session_open(session).to_pydatetime(), cal.session_close(session).to_pydatetime()
        assert utc(checkpoint_session(now, "PREMARKET")["scheduled_at"]) < opening
        for checkpoint in ("OPEN_1M", "OPEN_30M"):
            assert opening < utc(checkpoint_session(now, checkpoint)["scheduled_at"]) < closing, (session, checkpoint)
        for checkpoint in ("HOURLY_1100", "HOURLY_1200", "HOURLY_1300", "HOURLY_1400", "HOURLY_1500"):
            info = checkpoint_session(now, checkpoint)
            assert info["applicable"] == (utc(info["scheduled_at"]) < closing), (session, checkpoint)
        assert utc(checkpoint_session(now, "CLOSE_1M")["scheduled_at"]) > closing


def test_alternate_season_wakes_never_reach_a_synthesis_checkpoint():
    """Both :31 wakes exist so OPEN_1M lands at 6:31 PT in either season. The one that belongs to the
    other season is 7:31 PT or 5:31 PT and resolves to nothing: in particular it is never a second
    attempt at the 7:00 synthesis, whatever happened at 7:01."""
    from market_brief.schedule import TOLERANCE_MINUTES, due
    # EDT (2026-07-06): 13:31 UTC is 9:31 ET (open +1m), 14:31 UTC is 10:31 ET.
    assert scheduled_checkpoint(utc("2026-07-06T13:31:00+00:00")) == "OPEN_1M"
    assert scheduled_checkpoint(utc("2026-07-06T14:31:00+00:00")) is None
    assert not due(utc("2026-07-06T14:31:00+00:00"), "OPEN_30M")[0]
    # EST (2026-01-12 and, after British Columbia stopped following it, 2027-01-12): 13:31 UTC is 8:31 ET,
    # 14:31 UTC is 9:31 ET (open +1m).
    for winter in ("2026-01-12", "2027-01-12"):
        assert scheduled_checkpoint(utc(f"{winter}T13:31:00+00:00")) is None
        assert scheduled_checkpoint(utc(f"{winter}T14:31:00+00:00")) == "OPEN_1M"
        assert not due(utc(f"{winter}T14:31:00+00:00"), "PREMARKET")[0]  # 9:31 is not a premarket retry either
        assert scheduled_checkpoint(utc(f"{winter}T15:31:00+00:00")) is None  # no :31 wake exists here anyway
    # A synthesis is due only inside its own short window; an explicit wider tolerance never widens it.
    assert TOLERANCE_MINUTES["synthesis"] < 31 <= TOLERANCE_MINUTES["refresh"]
    assert due(utc("2026-07-06T14:15:00+00:00"), "OPEN_30M")[0]
    assert not due(utc("2026-07-06T14:31:00+00:00"), "OPEN_30M", 45)[0]
    assert scheduled_checkpoint(utc("2026-07-06T14:31:00+00:00"), 45) is None


def test_hourly_refresh_and_close_windows_are_unchanged():
    from market_brief.schedule import TOLERANCE_MINUTES, due
    assert TOLERANCE_MINUTES["refresh"] == 45 and TOLERANCE_MINUTES["close"] == 45
    for hour, checkpoint in ((15, "HOURLY_1100"), (16, "HOURLY_1200"), (17, "HOURLY_1300"),
                             (18, "HOURLY_1400"), (19, "HOURLY_1500")):
        assert scheduled_checkpoint(utc(f"2026-07-06T{hour:02d}:01:00+00:00")) == checkpoint
        assert scheduled_checkpoint(utc(f"2026-01-12T{hour + 1:02d}:01:00+00:00")) == checkpoint
    assert due(utc("2026-07-06T15:40:00+00:00"), "HOURLY_1100")[0]  # a late deterministic wake still refreshes
    assert due(utc("2026-07-06T20:40:00+00:00"), "CLOSE_1M")[0]
    assert scheduled_checkpoint(utc("2026-07-06T20:01:00+00:00")) == "CLOSE_1M"


def test_next_synthesis_is_where_an_analyst_can_next_judge():
    from market_brief.schedule import next_synthesis
    assert next_synthesis(utc("2026-09-08T13:00:00+00:00"), "PREMARKET")["checkpoint"] == "OPEN_30M"
    assert next_synthesis(utc("2026-09-08T13:31:00+00:00"), "OPEN_1M")["checkpoint"] == "OPEN_30M"
    later = next_synthesis(utc("2026-09-08T14:01:00+00:00"), "OPEN_30M")
    assert later["checkpoint"] == "PREMARKET" and later["session_date"] == "2026-09-09"
    friday = next_synthesis(utc("2026-09-04T20:03:00+00:00"), "CLOSE_1M")
    assert friday["session_date"] == "2026-09-08"  # Labor Day skipped


def test_scheduler_skips_weekends_holidays_and_wrong_time_candidates():
    assert scheduled_checkpoint(utc("2026-07-04T13:00:00+00:00")) is None
    assert scheduled_checkpoint(utc("2026-11-26T14:00:00+00:00")) is None
    assert scheduled_checkpoint(utc("2026-07-06T12:00:00+00:00")) is None


def test_scheduler_prefers_nearest_checkpoint_when_candidate_windows_overlap():
    assert scheduled_checkpoint(utc("2026-07-06T13:31:00+00:00")) == "OPEN_1M"


def test_scheduled_checkpoint_is_idempotent(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(cli, "due", lambda now, checkpoint: (True, {
        "trading_day": True, "session_date": "2026-09-08", "scheduled_at": now.isoformat()
    }))
    calls = []
    monkeypatch.setattr(cli, "run", lambda args: calls.append(args.checkpoint) or 0)
    args = SimpleNamespace(checkpoint="PREMARKET", replay=False, input=None,
                           cuttingboard=False, synthesize=False)
    assert cli.scheduled(args) == 0
    assert cli.scheduled(args) == 0
    assert calls == ["PREMARKET"]
    assert "already completed" in capsys.readouterr().out

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
    assert scheduled_checkpoint(utc("2026-07-06T19:07:00+00:00")) == "AFTERNOON"
    assert scheduled_checkpoint(utc("2026-07-06T20:01:00+00:00")) == "CLOSE_1M"
    assert scheduled_checkpoint(utc("2026-01-12T14:00:00+00:00")) == "PREMARKET"
    assert scheduled_checkpoint(utc("2026-01-12T14:31:00+00:00")) == "OPEN_1M"
    assert scheduled_checkpoint(utc("2026-01-12T15:00:00+00:00")) == "OPEN_30M"
    assert scheduled_checkpoint(utc("2026-01-12T20:07:00+00:00")) == "AFTERNOON"
    assert scheduled_checkpoint(utc("2026-01-12T21:01:00+00:00")) == "CLOSE_1M"


def test_early_close_candidates_resolve_to_close_plus_one():
    assert scheduled_checkpoint(utc("2026-11-27T18:01:00+00:00")) == "CLOSE_1M"


def test_scheduler_skips_weekends_holidays_and_wrong_time_candidates():
    assert scheduled_checkpoint(utc("2026-07-04T13:00:00+00:00")) is None
    assert scheduled_checkpoint(utc("2026-11-26T14:00:00+00:00")) is None
    assert scheduled_checkpoint(utc("2026-07-06T18:00:00+00:00")) is None


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

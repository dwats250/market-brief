from datetime import datetime, timezone
from types import SimpleNamespace

from market_brief import cli
from market_brief.schedule import checkpoint_session, due


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


def test_scheduled_checkpoint_is_idempotent(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
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

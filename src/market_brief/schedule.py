"""Exchange-aware checkpoint timing for local and scheduled briefing runs."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

VANCOUVER = ZoneInfo("America/Vancouver")
ET = ZoneInfo("America/New_York")
CHECKPOINTS = ("PREMARKET", "OPEN_1M", "OPEN_30M", "AFTERNOON", "CLOSE_1M")
STATIC_LOCAL_TIMES = {
    "PREMARKET": (6, 0),
    "OPEN_1M": (6, 31),
    "OPEN_30M": (7, 0),
    "AFTERNOON": (12, 7),
}


def checkpoint_session(now, checkpoint="PREMARKET"):
    if checkpoint not in CHECKPOINTS:
        raise ValueError("unsupported checkpoint")
    cal = xcals.get_calendar("XNYS")
    local = now.astimezone(ET)
    session = cal.date_to_session(local.date().isoformat(), direction="next")
    trading_day = cal.is_session(local.date().isoformat())
    close = cal.session_close(session).to_pydatetime()
    if checkpoint == "CLOSE_1M":
        scheduled = close + timedelta(minutes=1)
    else:
        hour, minute = STATIC_LOCAL_TIMES[checkpoint]
        scheduled = datetime(local.year, local.month, local.day, hour, minute,
                             tzinfo=VANCOUVER).astimezone(timezone.utc)
    return dict(checkpoint=checkpoint, session_date=session.date().isoformat(),
                trading_day=bool(trading_day), scheduled_at=scheduled.isoformat(),
                exchange_open=cal.session_open(session).isoformat(),
                exchange_close=close.isoformat(),
                scheduled_local=scheduled.astimezone(VANCOUVER).isoformat())


def due(now, checkpoint, tolerance_minutes=45):
    info = checkpoint_session(now, checkpoint)
    if not info["trading_day"]:
        return False, info
    scheduled = datetime.fromisoformat(info["scheduled_at"])
    delta = (now - scheduled).total_seconds()
    return 0 <= delta <= tolerance_minutes * 60, info


def session_relation(when, exchange_open, exchange_close):
    if when < exchange_open:
        return "BEFORE OPEN"
    if when <= exchange_close:
        return "DURING SESSION"
    return "AFTER CLOSE"

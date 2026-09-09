"""Exchange-aware checkpoint timing for local and scheduled briefing runs."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

VANCOUVER = ZoneInfo("America/Vancouver")
ET = ZoneInfo("America/New_York")
CHECKPOINTS = ("PREMARKET", "OPEN_1M", "OPEN_30M", "AFTERNOON", "CLOSE_1M")
CHECKPOINT_TITLES = {"PREMARKET": "Premarket", "OPEN_1M": "Open +1M", "OPEN_30M": "Opening structure",
                     "AFTERNOON": "Afternoon", "CLOSE_1M": "Close +1M"}
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


def scheduled_checkpoint(now, tolerance_minutes=45):
    """Resolve a UTC scheduler candidate to the nearest due Pacific checkpoint."""
    candidates = []
    for checkpoint in CHECKPOINTS:
        info = checkpoint_session(now, checkpoint)
        if not info["trading_day"]:
            continue
        delta = (now - datetime.fromisoformat(info["scheduled_at"])).total_seconds()
        if 0 <= delta <= tolerance_minutes * 60:
            candidates.append((delta, checkpoint))
    return min(candidates)[1] if candidates else None


def current_phase(now):
    """Map a clock time to the checkpoint whose session phase it falls in.

    Phases partition the trading day by the scheduled checkpoints themselves:
    before the open is PREMARKET, the first half hour is OPEN_1M, then OPEN_30M
    until the afternoon checkpoint, AFTERNOON until the close, and CLOSE_1M after.
    Non-trading days resolve to PREMARKET of the next session.
    """
    cal = xcals.get_calendar("XNYS")
    local = now.astimezone(ET)
    day = local.date().isoformat()
    if not cal.is_session(day):
        return "PREMARKET"
    opening = cal.session_open(day).to_pydatetime()
    closing = cal.session_close(day).to_pydatetime()
    afternoon = datetime.fromisoformat(checkpoint_session(now, "AFTERNOON")["scheduled_at"])
    if now < opening:
        return "PREMARKET"
    if now >= closing:
        return "CLOSE_1M"
    if now < opening + timedelta(minutes=30):
        return "OPEN_1M"
    if now < afternoon:
        return "OPEN_30M"
    return "AFTERNOON"


def session_relation(when, exchange_open, exchange_close):
    if when < exchange_open:
        return "BEFORE OPEN"
    if when <= exchange_close:
        return "DURING SESSION"
    return "AFTER CLOSE"


def next_session_date(now):
    """The exchange session after the one `now` belongs to (holiday and weekend aware)."""
    cal = xcals.get_calendar("XNYS")
    day = now.astimezone(ET).date().isoformat()
    session = cal.date_to_session(day, direction="next")
    if not cal.is_session(day) or now < cal.session_close(session).to_pydatetime():
        return session.date().isoformat()
    return cal.next_session(session).date().isoformat()


def next_checkpoint(now, current=None):
    """The next scheduled checkpoint after `now`: later today, or the next session's premarket.

    `current` is the checkpoint of the running edition, which is never its own next update.
    """
    later = [checkpoint_session(now, checkpoint) for checkpoint in CHECKPOINTS if checkpoint != current]
    later = [info for info in later if info["trading_day"] and datetime.fromisoformat(info["scheduled_at"]) > now]
    if later:
        return min(later, key=lambda info: info["scheduled_at"])
    # Nothing later today: the next session's premarket, probed at a time inside that day.
    probe = datetime.fromisoformat(next_session_date(now) + "T12:00:00+00:00")
    return checkpoint_session(probe, "PREMARKET")

"""Exchange-aware checkpoint timing for local and scheduled briefing runs.

The daily cadence keeps rich interpretation scarce: one premarket synthesis and one interpretive
update after the open. Every later checkpoint is a deterministic refresh of the observed record under
the last accepted interpretation, and the close is a deterministic snapshot that hands the session off.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

VANCOUVER = ZoneInfo("America/Vancouver")
ET = ZoneInfo("America/New_York")
CHECKPOINTS = ("PREMARKET", "OPEN_1M", "OPEN_30M", "HOURLY_0800", "HOURLY_0900", "HOURLY_1000",
               "HOURLY_1100", "HOURLY_1200", "CLOSE_1M")
# synthesis: one model call under the edition's budget profile; refresh: no model call, fresh
# observed rows under the carried interpretation; close: a refresh that also hands the session off.
CHECKPOINT_KINDS = {"PREMARKET": "synthesis", "OPEN_1M": "refresh", "OPEN_30M": "synthesis",
                    "HOURLY_0800": "refresh", "HOURLY_0900": "refresh", "HOURLY_1000": "refresh",
                    "HOURLY_1100": "refresh", "HOURLY_1200": "refresh", "CLOSE_1M": "close"}
SYNTHESIS_CHECKPOINTS = tuple(c for c in CHECKPOINTS if CHECKPOINT_KINDS[c] == "synthesis")
CHECKPOINT_TITLES = {"PREMARKET": "Premarket", "OPEN_1M": "Open +1M", "OPEN_30M": "Opening structure",
                     "HOURLY_0800": "8:00 AM refresh", "HOURLY_0900": "9:00 AM refresh",
                     "HOURLY_1000": "10:00 AM refresh", "HOURLY_1100": "11:00 AM refresh",
                     "HOURLY_1200": "12:00 PM refresh", "CLOSE_1M": "Close +1M"}
STATIC_LOCAL_TIMES = {
    "PREMARKET": (6, 0),
    "OPEN_1M": (6, 31),
    "OPEN_30M": (7, 0),
    "HOURLY_0800": (8, 0),
    "HOURLY_0900": (9, 0),
    "HOURLY_1000": (10, 0),
    "HOURLY_1100": (11, 0),
    "HOURLY_1200": (12, 0),
}
# How late a wake may be and still count as a checkpoint's own attempt. A synthesis is attempted once, by
# the wake that naturally follows its scheduled minute; the wake candidates that exist for the other
# Pacific season land thirty-one minutes later (14:31 UTC is 7:31 PDT) and must never become a second
# paid attempt, whatever the first attempt's outcome. Deterministic refreshes and the close keep the wider
# window because a late deterministic run costs nothing and repeats nothing.
TOLERANCE_MINUTES = {"synthesis": 20, "refresh": 45, "close": 45}


def checkpoint_kind(checkpoint):
    if checkpoint not in CHECKPOINT_KINDS:
        raise ValueError("unsupported checkpoint")
    return CHECKPOINT_KINDS[checkpoint]


def tolerance_minutes(checkpoint, override=None):
    """The due window for one checkpoint; an explicit override never widens a synthesis window."""
    own = TOLERANCE_MINUTES[checkpoint_kind(checkpoint)]
    return own if override is None else min(own, override)


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
    kind = CHECKPOINT_KINDS[checkpoint]
    # An hourly refresh at or after an early close has nothing to refresh; the close snapshot covers it.
    applicable = bool(trading_day) and (kind != "refresh" or scheduled < close)
    return dict(checkpoint=checkpoint, kind=kind, title=CHECKPOINT_TITLES[checkpoint],
                session_date=session.date().isoformat(),
                trading_day=bool(trading_day), applicable=applicable, scheduled_at=scheduled.isoformat(),
                exchange_open=cal.session_open(session).isoformat(),
                exchange_close=close.isoformat(),
                scheduled_local=scheduled.astimezone(VANCOUVER).isoformat())


def due(now, checkpoint, tolerance=None):
    """Whether `now` falls inside the checkpoint's own due window (see TOLERANCE_MINUTES)."""
    info = checkpoint_session(now, checkpoint)
    if not info["applicable"]:
        return False, info
    scheduled = datetime.fromisoformat(info["scheduled_at"])
    delta = (now - scheduled).total_seconds()
    return 0 <= delta <= tolerance_minutes(checkpoint, tolerance) * 60, info


def scheduled_checkpoint(now, tolerance=None):
    """Resolve a UTC scheduler candidate to the nearest due Pacific checkpoint, or None (a SKIP)."""
    candidates = []
    for checkpoint in CHECKPOINTS:
        ready, info = due(now, checkpoint, tolerance)
        if ready:
            candidates.append(((now - datetime.fromisoformat(info["scheduled_at"])).total_seconds(), checkpoint))
    return min(candidates)[1] if candidates else None


def current_phase(now):
    """Map a clock time to the checkpoint whose session phase it falls in.

    Phases partition the trading day by the scheduled checkpoints themselves: before the open is
    PREMARKET, then each checkpoint's phase runs until the next applicable checkpoint, and everything
    from the close onward is CLOSE_1M. Non-trading days resolve to PREMARKET of the next session.
    """
    cal = xcals.get_calendar("XNYS")
    local = now.astimezone(ET)
    day = local.date().isoformat()
    if not cal.is_session(day):
        return "PREMARKET"
    opening = cal.session_open(day).to_pydatetime()
    closing = cal.session_close(day).to_pydatetime()
    if now < opening:
        return "PREMARKET"
    if now >= closing:
        return "CLOSE_1M"
    phase = "PREMARKET"
    for checkpoint in CHECKPOINTS:
        if checkpoint == "CLOSE_1M":
            continue
        info = checkpoint_session(now, checkpoint)
        if info["applicable"] and datetime.fromisoformat(info["scheduled_at"]) <= now:
            phase = checkpoint
    return phase


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


def next_checkpoint(now, current=None, kinds=None):
    """The next scheduled checkpoint after `now`: later today, or the next session's premarket.

    `current` is the checkpoint of the running edition, which is never its own next update; `kinds`
    restricts the candidates (for example to synthesis checkpoints, the ones an analyst can judge at).
    """
    later = [checkpoint_session(now, checkpoint) for checkpoint in CHECKPOINTS
             if checkpoint != current and (kinds is None or CHECKPOINT_KINDS[checkpoint] in kinds)]
    later = [info for info in later if info["applicable"] and datetime.fromisoformat(info["scheduled_at"]) > now]
    if later:
        return min(later, key=lambda info: info["scheduled_at"])
    # Nothing later today: the next session's premarket, probed at a time inside that day. Before the
    # close `next_session_date` still names today's session, so step past it explicitly.
    cal = xcals.get_calendar("XNYS")
    following = next_session_date(now)
    if following == now.astimezone(ET).date().isoformat():
        following = cal.next_session(following).date().isoformat()
    probe = datetime.fromisoformat(following + "T12:00:00+00:00")
    return checkpoint_session(probe, "PREMARKET")


def next_synthesis(now, current=None):
    """The next checkpoint at which an analyst can judge a watch: the next synthesis, or the next premarket."""
    return next_checkpoint(now, current, kinds={"synthesis"})

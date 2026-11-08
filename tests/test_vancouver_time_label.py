"""Vancouver clocks are labelled "Vancouver time", never "PT".

British Columbia stays at UTC-7 all year (tzdata 2026b), so a winter "PT" reads as UTC-8 to most readers and
an hour wrong. The fix is display wording only: the UTC schedule and the converted clock are unchanged.
"""
import re
from datetime import datetime, timezone

from market_brief.render import pacific_time
from market_brief.schedule import LOCAL_TIME_LABEL, VANCOUVER, checkpoint_session


def test_label_is_unambiguous():
    assert LOCAL_TIME_LABEL == "Vancouver time"


def test_winter_clock_says_vancouver_time_not_pt():
    text = pacific_time("2027-01-12T14:00:00+00:00")
    assert text.endswith(" Vancouver time")
    assert not re.search(r"\bPT\b", text)
    assert text.removesuffix(" Vancouver time") == datetime(2027, 1, 12, 14, tzinfo=timezone.utc).astimezone(
        VANCOUVER).strftime("%-I:%M %p")


def test_dated_clock_keeps_its_date_and_label():
    assert pacific_time("2026-07-06T13:00:00+00:00", include_date=True) == "Monday, Jul 6 · 6:00 AM Vancouver time"


def test_schedule_stays_utc():
    """Display wording only: the scheduled UTC instant for a checkpoint is what it was."""
    premarket = checkpoint_session(datetime(2026, 7, 6, 12, tzinfo=timezone.utc), "PREMARKET")
    assert premarket["scheduled_at"].startswith("2026-07-06T13:00:00")

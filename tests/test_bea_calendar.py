"""The BEA release schedule: when a national GDP estimate, Personal Income and Outlays or International Trade is
scheduled, and nothing else. No figure, estimate or consensus is read, implied or collected.

`tests/fixtures/bea-calendar.ics` is BEA's complete subscription file as served on 2026-11-28 (HTTP 200, 43,459 bytes,
169 events dated 2025-01-07 to 2027-12-22, CRLF line endings, folded and escaped `SUMMARY` lines, no recurrence), kept
byte for byte: its sha256 is the sha256 of the response body. The file spells the national GDP releases two ways
(`Gross Domestic Product, …` and `GDP (…)`), bundles corporate profits, industries and state statistics into some of
them, and lists regional, territorial and satellite-account releases that begin with the same words.
"""

import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pytest
from test_pipeline import NOW, narrative
from test_provenance import drawer

from market_brief import collect
from market_brief.actuals import FAMILIES
from market_brief.collect import SourceError, calendar_events, collect_live
from market_brief.context import analyst_context, edition_profile
from market_brief.evidence import ROOT, finalize_coverage, model_packet, normalize_packet, read_json
from market_brief.metrics import derive
from market_brief.render import render
from market_brief.schedule import next_session_date
from market_brief.synthesize import compact_json, construct_prompt, validate_narrative

EASTERN = ZoneInfo("America/New_York")
FIXTURES = ROOT / "tests/fixtures"
BEA_URL = "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics"
BEA_SHA256 = "cbebc02fc884f265b1ae3820134b5b0ac2202ace72974129b27665c2660f15cd"
# As served: CRLF. (`Path.read_text` would translate the line endings the collector actually receives.)
BEA_ICS = (FIXTURES / "bea-calendar.ics").read_bytes().decode("utf-8")
BLS_ICS = (FIXTURES / "bls.2026-09-11.ics").read_text()
OFFLINE = SourceError("offline")
GDP_ADVANCE = "GDP (Advance Estimate), 4th Quarter and Year 2025"  # Friday, 2026-02-20
PIO_DECEMBER = "Personal Income and Outlays, December 2025"  # the same morning
TRADE_DECEMBER = "U.S. International Trade in Goods and Services, December and Annual 2025"  # Thursday, 2026-02-19
GDP_THIRD = ("GDP (Third Estimate), Industries, Corporate Profits, State GDP, and State Personal Income, "
             "4th Quarter and Year 2025")  # Thursday, 2026-04-09, with Personal Income and Outlays, February 2026


@pytest.fixture(autouse=True)
def no_alpaca(monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)


def at(day, hour, minute=0):
    """An Eastern wall-clock time on `day` (YYYY-MM-DD), as the UTC instant the collector is given."""
    return datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00").replace(tzinfo=EASTERN).astimezone(timezone.utc)


def morning(day):
    return at(day, 7)  # before the open of a session day: that day alone is in the admission window


def after_close(day):
    return at(day, 16, 30)  # after the close: that day and the next session


def read(now, body=BEA_ICS):
    """The BEA reader as the collector calls it, with `body` as the file it fetched."""
    events, url, reason, retrieved = collect.bea_calendar(now, time.monotonic() + 60, lambda url, deadline: body)
    assert (url, reason) == (BEA_URL, "") and retrieved.tzinfo is not None
    return events


def titles(events):
    return [event["title"] for event in events]


def et(event):
    return datetime.fromisoformat(event["scheduled_at"]).astimezone(EASTERN).strftime("%Y-%m-%d %H:%M")


def collected(now, responses):
    """The live collector, offline: `responses` maps a URL to its body or to the error fetching it raises; every other
    URL is offline. Returns the raw packet, the BEA source record and the BEA-host requests."""
    calls = []

    def fetcher(url, deadline):
        calls.append(url)
        result = responses.get(url, OFFLINE)
        if isinstance(result, Exception):
            raise result
        return result
    raw = collect_live(now, fetcher=fetcher)
    return raw, next(s for s in raw["sources"] if s["id"] == "bea"), [url for url in calls if "bea.gov" in url]


def bea_events(raw):
    return [event for event in raw["events"] if event["source_id"] == "bea"]


def freeze(monkeypatch, fixed):
    """The collector's clock is the run's, as in production: normalization drops an event checked hours later."""
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz else fixed.replace(tzinfo=None)
    monkeypatch.setattr(collect, "datetime", Frozen)


# --- The oracle: the fixture read independently of the code under test ------------------------------------------------

def feed():
    """Every event of the file as `(title, UTC start, whole raw block)`: unfolded, the RFC 5545 text escapes decoded."""
    raw_blocks = BEA_ICS.split("BEGIN:VEVENT\r\n")[1:]
    rows = []
    for raw in raw_blocks:
        block = re.sub(r"\r\n[ \t]", "", raw)
        summary = re.search(r"^SUMMARY:(.*)\r$", block, re.M).group(1)
        start = re.search(r"^DTSTART[^:]*:(\d{8}T\d{6})Z\r$", block, re.M).group(1)
        when = datetime.strptime(start, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        rows.append((re.sub(r"\\([,;\\])", r"\1", summary), when, "BEGIN:VEVENT\r\n" + raw.split("END:VEVENT\r\n")[0]
                     + "END:VEVENT\r\n"))
    return rows


# The spellings the file uses for a headline national GDP estimate, Personal Income and Outlays and International
# Trade, read off the complete file (the text before each title's first comma).
ADMITTED_HEADS = {"Gross Domestic Product", "GDP (Advance Estimate)", "GDP (Second Estimate)",
                  "GDP (Second Estimate) and Corporate Profits", "GDP (Third Estimate)",
                  "Personal Income and Outlays", "U.S. International Trade in Goods and Services"}


def head(title):
    return title.split(",", 1)[0]


def calendar(*blocks):
    """The file's own header and footer around `blocks` (whole events)."""
    return BEA_ICS.split("BEGIN:VEVENT")[0] + "".join(blocks) + "END:VCALENDAR\r\n"


def block_of(title, day):
    (found,) = [raw for name, when, raw in feed() if name == title and when.date().isoformat() == day]
    return found


def restart(block, start):
    """`block` with its start replaced by `start` (a whole `DTSTART…` line, no line end), whichever way it spells it."""
    changed = re.sub(r"DTSTART[^:\r]*:[0-9TZ]+", start, block, count=1)
    assert changed != block
    return changed


def event_block(title, day, hour=12):
    """One whole event, shaped as the file shapes them, for forms the file does not list today."""
    summary = title.replace(",", chr(92) + ",")
    return (f"BEGIN:VEVENT\r\nSUMMARY:{summary}\r\nDTSTART:{day.replace('-', '')}T{hour:02d}3000Z\r\nDURATION:PT30M"
            f"\r\nUID:{hashlib.sha256(title.encode()).hexdigest()[:8]}\r\nEND:VEVENT\r\n")


# --- The fixture ------------------------------------------------------------------------------------------------------

def test_the_fixture_is_the_complete_capture_byte_for_byte():
    assert hashlib.sha256((FIXTURES / "bea-calendar.ics").read_bytes()).hexdigest() == BEA_SHA256
    rows = feed()
    assert len(rows) == 169 >= 40
    assert min(row[1] for row in rows).isoformat() == "2025-01-07T13:30:00+00:00"
    assert max(row[1] for row in rows).isoformat() == "2027-12-22T13:30:00+00:00"
    assert "\r\n" in BEA_ICS and "RRULE" not in BEA_ICS
    assert "DTSTART;VALUE=DATE-TIME:" in BEA_ICS and "\r\nDTSTART:" in BEA_ICS  # both spellings of the start time


# --- The release families ---------------------------------------------------------------------------------------------

ADMITTED = [
    ("long advance", "Gross Domestic Product, 4th Quarter and Year 2024 (Advance Estimate)", "2025-01-30", "08:30"),
    ("long second", "Gross Domestic Product, 4th Quarter and Year 2024 (Second Estimate)", "2025-02-27", "08:30"),
    ("long second with corporate profits",
     "Gross Domestic Product, 1st Quarter 2025 (Second Estimate) and Corporate Profits (Preliminary)",
     "2025-05-29", "08:30"),
    ("long third with industries and corporate profits",
     "Gross Domestic Product, 4th Quarter and Year 2024 (Third Estimate), GDP by Industry, and Corporate Profits",
     "2025-03-27", "08:30"),
    ("long initial, the delayed third-quarter release",
     "Gross Domestic Product, 3rd Quarter 2025 (Initial Estimate) and Corporate Profits (Preliminary)",
     "2025-12-23", "08:30"),
    ("long updated", "Gross Domestic Product, 3rd Quarter 2025 (Updated Estimate), GDP by Industry, and Corporate "
     "Profits (Revised)", "2026-01-22", "08:30"),
    ("short advance", GDP_ADVANCE, "2026-02-20", "08:30"),
    ("short second", "GDP (Second Estimate), 4th Quarter and Year 2025", "2026-03-13", "08:30"),
    ("short second with corporate profits", "GDP (Second Estimate) and Corporate Profits, 1st Quarter 2026",
     "2026-05-28", "08:30"),
    ("short third with industries, profits, state GDP and state personal income", GDP_THIRD, "2026-04-09", "08:30"),
    ("personal income and outlays", PIO_DECEMBER, "2026-02-20", "08:30"),
    ("personal income and outlays at ten", "Personal Income and Outlays, October and November 2025", "2026-01-22",
     "10:00"),
    ("international trade", TRADE_DECEMBER, "2026-02-19", "08:30"),
]


@pytest.mark.parametrize("family, title, day, clock", ADMITTED, ids=[row[0] for row in ADMITTED])
def test_each_admitted_family_is_admitted_on_its_eastern_date(family, title, day, clock):
    (event,) = [event for event in read(morning(day)) if event["title"] == title]
    assert et(event) == f"{day} {clock}"
    assert (event["source_id"], event["status"], event["published_at"]) == ("bea", "SCHEDULED", None)


def test_the_window_is_today_and_the_next_session_only():
    assert GDP_ADVANCE in titles(read(morning("2026-02-20")))
    assert GDP_ADVANCE in titles(read(after_close("2026-02-19")))  # Thursday after the close: Friday is next
    assert GDP_ADVANCE not in titles(read(morning("2026-02-19")))  # Thursday morning: Friday is not yet next
    assert GDP_ADVANCE not in titles(read(morning("2026-02-18")))
    assert GDP_ADVANCE not in titles(read(morning("2026-02-23")))  # the Monday after


def test_two_eastern_dates_in_one_window_are_numbered_by_time_then_title():
    assert [(event["id"], et(event), event["title"]) for event in read(after_close("2026-02-19"))] == [
        ("bea-event-0", "2026-02-19 08:30", TRADE_DECEMBER),
        ("bea-event-1", "2026-02-20 08:30", GDP_ADVANCE),
        ("bea-event-2", "2026-02-20 08:30", PIO_DECEMBER)]


def test_day_decisions_are_in_eastern_time_not_vancouver_or_utc():
    # 22:00 Eastern on Thursday the 19th is already Friday the 20th in UTC; the 19th's release is still today's.
    now = datetime(2026, 2, 20, 3, 0, tzinfo=timezone.utc)
    assert now.astimezone(EASTERN).date().isoformat() == "2026-02-19"
    assert titles(read(now)) == [TRADE_DECEMBER, GDP_ADVANCE, PIO_DECEMBER]


def test_every_day_of_the_complete_file_admits_exactly_the_four_families():
    # Against the whole file, day by day: the unfiltered reader's window is the control (nothing is missing merely for
    # lying outside it), and what the family rules leave must be exactly the events the file spells with an admitted
    # head.
    rows = feed()
    # The exchange calendar ends 2027-11-26 and `next_session_date` cannot look past it: the last week is left out.
    limit = (xcals.get_calendar("XNYS").last_session.date() - timedelta(days=7)).isoformat()
    days = sorted(day for day in {when.astimezone(EASTERN).date().isoformat() for _, when, _ in rows} if day <= limit)
    assert len(days) > 80
    for day in days:
        now = at(day, 12)
        window = {day, next_session_date(now)}
        in_window = {event["scheduled_at"] for event in calendar_events(BEA_ICS, now, now)}
        expected = sorted((when.isoformat(), title) for title, when, _ in rows
                          if when.astimezone(EASTERN).date().isoformat() in window and head(title) in ADMITTED_HEADS)
        assert {when for when, _ in expected} <= in_window
        assert sorted((e["scheduled_at"], e["title"]) for e in read(now)) == expected, day


# --- What is not admitted ---------------------------------------------------------------------------------------------

EXCLUDED = [
    ("by state", "Gross Domestic Product by State and Personal Income by State, 4th Quarter 2024 and Year 2024 "
     "(Preliminary)", "2025-03-28"),
    ("by state with consumption", "Gross Domestic Product by State and Personal Income by State, 2nd quarter 2025 and "
     "Personal Consumption Expenditures by State, 2024", "2025-09-26"),
    ("by county and metropolitan area", "Gross Domestic Product by County and Metropolitan Area and Personal Income by "
     "County and Metropolitan Area, 2024", "2026-02-05"),
    ("short by county", "GDP by County and Personal Income by County, 2025", "2026-12-02"),
    ("puerto rico", "Gross Domestic Product for Puerto Rico, 2023", "2025-09-16"),
    ("travel and tourism", "Travel and Tourism Satellite Account, 2023", "2025-02-06"),
    ("international transactions", "U.S. International Transactions, 4th Quarter and Year 2024", "2025-03-20"),
    ("international investment position", "U.S. International Investment Position, 4th Quarter and Year 2024",
     "2025-03-26"),
    ("real consumption by state", "Real Personal Consumption Expenditures by State and Real Personal Income by State, "
     "2024", "2026-02-19"),
    ("distribution of personal income", "Distribution of U.S. Personal Income, 2025 (Experimental), 2024 "
     "(Provisional), and\xa02000-2023 (Updated)", "2026-06-16"),
]


@pytest.mark.parametrize("form, title, day", EXCLUDED, ids=[row[0] for row in EXCLUDED])
def test_every_excluded_form_in_the_file_produces_no_event(form, title, day):
    now = morning(day)
    assert title in titles(calendar_events(BEA_ICS, now, now))  # the window admits it; only the family rules refuse it
    assert title not in titles(read(now))


@pytest.mark.parametrize("title", [
    "Personal Consumption Expenditures by State, 2025",
    "Personal Consumption Expenditures by Industry, 2025",
    "Personal Income by County, 2025",
    "Personal Income by State, 4th Quarter 2025",
])
def test_the_adjacent_forms_the_file_does_not_list_today_are_refused_too(title):
    body = calendar(event_block(title, "2026-03-04"), block_of(GDP_ADVANCE, "2026-02-20"))
    now = morning("2026-03-04")
    assert title in titles(calendar_events(body, now, now))
    assert read(now, body) == []


@pytest.mark.parametrize("title, admitted", [
    # The prefix says admit; the guard (` by ` or ` for ` before the first comma) says region, territory or account.
    ("GDP (Advance Estimate) by State, 1st Quarter 2027", False),
    ("GDP (Second Estimate) for Puerto Rico, 2026", False),
    ("Personal Income and Outlays for Guam, 2026", False),
    ("Gross Domestic Product by State and Personal Income by State, 3rd quarter 2025", False),
    ("Gross Domestic Product for Puerto Rico, 2023", False),
    # The third estimate names State GDP and State Personal Income in its body, after the first comma: still national.
    (GDP_THIRD, True),
    ("GDP (Second Estimate) and Corporate Profits, 1st Quarter 2026", True),
    ("Gross Domestic Product, 4th Quarter and Year 2024 (Third Estimate), GDP by Industry, and Corporate Profits",
     True),
    ("Personal Income and Outlays, December 2025", True),
    ("U.S. International Trade in Goods and Services, Annual Update", True),
    # No ` by ` or ` for ` for the guard to find: only the prefixes (a comma after `Gross Domestic Product`, a
    # parenthesis after `GDP`) keep a satellite account or a spin-off of the name out.
    ("Gross Domestic Product Satellite Account, 2025", False),
    ("GDP Satellite Account, U.S. and States, 2025", False),
    ("GDP-in-Chained-Dollars Experiment, 2025", False),
    ("Personal Income and Outlays Satellite Account, 2025", False),
    # Not one of the four families at all.
    ("U.S. International Transactions, 2nd Quarter 2025", False),
    ("U.S. International Trade by Country, 2026", False),
    ("Real Personal Consumption Expenditures by State and Real Personal Income by State, 2025", False),
    ("Distribution of U.S. Personal Income, 2025", False),
    ("", False),
])
def test_the_family_rule_admits_national_headlines_and_guards_against_regions(title, admitted):
    assert collect.bea_release(title) is admitted


# --- Titles -----------------------------------------------------------------------------------------------------------

def test_titles_are_the_summary_verbatim_decoded_from_the_files_folds_and_escapes():
    unfolded = re.sub(r"\r\n[ \t]", "", BEA_ICS)
    assert len(re.findall(r"\r\n[ \t]", BEA_ICS)) == 49
    # The file folds this title between a backslash and the comma it escapes; the title still reconstructs.
    assert "State GDP\\\r\n , and State" in block_of(GDP_THIRD, "2026-04-09")
    assert GDP_THIRD in titles(read(morning("2026-04-09")))
    # A semicolon is escaped in the file (`\;`) and reads as a semicolon.
    assert "2nd Quarter 2026\\; State PCE\\, 2025" in unfolded
    assert ("GDP (Third Estimate), Industries, Corporate Profits, State GDP, and State Personal Income, 2nd Quarter "
            "2026; State PCE, 2025") in titles(read(morning("2026-09-30")))
    # BEA left a space after one title; the title is the summary verbatim.
    assert "SUMMARY:Personal Income and Outlays\\, July 2026 \r\n" in BEA_ICS
    assert "Personal Income and Outlays, July 2026 " in titles(read(morning("2026-08-26")))


@pytest.mark.parametrize("fold", ["\r\n ", "\r\n\t", "\n "])
def test_a_title_folded_anywhere_even_inside_an_escape_reconstructs_exactly(fold):
    title = "GDP (Third Estimate), Industries, Corporate Profits, State GDP, and State Personal Income, 4th Quarter"
    (line,) = re.findall(r"SUMMARY:[^\r]*\r\n", event_block(title, "2026-04-09"))
    inside_an_escape = line.index("\\,") + 1
    for cuts in ((20, 60), (inside_an_escape, 70), (9, 10, 11)):
        folded, previous = "", 0
        for cut in cuts:
            folded += line[previous:cut] + fold
            previous = cut
        folded += line[previous:]
        body = calendar(event_block(title, "2026-04-09").replace(line, folded))
        assert titles(read(morning("2026-04-09"), body)) == [title]


@pytest.mark.parametrize("encoded, decoded", [("a\\,b", "a,b"), ("a\\;b", "a;b"), ("a\\\\b", "a\\b"),
                                              ("a\\\\\\,b", "a\\,b"), ("a\\;\\,b", "a;,b")])
def test_the_text_escapes_are_decoded_once_and_only_those(encoded, decoded):
    block = event_block("X", "2026-04-09").replace("SUMMARY:X", "SUMMARY:GDP (Advance Estimate)\\, " + encoded)
    assert titles(read(morning("2026-04-09"), calendar(block))) == [f"GDP (Advance Estimate), {decoded}"]


# --- Time zones and duplicates ----------------------------------------------------------------------------------------

def test_january_1330z_is_0830_eastern_and_july_1230z_is_0830_eastern():
    (winter,) = [e for e in read(morning("2025-01-07")) if e["title"].startswith("U.S. International Trade")]
    assert (winter["scheduled_at"], et(winter)) == ("2025-01-07T13:30:00+00:00", "2025-01-07 08:30")
    (summer,) = [e for e in read(morning("2026-07-30")) if e["title"].startswith("GDP (Advance")]
    assert (summer["scheduled_at"], et(summer)) == ("2026-07-30T12:30:00+00:00", "2026-07-30 08:30")


def test_a_per_event_zone_goes_through_the_one_shared_zone_reader():
    block = block_of(GDP_ADVANCE, "2026-02-20")
    for zone in ("America/New_York", "US-Eastern"):  # the second is the name BLS's file gives New York's rules
        zoned = restart(block, f"DTSTART;TZID={zone}:20260220T083000")
        (event,) = read(morning("2026-02-20"), calendar(zoned))
        assert event["scheduled_at"] == "2026-02-20T13:30:00+00:00"


def test_a_duplicate_title_and_time_collapses_to_one_event():
    pio, gdp = block_of(PIO_DECEMBER, "2026-02-20"), block_of(GDP_ADVANCE, "2026-02-20")
    assert titles(read(morning("2026-02-20"), calendar(gdp, pio, pio, pio))) == [GDP_ADVANCE, PIO_DECEMBER]
    later = restart(pio, "DTSTART:20260220T150000Z")  # the same title at another time is another event
    assert len(read(morning("2026-02-20"), calendar(gdp, pio, later))) == 3


# --- A feed that cannot be read is an unavailable source, never a partial one -----------------------------------------

def damage_first_event(old, new):
    first = feed()[0][2]  # 2025-01-07: the file's first event, `DTSTART;VALUE=DATE-TIME:…`, starting with its SUMMARY
    assert old in first
    return BEA_ICS.replace(first, first.replace(old, new, 1))


START = "DTSTART;VALUE=DATE-TIME:20250107T133000Z\r\n"
VARIANTS = [
    ("fetch failure", SourceError("HTTP 503"), "HTTP 503"),
    ("not found", SourceError("HTTP 404"), "HTTP 404"),
    ("an HTML page", "<!DOCTYPE html><html><body><h1>Release schedule</h1></body></html>", "malformed calendar"),
    ("no end of calendar", BEA_ICS.rsplit("END:VCALENDAR", 1)[0], "malformed calendar"),
    ("no start of calendar", BEA_ICS.replace("BEGIN:VCALENDAR", "", 1), "malformed calendar"),
    ("an event without a start", damage_first_event(START, ""), "calendar event lacks time/title"),
    ("an event without a title", damage_first_event("SUMMARY:", "SUMMARX:"), "calendar event lacks time/title"),
    ("a recurring event", damage_first_event(START, START + "RRULE:FREQ=MONTHLY\r\n"),
     "incomplete or recurring calendar unsupported"),
    ("an unreadable start", damage_first_event(START, "DTSTART;VALUE=DATE-TIME:2025-01-07 13:30\r\n"),
     "malformed ValueError"),
    ("an unsupported zone", damage_first_event(START, "DTSTART;TZID=Mars/Olympus_Mons:20250107T083000\r\n"),
     "unsupported calendar time zone"),
]


@pytest.mark.parametrize("name, response, reason", VARIANTS, ids=[row[0] for row in VARIANTS])
def test_an_unreadable_feed_is_unavailable_with_its_reason_and_the_run_still_renders(name, response, reason):
    raw, bea, calls = collected(NOW, {BEA_URL: response})
    assert (bea["status"], bea["reason"], bea["coverage_date"], bea["url"]) == ("UNAVAILABLE", reason, None, BEA_URL)
    assert bea_events(raw) == []
    assert calls == [BEA_URL]  # one request: no retry, no fallback page, no other host
    packet, md, page = sample_page(bea)  # the run goes on and publishes, naming the unavailable source
    assert f"BEA release schedule: {reason}" in packet["coverage"]["limitations"]
    assert f"BEA release schedule: {reason}" in page and "BEA calendar" not in page


def ends_on(day):
    """The file as it would be if it stopped after `day`: a real prefix of it, whole events."""
    end = datetime.fromisoformat(f"{day}T23:59:59+00:00")
    return calendar(*[raw for _, when, raw in feed() if when <= end])


@pytest.mark.parametrize("now, body", [(morning("2024-12-30"), BEA_ICS),
                                       (morning("2026-03-04"), ends_on("2026-02-20"))],
                         ids=["before the file begins", "after the file ends"])
def test_a_file_that_does_not_cover_the_target_date_is_unavailable(now, body):
    raw, bea, calls = collected(now, {BEA_URL: body})
    assert (bea["status"], bea["reason"], bea["coverage_date"]) == (
        "UNAVAILABLE", "calendar does not establish coverage for target date", None)
    assert bea_events(raw) == [] and calls == [BEA_URL]


def test_one_malformed_event_admits_none_of_the_others():
    now = morning("2026-02-20")
    assert titles(read(now)) == [GDP_ADVANCE, PIO_DECEMBER]
    # The damaged event is a regional release on another day; the day's own national releases are intact, and yet
    # none is admitted.
    damaged = BEA_ICS.replace("SUMMARY:Real Personal Consumption", "SUMMARX:Real Personal Consumption", 1)
    assert damaged != BEA_ICS
    with pytest.raises(SourceError, match="lacks time/title"):
        read(now, damaged)
    raw, bea, _ = collected(now, {BEA_URL: damaged})
    assert bea["status"] == "UNAVAILABLE" and bea_events(raw) == []


def test_the_file_is_requested_once_by_name_and_the_record_names_what_was_read():
    raw, bea, calls = collected(morning("2026-02-20"), {BEA_URL: BEA_ICS})
    assert calls == [BEA_URL]
    assert (bea["status"], bea["url"], bea["reason"], bea["name"], bea["kind"]) == (
        "AVAILABLE", BEA_URL, "", "BEA release schedule", "calendar")
    assert [(e["id"], e["title"]) for e in bea_events(raw)] == [("bea-event-0", GDP_ADVANCE),
                                                               ("bea-event-1", PIO_DECEMBER)]


# --- Ids --------------------------------------------------------------------------------------------------------------

def test_ids_are_stable_for_a_feed_whichever_order_the_file_lists_it():
    def read_without_the_clock(body=BEA_ICS):
        return [{key: value for key, value in event.items() if key != "checked_at"}
                for event in read(after_close("2026-02-19"), body)]
    forward = read_without_the_clock()
    assert read_without_the_clock() == forward
    assert read_without_the_clock(calendar(*[raw for _, _, raw in reversed(feed())])) == forward
    assert [event["id"] for event in forward] == ["bea-event-0", "bea-event-1", "bea-event-2"]


def test_a_day_with_bls_and_bea_releases_has_no_id_in_common(monkeypatch):
    now = at("2026-09-03", 7, 5)  # BLS: Productivity and Costs. BEA: International Trade, July 2026.
    freeze(monkeypatch, now)
    raw, bea, _ = collected(now, {BEA_URL: BEA_ICS, collect.BLS: BLS_ICS})
    assert {event["id"]: event["source_id"] for event in raw["events"]} == {"bea-event-0": "bea", "bls-event-0": "bls"}
    assert bea_events(raw)[0]["title"] == "U.S. International Trade in Goods and Services, July 2026"
    packet = normalize_packet(raw, now, "LIVE", "PREMARKET")  # its identity check refuses a repeated id
    assert sorted(event["id"] for event in packet["events"]) == ["bea-event-0", "bls-event-0"]


# --- What the packet and the page say ---------------------------------------------------------------------------------

def sample_page(*records):
    """The sample fixture's day with `records` standing in for the sources of the same id, and the page it renders."""
    raw = read_json(FIXTURES / "evidence.sample.json")
    raw["sources"] = [next((r for r in records if r["id"] == s["id"]), s) for s in raw["sources"]]
    raw["cuttingboard"] = collect.cuttingboard_record(raw["cuttingboard"], NOW, NOW)
    packet = finalize_coverage(derive(normalize_packet(raw, NOW, "SAMPLE"), read_json(ROOT / "config/universe.json")))
    return packet, *render(packet, narrative())


def test_coverage_date_is_set_on_success_and_the_not_collected_line_goes(monkeypatch):
    freeze(monkeypatch, NOW)
    _, bea, _ = collected(NOW, {BEA_URL: BEA_ICS})
    assert (bea["status"], bea["coverage_date"], bea["retrieved_at"]) == ("AVAILABLE", "2026-09-08", NOW.isoformat())
    packet, md, page = sample_page(bea)
    assert not [line for line in packet["coverage"]["limitations"] if "BEA" in line]
    assert "BEA calendar" not in md and "BEA calendar: not collected" not in page
    row = re.search(r"<li><span class=\"source-name\"><a href=\"([^\"]+)\">BEA release schedule</a>.*?</li>",
                    drawer(page, "Sources ·")).group(0)
    assert BEA_URL in row and "Available" in row and "unavailable" not in row and "not collected" not in row


def test_a_failed_feed_reads_unavailable_with_its_reason_not_not_collected():
    _, bea, _ = collected(NOW, {BEA_URL: SourceError("HTTP 503")})
    packet, md, page = sample_page(bea)
    assert "BEA release schedule: HTTP 503" in packet["coverage"]["limitations"]
    assert "BEA release schedule: HTTP 503" in page and "BEA release schedule: not collected" not in page
    row = re.search(r"<li><span class=\"source-name\">.*?BEA release schedule.*?</li>", drawer(page, "Sources ·"),
                    re.S).group(0)
    assert "HTTP 503" in row and "not collected" not in row


def test_readiness_does_not_change_the_fed_calendar_still_holds_coverage_partial(monkeypatch):
    freeze(monkeypatch, NOW)
    _, bea, _ = collected(NOW, {BEA_URL: BEA_ICS})
    _, failed, _ = collected(NOW, {BEA_URL: SourceError("HTTP 503")})
    sample = read_json(FIXTURES / "evidence.sample.json")
    fed = next(s for s in sample["sources"] if s["id"] == "fed-calendar")
    assert (fed["status"], fed["coverage_date"]) == ("AVAILABLE", "2026-09-08")
    fed_stub = collect.source("fed-calendar", "Fed calendar", "calendar",
                              "https://www.federalreserve.gov/newsevents/calendar.htm", NOW, "UNAVAILABLE",
                              "not automated in this slice; sourced input supported")

    def status(bea_record, fed_record):
        value = read_json(FIXTURES / "evidence.sample.json")
        value["sources"] = [bea_record if s["id"] == "bea" else fed_record if s["id"] == "fed-calendar" else s
                            for s in value["sources"]]
        value["cuttingboard"] = collect.cuttingboard_record(value["cuttingboard"], NOW, NOW)
        packet = derive(normalize_packet(value, NOW, "SAMPLE"), read_json(ROOT / "config/universe.json"))
        return finalize_coverage(packet)["coverage"]["status"]
    # The control: with the Fed calendar available, the collected BEA record is all readiness asks of BEA.
    assert status(bea, fed) == "READY"
    # As collected today: BEA available, the Fed calendar still a stub. The gate is as it was: partial.
    assert status(bea, fed_stub) == "PARTIAL"
    assert status(failed, fed) == "PARTIAL"  # and a failed BEA alone holds it partial too


# --- What the analyst reads -------------------------------------------------------------------------------------------

RELEASE_FIELDS = {"id", "title", "scheduled_at", "session_relation", "source_id", "status"}


def live_edition(monkeypatch, checkpoint, now):
    """The real collector's BEA record and events through normalization, derivation and the edition's context."""
    freeze(monkeypatch, now)
    raw, _, _ = collected(now, {BEA_URL: BEA_ICS})
    packet = finalize_coverage(derive(normalize_packet(raw, now, "LIVE", checkpoint),
                                      read_json(ROOT / "config/universe.json")))
    return packet, analyst_context(packet, edition_profile(checkpoint))


def release(ident, title, relation):
    return dict(id=ident, title=title, scheduled_at="2026-02-20T13:30:00+00:00", session_relation=relation,
                source_id="bea", status="SCHEDULED")


def test_events_reach_the_analyst_only_as_the_six_release_fields_with_the_right_session_relation(monkeypatch):
    packet, context = live_edition(monkeypatch, "PREMARKET", at("2026-02-20", 7, 5))
    expected = [release("bea-event-0", GDP_ADVANCE, "BEFORE OPEN"), release("bea-event-1", PIO_DECEMBER, "BEFORE OPEN")]
    assert model_packet(packet)["events"] == context["events"] == expected
    # The stored record keeps its collection plumbing; the analyst never reads it.
    assert {"checked_at", "published_at", "scheduled_at_et", "session_date", "freshness",
            "expected_freshness"} <= set(packet["events"][0])
    assert set(model_packet(packet)["events"][0]) == RELEASE_FIELDS


def test_the_next_sessions_releases_are_the_next_session_never_today(monkeypatch):
    # The close is a deterministic checkpoint (it has no synthesis profile): what the analyst's view would carry is
    # `model_packet`, which `analyst_context` projects with the same six fields.
    now = at("2026-02-19", 16, 1)
    freeze(monkeypatch, now)
    raw, _, _ = collected(now, {BEA_URL: BEA_ICS})
    packet = finalize_coverage(derive(normalize_packet(raw, now, "LIVE", "CLOSE_1M"),
                                      read_json(ROOT / "config/universe.json")))
    assert [(e["id"], e["title"], e["session_relation"]) for e in model_packet(packet)["events"]] == [
        ("bea-event-0", TRADE_DECEMBER, "BEFORE OPEN"),
        ("bea-event-1", GDP_ADVANCE, "NEXT SESSION"),
        ("bea-event-2", PIO_DECEMBER, "NEXT SESSION")]
    assert all(set(event) == RELEASE_FIELDS for event in model_packet(packet)["events"])


def test_a_watch_can_be_timed_to_a_collected_bea_release():
    raw = read_json(FIXTURES / "evidence.sample.json")
    raw["events"][0].update(id="bea-event-0", source_id="bea")
    raw["cuttingboard"] = collect.cuttingboard_record(raw["cuttingboard"], NOW, NOW)
    packet = finalize_coverage(derive(normalize_packet(raw, NOW, "SAMPLE"), read_json(ROOT / "config/universe.json")))
    context = analyst_context(packet, edition_profile("PREMARKET"))
    assert context["events"][0]["id"] == "bea-event-0" and context["events"][0]["source_id"] == "bea"
    value = json.loads(json.dumps(narrative()).replace("sample-event", "bea-event-0"))
    value["watches"][0]["horizon"] = "EVENT(bea-event-0)"
    assert validate_narrative(value, packet) and validate_narrative(value, packet, context)
    value["watches"][0]["horizon"] = "EVENT(bea-event-9)"
    with pytest.raises(ValueError, match="unknown event horizon"):
        validate_narrative(value, packet)


# --- A4: the context budget, measured against a same-fixture controlled baseline -------------------------------------
#
# Each arm is the sample fixture's Tuesday premarket (08:45 ET) built twice: once with BEA's collected record and the
# releases BEA schedules that morning (real titles, moved to that Tuesday at 08:30 ET), once with the BEA source forced
# to the stub it was before and no BEA event. The two differ only in BEA. What the analyst's context gains is the
# events (six fields each), the curve's release note, which names every scheduled release timed after the curve's
# 3:30 PM ET quotes (`curve.release_notes`, for any source), and the source record's reason, which shrinks.

STUB_REASON = "not automated in this slice; sourced input supported"
PERMIT_BOUND = 400  # B: the permit's estimate for two releases, "about 150 B each"
# Measured over all 80 real release mornings in the file, each moved to this fixture's Tuesday: one release adds 259 to
# 429 B (median 305), two add 494 to 694 B (median 538; the heaviest is 2027-09-30). The permit's estimate of about
# 150 B an event is low: real titles run to 120 characters, and the curve's note names each release a second time.
MEASURED_CEILING = 700
ONE = [("U.S. International Trade in Goods and Services, May 2026", "2026-07-07")]
TWO = [(GDP_ADVANCE, "2026-02-20"), (PIO_DECEMBER, "2026-02-20")]
TWO_LONGEST = [(GDP_THIRD, "2026-04-09"), ("Personal Income and Outlays, February 2026", "2026-04-09")]


def moved_to_the_sample_tuesday(releases):
    return calendar(*[restart(block_of(title, day), "DTSTART:20260908T123000Z") for title, day in releases])


def context_arms(monkeypatch, releases):
    freeze(monkeypatch, NOW)
    raw, bea, _ = collected(NOW, {BEA_URL: moved_to_the_sample_tuesday(releases)})
    assert bea["status"] == "AVAILABLE" and len(bea_events(raw)) == len(releases)
    stub = collect.source("bea", "BEA calendar", "calendar", "https://www.bea.gov/news/schedule", NOW, "UNAVAILABLE",
                          STUB_REASON)

    def build(record, events):
        value = read_json(FIXTURES / "evidence.sample.json")
        value["sources"] = [record if s["id"] == "bea" else s for s in value["sources"]]
        value["events"] = [*value["events"], *events]
        value["cuttingboard"] = collect.cuttingboard_record(value["cuttingboard"], NOW, NOW)
        packet = finalize_coverage(derive(normalize_packet(value, NOW, "SAMPLE"),
                                          read_json(ROOT / "config/universe.json")))
        return packet, analyst_context(packet, edition_profile("PREMARKET"))
    return build(bea, bea_events(raw)), build(stub, [])


@pytest.mark.parametrize("name, releases, bound", [
    ("one release", ONE, PERMIT_BOUND),
    ("two releases", TWO, MEASURED_CEILING),
    ("the longest two", TWO_LONGEST, MEASURED_CEILING),
])
def test_what_bea_adds_to_the_same_fixtures_context_is_measured_and_bounded(monkeypatch, capsys, name, releases, bound):
    (with_bea, context), (base, base_context) = context_arms(monkeypatch, releases)
    assert [e["source_id"] for e in context["events"]].count("bea") == len(releases)
    assert [e["source_id"] for e in base_context["events"]].count("bea") == 0
    size, base_size = (len(compact_json(c).encode()) for c in (context, base_context))
    request, base_request = (len(construct_prompt(p, context=c)[1].encode())
                             for p, c in ((with_bea, context), (base, base_context)))
    with capsys.disabled():
        print(f"\nA4 {name}: context {size} B vs {base_size} B base = {size - base_size:+d} B "
              f"(the permit's bound {PERMIT_BOUND} B); request {request} B vs {base_request} B = "
              f"{request - base_request:+d} B")
    assert size - base_size == request - base_request  # the request carries nothing of BEA beyond the context
    assert size - base_size <= bound
    assert max(size, base_size, request, base_request) <= 64_000


def test_a_release_whose_time_has_passed_joins_the_curves_note_exactly_as_a_bls_release_does(monkeypatch):
    (with_bea, context), (base, base_context) = context_arms(monkeypatch, TWO)
    assert "release_note" not in base_context["curve"]
    note = context["curve"]["release_note"]
    assert note.startswith("Curve predates the ") and note.endswith(" releases.")
    assert f"{GDP_ADVANCE} and {PIO_DECEMBER} releases" in note  # two releases on one clock are named once
    assert not re.search(r"consensus|forecast|expected|actual|estimate of|rose|fell", note.replace("Estimate", ""))
    assert "Curve predates" in render(with_bea, narrative())[1]  # and the page says the same, in the same place


# --- The request identity, the allowlist and the budget ---------------------------------------------------------------

def test_only_the_one_bea_url_is_allowed_and_no_other_host_gained_anything():
    assert collect.BEA == BEA_URL and collect.allowed(BEA_URL)
    for url in ("http://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics",
                "https://www.bea.gov/news/schedule",
                "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics?x=1",
                "https://www.bea.gov/news/schedule/ics/other.ics",
                "https://www.bea.gov/news/2026/gross-domestic-product-advance-estimate-4th-quarter-and-year-2025",
                "https://apps.bea.gov/iTable/",
                "https://bea.gov/news/schedule/ics/online-calendar-subscription.ics"):
        assert not collect.allowed(url), url
    assert collect.allowed(collect.BLS) and collect.allowed(collect.FED) and not collect.allowed("https://example.com/")


def test_the_bls_owner_contact_never_reaches_bea(monkeypatch):
    monkeypatch.setenv("BLS_CONTACT", "owner@example.com")
    assert collect.user_agent(BEA_URL) == collect.USER_AGENT
    assert "owner@example.com" in collect.user_agent(collect.BLS)


def test_bea_has_its_own_slice_of_the_deadline_and_cannot_starve_the_sources_after_it():
    deadlines = {}

    def fetcher(url, deadline):
        deadlines[url] = deadline
        raise SourceError("HTTP 503")
    start = time.monotonic()
    collect_live(morning("2026-02-20"), fetcher=fetcher)
    assert deadlines[BEA_URL] - start <= collect.BEA_SECONDS + 1
    assert deadlines[collect.NVIDIA_IR] - deadlines[BEA_URL] > 60  # a slow BEA cannot starve what is collected after it


def test_the_collectors_deadline_wins_when_it_is_shorter():
    seen = []
    collect.bea_calendar(morning("2026-02-20"), time.monotonic() + 3,
                         lambda url, deadline: seen.append(deadline) or BEA_ICS)
    assert seen[0] <= time.monotonic() + 3


# --- Scope: nothing but a calendar ------------------------------------------------------------------------------------

def test_bea_is_wired_as_scheduled_releases_only_and_has_no_value_reader():
    config = read_json(ROOT / "config/sources.json")
    assert "BEA calendar" not in config["deferred"]
    (entry,) = [item for item in config["wired"] if "BEA" in item]
    assert "schedule" in entry.lower() and "no value" in entry.lower()
    assert "bea" not in {family.lower() for family in FAMILIES}
    assert "bea" not in json.dumps(read_json(ROOT / "config/economic_actuals.json")).lower()
    prompt = (ROOT / "prompts/synthesis.md").read_text()
    assert "BEA" not in prompt and len(prompt.encode()) < 11_000


def test_the_bls_reader_is_unchanged_by_the_shared_parser_taking_a_source():
    now = datetime.fromisoformat("2026-09-11T13:00:00+00:00")
    default = calendar_events(BLS_ICS, now, now)
    assert default == calendar_events(BLS_ICS, now, now, "bls", "bls-event")
    assert [(e["id"], e["source_id"], e["title"]) for e in default] == [
        ("bls-event-0", "bls", "Consumer Price Index"), ("bls-event-1", "bls", "Real Earnings")]

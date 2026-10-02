"""The BLS release calendar: the official ICS file, else the official monthly List View pages.

The fixtures are trimmed copies of the real pages retrieved on 2026-10-02, kept line for line (LF endings). The ICS
names its own time zone `US-Eastern`, which is not an IANA key, and every List View page carries BLS's site-wide
script that removes withdrawn rows in the browser. Production received HTTP 403 on every BLS request because the
anonymous client carried no owner contact; BLS_CONTACT now identifies BLS requests (and only those).
"""

import io
import json
import re
import time
from datetime import datetime
from email.message import Message
from urllib.error import HTTPError
from urllib.request import HTTPSHandler
from urllib.request import build_opener as real_build_opener
from urllib.response import addinfourl

import pytest
from test_pipeline import NOW, narrative
from test_provenance import drawer

from market_brief import collect
from market_brief.collect import (
    BLS,
    FED,
    TREASURY,
    SourceError,
    calendar_events,
    collect_live,
    cuttingboard_record,
    fetch,
)
from market_brief.evidence import ROOT, finalize_coverage, normalize_packet, read_json
from market_brief.metrics import derive
from market_brief.render import render
from market_brief.synthesize import validate_narrative

FIXTURES = ROOT / "tests/fixtures"
ICS = (FIXTURES / "bls.2026-09-11.ics").read_text()
PAGES = {month: (FIXTURES / f"bls-list.2026-{month:02d}.htm").read_text() for month in (9, 10, 11, 12)}
LIST = {month: f"https://www.bls.gov/schedule/2026/{month:02d}_sched_list.htm" for month in (9, 10, 11, 12)}
FORBIDDEN = SourceError("HTTP 403")
NFP_MORNING = "2026-10-02T12:10:00+00:00"  # 8:10 AM ET Friday, October 2, 2026: production showed BLS unavailable
NFP_ROW = ('<tr class="release-list-even-row">\n<td class="date-cell"><p>Friday, October 2, 2026</p></td>\n'
           '<td class="time-cell"><p>08:30 AM</p></td>\n'
           '<td class="desc-cell"><p><strong>Employment Situation</strong> for September 2026</p></td></tr>\n')


@pytest.fixture(autouse=True)
def no_alpaca(monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)


def utc(text):
    return datetime.fromisoformat(text)


def collected(now, responses):
    """The live collector, offline: `responses` maps a URL to its body or to the error fetching it raises, and every
    other URL (Treasury, the Fed) is offline. Returns the raw packet, the BLS source record and the BLS requests."""
    calls = []

    def fetcher(url, deadline):
        calls.append(url)
        result = responses.get(url, SourceError("offline"))
        if isinstance(result, Exception):
            raise result
        return result
    raw = collect_live(now, fetcher=fetcher)
    bls = next(source for source in raw["sources"] if source["id"] == "bls")
    return raw, bls, [url for url in calls if "bls.gov" in url]


def releases(raw):
    return [(event["title"], event["scheduled_at"]) for event in raw["events"]]


# --- The ICS file ---------------------------------------------------------------------------------------------------

def test_ics_success_is_unchanged_and_requests_no_fallback():
    raw, bls, calls = collected(utc(NFP_MORNING), {BLS: ICS})
    assert calls == [BLS]
    assert (bls["status"], bls["url"], bls["reason"], bls["coverage_date"]) == ("AVAILABLE", BLS, "", "2026-10-02")
    assert raw["events"] == [dict(id="bls-event-0", title="Employment Situation", source_id="bls",
                                  published_at=None, checked_at=bls["retrieved_at"],
                                  scheduled_at="2026-10-02T12:30:00+00:00", status="SCHEDULED")]


def test_bls_us_eastern_zone_is_new_york_time():
    events = calendar_events(ICS, utc("2026-09-11T13:00:00+00:00"), utc("2026-09-11T13:00:00+00:00"))
    assert [(event["title"], event["scheduled_at"]) for event in events] == [
        ("Consumer Price Index", "2026-09-11T12:30:00+00:00"), ("Real Earnings", "2026-09-11T12:30:00+00:00")]


def test_an_unknown_ics_zone_falls_back_instead_of_crashing_collection():
    unknown = ICS.replace("TZID=US-Eastern:20261002", "TZID=Mars/Olympus_Mons:20261002")
    raw, bls, calls = collected(utc(NFP_MORNING), {BLS: unknown, LIST[10]: PAGES[10]})
    assert calls == [BLS, LIST[10]]
    assert bls["status"] == "AVAILABLE" and "time zone" in bls["reason"]
    assert releases(raw) == [("Employment Situation for September 2026", "2026-10-02T12:30:00+00:00")]


# --- The official monthly List View fallback ------------------------------------------------------------------------

def test_ics_403_recovers_the_employment_situation_from_the_official_list_page():
    raw, bls, calls = collected(utc(NFP_MORNING), {BLS: FORBIDDEN, LIST[10]: PAGES[10]})
    assert calls == [BLS, LIST[10]]
    assert (bls["status"], bls["url"], bls["coverage_date"]) == ("AVAILABLE", LIST[10], "2026-10-02")
    assert bls["reason"] == "monthly schedule 2026-10; calendar file: HTTP 403"
    assert raw["events"] == [dict(id="bls-event-0", title="Employment Situation for September 2026", source_id="bls",
                                  published_at=None, checked_at=bls["retrieved_at"],
                                  scheduled_at="2026-10-02T12:30:00+00:00", status="SCHEDULED")]


@pytest.mark.parametrize("now,responses,expected", [
    # 9:00 AM ET Friday, September 11, 2026: the CPI morning issue #23 recorded.
    ("2026-09-11T13:00:00+00:00", {BLS: FORBIDDEN, LIST[9]: PAGES[9]},
     [("Consumer Price Index for August 2026", "2026-09-11T12:30:00+00:00"),
      ("Real Earnings for August 2026", "2026-09-11T12:30:00+00:00")]),
    ("2026-10-14T12:00:00+00:00", {BLS: FORBIDDEN, LIST[10]: PAGES[10]},
     [("Consumer Price Index for September 2026", "2026-10-14T12:30:00+00:00"),
      ("Real Earnings for September 2026", "2026-10-14T12:30:00+00:00")]),
    ("2026-10-14T12:00:00+00:00", {BLS: ICS},
     [("Consumer Price Index", "2026-10-14T12:30:00+00:00"), ("Real Earnings", "2026-10-14T12:30:00+00:00")]),
])
def test_cpi_day_emits_cpi_at_0830_eastern(now, responses, expected):
    raw, bls, _ = collected(utc(now), responses)
    assert bls["status"] == "AVAILABLE"
    assert releases(raw) == expected


def test_a_cross_month_next_session_reads_both_monthly_pages():
    # 4:30 PM ET Wednesday, September 30: the next session is Thursday, October 1. The real October page lists
    # nothing on the 1st, so this copy moves the Employment Situation there to prove the second page is admitted.
    october_first = PAGES[10].replace("Friday, October 2, 2026", "Thursday, October 1, 2026")
    raw, bls, calls = collected(utc("2026-09-30T20:30:00+00:00"),
                                {BLS: FORBIDDEN, LIST[9]: PAGES[9], LIST[10]: october_first})
    assert calls == [BLS, LIST[9], LIST[10]]
    assert (bls["status"], bls["url"], bls["coverage_date"]) == ("AVAILABLE", LIST[9], "2026-09-30")
    assert bls["reason"] == "monthly schedule 2026-09, 2026-10; calendar file: HTTP 403"
    assert [(event["id"], *pair) for event, pair in zip(raw["events"], releases(raw))] == [
        ("bls-event-0", "Metropolitan Area Employment and Unemployment (Monthly) for August 2026",
         "2026-09-30T14:00:00+00:00"),
        ("bls-event-1", "Employment Situation for September 2026", "2026-10-01T12:30:00+00:00")]


def test_releases_after_the_next_session_are_not_admitted():
    raw, _, _ = collected(utc("2026-09-30T20:30:00+00:00"), {BLS: FORBIDDEN, LIST[9]: PAGES[9], LIST[10]: PAGES[10]})
    assert releases(raw) == [("Metropolitan Area Employment and Unemployment (Monthly) for August 2026",
                              "2026-09-30T14:00:00+00:00")]


def test_a_month_end_weekend_crosses_into_eastern_standard_time():
    # 4:30 PM ET Friday, October 30: the next session is Monday, November 2, after the clocks go back on the 1st.
    raw, bls, calls = collected(utc("2026-10-30T20:30:00+00:00"),
                                {BLS: FORBIDDEN, LIST[10]: PAGES[10], LIST[11]: PAGES[11]})
    assert calls == [BLS, LIST[10], LIST[11]] and bls["status"] == "AVAILABLE"
    assert releases(raw) == [("Employment Cost Index for Third Quarter 2026", "2026-10-30T12:30:00+00:00")]


@pytest.mark.parametrize("responses,expected", [
    ({BLS: FORBIDDEN, LIST[11]: PAGES[11]},
     [("Productivity and Costs (P) for Third Quarter 2026", "2026-11-05T13:30:00+00:00"),
      ("Employment Situation for October 2026", "2026-11-06T13:30:00+00:00")]),
    ({BLS: ICS},
     [("Productivity and Costs", "2026-11-05T13:30:00+00:00"),
      ("Employment Situation", "2026-11-06T13:30:00+00:00")]),
])
def test_release_times_follow_new_york_standard_time(responses, expected):
    # 4:30 PM ET Thursday, November 5: 8:30 AM EST is 13:30 UTC, an hour later than in October.
    raw, _, _ = collected(utc("2026-11-05T21:30:00+00:00"), responses)
    assert releases(raw) == expected


def test_the_year_boundary_requests_next_years_page_and_fails_closed_without_it():
    # 4:30 PM ET Thursday, December 31: the next session is Monday, January 4, 2027, whose page is not published.
    raw, bls, calls = collected(utc("2026-12-31T21:30:00+00:00"), {BLS: FORBIDDEN, LIST[12]: PAGES[12]})
    assert calls == [BLS, LIST[12], "https://www.bls.gov/schedule/2027/01_sched_list.htm"]
    assert bls["status"] == "UNAVAILABLE" and bls["reason"] == "HTTP 403; monthly schedule: offline"
    assert not raw["events"]


def test_an_unreadable_next_session_page_fails_closed():
    raw, bls, _ = collected(utc("2026-09-30T20:30:00+00:00"),
                            {BLS: FORBIDDEN, LIST[9]: PAGES[9], LIST[10]: SourceError("HTTP 404")})
    assert (bls["status"], bls["reason"]) == ("UNAVAILABLE", "HTTP 403; monthly schedule: HTTP 404")
    assert not raw["events"]


def test_a_holiday_row_is_not_a_release():
    # Monday, October 12 (Columbus Day): the exchange trades, BLS publishes nothing, and its page lists the holiday
    # without a time.
    raw, bls, _ = collected(utc("2026-10-12T12:00:00+00:00"), {BLS: FORBIDDEN, LIST[10]: PAGES[10]})
    assert (bls["status"], bls["coverage_date"]) == ("AVAILABLE", "2026-10-12")
    assert raw["events"] == []


def test_a_forbidden_list_page_stays_visibly_unavailable():
    now = utc(NFP_MORNING)
    raw, bls, calls = collected(now, {BLS: FORBIDDEN, LIST[10]: FORBIDDEN})
    assert calls == [BLS, LIST[10]]
    assert (bls["status"], bls["url"], bls["reason"]) == ("UNAVAILABLE", BLS, "HTTP 403; monthly schedule: HTTP 403")
    assert not raw["events"]
    packet = finalize_coverage(normalize_packet(raw, now, "LIVE"))
    assert "BLS calendar: HTTP 403; monthly schedule: HTTP 403" in packet["coverage"]["limitations"]


MALFORMED = {
    "no release table": lambda: PAGES[10].replace('class="release-list"', 'class="release-grid"'),
    "renamed column": lambda: PAGES[10].replace("<p>Release</p>", "<p>Title</p>"),
    "time without meridiem": lambda: PAGES[10].replace("08:30 AM", "08:30", 1),
    "missing cell": lambda: PAGES[10].replace('<td class="time-cell"><p>08:30 AM</p></td>\n', "", 1),
    "empty title": lambda: PAGES[10].replace("<strong>Employment Situation</strong> for September 2026", ""),
    "weekday disagrees": lambda: PAGES[10].replace("Friday, October 2, 2026", "Monday, October 2, 2026"),
    "impossible date": lambda: PAGES[10].replace("Friday, October 30, 2026", "Saturday, October 31, 2026")
                                        .replace("Thursday, October 29, 2026", "Thursday, October 32, 2026"),
    "prior-month row": lambda: PAGES[10].replace("Friday, October 30, 2026", "Wednesday, September 30, 2026"),
    "another month's heading": lambda: PAGES[10].replace(">October 2026</h1>", ">September 2026</h1>"),
    "September at October's address": lambda: PAGES[9],
    "truncated mid-table": lambda: PAGES[10][:PAGES[10].index("Consumer Price Index")],
    "no time basis": lambda: PAGES[10].replace("All times on calendar are Eastern Time.", ""),
    "no rows": lambda: re.sub(r"<tbody>.*</tbody>", "<tbody>\n</tbody>", PAGES[10], flags=re.S),
    "two release tables": lambda: PAGES[10].replace("</table>\n<p> </p>", "</table>\n" + PAGES[10][
        PAGES[10].index('<table class="release-list">'):PAGES[10].index("</tbody>")] + "</tbody>\n</table>\n<p> </p>"),
    "unreadable removal": lambda: PAGES[10].replace("'Thursday, February 5, 2026'", "'feb5'"),
    "removal in another form": lambda: PAGES[10].replace("Object.entries({'Thursday", "Object.entries( {'Thursday"),
    # A browser would still show every later row in these three; the parser must not quietly lose them.
    "stray table close": lambda: PAGES[10].replace(NFP_ROW, NFP_ROW + "</table>\n"),
    "comment over the rest": lambda: PAGES[10].replace(NFP_ROW, NFP_ROW + "<!--\n").replace(
        "Last Modified Date: </strong>February 18, 2026\n", "Last Modified Date: </strong>February 18, 2026\n-->\n"),
    "list split in two": lambda: PAGES[10].replace(
        NFP_ROW, NFP_ROW + '</tbody>\n</table>\n<table class="later">\n<tbody>\n'),
    "not a page": lambda: "Access Denied",
}


@pytest.mark.parametrize("damage", MALFORMED.values(), ids=MALFORMED.keys())
def test_a_malformed_list_page_fails_closed(damage):
    page = damage()
    raw, bls, _ = collected(utc(NFP_MORNING), {BLS: FORBIDDEN, LIST[10]: page})
    assert bls["status"] == "UNAVAILABLE" and bls["reason"].startswith("HTTP 403; monthly schedule: ")
    assert not raw["events"]


def test_the_list_page_reads_through_crlf_line_endings():
    raw, bls, _ = collected(utc(NFP_MORNING), {BLS: FORBIDDEN, LIST[10]: PAGES[10].replace("\n", "\r\n")})
    assert bls["status"] == "AVAILABLE"
    assert releases(raw) == [("Employment Situation for September 2026", "2026-10-02T12:30:00+00:00")]


@pytest.mark.parametrize("entry", ["'Friday, October 2, 2026': 'Employment Situation'",
                                   "'Friday, October 02, 2026': 'Employment\\x20Situation'"])
def test_a_page_that_withdraws_a_release_on_a_target_date_fails_closed(entry):
    # BLS withdraws a listed release in the browser with its site-wide script (it did so for three February 2026
    # releases). What a reader then sees on a target date depends on script semantics, so the page is not read.
    withdrawn = PAGES[10].replace("'Thursday, February 5, 2026': 'Productivity and Costs (P)'", entry)
    raw, bls, _ = collected(utc(NFP_MORNING), {BLS: FORBIDDEN, LIST[10]: withdrawn})
    assert bls["status"] == "UNAVAILABLE" and "withdraws a release on 2026-10-02" in bls["reason"]
    assert not raw["events"]
    # The same entry on another date is not this run's concern: October 14 reads normally.
    raw, bls, _ = collected(utc("2026-10-14T12:00:00+00:00"), {BLS: FORBIDDEN, LIST[10]: withdrawn})
    assert bls["status"] == "AVAILABLE" and len(raw["events"]) == 2


def test_one_event_per_release():
    assert PAGES[10].count(NFP_ROW) == 1
    raw, _, calls = collected(utc(NFP_MORNING), {BLS: FORBIDDEN, LIST[10]: PAGES[10].replace(NFP_ROW, NFP_ROW * 2)})
    assert calls == [BLS, LIST[10]]  # today and the next session share October: one request
    assert [(event["id"], event["title"]) for event in raw["events"]] == [
        ("bls-event-0", "Employment Situation for September 2026")]
    block = ICS[ICS.index("BEGIN:VEVENT", ICS.index("20261002T083000") - 200):]
    block = block[:block.index("END:VEVENT") + len("END:VEVENT\n")]
    raw, _, _ = collected(utc(NFP_MORNING), {BLS: ICS.replace(block, block * 2)})
    assert releases(raw) == [("Employment Situation", "2026-10-02T12:30:00+00:00")]


def test_a_release_keeps_its_id_whichever_path_read_it():
    # CPI day: the calendar file lists Real Earnings before CPI, the List View the reverse. Both number by time, then
    # release name, so a frozen bls-event-<n> names the same release on a later refresh that read the other path.
    now = utc("2026-10-14T12:00:00+00:00")
    from_file, _, _ = collected(now, {BLS: ICS})
    from_page, _, _ = collected(now, {BLS: FORBIDDEN, LIST[10]: PAGES[10]})
    assert [(event["id"], event["title"]) for event in from_file["events"]] == [
        ("bls-event-0", "Consumer Price Index"), ("bls-event-1", "Real Earnings")]
    assert [(event["id"], event["title"]) for event in from_page["events"]] == [
        ("bls-event-0", "Consumer Price Index for September 2026"), ("bls-event-1", "Real Earnings for September 2026")]


def test_a_watch_can_be_timed_to_a_collected_release():
    # Collected IDs carry a digit (bls-event-0); a structured EVENT(<id>) horizon is a reference, not a numeric claim.
    raw = read_json(ROOT / "tests/fixtures/evidence.sample.json")
    raw["events"][0]["id"] = "bls-event-0"
    raw["cuttingboard"] = cuttingboard_record(raw["cuttingboard"], NOW, NOW)
    packet = finalize_coverage(derive(normalize_packet(raw, NOW, "SAMPLE"), read_json(ROOT / "config/universe.json")))
    value = json.loads(json.dumps(narrative()).replace("sample-event", "bls-event-0"))
    value["watches"][0]["horizon"] = "EVENT(bls-event-0)"
    assert validate_narrative(value, packet)
    value["watches"][0]["horizon"] = "EVENT(bls-event-9)"
    with pytest.raises(ValueError, match="unknown event horizon"):
        validate_narrative(value, packet)
    value["watches"][0]["horizon"] = "OPENING_HOUR"
    value["watches"][0]["condition"] = "Whether SPY holds 8 handles"
    with pytest.raises(ValueError, match="literal numeric claim"):
        validate_narrative(value, packet)


def test_the_fallback_shares_the_calendar_files_time_budget():
    deadlines = {}

    def fetcher(url, deadline):
        deadlines[url] = deadline
        raise SourceError("HTTP 403")
    collect_live(utc(NFP_MORNING), fetcher=fetcher)
    assert deadlines[BLS] == deadlines[LIST[10]]
    assert deadlines[FED] - deadlines[BLS] > 60  # a slow BLS cannot starve the sources collected after it


# --- The page -------------------------------------------------------------------------------------------------------

def test_a_recovered_calendar_reads_available_on_the_page():
    _, bls, _ = collected(NOW, {BLS: FORBIDDEN, LIST[9]: PAGES[9]})
    raw = read_json(ROOT / "tests/fixtures/evidence.sample.json")
    raw["sources"] = [bls if source["id"] == "bls" else source for source in raw["sources"]]
    raw["cuttingboard"] = cuttingboard_record(raw["cuttingboard"], NOW, NOW)
    packet = finalize_coverage(derive(normalize_packet(raw, NOW, "SAMPLE"), read_json(ROOT / "config/universe.json")))
    md, page = render(packet, narrative())
    assert "BLS calendar: HTTP 403" not in page and "BLS calendar: HTTP 403" not in md
    assert not any("BLS calendar" in limitation for limitation in packet["coverage"]["limitations"])
    sources = drawer(page, "Sources ·")
    row = re.search(r"<li><span class=\"source-name\"><a href=\"([^\"]+)\">BLS calendar</a>.*?</li>", sources).group(0)
    assert LIST[9] in row and "Available" in row and "unavailable" not in row and "HTTP 403" not in row
    technical = drawer(page, "Technical details")
    assert "bls AVAILABLE" in technical and "(monthly schedule 2026-09; calendar file: HTTP 403)" in technical


# --- The request identity and the allowlist -------------------------------------------------------------------------

class Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, limit):
        return self.body[:limit]


def network(monkeypatch, bodies):
    """Replace the network under `fetch`: each URL answers with its body, every other URL with BLS's 403."""
    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            if request.full_url not in bodies:
                raise HTTPError(request.full_url, 403, "Forbidden", {}, None)
            return Response(bodies[request.full_url].encode())
    monkeypatch.setattr(collect, "build_opener", lambda *handlers: Opener())
    return requests


def test_bls_requests_carry_the_owner_contact_and_no_other_host_sees_it(monkeypatch):
    monkeypatch.setenv("BLS_CONTACT", " owner@example.org\n")  # secrets often carry a trailing newline
    requests = network(monkeypatch, {BLS: ICS, LIST[10]: PAGES[10]})
    assert fetch(BLS, time.monotonic() + 30) == ICS and fetch(LIST[10], time.monotonic() + 30) == PAGES[10]
    with pytest.raises(SourceError):
        fetch(FED, time.monotonic() + 30)
    assert [(request.full_url, request.get_header("User-agent")) for request in requests] == [
        (BLS, "MarketBrief/0.1 (owner@example.org)"), (LIST[10], "MarketBrief/0.1 (owner@example.org)"),
        (FED, "MarketBrief/0.1 personal research")]


def test_without_a_contact_bls_requests_stay_anonymous(monkeypatch):
    requests = network(monkeypatch, {BLS: ICS})
    fetch(BLS, time.monotonic() + 30)
    assert requests[0].get_header("User-agent") == "MarketBrief/0.1 personal research"


@pytest.mark.parametrize("contact", ["owner at example.org", "https://example.org/owner", "owner@example.org (desk)",
                                     "owner@example.org\r\nX-Injected: 1", "owner@localhost"])
def test_a_malformed_contact_fails_closed_without_echoing_it(monkeypatch, contact):
    monkeypatch.setenv("BLS_CONTACT", contact)
    requests = network(monkeypatch, {BLS: ICS})
    with pytest.raises(SourceError) as failure:
        fetch(BLS, time.monotonic() + 30)
    assert not requests and "example.org" not in str(failure.value) and "localhost" not in str(failure.value)


def test_the_contact_never_reaches_the_record(monkeypatch):
    monkeypatch.setenv("BLS_CONTACT", "owner@example.org")
    network(monkeypatch, {BLS: ICS})
    raw = collect_live(utc(NFP_MORNING))
    assert next(source for source in raw["sources"] if source["id"] == "bls")["status"] == "AVAILABLE"
    assert "example.org" not in json.dumps(raw)


class Scripted(HTTPSHandler):
    """HTTPS answered from a script, URL -> (status, Location or None, body), so the real redirect chain runs."""

    def __init__(self, script, requests):
        super().__init__()
        self.script, self.requests = script, requests

    def https_open(self, request):
        self.requests.append((request.full_url, request.get_header("User-agent")))
        status, location, body = self.script[request.full_url]
        headers = Message()
        if location:
            headers["Location"] = location
        response = addinfourl(io.BytesIO(body.encode()), headers, request.full_url, status)
        response.msg = "Found" if location else "OK"
        return response


@pytest.mark.parametrize("start,location,outcome", [
    (BLS, "https://evil.example/calendar.ics", "cross-host redirect rejected"),
    (BLS, "https://www.bls.gov/cpi/", "redirect outside allowlist"),
    (LIST[10], "https://www.bls.gov/schedule/2026/10_sched.htm", "redirect outside allowlist"),
])
def test_a_bls_redirect_off_the_two_schedule_forms_is_refused_before_it_is_followed(monkeypatch, start, location,
                                                                                    outcome):
    monkeypatch.setenv("BLS_CONTACT", "owner@example.org")
    requests = []
    monkeypatch.setattr(collect, "build_opener", lambda *handlers: real_build_opener(
        *handlers, Scripted({start: (302, location, "")}, requests)))
    with pytest.raises(SourceError, match=outcome):
        fetch(start, time.monotonic() + 30)
    assert requests == [(start, "MarketBrief/0.1 (owner@example.org)")]


def test_redirects_inside_the_allowlist_are_followed_with_the_same_identity(monkeypatch):
    monkeypatch.setenv("BLS_CONTACT", "owner@example.org")
    moved = f"{TREASURY}?moved"
    requests = []
    monkeypatch.setattr(collect, "build_opener", lambda *handlers: real_build_opener(*handlers, Scripted({
        BLS: (301, LIST[10], ""), LIST[10]: (200, None, PAGES[10]),
        TREASURY: (302, moved, ""), moved: (200, None, "<feed/>")}, requests)))
    assert fetch(BLS, time.monotonic() + 30) == PAGES[10]
    assert fetch(TREASURY, time.monotonic() + 30) == "<feed/>"
    assert requests == [(BLS, "MarketBrief/0.1 (owner@example.org)"), (LIST[10], "MarketBrief/0.1 (owner@example.org)"),
                        (TREASURY, "MarketBrief/0.1 personal research"), (moved, "MarketBrief/0.1 personal research")]


@pytest.mark.parametrize("url", [
    "https://www.bls.gov/cpi/", "https://www.bls.gov/schedule/2026/10_sched.htm",
    "https://www.bls.gov/schedule/٢٠٢٦/10_sched_list.htm",
    "https://www.bls.gov/schedule/2026/13_sched_list.htm", "https://www.bls.gov/schedule/2026/10_sched_list.htm?x=1",
    "https://www.bls.gov/schedule/news_release/bls.ics#x", "http://www.bls.gov/schedule/2026/10_sched_list.htm",
    "https://data.bls.gov/schedule/2026/10_sched_list.htm"])
def test_bls_fetches_are_limited_to_the_two_official_schedule_forms(monkeypatch, url):
    requests = network(monkeypatch, {})
    with pytest.raises(SourceError, match="allowlist"):
        fetch(url, time.monotonic() + 30)
    assert not requests

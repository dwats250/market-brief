"""BLS release actuals: the official values of the Employment Situation and the CPI, read from BLS's fixed
current-edition pages once the admitted calendar says the release has happened, and shown on the page whatever the
analyst does.

The fixtures are the real pages retrieved on 2026-10-02 (LF endings, trailing spaces removed): the Employment Situation
for September 2026, released 8:30 AM ET Friday, October 2, 2026, and the CPI for August 2026, released 8:30 AM ET
Friday, September 11, 2026 (still the current CPI edition then). `bls-empsit.2026-09-04.txt` is the release text of the
previous Employment Situation (August 2026), the first <pre> of BLS's archived copy of that release.
"""

import http.client
import json
import re
import time
from datetime import datetime
from urllib.request import build_opener as real_build_opener

import pytest
from test_bls_calendar import FORBIDDEN, ICS, LIST, PAGES, Scripted, collected, network, releases, utc
from test_cadence import Day, interpretation_fragments
from test_continuity import PREMARKET_TUE, run_packet
from test_contract import edition_response
from test_light_context import OPEN_30M_TUE, light_edition
from test_pipeline import fixture_packet
from test_provenance import drawer
from test_render import clock_lines

from market_brief import collect
from market_brief.actuals import (
    CONSUMER_PRICE_INDEX,
    EMPLOYMENT_SITUATION,
    FAMILIES,
    ReleaseError,
    read_release,
    release_rows,
    release_text,
)
from market_brief.collect import BLS, RELEASE_SECONDS, SourceError, fetch, release_actuals
from market_brief.context import analyst_context, edition_profile, supplied_ids
from market_brief.evidence import (
    ET,
    ROOT,
    evidence_catalog,
    finalize_coverage,
    metric_identity,
    normalize_observation,
    normalize_packet,
    read_json,
)
from market_brief.metrics import annotate_magnitude, derive
from market_brief.render import formatted, release_clock, render
from market_brief.synthesize import construct_prompt, validate_narrative

FIXTURES = ROOT / "tests/fixtures"
UNREAD = "payroll revisions not read cleanly"
EMPSIT_PAGE = (FIXTURES / "bls-empsit.2026-10-02.htm").read_text()
CPI_PAGE = (FIXTURES / "bls-cpi.2026-09-11.htm").read_text()
AUGUST_TEXT = (FIXTURES / "bls-empsit.2026-09-04.txt").read_text()


def damaged(page, old, new, count=1):
    """The page with `old` replaced by `new` exactly `count` times; the test fails if the text it edits is absent."""
    assert page.count(old) == count, old
    return page.replace(old, new)


# --- The Employment Situation ---------------------------------------------------------------------------------------

def test_the_september_employment_situation_reads_every_required_value_and_both_revisions():
    release = read_release("empsit", EMPSIT_PAGE)
    assert release["title"] == "Employment Situation" and release["period"] == "2026-09"
    assert release["released_at"] == datetime(2026, 10, 2, 8, 30, tzinfo=ET)
    assert release["values"] == dict(payrolls=29, unemployment=4.2, earnings_mm=0.1, earnings_yy=3.0)
    assert release["revisions"] == [dict(month="2026-07", previous=21, revised=-10, change=-31),
                                    dict(month="2026-08", previous=162, revised=133, change=-29)]
    assert release["combined"] == -60 and release["note"] == ""


def test_the_august_release_text_reads_through_its_own_wording():
    # "increased by" and "rose by", "was unchanged at", "Over the year", and both months revised up.
    release = release_text("empsit", AUGUST_TEXT)
    assert release["period"] == "2026-08" and release["released_at"] == datetime(2026, 9, 4, 8, 30, tzinfo=ET)
    assert release["values"] == dict(payrolls=162, unemployment=4.1, earnings_mm=0.3, earnings_yy=3.1)
    assert release["revisions"] == [dict(month="2026-06", previous=20, revised=31, change=11),
                                    dict(month="2026-07", previous=-23, revised=21, change=44)]
    assert release["combined"] == 55


def test_consecutive_releases_agree_on_the_estimates_the_later_one_revises():
    august, september = release_text("empsit", AUGUST_TEXT), read_release("empsit", EMPSIT_PAGE)
    previous = {revision["month"]: revision["previous"] for revision in september["revisions"]}
    assert previous["2026-07"] == august["revisions"][-1]["revised"] == 21
    assert previous["2026-08"] == august["values"]["payrolls"] == 162


def test_crlf_pages_read_the_same():
    assert read_release("empsit", EMPSIT_PAGE.replace("\n", "\r\n")) == read_release("empsit", EMPSIT_PAGE)
    assert read_release("cpi", CPI_PAGE.replace("\n", "\r\n")) == read_release("cpi", CPI_PAGE)


# Each damage: the reason the page must be refused for, and the edits that make it so.
EMPSIT_DAMAGE = {
    "no payroll statement": ("no nonfarm payroll statement", [
        ("Both nonfarm payroll employment (+29,000) and", "Both payroll measures and"),
        ("Total nonfarm payroll employment changed little in September (+29,000)",
         "Total nonfarm payroll employment changed little in September")]),
    "conflicting payroll statements": ("conflicting nonfarm payroll", [
        ("changed little in September (+29,000)", "changed little in September (+31,000)")]),
    "payrolls for another month": ("no nonfarm payroll statement", [
        ("Total nonfarm payroll employment changed little in September (+29,000)",
         "Total nonfarm payroll employment changed little in August (+29,000)"),
        ("(4.2 percent) changed little in\nSeptember", "(4.2 percent) changed little in\nAugust")]),
    "payrolls not in whole thousands": ("not in whole thousands", [
        ("(+29,000) and", "(+29,500) and"), ("September (+29,000)", "September (+29,500)")]),
    "no unemployment rate": ("no unemployment rate statement", [
        ("and the unemployment rate (4.2 percent)", "and the jobless measure"),
        ("Both the unemployment rate, at 4.2 percent, and", "Both the jobless measure and")]),
    "conflicting unemployment rates": ("conflicting unemployment rate", [
        ("Both the unemployment rate, at 4.2 percent,", "Both the unemployment rate, at 4.3 percent,")]),
    "an impossible unemployment rate": ("implausible unemployment rate", [
        ("the unemployment rate (4.2 percent)", "the unemployment rate (42.0 percent)"),
        ("the unemployment rate, at 4.2 percent,", "the unemployment rate, at 42.0 percent,")]),
    "no monthly earnings": ("no monthly earnings statement", [
        ("edged up by 5\ncents, or 0.1 percent, to $37.81", "moved")]),
    "an earnings verb outside the vocabulary": ("no monthly earnings statement", [
        ("payrolls edged up by 5\ncents", "payrolls zoomed by 5\ncents")]),
    "earnings cents that disagree with the percent": ("disagrees with its cents and dollar level", [
        ("edged up by 5\ncents, or 0.1 percent", "edged up by 50\ncents, or 0.1 percent")]),
    "no 12-month earnings": ("no 12-month earnings statement", [
        ("Over the past 12 months, average hourly earnings have increased\nby 3.0 percent.", "")]),
    "a heading month that disagrees with the title": ("title and its release text name different months", [
        ("SITUATION - SEPTEMBER 2026", "SITUATION - AUGUST 2026")]),
    "a second release heading": ("no single reference month", [
        ("THE EMPLOYMENT SITUATION - SEPTEMBER 2026",
         "THE EMPLOYMENT SITUATION - SEPTEMBER 2026 THE EMPLOYMENT SITUATION - SEPTEMBER 2026")]),
    "no embargo line": ("no single embargo line", [("embargoed until", "released")]),
    "a weekday that disagrees with the date": ("weekday does not match", [
        ("8:30 a.m. (ET) Friday, October 2, 2026", "8:30 a.m. (ET) Thursday, October 2, 2026")]),
    "no release number": ("no single embargo line and release number", [("USDL-26-1549", "")]),
    "another page's heading": ("not the Employment Situation release page", [
        ("<h1>Employment Situation Summary\n</h1>", "<h1>Employment Situation News Release\n</h1>")]),
    "a title for another month": ("title and its release text name different months", [
        (" - 2026 M09 Results </title>", " - 2026 M08 Results </title>")]),
    "a second news block": ("not one title, one heading and one release text", [
        ('<div class="normalnews">', '<div class="normalnews"></div><div class="normalnews">')]),
    "an unclosed release text": ("never closes", [("</pre>", "")]),
}


@pytest.mark.parametrize("damage", EMPSIT_DAMAGE)
def test_a_damaged_employment_situation_fails_closed_for_its_own_reason(damage):
    reason, edits = EMPSIT_DAMAGE[damage]
    page = EMPSIT_PAGE
    for old, new in edits:
        page = damaged(page, old, new)
    with pytest.raises(ReleaseError, match=re.escape(reason)):
        read_release("empsit", page)


@pytest.mark.parametrize("old,new", [
    # Revisions are stated by BLS and read only when they read cleanly; otherwise they are left out and say why.
    ("revised down by 31,000, from +21,000", "revised down by 30,000, from +21,000"),
    ("July and August combined is 60,000 lower", "July and August combined is 50,000 lower"),
    ("The change in total nonfarm payroll employment for July was revised down",
     "The change in total nonfarm payroll employment for June was revised down"),
    ("and the change for August was revised down by 29,000", "and the change for August was trimmed by 29,000"),
])
def test_revisions_that_do_not_read_cleanly_are_left_out_and_noted(old, new):
    release = read_release("empsit", damaged(EMPSIT_PAGE, old, new))
    assert release["values"] == dict(payrolls=29, unemployment=4.2, earnings_mm=0.1, earnings_yy=3.0)
    assert release["revisions"] == [] and release["combined"] is None
    assert release["note"] == UNREAD


def test_a_release_without_a_revisions_paragraph_reports_none():
    page = damaged(EMPSIT_PAGE, re.search(r"The change in total nonfarm payroll employment for July.*?since the last "
                                          r"published estimates and from the recalculation of seasonal factors\.\)",
                                          EMPSIT_PAGE, re.S).group(0), "")
    release = read_release("empsit", page)
    assert release["revisions"] == [] and release["combined"] is None and release["note"] == ""


# --- The Consumer Price Index ---------------------------------------------------------------------------------------

def test_the_august_cpi_reads_headline_and_core_from_table_a():
    release = read_release("cpi", CPI_PAGE)
    assert release["title"] == "Consumer Price Index" and release["period"] == "2026-08"
    assert release["released_at"] == datetime(2026, 9, 11, 8, 30, tzinfo=ET)
    assert release["values"] == dict(all_items_mm=0.4, all_items_yy=3.4, core_mm=0.3, core_yy=2.4)
    assert release["revisions"] == [] and release["combined"] is None and release["note"] == ""


CPI_AUGUST_ALL_ITEMS = ('<td headers="cpi_pressa.r.1 cpi_pressa.h.1.2 cpi_pressa.h.2.8">'
                        '<span class="datavalue">0.4</span></td>')
CPI_DAMAGE = {
    "no Table A": ("no single Table A", [('id="cpi_pressa"', 'id="cpi_pressx"')]),
    "a second Table A": ("no single Table A", [
        ('<table class="regular" id="cpi_pressa">',
         '<table class="regular" id="cpi_pressa"></table><table class="regular" id="cpi_pressa">')]),
    "a table caption for another index": ("not the CPI-U U.S. city average table", [
        ("CPI for All Urban Consumers (CPI-U): U.S. city average",
         "CPI for Urban Wage Earners (CPI-W): U.S. city average")]),
    "a latest column for another month": ("latest month is not the reference month", [
        ('h.2.8">Aug.<br />2026', 'h.2.8">Jul.<br />2026')]),
    "a 12-month column for another month": ("columns are not the expected ones", [
        ("ended<br />Aug. 2026", "ended<br />Jul. 2026")]),
    "a second all items row": ("repeats its All items row", [
        ('<p class="sub1">Food</p>', '<p class="sub1">All items</p>')]),
    "no core row": ("no complete All items less food and energy row", [
        ('<p class="sub1">All items less food and energy</p>', '<p class="sub1">Core</p>')]),
    "a value that is not a number": ("is not a number", [
        (CPI_AUGUST_ALL_ITEMS, CPI_AUGUST_ALL_ITEMS.replace(">0.4<", ">-<"))]),
    "an impossible value": ("implausible monthly CPI change", [
        (CPI_AUGUST_ALL_ITEMS, CPI_AUGUST_ALL_ITEMS.replace(">0.4<", ">45.0<"))]),
    "a heading month that disagrees with the title": ("title and its release text name different months", [
        ("CONSUMER PRICE INDEX - AUGUST 2026", "CONSUMER PRICE INDEX - JULY 2026")]),
    "an unclosed Table A": ("inside another element", [("</tfoot>\n</table>", "</tfoot>")]),
}


@pytest.mark.parametrize("damage", CPI_DAMAGE)
def test_a_damaged_cpi_page_fails_closed_for_its_own_reason(damage):
    reason, edits = CPI_DAMAGE[damage]
    page = CPI_PAGE
    for old, new in edits:
        page = damaged(page, old, new)
    with pytest.raises(ReleaseError, match=re.escape(reason)):
        read_release("cpi", page)


def test_each_page_reads_only_as_its_own_release():
    with pytest.raises(ReleaseError):
        read_release("cpi", EMPSIT_PAGE)
    with pytest.raises(ReleaseError):
        read_release("empsit", CPI_PAGE)


# --- Collection: today's admitted calendar is the only trigger ------------------------------------------------------

RELEASE_MORNING = "2026-10-02T13:31:00+00:00"  # 9:31 AM ET, the open +1M refresh: an hour after the 8:30 release
EMPSIT_ACTUALS = {"bls-empsit-payrolls": 29, "bls-empsit-unemployment-rate": 4.2, "bls-empsit-earnings-mm": 0.1,
                  "bls-empsit-earnings-yy": 3.0, "bls-empsit-revision-2026-07": -31, "bls-empsit-revision-2026-08": -29,
                  "bls-empsit-revision-combined": -60}
CPI_ACTUALS = {"bls-cpi-all-items-mm": 0.4, "bls-cpi-all-items-yy": 3.4, "bls-cpi-core-mm": 0.3, "bls-cpi-core-yy": 2.4}
NFP = ("Employment Situation", "2026-10-02T12:30:00+00:00")


@pytest.fixture(autouse=True)
def no_alpaca(monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)


def actuals(raw):
    return {row["id"]: row["value"] for row in raw["observations"] if row.get("frequency") == "release"}


def release_source(raw, ident="bls-empsit"):
    return next((source for source in raw["sources"] if source["id"] == ident), None)


def test_before_the_release_time_no_release_page_is_requested():
    raw, _, requests = collected(utc("2026-10-02T12:15:00+00:00"), {BLS: ICS, EMPLOYMENT_SITUATION: EMPSIT_PAGE})
    assert requests == [BLS]
    assert NFP in releases(raw) and actuals(raw) == {} and release_source(raw) is None


def test_after_the_release_time_the_official_page_is_read_and_admitted():
    raw, _, requests = collected(utc(RELEASE_MORNING), {BLS: ICS, EMPLOYMENT_SITUATION: EMPSIT_PAGE})
    assert requests == [BLS, EMPLOYMENT_SITUATION]
    assert actuals(raw) == EMPSIT_ACTUALS
    record = release_source(raw)
    assert {key: record[key] for key in ("name", "kind", "url", "status", "reason", "coverage_date")} == dict(
        name="BLS Employment Situation", kind="release", url=EMPLOYMENT_SITUATION, status="AVAILABLE", reason="",
        coverage_date="2026-10-02")
    rows = {row["id"]: row for row in raw["observations"] if row.get("frequency") == "release"}
    assert {(row["topic"], row["observed_at"], row["reference_period"], row["source_id"]) for row in rows.values()} == {
        ("Employment Situation", "2026-10-02T12:30:00+00:00", "2026-09", "bls-empsit")}
    revision = rows["bls-empsit-revision-2026-07"]
    assert (revision["revised_month"], revision["revised_from"], revision["revised_to"]) == ("2026-07", 21, -10)
    assert not {"revised_month", "revised_from", "revised_to"} & set(rows["bls-empsit-payrolls"])


def test_cpi_morning_reads_only_the_cpi_page():
    # Real Earnings is released at the same minute and is not one of the two families.
    raw, _, requests = collected(utc("2026-09-11T13:31:00+00:00"), {BLS: ICS, CONSUMER_PRICE_INDEX: CPI_PAGE})
    assert requests == [BLS, CONSUMER_PRICE_INDEX]
    assert actuals(raw) == CPI_ACTUALS and release_source(raw, "bls-cpi")["status"] == "AVAILABLE"


def test_the_list_view_reads_the_same_release():
    raw, _, requests = collected(utc(RELEASE_MORNING),
                                 {BLS: FORBIDDEN, LIST[10]: PAGES[10], EMPLOYMENT_SITUATION: EMPSIT_PAGE})
    assert requests == [BLS, LIST[10], EMPLOYMENT_SITUATION] and actuals(raw) == EMPSIT_ACTUALS


@pytest.mark.parametrize("now,url,page,shown", [
    ("2026-10-14T13:31:00+00:00", CONSUMER_PRICE_INDEX, CPI_PAGE, "September 11, 2026"),
    ("2026-11-06T14:31:00+00:00", EMPLOYMENT_SITUATION, EMPSIT_PAGE, "October 2, 2026"),
])
def test_the_previous_edition_after_the_release_time_is_not_todays_release(now, url, page, shown):
    raw, _, _ = collected(utc(now), {BLS: ICS, url: page})
    record = next(source for source in raw["sources"] if source["url"] == url)
    assert actuals(raw) == {}
    assert (record["status"], record["reason"]) == ("UNAVAILABLE", f"the page still shows the {shown} release")


def test_a_calendar_reference_month_the_page_does_not_report_is_refused():
    listed = damaged(PAGES[10], "<strong>Employment Situation</strong> for September 2026",
                     "<strong>Employment Situation</strong> for August 2026")
    raw, _, _ = collected(utc(RELEASE_MORNING), {BLS: FORBIDDEN, LIST[10]: listed, EMPLOYMENT_SITUATION: EMPSIT_PAGE})
    assert actuals(raw) == {}
    assert release_source(raw)["reason"] == "the page reports September 2026, not the scheduled August 2026"


@pytest.mark.parametrize("answer,reason", [
    (FORBIDDEN, "HTTP 403"),
    ("<html><body><h1>Service unavailable</h1></body></html>",
     "malformed release page: not one title, one heading and one release text"),
])
def test_an_unreadable_release_page_keeps_the_event_and_says_why(answer, reason):
    raw, _, _ = collected(utc(RELEASE_MORNING), {BLS: ICS, EMPLOYMENT_SITUATION: answer})
    assert NFP in releases(raw) and actuals(raw) == {}
    assert (release_source(raw)["status"], release_source(raw)["reason"]) == ("UNAVAILABLE", reason)


def test_without_an_admitted_calendar_no_release_page_is_requested():
    raw, _, requests = collected(utc(RELEASE_MORNING),
                                 {BLS: FORBIDDEN, LIST[10]: FORBIDDEN, EMPLOYMENT_SITUATION: EMPSIT_PAGE})
    assert EMPLOYMENT_SITUATION not in requests and actuals(raw) == {} and release_source(raw) is None


def test_tomorrows_release_is_not_read_today():
    # After Thursday's close the calendar admits Friday's release as the next session's; it is read once it happens.
    raw, _, requests = collected(utc("2026-10-01T20:30:00+00:00"), {BLS: ICS, EMPLOYMENT_SITUATION: EMPSIT_PAGE})
    assert requests == [BLS] and NFP in releases(raw) and actuals(raw) == {}


def test_a_family_listed_twice_today_is_not_read():
    events = [dict(id=f"bls-event-{n}", title="Employment Situation", source_id="bls", published_at=None,
                   checked_at=RELEASE_MORNING, scheduled_at=at, status="SCHEDULED")
              for n, at in enumerate(("2026-10-02T12:30:00+00:00", "2026-10-02T13:00:00+00:00"))]
    requests = []
    rows, records = release_actuals(events, utc(RELEASE_MORNING), time.monotonic() + 60,
                                    lambda url, deadline: requests.append(url) or EMPSIT_PAGE)
    assert requests == [] and rows == []
    assert [(r["id"], r["status"], r["reason"]) for r in records] == [
        ("bls-empsit", "UNAVAILABLE", "the calendar lists this release more than once today")]


def test_a_release_page_gets_a_bounded_share_of_the_collection_time():
    budgets = []

    def fetcher(url, deadline):
        budgets.append(deadline - time.monotonic())
        return EMPSIT_PAGE
    events = [dict(id="bls-event-0", title="Employment Situation", source_id="bls", published_at=None,
                   checked_at=RELEASE_MORNING, scheduled_at=NFP[1], status="SCHEDULED")]
    release_actuals(events, utc(RELEASE_MORNING), time.monotonic() + 600, fetcher)
    assert len(budgets) == 1 and budgets[0] <= RELEASE_SECONDS


@pytest.mark.parametrize("url", [EMPLOYMENT_SITUATION, CONSUMER_PRICE_INDEX])
def test_each_release_page_is_requested_with_the_owner_contact(monkeypatch, url):
    monkeypatch.setenv("BLS_CONTACT", "owner@example.org")
    requests = network(monkeypatch, {url: "page"})
    assert fetch(url, time.monotonic() + 30) == "page"
    assert [(request.full_url, request.get_header("User-agent")) for request in requests] == [
        (url, "MarketBrief/0.1 (owner@example.org)")]


@pytest.mark.parametrize("url", [
    "https://www.bls.gov/news.release/empsit.nr1.htm", "https://www.bls.gov/news.release/empsit.t01.htm",
    "https://www.bls.gov/news.release/archives/empsit_10022026.htm", "https://www.bls.gov/news.release/ppi.nr0.htm",
    "http://www.bls.gov/news.release/empsit.nr0.htm", "https://www.bls.gov/news.release/empsit.nr0.htm?x=1",
    "https://www.bls.gov/news.release/cpi.nr0.htm#a", "https://www.bls.gov/news.release/cpi.nr0.html",
    "https://data.bls.gov/news.release/cpi.nr0.htm"])
def test_no_other_release_page_can_be_requested(monkeypatch, url):
    requests = network(monkeypatch, {})
    with pytest.raises(SourceError, match="allowlist"):
        fetch(url, time.monotonic() + 30)
    assert not requests


def test_a_redirect_off_a_release_page_is_refused(monkeypatch):
    monkeypatch.setattr(collect, "build_opener", lambda *handlers: real_build_opener(*handlers, Scripted({
        EMPLOYMENT_SITUATION: (302, "https://www.bls.gov/news.release/archives/empsit_09042026.htm", "")}, [])))
    with pytest.raises(SourceError, match="redirect outside allowlist"):
        fetch(EMPLOYMENT_SITUATION, time.monotonic() + 30)


# --- Evidence: dated official rows, current only in the session that published them ---------------------------------

TUESDAY_RELEASE = "2026-09-08T12:30:00+00:00"  # the sample fixture's Tuesday, 8:30 AM ET
RELEASE_FIELDS = {"reference_period", "revised_month", "revised_from", "revised_to"}


def frozen(monkeypatch, now):
    """The collector's clock pinned to the run's, as in production (its retrieval times then fall inside the run)."""
    fixed = utc(now)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz else fixed.replace(tzinfo=None)
    monkeypatch.setattr(collect, "datetime", Frozen)
    return fixed


def release_packet(monkeypatch, checkpoint="OPEN_1M", now=RELEASE_MORNING, page=EMPSIT_PAGE):
    """NFP morning through the real collector, normalization, derivation, magnitude and coverage, as `cli.run` does."""
    fixed = frozen(monkeypatch, now)
    raw, _, _ = collected(fixed, {BLS: ICS, EMPLOYMENT_SITUATION: page})
    packet = derive(normalize_packet(raw, fixed, "LIVE", checkpoint), read_json(ROOT / "config/universe.json"))
    annotate_magnitude(packet, read_json(ROOT / "config/magnitude.json"))
    return finalize_coverage(packet)


def tuesday_release(family="empsit", page=EMPSIT_PAGE, at=TUESDAY_RELEASE):
    """The real release's rows and source record with its release time moved onto the sample fixture's Tuesday, so it
    can join that fixture's whole market record. Values, months and IDs are the real ones."""
    release = read_release(family, page)
    release["released_at"] = utc(at)
    spec = FAMILIES[family]
    record = collect.source(spec["source"], spec["name"], "release", spec["url"], utc(at),
                            **(dict(status="DEGRADED", reason=release["note"]) if release["note"] else {}))
    event = dict(id="bls-event-0", title=spec["title"], source_id="bls", published_at=None, checked_at=at,
                 scheduled_at=at, status="SCHEDULED")
    return release_rows(release, utc(at)), record, event


def test_release_values_are_admitted_as_dated_rows_of_this_session(monkeypatch):
    packet = release_packet(monkeypatch)
    rows = {row["id"]: row for row in packet["observations"] if row["frequency"] == "release"}
    assert {ident: row["value"] for ident, row in rows.items()} == EMPSIT_ACTUALS
    assert {(row["status"], row["freshness"], row["reference_period"]) for row in rows.values()} == {
        ("AVAILABLE", "DATED", "2026-09")}
    august = rows["bls-empsit-revision-2026-08"]
    assert (august["revised_month"], august["revised_from"], august["revised_to"]) == ("2026-08", 162, 133)
    assert set(EMPSIT_ACTUALS) <= set(evidence_catalog(packet))
    assert not any(row["frequency"] == "release" for row in packet["derived"])
    # No other row grows a release field, null or not.
    assert not any(RELEASE_FIELDS & set(row) for row in fixture_packet()["observations"])
    assert not RELEASE_FIELDS & set(rows["bls-empsit-payrolls"]) - {"reference_period"}


@pytest.mark.parametrize("change,status,reason", [
    (dict(observed_at="2026-10-01T12:30:00+00:00"), "STALE", "release published in an earlier session"),
    (dict(observed_at="2026-10-02T13:45:00+00:00"), "INVALID", "release published after collection"),
    (dict(reference_period=None), "INVALID", "release without a reference month"),
    (dict(revised_from=float("nan")), "INVALID", "malformed release revision"),
])
def test_a_release_row_from_another_session_or_without_its_month_is_never_current(change, status, reason):
    row = dict(id="bls-empsit-payrolls", topic="Employment Situation", metric="nonfarm payroll change", value=29,
               unit="thousand jobs", baseline="over the month", frequency="release",
               observed_at="2026-10-02T12:30:00+00:00", retrieved_at=RELEASE_MORNING, source_id="bls-empsit",
               status="AVAILABLE", reason="", reference_period="2026-09")
    result = normalize_observation({**row, **change}, utc(RELEASE_MORNING))
    assert (result["status"], result["value"], result["reason"]) == (status, None, reason)


def test_the_rich_analyst_reads_what_released_when_for_which_month_and_every_value(monkeypatch):
    packet = release_packet(monkeypatch, "PREMARKET", now="2026-10-02T13:01:00+00:00")
    context = analyst_context(packet, edition_profile("PREMARKET"))
    group = next(group for group in context["catalog"] if group["topic"] == "Employment Situation")
    # The release states its official time and reference month once; each row carries its value.
    assert (group["released_at"], group["reference_period"]) == ("2026-10-02T12:30:00+00:00", "2026-09")
    rows = {row["id"]: row for row in group["rows"]}
    assert set(rows) == set(EMPSIT_ACTUALS)
    assert rows["bls-empsit-payrolls"] == dict(
        id="bls-empsit-payrolls", metric="nonfarm payroll change", value=29, unit="thousand jobs", magnitude="NEUTRAL",
        status="AVAILABLE")
    july = rows["bls-empsit-revision-2026-07"]
    assert (july["value"], july["revised_month"], july["revised_from"], july["revised_to"]) == (-31, "2026-07", 21, -10)
    assert context["baselines"]["payroll revision"] == "change to the revised month's previously published estimate"
    assert context["baselines"]["unemployment rate"] == "share of the labor force, seasonally adjusted"
    # Every other group keeps its rows' own clocks.
    assert all("released_at" not in group for group in context["catalog"] if group["topic"] != "Employment Situation")
    assert next(s for s in context["sources"] if s["id"] == "bls-empsit") == dict(
        id="bls-empsit", name="BLS Employment Situation", kind="release", status="AVAILABLE",
        coverage_date="2026-10-02")


def test_the_light_context_always_keeps_todays_release():
    rows, record, event = tuesday_release()
    _, _, _, context = light_edition(OPEN_30M_TUE, "OPEN_30M", "sample-open_30m-tue", intraday_value=0.21,
                                     sources=[record], observations=rows, events=[event])
    assert context["selection"]["omitted_count"] > 0  # a genuinely bounded context, and the release is not in it
    assert set(EMPSIT_ACTUALS) <= supplied_ids(context)
    assert "Employment Situation" not in context["selection"]["omitted_topics"]


def test_todays_release_costs_the_light_request_little():
    rows, record, event = tuesday_release()
    quiet = light_edition(OPEN_30M_TUE, "OPEN_30M", "sample-open_30m-tue", intraday_value=0.21, events=[event])
    released = light_edition(OPEN_30M_TUE, "OPEN_30M", "sample-open_30m-tue", intraday_value=0.21,
                             sources=[record], observations=rows, events=[event])
    size = {name: len(construct_prompt(edition[0], context=edition[3])[1].encode())
            for name, edition in (("quiet", quiet), ("released", released))}
    assert size["released"] - size["quiet"] < 2_500  # seven whole rows, their legend and one source record
    assert size["released"] <= 34_000  # the light editions' own headroom bound, 6,000 bytes inside the 40,000 limit


@pytest.mark.parametrize("ident", sorted(EMPSIT_ACTUALS))
def test_the_analyst_can_cite_every_release_value(ident):
    rows, record, event = tuesday_release()
    packet = run_packet(PREMARKET_TUE, "sample-premarket-tue", sources=[record], observations=rows, events=[event])
    profile = edition_profile("PREMARKET")
    context = analyst_context(packet, profile)
    value = edition_response(profile, context)
    value["sections"]["macro"] = [{"text": f"The release printed {{{{{ident}}}}} for its month.", "class": "OBSERVED",
                                   "evidence_ids": [ident], "uncertainty": "", "alternative": ""}]
    assert validate_narrative(value, packet, context)
    markdown, page = render(packet, value, context)
    shown = formatted(next(row for row in rows if row["id"] == ident))
    assert f"The release printed {shown} for its month." in page


# --- The page: one deterministic card in Macro & rates --------------------------------------------------------------

@pytest.mark.parametrize("value,unit,shown", [
    (29, "thousand jobs", "+29k"), (-10, "thousand jobs", "−10k"), (0, "thousand jobs", "0k"),
    (133, "thousand jobs", "+133k"), (4.2, "percent", "4.2%"), (0.1, "percent change", "+0.1%"),
    (-0.1, "percent change", "−0.1%"), (0.0, "percent change", "0.0%"), (3.0, "percent change", "+3.0%"),
])
def test_release_values_read_as_bls_prints_them(value, unit, shown):
    assert formatted(dict(value=value, unit=unit)) == shown


def tuesday_page(family="empsit", page=EMPSIT_PAGE, events=()):
    """The sample fixture's Tuesday premarket with the real release joined to it, rendered from an accepted response."""
    rows, record, event = tuesday_release(family, page)
    upcoming = [dict(id="bls-event-1", title="Real Earnings", source_id="bls", published_at=None,
                     checked_at=PREMARKET_TUE, scheduled_at=TUESDAY_RELEASE, status="SCHEDULED"), *events]
    packet = run_packet(PREMARKET_TUE, "sample-premarket-tue", sources=[record], observations=rows,
                        events=[event, *upcoming])
    profile = edition_profile("PREMARKET")
    context = analyst_context(packet, profile)
    value = edition_response(profile, context)
    return packet, value, context, render(packet, value, context)


def macro(page):
    return page.split("<h2>Macro &amp; rates</h2>", 1)[1].split("</section>", 1)[0]


def card_lines(html_text):
    """The card's measures as (label, [value lines])."""
    block = re.search(r'<dl class="release-values">(.*?)</dl>', html_text, re.S).group(1)
    return [(label, re.findall(r"<span>(.*?)</span>", values))
            for label, values in re.findall(r"<dt>(.*?)</dt><dd>(.*?)</dd>", block, re.S)]


def test_the_employment_situation_card_leads_macro_and_rates():
    _, _, _, (markdown, page) = tuesday_page()
    section = macro(page)
    card = section.split('<div class="release">', 1)[1].split("</dl>", 1)[0] + "</dl>"
    assert section.index('<div class="release">') < section.index("U.S. Treasury par curve")
    assert re.search(r'<div class="caption">Economic release<span>5:30 AM PT</span></div>', card)
    assert "<b>Employment Situation</b> · <span>September 2026</span>" in card
    assert card_lines(card) == [
        ("Payrolls", ["+29k"]), ("Unemployment", ["4.2%"]), ("Avg hourly earnings", ["+0.1% m/m · +3.0% y/y"]),
        ("Revisions", ["Jul +21k → −10k", "Aug +162k → +133k", "Combined −60k"])]
    assert "direction-" not in card  # official values are never coloured as good or bad
    assert page.count('<div class="release">') == 1


def test_the_card_reads_the_same_in_markdown():
    _, _, _, (markdown, _) = tuesday_page()
    section = markdown.split("## Macro & rates", 1)[1].split("\n## ", 1)[0]
    assert ("**ECONOMIC RELEASE** · 5:30 AM PT · Employment Situation · September 2026\n\n"
            "- Payrolls · +29k\n- Unemployment · 4.2%\n- Avg hourly earnings · +0.1% m/m · +3.0% y/y\n"
            "- Revisions · Jul +21k → −10k; Aug +162k → +133k; Combined −60k\n") in section
    assert section.index("ECONOMIC RELEASE") < section.index("U.S. TREASURY PAR CURVE")


def test_the_cpi_card_reads_headline_and_core():
    _, _, _, (markdown, page) = tuesday_page("cpi", CPI_PAGE)
    assert "<b>Consumer Price Index</b> · <span>August 2026</span>" in macro(page)
    assert card_lines(macro(page)) == [("CPI", ["+0.4% m/m · +3.4% y/y"]), ("Core CPI", ["+0.3% m/m · +2.4% y/y"])]
    assert "- CPI · +0.4% m/m · +3.4% y/y\n- Core CPI · +0.3% m/m · +2.4% y/y" in markdown


def test_a_released_event_leaves_what_matters_next_and_others_stay():
    _, _, _, (markdown, page) = tuesday_page()
    upcoming = page.split('<section class="next">', 1)[1].split("</section>", 1)[0]
    assert "<b>Employment Situation</b>" not in upcoming and "<b>Real Earnings</b>" in upcoming
    assert "**Event** — Employment Situation" not in markdown and "**Event** — Real Earnings" in markdown


def test_every_card_value_is_in_the_evidence_ledger_with_its_source():
    _, _, _, (_, page) = tuesday_page()
    ledger = drawer(page, "Evidence ledger")
    for ident in EMPSIT_ACTUALS:
        assert f'id="evidence-{ident}"' in ledger
    assert "BLS Employment Situation" in ledger
    proof = macro(page).split('<div class="release">', 1)[1].split("</div></div>", 1)[0]
    assert "View exact values" in proof and 'href="#evidence-bls-empsit-payrolls"' in proof


def test_without_admitted_actuals_the_event_stays_and_no_card_shows():
    packet = run_packet(PREMARKET_TUE, "sample-premarket-tue", events=[tuesday_release()[2]])
    profile = edition_profile("PREMARKET")
    context = analyst_context(packet, profile)
    markdown, page = render(packet, edition_response(profile, context), context)
    assert '<div class="release">' not in page and "ECONOMIC RELEASE" not in markdown
    assert "<b>Employment Situation</b>" in page.split('<section class="next">', 1)[1].split("</section>", 1)[0]


# --- The production day: refreshes improve the observed record under an older interpretation ------------------------

FRIDAY, MONDAY = "2026-10-02", "2026-10-05"


class ReleaseDay(Day):
    """A production day through the CLI whose BLS records come from the real `bls_collection` at each run's clock,
    answered offline: the calendar file, and the Employment Situation page as `page` (a body, or the error fetching it
    raises). `fetcher=collect.fetch` sends the requests through the real request path instead."""

    def __init__(self, monkeypatch, root):
        super().__init__(monkeypatch, root)
        self.requests = []

    def release_run(self, now, checkpoint, page, fetcher=None, session=FRIDAY, **kwargs):
        history = "2026-10-01" if session == FRIDAY else FRIDAY

        def bls(raw, fixed):
            frozen(self.monkeypatch, now)
            for row in raw["observations"]:
                if row["frequency"] == "daily":  # the fixture's dated yields move onto the prior session too
                    row["observed_at"] = history

            def offline(url, deadline):
                self.requests.append((checkpoint, url))
                answer = {BLS: ICS, EMPLOYMENT_SITUATION: page}.get(url, FORBIDDEN)
                if isinstance(answer, Exception):
                    raise answer
                return answer
            sources, events, rows = collect.bls_collection(fixed, time.monotonic() + 60, fetcher or offline)
            raw["sources"] = [source for source in raw["sources"] if source["id"] != "bls"] + sources
            raw["events"] = events
            raw["observations"] += rows
        return self.run(now, checkpoint, collect=bls, last_history_date=history, **kwargs)

    def evidence(self, checkpoint, session=FRIDAY):
        return json.loads((self.folder(checkpoint, session) / "evidence.json").read_text())

    def actuals(self, checkpoint, session=FRIDAY):
        """The run's admitted release rows as (id, value, official time, reference month)."""
        return sorted((row["id"], row["value"], row["observed_at"], row["reference_period"])
                      for row in self.evidence(checkpoint, session)["observations"]
                      if row["frequency"] == "release" and row["status"] == "AVAILABLE")


def upcoming(page):
    return page.split('<section class="next">', 1)[1].split("</section>", 1)[0]


SEPTEMBER_CARD = [("Payrolls", ["+29k"]), ("Unemployment", ["4.2%"]),
                  ("Avg hourly earnings", ["+0.1% m/m · +3.0% y/y"]),
                  ("Revisions", ["Jul +21k → −10k", "Aug +162k → +133k", "Combined −60k"])]


@pytest.mark.parametrize("premarket_at,premarket_page", [
    ("2026-10-02T12:15:00+00:00", EMPSIT_PAGE),  # a premarket before the 8:30 release: the page is not even read
    ("2026-10-02T13:00:00+00:00", FORBIDDEN),  # the scheduled 9:00 premarket, with the page refused
], ids=["premarket before the release", "page refused at the premarket"])
def test_a_release_morning_from_the_premarket_to_the_close(monkeypatch, tmp_path, premarket_at, premarket_page):
    day = ReleaseDay(monkeypatch, tmp_path)
    assert day.release_run(premarket_at, "PREMARKET", premarket_page, intraday=False) == 0
    premarket = day.page("PREMARKET", FRIDAY)
    assert day.calls == ["PREMARKET"] and day.actuals("PREMARKET") == []
    assert '<div class="release">' not in premarket and "<b>Employment Situation</b>" in upcoming(premarket)
    context = json.loads((day.folder("PREMARKET", FRIDAY) / "analyst_context.json").read_text())
    assert not set(EMPSIT_ACTUALS) & supplied_ids(context)
    if premarket_page is EMPSIT_PAGE:
        assert (("PREMARKET", EMPLOYMENT_SITUATION)) not in day.requests
    interpretation = day.bundle()["interpretation"]["content_hash"]

    # 6:31 AM PT: the deterministic refresh reads the page, and the release shows at once under the premarket analysis.
    assert day.release_run("2026-10-02T13:31:00+00:00", "OPEN_1M", EMPSIT_PAGE) == 0
    opening = day.page("OPEN_1M", FRIDAY)
    assert day.calls == ["PREMARKET"] and day.metadata("OPEN_1M", FRIDAY)["synthesis"] == dict(kind="refresh", calls=0)
    assert card_lines(macro(opening)) == SEPTEMBER_CARD
    assert "<b>Employment Situation</b>" not in upcoming(opening)
    assert interpretation_fragments(opening)[0] == interpretation_fragments(premarket)[0]
    assert clock_lines(opening)[1].startswith("Analysis · ") and clock_lines(opening)[1].endswith("premarket")
    assert day.bundle()["interpretation"]["content_hash"] == interpretation  # the refresh never rewrites it
    released = day.actuals("OPEN_1M")
    assert {row[0] for row in released} == set(EMPSIT_ACTUALS)

    # 7:00 AM PT: the one light synthesis after the open reads the release whole; still one call.
    assert day.release_run("2026-10-02T14:01:00+00:00", "OPEN_30M", EMPSIT_PAGE) == 0
    assert day.calls == ["PREMARKET", "OPEN_30M"]
    context = json.loads((day.folder("OPEN_30M", FRIDAY) / "analyst_context.json").read_text())
    assert context["edition"]["profile"] == "light" and set(EMPSIT_ACTUALS) <= supplied_ids(context)
    assert card_lines(macro(day.page("OPEN_30M", FRIDAY))) == SEPTEMBER_CARD

    # Hourly refreshes and the close: no calls, the same release, the same values, the same card.
    for now, checkpoint in (("2026-10-02T15:00:00+00:00", "HOURLY_1100"), ("2026-10-02T18:00:00+00:00", "HOURLY_1400")):
        assert day.release_run(now, checkpoint, EMPSIT_PAGE) == 0
        assert card_lines(macro(day.page(checkpoint, FRIDAY))) == SEPTEMBER_CARD
        assert day.actuals(checkpoint) == released
    assert day.release_run("2026-10-02T20:03:00+00:00", "CLOSE_1M", EMPSIT_PAGE,
                           print_at="2026-10-02T19:59:58+00:00") == 0
    assert card_lines(macro(day.page("CLOSE_1M", FRIDAY))) == SEPTEMBER_CARD and day.actuals("CLOSE_1M") == released
    assert day.calls == ["PREMARKET", "OPEN_30M"]
    assert day.metadata("CLOSE_1M", FRIDAY)["continuity"]["handoff"] == "written"

    # Monday: no release is scheduled, so none is read, admitted or shown; Friday's print is not Monday's.
    assert day.release_run("2026-10-05T13:00:00+00:00", "PREMARKET", EMPSIT_PAGE, session=MONDAY, intraday=False) == 0
    monday = day.page("PREMARKET", MONDAY)
    assert day.actuals("PREMARKET", MONDAY) == [] and '<div class="release">' not in monday
    assert ("PREMARKET", EMPLOYMENT_SITUATION) not in day.requests[-3:]
    assert not any(source["id"] == "bls-empsit" for source in day.evidence("PREMARKET", MONDAY)["sources"])


def test_a_rejected_opening_synthesis_keeps_the_release_card_and_is_not_retried(monkeypatch, tmp_path):
    day = ReleaseDay(monkeypatch, tmp_path)
    assert day.release_run("2026-10-02T13:00:00+00:00", "PREMARKET", EMPSIT_PAGE, intraday=False) == 0
    assert card_lines(macro(day.page("PREMARKET", FRIDAY))) == SEPTEMBER_CARD  # the 9:00 premarket reads it itself
    assert day.release_run("2026-10-02T13:31:00+00:00", "OPEN_1M", EMPSIT_PAGE) == 0
    assert day.release_run("2026-10-02T14:01:00+00:00", "OPEN_30M", EMPSIT_PAGE, fail_synthesis=True) == 2
    assert day.release_run("2026-10-02T15:00:00+00:00", "HOURLY_1100", EMPSIT_PAGE) == 0
    hourly = day.page("HOURLY_1100", FRIDAY)
    assert day.calls == ["PREMARKET", "OPEN_30M"]  # the rejection was the one attempt; the refresh never retries
    assert card_lines(macro(hourly)) == SEPTEMBER_CARD
    assert day.bundle()["interpretation"]["origin"]["checkpoint"] == "PREMARKET"
    assert clock_lines(hourly)[1].endswith("premarket")


def test_a_page_that_changes_between_refreshes_is_read_again_as_the_same_release_or_not_at_all(monkeypatch, tmp_path):
    corrected = EMPSIT_PAGE.replace("(+29,000)", "(+31,000)")  # a corrected print, still September's release
    moved = damaged(EMPSIT_PAGE, "8:30 a.m. (ET) Friday, October 2, 2026", "8:30 a.m. (ET) Friday, September 4, 2026")
    day = ReleaseDay(monkeypatch, tmp_path)
    assert day.release_run("2026-10-02T13:00:00+00:00", "PREMARKET", EMPSIT_PAGE, intraday=False) == 0
    for now, checkpoint, page in (("2026-10-02T15:00:00+00:00", "HOURLY_1100", EMPSIT_PAGE),
                                  ("2026-10-02T16:00:00+00:00", "HOURLY_1200", corrected),
                                  ("2026-10-02T17:00:00+00:00", "HOURLY_1300", moved)):
        assert day.release_run(now, checkpoint, page) == 0
    assert card_lines(macro(day.page("HOURLY_1100", FRIDAY)))[0] == ("Payrolls", ["+29k"])
    assert card_lines(macro(day.page("HOURLY_1200", FRIDAY)))[0] == ("Payrolls", ["+31k"])
    assert dict((row[0], row[3]) for row in day.actuals("HOURLY_1200"))["bls-empsit-payrolls"] == "2026-09"
    later = day.page("HOURLY_1300", FRIDAY)
    assert day.actuals("HOURLY_1300") == [] and '<div class="release">' not in later
    assert "<b>Employment Situation</b>" in upcoming(later)
    assert "the page still shows the September 4, 2026 release" in drawer(later, "Coverage limitations")
    assert day.calls == ["PREMARKET"]


def test_a_page_missed_at_the_open_is_added_by_a_later_refresh(monkeypatch, tmp_path):
    day = ReleaseDay(monkeypatch, tmp_path)
    assert day.release_run("2026-10-02T13:00:00+00:00", "PREMARKET", FORBIDDEN, intraday=False) == 0
    assert day.release_run("2026-10-02T13:31:00+00:00", "OPEN_1M", SourceError("network unavailable or timeout")) == 0
    missed = day.page("OPEN_1M", FRIDAY)  # still published: the event stands and the limitation says why
    assert '<div class="release">' not in missed and "<b>Employment Situation</b>" in upcoming(missed)
    assert "BLS Employment Situation: network unavailable or timeout" in drawer(missed, "Coverage limitations")
    assert day.release_run("2026-10-02T15:00:00+00:00", "HOURLY_1100", EMPSIT_PAGE) == 0
    assert card_lines(macro(day.page("HOURLY_1100", FRIDAY))) == SEPTEMBER_CARD
    assert day.calls == ["PREMARKET"] and day.published == ["PREMARKET", "OPEN_1M", "HOURLY_1100"]


def test_the_owner_contact_identifies_the_requests_and_appears_in_nothing_the_runs_keep(monkeypatch, tmp_path, capsys):
    contact = "owner-contact@example.org"
    monkeypatch.setenv("BLS_CONTACT", contact)
    sent = network(monkeypatch, {BLS: ICS, EMPLOYMENT_SITUATION: EMPSIT_PAGE})
    day = ReleaseDay(monkeypatch, tmp_path)
    assert day.release_run("2026-10-02T13:00:00+00:00", "PREMARKET", None, fetcher=fetch, intraday=False) == 0
    assert day.release_run("2026-10-02T13:31:00+00:00", "OPEN_1M", None, fetcher=fetch) == 0
    assert day.release_run("2026-10-02T14:01:00+00:00", "OPEN_30M", None, fetcher=fetch) == 0
    assert card_lines(macro(day.page("OPEN_30M", FRIDAY))) == SEPTEMBER_CARD
    assert {request.get_header("User-agent") for request in sent} == {f"MarketBrief/0.1 ({contact})"}
    kept = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert {path.name for path in kept} >= {"evidence.json", "analyst_context.json", "metadata.json", "brief.html",
                                            "brief.md", "narrative.json", "edition_state.json", "bundle.json"}
    assert not [path for path in kept if "owner-contact" in path.read_text(errors="replace")]
    output = capsys.readouterr()
    assert "owner-contact" not in output.out + output.err


# --- Review fixes ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("old,new", [
    # A rate stated for another month never stands in for the reference month's, even when the reference month's own
    # statement is in a form the reader does not accept.
    ("Both the unemployment rate, at 4.2 percent, and",
     "The unemployment rate, at 4.2 percent in September, was little changed from August, when the unemployment rate "
     "was 4.3 percent. Both the jobless rate and"),
    ("Both the unemployment rate, at 4.2 percent, and", "In August, the unemployment rate was 4.3 percent. Both the"),
    ("Over the past 12 months, average hourly earnings have increased\nby 3.0 percent.",
     "In August, over the year, wages rose. Over the past 12 months, average hourly earnings have increased\nby 3.0 "
     "percent in August."),
])
def test_a_rate_stated_for_another_month_refuses_the_release(old, new):
    page = damaged(EMPSIT_PAGE, "and the unemployment rate (4.2 percent)", "and the jobless measure")
    with pytest.raises(ReleaseError, match="names another month"):
        read_release("empsit", damaged(page, old, new))


@pytest.mark.parametrize("old,new,payrolls", [
    ("(+29,000)", "(−29,000)", -29),  # a true minus
    ("(+29,000)", "(0)", 0),  # an unchanged print
])
def test_signed_payroll_prints_read_in_every_form_bls_uses(old, new, payrolls):
    assert read_release("empsit", EMPSIT_PAGE.replace(old, new))["values"]["payrolls"] == payrolls


def test_an_en_dash_heading_reads():
    page = damaged(EMPSIT_PAGE, "SITUATION - SEPTEMBER 2026", "SITUATION – SEPTEMBER 2026")
    assert read_release("empsit", page)["period"] == "2026-09"


def test_an_impossible_earnings_level_is_a_refusal_not_a_crash():
    page = damaged(EMPSIT_PAGE, "edged up by 5\ncents, or 0.1 percent, to $37.81",
                   "edged up by 5\ncents, or 0.1 percent, to $0.05")
    with pytest.raises(ReleaseError, match="cents and dollar level"):
        read_release("empsit", page)


def test_an_unreadable_revision_number_leaves_only_the_revisions_out():
    page = damaged(EMPSIT_PAGE, "combined is 60,000 lower", "combined is " + "9" * 5000 + " lower")
    release = read_release("empsit", page)
    assert release["values"]["payrolls"] == 29 and release["revisions"] == [] and release["note"] == UNREAD


@pytest.mark.parametrize("error", [http.client.IncompleteRead(b""), http.client.BadStatusLine("HTTP/1.1 2xx OK")])
def test_a_broken_http_response_is_a_source_failure_not_a_crash(monkeypatch, error):
    attempts = []

    class Opener:
        def open(self, request, timeout):
            attempts.append(request.full_url)
            raise error
    monkeypatch.setattr(collect, "build_opener", lambda *handlers: Opener())
    with pytest.raises(SourceError, match="network unavailable or timeout"):
        fetch(EMPLOYMENT_SITUATION, time.monotonic() + 30)
    assert attempts == [EMPLOYMENT_SITUATION] * 2


def test_each_revised_month_is_its_own_measurement():
    rows, _, _ = tuesday_release()
    keys = {row["id"]: metric_identity(row)["key"] for row in rows}
    assert keys["bls-empsit-revision-2026-07"] != keys["bls-empsit-revision-2026-08"]


def degraded_page():
    page = damaged(EMPSIT_PAGE, "July and August combined is 60,000 lower", "July and August combined is 50,000 lower")
    packet, value, context, rendered = tuesday_page(page=page)
    return packet, rendered


def test_a_release_whose_revisions_did_not_read_says_so_on_the_card_and_is_not_unavailable():
    packet, (markdown, page) = degraded_page()
    assert card_lines(macro(page))[-1] == ("Revisions", ["not read from the release"])
    assert "- Revisions · not read from the release" in markdown
    sources = drawer(page, "Sources ·")
    assert "unavailable" not in sources.split("</summary>", 1)[0]
    assert "Degraded · payroll revisions not read cleanly" in sources


def test_a_card_counts_as_macro_and_rates_content():
    rows, record, event = tuesday_release()
    packet = run_packet(PREMARKET_TUE, "sample-premarket-tue", sources=[record], observations=rows, events=[event])
    packet["observations"] = [row for row in packet["observations"] if not row["topic"].startswith("US ")]
    packet["derived"] = [row for row in packet["derived"] if row["topic"] not in ("GLD", "GDX", "SLV")
                         and not row["topic"].startswith("US ")]
    packet.pop("curve", None)
    profile = edition_profile("PREMARKET")
    context = analyst_context(packet, profile)
    value = edition_response(profile, context)
    value["sections"]["macro"] = []
    markdown, page = render(packet, value, context)
    assert '<div class="release">' in page
    omitted = [line for line in markdown.splitlines() if "Not in this edition" in line]
    assert omitted and not any("Macro" in line or "Treasury" in line for line in omitted)


def test_revision_evidence_names_its_month_and_estimates():
    _, _, _, (_, page) = tuesday_page()
    assert "Payroll revision, July 2026 (+21k → −10k)" in drawer(page, "Evidence ledger")
    proof = macro(page).split('<div class="release">', 1)[1].split("</div></div>", 1)[0]
    assert "Payroll revision, August 2026 (+162k → +133k)" in proof


def test_a_release_from_another_session_shows_its_date():
    rows, _, _ = tuesday_release()
    session = dict(date="2026-09-09", open="2026-09-09T13:30:00+00:00", close="2026-09-09T20:00:00+00:00")
    assert release_clock(rows[0], session) == "Tuesday, Sep 8 · 5:30 AM PT"
    assert release_clock(rows[0], dict(session, date="2026-09-08")) == "5:30 AM PT"


def test_large_payroll_values_keep_their_thousands_separator():
    assert formatted(dict(value=-20493, unit="thousand jobs")) == "−20,493k"


def test_markdown_revisions_separate_months_from_their_estimates():
    _, _, _, (markdown, _) = tuesday_page()
    assert "- Revisions · Jul +21k → −10k; Aug +162k → +133k; Combined −60k" in markdown

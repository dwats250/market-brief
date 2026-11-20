"""NVIDIA's official press releases as dated issuer evidence (WP-MB-NVDA-EVIDENCE-01).

Every test is offline. `tests/fixtures/nvidia/press-releases.xml` holds the real Nov 8, Nov 10 and Nov 12 issuer
disclosures, the Oct 28 results-schedule notice and the historical Q2 FY27 results release (date-only: it had left the
live feed and its page gives no clock). The Nov 18 replay adds MOCK_Q3_RESULTS, a test-only stand-in for a results
release that is scheduled for Nov 17 and not published when this was written: its clock and figures are fixture data,
never NVIDIA's results.
"""

import json
from datetime import datetime, timedelta, timezone
from urllib.request import Request

import pytest
from profiles import light_profile
from test_contract import edition_response
from test_history_admission import packet_at

from market_brief import collect
from market_brief.collect import NVIDIA_IR, SameHostRedirect, SourceError, allowed, collect_live, fetch, xml_root
from market_brief.context import analyst_context, edition_profile, supplied_ids
from market_brief.evidence import (
    CONTEXT_ITEM_LIMIT,
    PLACEHOLDER,
    ROOT,
    context_record,
    finalize_coverage,
    model_packet,
    normalize_observation,
    normalize_packet,
    read_json,
)
from market_brief.issuer import ATTRIBUTION, IssuerError, item_id, nvidia_releases, publication
from market_brief.metrics import derive
from market_brief.render import render
from market_brief.synthesize import construct_prompt, validate_narrative

FEED = (ROOT / "tests/fixtures/nvidia/press-releases.xml").read_text()
NEWS = "https://nvidianews.nvidia.com/news/"
CEO = NEWS + "nvidia-announces-ceo-transition"
INDEPENDENT = NEWS + "statement-from-the-independent-members-of-the-nvidia-board"
BOARD = NEWS + "statement-from-nvidia-board-of-directors"
SCHEDULE = NEWS + "nvidia-sets-conference-call-for-third-quarter-financial-results-6928873"
Q2 = NEWS + "nvidia-announces-financial-results-for-second-quarter-fiscal-2027"
MOCK_URL = NEWS + "test-only-mock-third-quarter-fiscal-2027-results"


def utc(text):
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def table(heading, column, revenue, eps):
    return (f'&lt;table&gt;&lt;tr&gt;&lt;td colspan="6"&gt;&lt;strong&gt;{heading}&lt;/strong&gt;&lt;/td&gt;&lt;/tr&gt;'
            f'&lt;tr&gt;&lt;td&gt;&lt;em&gt;($ in millions, except earnings per share)&lt;/em&gt;&lt;/td&gt;'
            f'&lt;td&gt;{column}&lt;/td&gt;&lt;td&gt;prior&lt;/td&gt;&lt;/tr&gt;'
            f'&lt;tr&gt;&lt;td&gt;Revenue&lt;/td&gt;&lt;td&gt;{revenue}&lt;/td&gt;&lt;td&gt;$1&lt;/td&gt;&lt;/tr&gt;'
            '&lt;tr&gt;&lt;td&gt;Diluted earnings per share&lt;/td&gt;'
            f'&lt;td&gt;{eps}&lt;/td&gt;&lt;td&gt;$1.00&lt;/td&gt;&lt;/tr&gt;'
            '&lt;/table&gt;')


def entry(title, link, published, description="An official release.", body=""):
    date = f"<pubDate>{published}</pubDate>" if published is not None else ""
    return (f"<item><title>{title}</title><link>{link}</link><content>&lt;![CDATA[{body}]]&gt;</content>"
            f"<description>{description}</description>{date}</item>")


def feed(*entries):
    return ('<?xml version="1.0"?><rss version="2.0"><channel><title>Press Release</title>'
            f'{"".join(entries)}</channel></rss>')


def with_entries(*entries):
    """The fixture feed with test entries listed first, as a newer release would be."""
    return FEED.replace("<item>", "".join(entries) + "<item>", 1)


# TEST-ONLY MOCK: the Q3 FY27 results are scheduled for Tue Nov 17 (about 1:20 p.m. PT, per NVIDIA's Nov 12 and Oct 28
# releases) and not published when this was written. This entry's clock, URL and figures are synthetic fixture data.
MOCK_Q3_RESULTS = entry(
    "NVIDIA Announces Financial Results for Third Quarter Fiscal 2027", MOCK_URL, "Tue, 17 Nov 2026 21:20:00 GMT",
    "TEST-ONLY MOCK replay input, not an NVIDIA publication: synthetic figures for the Nov 18 replay.",
    table("GAAP", "Q3 FY27", "$11,111", "$1.11") + table("Non-GAAP", "Q3 FY27", "$11,111", "$1.01"))


def parsed(text, now):
    return nvidia_releases(xml_root(text), utc(now), utc(now))


# --- Collection: issuer disclosures ---------------------------------------------------------------------------------

def test_recent_board_statements_are_admitted_with_exact_provenance():
    items, rows, notes = parsed(FEED, "2026-11-12T15:00:00+00:00")
    assert rows == [] and notes == []
    # Nov 12 and Nov 10 are inside the two-day window; Nov 8, Oct 28 and Aug 26 are not.
    assert [item["url"] for item in items] == [CEO, INDEPENDENT]
    ceo, independent = items
    assert ceo == dict(id=item_id(CEO), title="NVIDIA Announces CEO Transition", source_id="nvidia-ir", url=CEO,
                       attribution=ATTRIBUTION, status="AVAILABLE",
                       summary="NVIDIA (NASDAQ: NVDA) Board of Directors announced today that Jensen Huang has "
                               "resigned as President, Chief Executive Officer, and a member of the Board, effective "
                               "immediately.",
                       published_at="2026-11-12T13:48:00+00:00")
    assert independent["published_at"] == "2026-11-10T19:07:00+00:00"
    assert "Wachtell, Lipton, Rosen & Katz" in independent["summary"]


def test_an_investigation_statement_stays_the_companys_statement():
    items, _, notes = parsed(FEED, "2026-11-09T14:00:00+00:00")
    (board,) = items
    assert board["url"] == BOARD and board["published_at"] == "2026-11-08T20:00:00+00:00"
    # The feed's own words, attributed; "recent reporting" is never restated as a finding.
    assert board["attribution"] == "NVIDIA statement"
    assert board["summary"].startswith("The NVIDIA Board is initiating an independent investigation in light of "
                                       "recent reporting")
    # Nov 10 and Nov 12 are after this run: left out and named, never admitted early.
    assert notes == ["NVIDIA Announces CEO Transition: publication after this run",
                     "Statement From the Independent Members of the NVIDIA Board: publication after this run"]


def test_item_ids_are_stable_whatever_the_feed_order():
    newer = entry("NVIDIA Announces Something Newer", NEWS + "something-newer", "Thu, 12 Nov 2026 14:30:00 GMT")
    before, _, _ = parsed(FEED, "2026-11-12T15:00:00+00:00")
    after, _, _ = parsed(with_entries(newer), "2026-11-12T15:00:00+00:00")
    assert {i["url"]: i["id"] for i in before}.items() <= {i["url"]: i["id"] for i in after}.items()


def test_item_count_title_and_summary_are_bounded():
    long_title = "NVIDIA " + "Announces " * 40
    entries = [entry(f"Release {n}", NEWS + f"release-{n}", f"Thu, 12 Nov 2026 0{n}:00:00 GMT", "word " * 200)
               for n in range(6)]
    items, _, notes = parsed(feed(entry(long_title, NEWS + "long", "Thu, 12 Nov 2026 09:00:00 GMT"), *entries),
                             "2026-11-12T15:00:00+00:00")
    assert len(items) == 4 and items[0]["url"] == NEWS + "long"
    assert len(items[0]["title"]) <= 200 and items[0]["title"].endswith("…")
    assert all(len(item["summary"]) <= 300 and item["summary"].endswith("…") for item in items[1:])
    assert notes == ["3 older release(s) inside the window not admitted (limit 4)"]


# --- Collection: publication metadata, links, duplicates, malformed feeds -------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("Thu, 12 Nov 2026 13:48:00 GMT", ("at", utc("2026-11-12T13:48:00+00:00"))),
    ("Thu, 12 Nov 2026 08:48:00 -0500", ("at", utc("2026-11-12T13:48:00+00:00"))),
    ("Wed, 26 Aug 2026", ("on", datetime(2026, 8, 26).date())),
    ("2026-08-26", ("on", datetime(2026, 8, 26).date())),
])
def test_publication_keeps_its_own_precision(value, expected):
    assert publication(value) == expected


@pytest.mark.parametrize("value,reason", [
    (None, "lacks a publication date"), ("", "lacks a publication date"),
    ("Thu, 12 Nov 2026 13:48:00 -0000", "lacks a time zone"),
    ("Thu, 12 Nov 2026 13:48:00", "lacks a time zone"),
    ("sometime soon", "malformed"), ("Fri, 26 Aug 2026", "weekday disagree"), ("2026-02-30", "does not exist"),
])
def test_missing_or_malformed_publication_is_refused(value, reason):
    with pytest.raises(IssuerError, match=reason):
        publication(value)


def test_bad_entries_are_left_out_and_named_while_good_ones_stand():
    good = entry("Good Release", NEWS + "good-release", "Thu, 12 Nov 2026 14:00:00 GMT")
    bad = [entry("No Date", NEWS + "no-date", None),
           entry("Naive Date", NEWS + "naive-date", "Thu, 12 Nov 2026 13:00:00 -0000"),
           entry("Plain HTTP", "http://nvidianews.nvidia.com/news/plain-http", "Thu, 12 Nov 2026 13:00:00 GMT"),
           entry("Other Host", "https://example.com/news/other-host", "Thu, 12 Nov 2026 13:00:00 GMT"),
           entry("Query", NEWS + "query?utm=1", "Thu, 12 Nov 2026 13:00:00 GMT"),
           entry("Feed Page", "https://nvidianews.nvidia.com/cats/press_release", "Thu, 12 Nov 2026 13:00:00 GMT"),
           entry("Future", NEWS + "future", "Fri, 13 Nov 2026 13:00:00 GMT"),
           entry("Future Day", NEWS + "future-day", "Fri, 13 Nov 2026"),
           entry("", NEWS + "untitled", "Thu, 12 Nov 2026 13:00:00 GMT"),
           entry("Duplicate", NEWS + "good-release", "Thu, 12 Nov 2026 12:00:00 GMT")]
    items, rows, notes = parsed(feed(good, *bad), "2026-11-12T15:00:00+00:00")
    assert [item["url"] for item in items] == [NEWS + "good-release"] and rows == []
    assert notes == ["No Date: entry lacks a publication date",
                     "Naive Date: entry publication time lacks a time zone",
                     *(f"{name}: entry link is not an NVIDIA newsroom release URL"
                       for name in ("Plain HTTP", "Other Host", "Query", "Feed Page")),
                     "Future: publication after this run", "Future Day: publication after this run",
                     "untitled entry: entry lacks a title", "Duplicate: duplicate release URL"]


def test_date_only_entries_keep_their_date_and_age_by_day():
    day = entry("Dated Release", NEWS + "dated-release", "Tue, 10 Nov 2026")
    old = entry("Old Release", NEWS + "old-release", "Mon, 09 Nov 2026")
    items, _, _ = parsed(feed(day, old), "2026-11-12T15:00:00+00:00")
    assert [(item["url"], item.get("published_on"), "published_at" in item) for item in items] == [
        (NEWS + "dated-release", "2026-11-10", False)]


@pytest.mark.parametrize("text,reason", [
    ("<feed><entry/></feed>", "malformed RSS"),
    ('<rss version="2.0"><channel></channel></rss>', "lists no entries"),
])
def test_a_feed_that_is_not_this_rss_is_refused(text, reason):
    with pytest.raises(IssuerError, match=reason):
        parsed(text, "2026-11-12T15:00:00+00:00")


# --- Collection: the representative results release ----------------------------------------------------------------

def test_q2_fy27_results_yield_only_the_reported_revenue_and_diluted_eps():
    items, rows, notes = parsed(FEED, "2026-08-27T13:00:00+00:00")
    assert notes == [f"{title}: publication after this run" for title in (
        "NVIDIA Announces CEO Transition", "Statement From the Independent Members of the NVIDIA Board",
        "Statement From NVIDIA Board of Directors", "NVIDIA Sets Conference Call for Third-Quarter Financial Results")]
    (release,) = items
    # The entry gives the date alone: the release and its figures keep date-only precision, never an inferred clock.
    assert release["url"] == Q2 and release["published_on"] == "2026-08-26" and "published_at" not in release
    common = dict(topic="NVIDIA", frequency="release", observed_at="2026-08-26",
                  retrieved_at="2026-08-27T13:00:00+00:00",
                  source_id="nvidia-ir", status="AVAILABLE", reason="", fiscal_period="FY2027-Q2",
                  release_id=release["id"],
                  baseline="as reported in NVIDIA's Q2 FY27 results release; no consensus comparison")
    assert rows == [
        dict(common, id="nvda-fy2027-q2-gaap-diluted-eps", metric="GAAP diluted EPS", value=2.46,
             unit="USD per share", basis="GAAP"),
        dict(common, id="nvda-fy2027-q2-gaap-revenue", metric="GAAP revenue", value=96221, unit="USD millions",
             basis="GAAP"),
        dict(common, id="nvda-fy2027-q2-non-gaap-diluted-eps", metric="non-GAAP diluted EPS", value=2.22,
             unit="USD per share", basis="non-GAAP"),
        dict(common, id="nvda-fy2027-q2-non-gaap-revenue", metric="non-GAAP revenue", value=96221,
             unit="USD millions", basis="non-GAAP")]
    # Margins, prior quarters, growth rates and the outlook are left in the release.
    assert {row["value"] for row in rows} == {2.46, 2.22, 96221}


@pytest.mark.parametrize("body,reason", [
    (table("GAAP", "Q3 FY27", "$11,111", "$1.11") + table("Non-GAAP", "Q3 FY27", "$11,111", "$1.01"),
     "does not state Q2 FY27"),
    # A header that differs only by a footnote marker is still refused, and the refusal names what it read.
    (table("GAAP", "Q2 FY27 (1)", "$96,221", "$2.46") + table("Non-GAAP", "Q2 FY27", "$96,221", "$2.22"),
     "does not state Q2 FY27 in ($ in millions, except earnings per share); its header reads: "
     "($ in millions, except earnings per share) | Q2 FY27 (1) | prior"),
    (table("GAAP", "Q2 FY27", "$96,221", "$2.46"), "exactly one Non-GAAP summary table"),
    (table("GAAP", "Q2 FY27", "$96,221", "$2.46") * 2 + table("Non-GAAP", "Q2 FY27", "$96,221", "$2.22"),
     "exactly one GAAP summary table"),
    (table("GAAP", "Q2 FY27", "96.2 billion", "$2.46") + table("Non-GAAP", "Q2 FY27", "$96,221", "$2.22"),
     "GAAP Revenue for Q2 FY27 does not read"),
    (table("GAAP", "Q2 FY27", "$96,221", "n/a") + table("Non-GAAP", "Q2 FY27", "$96,221", "$2.22"),
     "GAAP Diluted earnings per share for Q2 FY27 does not read"),
    ("<p>No tables at all.</p>", "exactly one GAAP summary table"),
    ("", "no body"),
])
def test_an_ambiguous_results_release_emits_no_figures_and_stays_a_disclosure(body, reason):
    release = entry("NVIDIA Announces Financial Results for Second Quarter Fiscal 2027", NEWS + "results",
                    "Wed, 26 Aug 2026 21:00:00 GMT", "Results.", body)
    items, rows, notes = parsed(feed(release), "2026-08-27T13:00:00+00:00")
    assert [item["url"] for item in items] == [NEWS + "results"] and rows == []
    (note,) = notes
    assert reason in note and note.endswith("shown as a disclosure only")


def test_negative_eps_in_parentheses_reads_as_negative():
    body = table("GAAP", "Q2 FY27", "$96,221", "($0.12)") + table("Non-GAAP", "Q2 FY27", "$96,221", "$2.22")
    release = entry("NVIDIA Announces Financial Results for Second Quarter Fiscal 2027", NEWS + "results",
                    "Wed, 26 Aug 2026 21:00:00 GMT", "Results.", body)
    _, rows, _ = parsed(feed(release), "2026-08-27T13:00:00+00:00")
    assert {row["id"]: row["value"] for row in rows}["nvda-fy2027-q2-gaap-diluted-eps"] == -0.12


# --- The live collector: exact URL, failures visible, unrelated sources unaffected ----------------------------------

def test_only_the_exact_feed_url_is_allowlisted():
    assert allowed(NVIDIA_IR)
    for url in (CEO, "https://nvidianews.nvidia.com/cats/press_release.xml?page=2", "http://" + NVIDIA_IR[8:],
                "https://nvidianews.nvidia.com/", "https://investor.nvidia.com/rss", "https://www.sec.gov/"):
        assert not allowed(url)
        with pytest.raises(SourceError, match="outside allowlist"):
            fetch(url, 0)
    handler = SameHostRedirect()
    with pytest.raises(SourceError, match="redirect outside allowlist"):
        handler.redirect_request(Request(NVIDIA_IR), None, 302, "Found", {}, CEO)
    with pytest.raises(SourceError, match="cross-host redirect"):
        handler.redirect_request(Request(NVIDIA_IR), None, 302, "Found", {}, "https://example.com/feed.xml")


@pytest.fixture
def frozen(monkeypatch):
    """Collection at a replayed instant: no Alpaca credentials, and the collector's clock reads that instant."""
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)

    def freeze(now):
        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return now if tz else now.replace(tzinfo=None)
        monkeypatch.setattr(collect, "datetime", Frozen)
    return freeze


def offline(responses, calls=None):
    """Every URL not in `responses` is offline; an Exception value is raised for its URL."""
    def fetcher(url, deadline):
        if calls is not None:
            calls.append(url)
        result = responses.get(url, SourceError("offline"))
        if isinstance(result, Exception):
            raise result
        return result
    return fetcher


def nvidia_source(raw):
    return next(source for source in raw["sources"] if source["id"] == "nvidia-ir")


@pytest.mark.parametrize("response,reason", [
    (SourceError("HTTP 503"), "HTTP 503"),
    ("<rss><channel><item>", "malformed ParseError"),
    ('<!DOCTYPE rss><rss version="2.0"><channel/></rss>', "XML declarations rejected"),
    ("<feed/>", "malformed RSS"),
])
def test_a_feed_failure_reads_unavailable_and_costs_nothing_else(frozen, response, reason):
    now = utc("2026-11-12T15:00:00+00:00")
    frozen(now)
    fed = ('<rss version="2.0"><channel><item><title>Federal Reserve Board announces something</title>'
           '<pubDate>Thu, 12 Nov 2026 14:00:00 GMT</pubDate></item></channel></rss>')
    calls = []
    raw = collect_live(now, fetcher=offline({NVIDIA_IR: response, collect.FED: fed}, calls))
    assert calls.count(NVIDIA_IR) == 1
    source = nvidia_source(raw)
    assert (source["status"], source["reason"], source["url"]) == ("UNAVAILABLE", reason, NVIDIA_IR)
    assert [item["id"] for item in raw["context_items"]] == ["fed-item-0"]
    assert next(s for s in raw["sources"] if s["id"] == "fed")["status"] == "AVAILABLE"


def test_a_live_run_admits_recent_releases_and_names_what_it_left_out(frozen):
    now = utc("2026-11-12T15:00:00+00:00")
    frozen(now)
    future = entry("Future Release", NEWS + "future-release", "Fri, 13 Nov 2026 09:00:00 GMT")
    raw = collect_live(now, fetcher=offline({NVIDIA_IR: with_entries(future)}))
    source = nvidia_source(raw)
    assert (source["status"], source["reason"]) == ("DEGRADED", "Future Release: publication after this run")
    assert source["retrieved_at"] == now.isoformat() and source["kind"] == "news"
    assert [item["url"] for item in raw["context_items"]] == [CEO, INDEPENDENT]
    packet = finalize_coverage(derive(normalize_packet(raw, now, "LIVE"), read_json(ROOT / "config/universe.json")))
    assert [item["url"] for item in packet["context_items"]] == [CEO, INDEPENDENT]
    assert "NVIDIA official press releases: Future Release: publication after this run" in packet["coverage"][
        "limitations"]


def test_failed_optional_issuer_request_runs_after_equity_snapshot(frozen, monkeypatch):
    now = utc("2026-11-12T15:00:00+00:00")
    frozen(now)
    order = []

    def equity(_now):
        order.append("alpaca snapshot")
        return dict(sources=[], observations=[], history=[])

    def fetcher(url, _deadline):
        if url == NVIDIA_IR:
            # Even if the real request consumes both 15-second attempts, the snapshot is already captured.
            order.append("NVIDIA RSS request")
            raise SourceError("network unavailable or timeout")
        raise SourceError("offline")

    monkeypatch.setattr(collect, "alpaca_collect", equity)
    raw = collect_live(now, fetcher=fetcher)
    assert order == ["alpaca snapshot", "NVIDIA RSS request"]
    assert (nvidia_source(raw)["status"], nvidia_source(raw)["reason"]) == (
        "UNAVAILABLE", "network unavailable or timeout")


# --- Normalization: the dedicated context record and issuer result rows ---------------------------------------------

NOW = utc("2026-11-12T15:00:00+00:00")
BASE_ITEM = dict(id="nvidia-ir-0123456789", title="NVIDIA Announces CEO Transition", source_id="nvidia-ir",
                 url=CEO, summary="A summary.", attribution=ATTRIBUTION, status="AVAILABLE",
                 published_at="2026-11-12T13:48:00+00:00")


@pytest.mark.parametrize("change", [
    dict(published_at=None),  # no publication at all
    dict(published_at="2026-11-12T13:48:00"),  # no time zone
    dict(published_at="2026-11-12T16:00:00+00:00"),  # future
    dict(published_at="2026-11-10T14:59:00+00:00"),  # older than two days
    dict(published_on="2026-11-12"),  # both an instant and a date
    dict(published_at=None, published_on="2026-11-13"),  # future date
    dict(published_at=None, published_on="2026-11-09"),  # stale date
    dict(published_at=None, published_on="12 Nov 2026"),  # malformed date
    dict(url="http://nvidianews.nvidia.com/news/x"), dict(url=CEO + "?a=1"), dict(url=""),
    dict(url=CEO + ">x"), dict(url=CEO + " x"),
    dict(summary="x" * 401), dict(title="x" * 401), dict(attribution="x" * 81),
])
def test_the_context_record_admits_nothing_ambiguous(change):
    assert context_record(dict(BASE_ITEM, **change), NOW, []) is None


def test_the_context_record_keeps_identity_title_summary_time_source_and_url():
    assert context_record(BASE_ITEM, NOW, []) == dict(
        id=BASE_ITEM["id"], title=BASE_ITEM["title"], source_id="nvidia-ir", published_at=BASE_ITEM["published_at"],
        checked_at=None, scheduled_at=None, status="AVAILABLE", summary="A summary.", attribution=ATTRIBUTION, url=CEO)
    dated = context_record(dict(BASE_ITEM, published_at=None, published_on="2026-11-10"), NOW, [])
    assert dated["published_on"] == "2026-11-10" and dated["published_at"] is None
    # A repeated URL and a full run are not admitted again.
    assert context_record(dict(BASE_ITEM, id="other"), NOW, [dict(url=CEO)]) is None
    assert context_record(BASE_ITEM, NOW, [{}] * CONTEXT_ITEM_LIMIT) is None


RESULT = dict(id="nvda-fy2027-q3-gaap-revenue", topic="NVIDIA", metric="GAAP revenue", value=11111,
              unit="USD millions", baseline="as reported", frequency="release",
              observed_at="2026-11-17T21:20:00+00:00", retrieved_at="2026-11-18T14:00:00+00:00",
              source_id="nvidia-ir", status="AVAILABLE", reason="", fiscal_period="FY2027-Q3", basis="GAAP",
              release_id="nvidia-ir-0123456789")
REPLAY = utc("2026-11-18T14:00:00+00:00")


def test_an_issuer_result_is_current_for_the_two_day_window_only():
    row = normalize_observation(RESULT, REPLAY)
    assert (row["status"], row["freshness"], row["fiscal_period"], row["basis"], row["release_id"]) == (
        "AVAILABLE", "DATED", "FY2027-Q3", "GAAP", "nvidia-ir-0123456789")
    assert normalize_observation(dict(RESULT, observed_at="2026-11-16T13:59:00+00:00"), REPLAY)["status"] == "STALE"
    assert normalize_observation(dict(RESULT, observed_at="2026-11-18T14:01:00+00:00"), REPLAY)["status"] == "INVALID"
    dated = normalize_observation(dict(RESULT, observed_at="2026-11-17"), REPLAY)
    assert (dated["status"], dated["observed_at"]) == ("AVAILABLE", "2026-11-17")
    assert normalize_observation(dict(RESULT, observed_at="2026-11-15"), REPLAY)["status"] == "STALE"
    assert normalize_observation(dict(RESULT, observed_at="2026-11-19"), REPLAY)["status"] == "INVALID"


@pytest.mark.parametrize("change", [dict(fiscal_period="2027-Q3"), dict(basis="adjusted"), dict(release_id=""),
                                    dict(reference_period="2026-10")])
def test_an_issuer_result_without_its_period_basis_or_release_is_invalid(change):
    assert normalize_observation(dict(RESULT, **change), REPLAY)["status"] == "INVALID"


# --- The analyst context: preserved, bounded, citable, rendered -----------------------------------------------------

def nvidia_packet(now, checkpoint, feed_text=FEED, fed_items=(), last_history_date="2026-09-04"):
    """The sample evidence (history ending `last_history_date`) plus what the collector admits from `feed_text`."""
    items, rows, _ = parsed(feed_text, now.isoformat())
    source = dict(id="nvidia-ir", name="NVIDIA official press releases", kind="news", url=NVIDIA_IR,
                  retrieved_at=now.isoformat(), status="AVAILABLE", reason="", llm_allowed=True,
                  retention_allowed=True, coverage_date=None)
    return packet_at(now, last_history_date=last_history_date, checkpoint=checkpoint, sources=[source],
                     observations=rows, context_items=[*items, *fed_items])


def worst_case(now):
    """Four releases at the title and summary bounds, a results release, and six long Fed items."""
    body = table("GAAP", "Q3 FY27", "$11,111", "$1.11") + table("Non-GAAP", "Q3 FY27", "$11,111", "$1.01")
    entries = [entry("NVIDIA Announces Financial Results for Third Quarter Fiscal 2027", NEWS + "results",
                     (now - timedelta(hours=1)).strftime("%a, %d %b %Y %H:%M:%S GMT"), "word " * 100, body)]
    entries += [entry("Statement " * 25, NEWS + f"statement-{n}",
                      (now - timedelta(hours=2 + n)).strftime("%a, %d %b %Y %H:%M:%S GMT"), "word " * 100)
                for n in range(3)]
    fed = [dict(id=f"fed-item-{n}", title="Federal Reserve " * 25, source_id="fed-calendar",
                published_at=(now - timedelta(hours=n + 1)).isoformat(), status="AVAILABLE") for n in range(6)]
    return feed(*entries), fed


@pytest.mark.parametrize("now,checkpoint,light", [("2026-09-08T12:45:00+00:00", "PREMARKET", False),
                                                  ("2026-09-08T14:05:00+00:00", "OPEN_30M", False),
                                                  ("2026-09-08T14:05:00+00:00", "OPEN_30M", True)])
def test_every_edition_keeps_dated_issuer_evidence_whole_and_within_budget(now, checkpoint, light):
    now = utc(now)
    text, fed = worst_case(now)
    packet = nvidia_packet(now, checkpoint, text, fed)
    profile = light_profile() if light else edition_profile(checkpoint)
    context = analyst_context(packet, profile)
    items = [item for item in context["context_items"] if item["source_id"] == "nvidia-ir"]
    assert len(items) == 4 and len(context["context_items"]) == 10
    assert set(items[0]) == {"id", "title", "summary", "attribution", "published_at", "source_id", "url"}
    results = [row for group in context["catalog"] if group["topic"] == "NVIDIA" for row in group["rows"]]
    assert {row["id"] for row in results} == {"nvda-fy2027-q3-gaap-revenue", "nvda-fy2027-q3-gaap-diluted-eps",
                                             "nvda-fy2027-q3-non-gaap-revenue",
                                             "nvda-fy2027-q3-non-gaap-diluted-eps"}
    assert all(row["fiscal_period"] == "FY2027-Q3" and row["release_id"] == items[0]["id"] for row in results)
    assert {item["id"] for item in context["context_items"]} | {row["id"] for row in results} <= supplied_ids(context)
    _, user = construct_prompt(packet, context=context)
    assert len(user.encode()) <= profile["input_limit_bytes"]


def test_the_model_packet_carries_the_item_record_not_the_measurement_fields():
    now = utc("2026-09-08T12:45:00+00:00")
    text, _ = worst_case(now)
    packet = nvidia_packet(now, "PREMARKET", text)
    (stored, *_) = [item for item in packet["context_items"] if item["source_id"] == "nvidia-ir"]
    projected = next(item for item in model_packet(packet)["context_items"] if item["id"] == stored["id"])
    assert projected == {key: stored[key] for key in ("id", "title", "summary", "attribution", "published_at",
                                                      "source_id", "url", "status")}
    # No issuer row is a return, a consensus comparison or an after-hours print.
    rows = [row for row in packet["observations"] if row["source_id"] == "nvidia-ir"]
    assert rows and all(row["frequency"] == "release" and "return" not in row["metric"] for row in rows)


def test_a_cited_release_and_its_figures_validate_and_render_with_their_provenance():
    now = utc("2026-08-27T12:45:00+00:00")
    packet = nvidia_packet(now, "PREMARKET", last_history_date="2026-08-26")
    profile = edition_profile("PREMARKET")
    context = analyst_context(packet, profile)
    (release,) = [item for item in context["context_items"] if item["source_id"] == "nvidia-ir"]
    assert release["published_on"] == "2026-08-26" and "published_at" not in release
    # The sample narrative's numeric placeholders cite September rows this August packet does not hold.
    value = json.loads(PLACEHOLDER.sub("the cited level", json.dumps(edition_response(profile, context))))
    value["summary"][0]["evidence_ids"] += [release["id"], "nvda-fy2027-q2-gaap-revenue"]
    assert validate_narrative(value, packet, context)
    markdown, page = render(packet, value, context)
    assert f'href="{Q2}"' in page and "NVIDIA statement: NVIDIA (NASDAQ: NVDA) today reported revenue" in page
    assert "$96,221M" in page and "$2.46" in page and "Non-GAAP diluted EPS" in page
    assert "Wed, Aug 26" in page  # date-only precision, never a clock
    assert f"[release](<{Q2}>)" in markdown
    # The figures are not an economic release card or a macro fact.
    assert "Economic release" not in page


# --- The Nov 18 PREMARKET replay: deterministic, offline, no model, nothing published -------------------------------

def replay(frozen, now, checkpoint):
    now = utc(now)
    frozen(now)
    raw = collect_live(now, fetcher=offline({NVIDIA_IR: with_entries(MOCK_Q3_RESULTS)}))
    packet = finalize_coverage(derive(normalize_packet(raw, now, "LIVE", checkpoint),
                                      read_json(ROOT / "config/universe.json")))
    return packet, analyst_context(packet, edition_profile(checkpoint))


@pytest.mark.parametrize("now,checkpoint", [("2026-11-18T14:00:00+00:00", "PREMARKET"),
                                            ("2026-11-18T15:00:00+00:00", "OPEN_30M")])
def test_nov_18_replay_reaches_both_syntheses_with_reported_actuals_only(frozen, now, checkpoint):
    packet, context = replay(frozen, now, checkpoint)
    mock_id = item_id(MOCK_URL)
    # Only the (mocked) Nov 17 release is inside the window: Nov 12, Nov 10, Nov 8, Oct 28 and Aug 26 are older.
    assert [item["url"] for item in packet["context_items"]] == [MOCK_URL]
    assert context["context_items"] == [dict(
        id=mock_id, title="NVIDIA Announces Financial Results for Third Quarter Fiscal 2027",
        summary="TEST-ONLY MOCK replay input, not an NVIDIA publication: synthetic figures for the Nov 18 replay.",
        attribution=ATTRIBUTION, published_at="2026-11-17T21:20:00+00:00", source_id="nvidia-ir", url=MOCK_URL)]
    (group,) = [group for group in context["catalog"] if group["topic"] == "NVIDIA"]
    assert {row["id"]: (row["value"], row["unit"], row["basis"], row["fiscal_period"], row["observed_at"])
            for row in group["rows"]} == {
        "nvda-fy2027-q3-gaap-revenue": (11111, "USD millions", "GAAP", "FY2027-Q3", "2026-11-17T21:20:00+00:00"),
        "nvda-fy2027-q3-non-gaap-revenue": (11111, "USD millions", "non-GAAP", "FY2027-Q3",
                                            "2026-11-17T21:20:00+00:00"),
        "nvda-fy2027-q3-gaap-diluted-eps": (1.11, "USD per share", "GAAP", "FY2027-Q3", "2026-11-17T21:20:00+00:00"),
        "nvda-fy2027-q3-non-gaap-diluted-eps": (1.01, "USD per share", "non-GAAP", "FY2027-Q3",
                                                "2026-11-17T21:20:00+00:00")}
    assert all(row["release_id"] == mock_id for row in group["rows"])
    # No after-hours return, price reaction, consensus or derived figure enters from the issuer source.
    issuer = [row for row in [*packet["observations"], *packet["derived"]] if row["source_id"] == "nvidia-ir"]
    assert len(issuer) == 4 and not [row for row in packet["observations"] if row["topic"] == "NVDA"]
    text = json.dumps(context).lower()
    assert "consensus" not in text.replace("no consensus comparison", "") and "after-hours" not in text
    _, user = construct_prompt(packet, context=context)
    assert len(user.encode()) <= edition_profile(checkpoint)["input_limit_bytes"]

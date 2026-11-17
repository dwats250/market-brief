"""WP-MB-ISSUER-01: NVIDIA's release URL bound and its announced results call as an upcoming event.

Offline. The conference-call facts are those of NVIDIA's official October 28, 2026 announcement
(https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Sets-Conference-Call-for-Third-Quarter-Financial-Results/):
a call on Tuesday, November 17, 2026 at 2 p.m. PT (5 p.m. ET); results publicly announced at approximately 1:20 p.m.
PT with written CFO commentary posted immediately after. The fixture holds only two quoted excerpts joined by "…", so
the sentence that ties the commentary to that time is not in it and the note states the results time alone; the tie
is tested on TEST-ONLY synthetic wording. No results, consensus or exact publication time is assumed.
"""

import json

import pytest
from profiles import light_profile
from test_contract import edition_response
from test_history_admission import packet_at
from test_nvidia_issuer import FEED, NEWS, SCHEDULE, entry, feed, frozen, nvidia_source, offline, utc  # noqa: F401

from market_brief.collect import NVIDIA_IR, collect_live, xml_root
from market_brief.context import analyst_context, edition_profile
from market_brief.evidence import PLACEHOLDER, ROOT, finalize_coverage, model_packet, normalize_packet, read_json
from market_brief.issuer import ARTICLE, SLUG_LIMIT, conference_calls, nvidia_releases
from market_brief.metrics import derive
from market_brief.render import render
from market_brief.synthesize import validate_narrative

# The release that runs 43945376875 (HOURLY_1500) and 43955731092 (CLOSE_1M) marked Degraded on 2026-11-16: its exact
# newsroom slug, 201 characters.
LONG_SLUG = ("nvidia-announces-collaboration-with-ibm-and-fermi-national-accelerator-laboratory-to-launch-quantum-"
             "classical-codesign-platform-plus-error-correction-breakthrough-to-advance-practical-quantum-computing")
LONG = NEWS + LONG_SLUG
CALL_ID = "nvidia-ir-call-fy2027-q3"
CALL_SENTENCE = ("NVIDIA will host a conference call on Tuesday, November 17, at 2 p.m. PT (5 p.m. ET) to discuss its "
                 "financial results for the third quarter of fiscal year 2027, which ended October 25, 2026.")
TIMING = ("&lt;p&gt;Ahead of the call, NVIDIA will provide written commentary on its third-quarter results from "
          "Colette Kress, … immediately after the company's results are publicly announced at approximately 1:20 p.m. "
          "PT.&lt;/p&gt;")
CALL_TITLE = "NVIDIA Sets Conference Call for Third-Quarter Financial Results"


def calls(text, now):
    return conference_calls(xml_root(text), utc(now), utc(now))


# --- The release URL bound ------------------------------------------------------------------------------------------

def test_the_201_character_release_slug_is_admitted_and_the_bound_stays_finite():
    assert len(LONG_SLUG) == SLUG_LIMIT == 201
    assert ARTICLE.fullmatch(LONG)
    assert not ARTICLE.fullmatch(LONG + "a")  # 202: still bounded


@pytest.mark.parametrize("url", [
    "http://nvidianews.nvidia.com/news/a-release",                     # plain HTTP
    "https://nvidianews.nvidia.com.example.com/news/a-release",        # lookalike host
    "https://investor.nvidia.com/news/a-release",                      # other NVIDIA host
    "https://example.com/news/a-release",                              # wrong host
    "https://user:secret@nvidianews.nvidia.com/news/a-release",        # credentials
    "https://user@nvidianews.nvidia.com/news/a-release",
    "https://nvidianews.nvidia.com:8443/news/a-release",               # port
    "https://nvidianews.nvidia.com/news/a-release?utm=1",              # query
    "https://nvidianews.nvidia.com/news/a-release#top",                # fragment
    "https://nvidianews.nvidia.com/news/a-release/",                   # trailing segment
    "https://nvidianews.nvidia.com/news/a/release",                    # second path segment
    "https://nvidianews.nvidia.com/news/../cats/press_release.xml",    # traversal
    "https://nvidianews.nvidia.com/newsroom/a-release",                # wrong path
    "https://nvidianews.nvidia.com/news/",                             # no slug
    "https://nvidianews.nvidia.com/news/-a-release",                   # leading hyphen
    "https://nvidianews.nvidia.com/news/a-release-",                   # trailing hyphen
    "https://nvidianews.nvidia.com/news/A-Release",                    # uppercase
    "https://nvidianews.nvidia.com/news/a%2Frelease",                  # encoding
    "https://nvidianews.nvidia.com/news/a release",                    # whitespace
    "https://nvidianews.nvidia.com/news/a-relеase",                    # Cyrillic e
    "javascript:alert(1)//nvidianews.nvidia.com/news/a-release",
    NEWS + "a" * 202,                                                  # over the bound
])
def test_malformed_unsafe_or_untrusted_release_links_are_refused_and_named(url):
    assert not ARTICLE.fullmatch(url)
    bad = entry("Untrusted", url.replace("&", "&amp;"), "Mon, 16 Nov 2026 13:00:00 GMT")
    items, _, notes = nvidia_releases(xml_root(feed(bad)), utc("2026-11-16T15:00:00+00:00"),
                                      utc("2026-11-16T15:00:00+00:00"))
    assert items == [] and notes == ["Untrusted: entry link is not an NVIDIA newsroom release URL"]


def test_a_live_run_admits_the_201_character_release_and_stays_available(frozen):  # noqa: F811
    now = utc("2026-11-16T20:00:00+00:00")
    frozen(now)
    long = entry("IBM and NVIDIA Release", LONG, "Mon, 16 Nov 2026 13:00:00 GMT", "An official release.")
    raw = collect_live(now, fetcher=offline({NVIDIA_IR: feed(long)}))
    assert [item["url"] for item in raw["context_items"]] == [LONG]
    source = nvidia_source(raw)
    assert source["status"] == "AVAILABLE" and source["reason"] == ""


# --- The announced results call -------------------------------------------------------------------------------------

def test_the_official_notice_yields_the_call_at_the_instant_nvidia_states():
    (event,), notes = calls(FEED, "2026-11-16T20:00:00+00:00")
    assert notes == []
    assert event == dict(
        id=CALL_ID, title="NVIDIA third-quarter fiscal 2027 results conference call",
        # 2 p.m. US Pacific (PST, UTC-8) is 5 p.m. Eastern (EST, UTC-5): 22:00 UTC.
        scheduled_at="2026-11-17T22:00:00+00:00", stated_time="2 p.m. PT (5 p.m. ET)", source_id="nvidia-ir",
        published_at=None, checked_at="2026-11-16T20:00:00+00:00", status="SCHEDULED", url=SCHEDULE,
        # The excerpts do not hold the sentence that ties the CFO commentary to this time, so no tie is stated.
        note="NVIDIA says results are publicly announced at approximately 1:20 p.m. PT; no exact publication time is "
             "given.",
        announced_on="2026-10-28")


def test_without_the_timing_sentences_the_call_stands_and_the_results_time_is_not_claimed():
    notice = entry(CALL_TITLE, SCHEDULE, "Wed, 28 Oct 2026 21:00:00 GMT", CALL_SENTENCE)
    (event,), notes = calls(feed(notice), "2026-11-16T20:00:00+00:00")
    assert notes == [] and event["note"] == "The results time is not read from this feed entry."
    assert "1:20" not in json.dumps(event)


@pytest.mark.parametrize("description,reason", [
    (CALL_SENTENCE.replace("Tuesday", "Wednesday"), "day and weekday disagree"),
    (CALL_SENTENCE.replace("(5 p.m. ET)", "(4 p.m. ET)"), "Pacific and Eastern times disagree"),
    (CALL_SENTENCE.replace("third quarter", "second quarter"), "names two quarters"),
    (CALL_SENTENCE.replace("at 2 p.m. PT", "at 13 p.m. PT"), "time does not exist"),
    (CALL_SENTENCE.replace("November 17", "November 31"), "day does not exist"),
    ("NVIDIA will host a conference call soon.", "does not state its day, time and quarter"),
])
def test_a_notice_that_does_not_read_unambiguously_is_named_and_yields_no_event(description, reason):
    notice = entry(CALL_TITLE, SCHEDULE, "Wed, 28 Oct 2026 21:00:00 GMT", description)
    events, (note,) = calls(feed(notice), "2026-11-16T20:00:00+00:00")
    assert events == [] and note.startswith(f"{CALL_TITLE}: conference call") and reason in note


def test_a_notice_on_an_untrusted_link_or_far_from_its_call_is_refused():
    other = entry(CALL_TITLE, "https://investor.nvidia.com/news/call", "Wed, 28 Oct 2026 21:00:00 GMT",
                  CALL_SENTENCE)
    events, notes = calls(feed(other), "2026-11-16T20:00:00+00:00")
    assert events == [] and notes == [f"{CALL_TITLE}: entry link is not an NVIDIA newsroom release URL"]
    early = entry(CALL_TITLE, SCHEDULE, "Fri, 10 Jul 2026 21:00:00 GMT", CALL_SENTENCE)
    events, notes = calls(feed(early), "2026-11-16T20:00:00+00:00")
    assert events == [] and notes == [f"{CALL_TITLE}: conference call is too far from its notice"]


def test_a_past_call_and_a_future_notice_yield_nothing():
    # Upcoming only: at 2 p.m. PT (22:00 UTC) on Nov 17 the call is no longer listed, nor on any later run.
    assert [e["id"] for e in calls(FEED, "2026-11-17T21:59:00+00:00")[0]] == [CALL_ID]
    for now in ("2026-11-17T22:00:00+00:00", "2026-11-17T22:30:00+00:00", "2026-11-18T14:00:00+00:00"):
        assert calls(FEED, now) == ([], [])
    # A run before the notice was published never reads it early.
    assert calls(FEED, "2026-10-28T20:00:00+00:00") == ([], [])


def nov16(frozen, now="2026-11-16T21:01:00+00:00", checkpoint="CLOSE_1M", text=FEED):  # noqa: F811
    now = utc(now)
    frozen(now)
    raw = collect_live(now, fetcher=offline({NVIDIA_IR: text}))
    return raw, finalize_coverage(derive(normalize_packet(raw, now, "LIVE", checkpoint),
                                         read_json(ROOT / "config/universe.json")))


def nvidia_events(packet):
    return [e for e in packet["events"] if e["source_id"] == "nvidia-ir"]


# The archived Nov 16 clocks: OPEN_30M around 15:01Z and CLOSE_1M around 21:01Z; Nov 17's PREMARKET at 14:00Z.
@pytest.mark.parametrize("now,checkpoint", [("2026-11-16T15:01:00+00:00", "OPEN_30M"),
                                            ("2026-11-16T20:00:00+00:00", "HOURLY_1500"),
                                            ("2026-11-16T21:01:00+00:00", "CLOSE_1M")])
def test_the_call_is_upcoming_next_session_on_every_nov_16_page(frozen, now, checkpoint):  # noqa: F811
    raw, packet = nov16(frozen, now, checkpoint)
    assert nvidia_source(raw)["status"] == "AVAILABLE"
    (event,) = nvidia_events(packet)
    assert (event["id"], event["session_relation"], event["session_date"]) == (CALL_ID, "NEXT SESSION", "2026-11-17")
    assert event["stated_time"] == "2 p.m. PT (5 p.m. ET)" and event["url"] == SCHEDULE


def test_on_nov_17_the_call_is_today_until_its_time_passes(frozen):  # noqa: F811
    for now, checkpoint in (("2026-11-17T14:00:00+00:00", "PREMARKET"), ("2026-11-17T21:01:00+00:00", "CLOSE_1M")):
        (event,) = nvidia_events(nov16(frozen, now, checkpoint)[1])
        assert event["session_date"] == "2026-11-17" and event["session_relation"] == "AFTER CLOSE"
    assert nvidia_events(nov16(frozen, "2026-11-17T22:01:00+00:00", "CLOSE_1M")[1]) == []
    assert nvidia_events(nov16(frozen, "2026-11-18T14:00:00+00:00", "PREMARKET")[1]) == []


def test_calendar_events_keep_the_shared_horizon():
    """Only an issuer-announced event (one with a stated clock) reaches the session after today during the session; a
    BLS release dated Nov 17 is still not admitted on Nov 16's opening page."""
    now = utc("2026-11-16T15:01:00+00:00")
    (call,), _ = calls(FEED, now.isoformat())
    bls = dict(id="bls-event-0", title="Consumer Price Index for October 2026", source_id="nvidia-ir",
               published_at=None, checked_at=now.isoformat(), scheduled_at="2026-11-17T13:30:00+00:00",
               status="SCHEDULED")
    source = dict(id="nvidia-ir", name="NVIDIA official press releases", kind="news", url=NVIDIA_IR,
                  retrieved_at=now.isoformat(), status="AVAILABLE", reason="", llm_allowed=True,
                  retention_allowed=True, coverage_date=None)
    packet = packet_at(now, last_history_date="2026-11-13", checkpoint="OPEN_30M", sources=[source],
                       events=[bls, call])
    assert [e["id"] for e in packet["events"] if e["id"] in (CALL_ID, "bls-event-0")] == [CALL_ID]


def test_the_analyst_reads_the_event_record_only_never_the_note_or_url(frozen):  # noqa: F811
    _, packet = nov16(frozen, "2026-11-17T14:00:00+00:00", "PREMARKET")
    (projected,) = [e for e in model_packet(packet)["events"] if e["id"] == CALL_ID]
    assert set(projected) == {"id", "title", "scheduled_at", "session_relation", "source_id", "status"}
    for profile in (edition_profile("PREMARKET"), light_profile()):
        context = analyst_context(packet, profile)
        assert [e["id"] for e in context["events"] if e["source_id"] == "nvidia-ir"] == [CALL_ID]
        assert "Vancouver" not in json.dumps(context) and "1:20" not in json.dumps(context)


@pytest.mark.parametrize("change", [dict(url="http://nvidianews.nvidia.com/news/x"), dict(note="x" * 241),
                                    dict(stated_time=""), dict(announced_on="Oct 28"), dict(url=7)])
def test_malformed_event_extras_fail_closed(change):
    now = utc("2026-11-16T20:00:00+00:00")
    (event,), _ = calls(FEED, now.isoformat())
    raw = dict(mode="LIVE", sources=[dict(id="nvidia-ir", name="NVIDIA official press releases", kind="news",
                                          url=NVIDIA_IR, retrieved_at=now.isoformat(), status="AVAILABLE",
                                          reason="", retention_allowed=True, llm_allowed=True,
                                          expected_freshness="DATED")],
               events=[dict(event, **change)])
    with pytest.raises(ValueError, match="malformed event"):
        normalize_packet(raw, now, "LIVE", "HOURLY_1500")


def nvidia_raw(now, event):
    source = dict(id="nvidia-ir", name="NVIDIA official press releases", kind="news", url=NVIDIA_IR,
                  retrieved_at=now.isoformat(), status="AVAILABLE", reason="", retention_allowed=True,
                  llm_allowed=True, expected_freshness="DATED")
    other = dict(source, id="bls", name="BLS calendar", url="https://www.bls.gov/schedule/news_release/bls.ics")
    return dict(mode="LIVE", sources=[source, other], events=[event])


@pytest.mark.parametrize("url", ["https://evil.example/path", "https://evil.example/news/nvidia-sets-call",
                                 "https://nvidianews.nvidia.com.evil.example/news/a-release",
                                 "https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Sets-Call/",
                                 "https://nvidianews.nvidia.com/cats/press_release.xml",
                                 "https://nvidianews.nvidia.com/news/a/b", NEWS + "a" * 202])
def test_an_event_url_must_be_an_exact_nvidia_newsroom_release(url):
    now = utc("2026-11-16T21:01:00+00:00")
    (event,), _ = calls(FEED, now.isoformat())
    with pytest.raises(ValueError, match="malformed event url"):
        normalize_packet(nvidia_raw(now, dict(event, url=url)), now, "LIVE", "CLOSE_1M")


def test_only_the_nvidia_source_may_carry_a_linked_url_or_stated_clock():
    now = utc("2026-11-16T21:01:00+00:00")
    (event,), _ = calls(FEED, now.isoformat())
    with pytest.raises(ValueError, match="only the nvidia-ir source carries it"):
        normalize_packet(nvidia_raw(now, dict(event, source_id="bls")), now, "LIVE", "CLOSE_1M")
    bare = {k: v for k, v in event.items() if k not in ("url", "note", "stated_time", "announced_on")}
    with pytest.raises(ValueError, match="only the nvidia-ir source carries it"):
        normalize_packet(nvidia_raw(now, dict(bare, source_id="bls", url=SCHEDULE)), now, "LIVE", "CLOSE_1M")
    # A calendar event without the fields is unchanged.
    (plain,) = normalize_packet(nvidia_raw(now, dict(bare, source_id="bls")), now, "LIVE", "CLOSE_1M")["events"]
    assert "url" not in plain and "stated_time" not in plain


def test_nvidias_valid_release_url_is_retained():
    now = utc("2026-11-16T21:01:00+00:00")
    (event,), _ = calls(FEED, now.isoformat())
    (kept,) = normalize_packet(nvidia_raw(now, event), now, "LIVE", "CLOSE_1M")["events"]
    assert kept["url"] == SCHEDULE and kept["stated_time"] == "2 p.m. PT (5 p.m. ET)"


# TEST-ONLY synthetic wording around the two quoted fragments: not NVIDIA's text.
CFO = "Ahead of the call, NVIDIA will provide written commentary on its third-quarter results from its CFO."
TIED = ("This material will be posted to the investor site immediately after the company's results are publicly "
        "announced at approximately 1:20 p.m. PT.")
TIME_ONLY = "The company's results are publicly announced at approximately 1:20 p.m. PT."


@pytest.mark.parametrize("body,tied", [
    (CFO + " " + TIED, True),                                     # the source ties commentary to the time
    (CFO + " " + TIME_ONLY, False),                               # relation omitted
    (TIME_ONLY + " " + CFO, False),                               # commentary after the time, relation absent
    (TIED.replace("This material", "A replay") + " " + CFO, False),  # the tie belongs to something else
    (CFO + " The webcast will be posted immediately after the call. " + TIME_ONLY, False),
    (CFO.replace("third", "second") + " " + TIED, False),         # commentary for another quarter
])
def test_the_cfo_commentary_timing_is_stated_only_when_the_source_ties_it(body, tied):
    notice = entry(CALL_TITLE, SCHEDULE, "Wed, 28 Oct 2026 21:00:00 GMT", CALL_SENTENCE,
                   "&lt;p&gt;" + body.replace("'", "&#39;") + "&lt;/p&gt;")
    (event,), _ = calls(feed(notice), "2026-11-16T20:00:00+00:00")
    assert event["note"].startswith("NVIDIA says results are publicly announced at approximately 1:20 p.m. PT")
    assert ("written CFO commentary posted immediately after" in event["note"]) is tied


def rendered(now="2026-11-17T14:00:00+00:00", collected=None):
    """The sample evidence plus the call as the collector emits it (the narrative fixture is a SAMPLE)."""
    now = utc(now)
    (event,), _ = calls(FEED, collected or now.isoformat())
    source = dict(id="nvidia-ir", name="NVIDIA official press releases", kind="news", url=NVIDIA_IR,
                  retrieved_at=now.isoformat(), status="AVAILABLE", reason="", llm_allowed=True,
                  retention_allowed=True, coverage_date=None)
    packet = packet_at(now, last_history_date="2026-11-16", sources=[source], events=[event])
    profile = edition_profile("PREMARKET")
    context = analyst_context(packet, profile)
    value = json.loads(PLACEHOLDER.sub("the cited level", json.dumps(edition_response(profile, context))))
    assert validate_narrative(value, packet, context)
    return render(packet, value, context)


def test_the_event_renders_the_source_clock_verbatim_and_vancouver_time_under_its_own_name():
    markdown, page = rendered()
    assert ("<b>NVIDIA third-quarter fiscal 2027 results conference call</b>Tuesday, Nov 17 · 2 p.m. PT (5 p.m. ET)"
            " · after close") in page
    assert "<small>3:00 PM Vancouver time. NVIDIA says results are publicly announced at approximately 1:20 p.m. PT"\
           in page
    assert f'Source: <a href="{SCHEDULE}">NVIDIA release, Oct 28</a></small>' in page
    # The instant is never relabelled as the page's Pacific clock.
    assert "3:00 PM PT" not in page and "2:00 PM PT" not in page
    assert ("- **Event** — NVIDIA third-quarter fiscal 2027 results conference call · Tuesday, Nov 17 · "
            "2 p.m. PT (5 p.m. ET) · after close") in markdown
    assert "  3:00 PM Vancouver time. NVIDIA says results" in markdown
    assert f"Source: [NVIDIA release, Oct 28]({SCHEDULE})" in markdown
    # No results, consensus or exact publication time is invented.
    start = page.index("<b>NVIDIA third-quarter")
    line = page[start:page.index("</li>", start)]
    for text in (line, "\n".join(row for row in markdown.splitlines() if "NVIDIA" in row)):
        assert "consensus" not in text.lower() and "estimate" not in text.lower()
        assert "1:20 PM" not in text and "approximately 1:20 p.m. PT" in text


def test_a_call_carried_past_its_time_says_only_that_it_passed():
    """Defensive: the collector stops listing a passed call, but an event held past its time never claims values."""
    _, page = rendered("2026-11-17T22:30:00+00:00", collected="2026-11-17T21:59:00+00:00")
    line = page[page.index("results conference call</b>"):]
    line = line[:line.index("</li>")]
    assert "scheduled time passed" in line and "values not collected" not in line


def test_a_feed_without_the_notice_shows_no_call_and_coverage_stays_explicit(frozen):  # noqa: F811
    raw, packet = nov16(frozen, text=feed(entry("Other", NEWS + "other", "Mon, 16 Nov 2026 13:00:00 GMT")))
    assert not [e for e in packet["events"] if e["source_id"] == "nvidia-ir"]
    raw, packet = nov16(frozen, text="<feed/>")
    assert nvidia_source(raw)["status"] == "UNAVAILABLE"
    assert any(limit.startswith("NVIDIA official press releases: ") for limit in packet["coverage"]["limitations"])

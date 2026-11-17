"""WP-MB-STATE-01: a deterministic notice when current prints diverge materially from the analysis a page carries.

Scenario values follow the verified 2026-11-16 production findings: OPEN_30M (about 15:01Z) 11/11 sector ETFs, 6 lower
and 5 higher, median −0.256%, SPY −1.1262545242%, QQQ −2.011%; HOURLY_1500 (about 20:00Z) 11/11, 10 lower and 1 higher,
median −1.293%, SPY −1.8799634083%, XLB's print DELAYED; CLOSE_1M (20:59:54Z–20:59:59Z) 10/11 with XLI refused as
after-hours, 8 lower and 2 higher, median −1.117%, SPY −1.887%, QQQ refused as after-hours. Each sector ETF's value is
a test-only choice that reproduces those counts and medians; QQQ at HOURLY_1500 is test-only.
"""

import copy
import json
import re
from datetime import timedelta
from statistics import median

import pytest
from test_history_admission import packet_at, utc
from test_pipeline import narrative

from market_brief.continuity import interpretation_record
from market_brief.evidence import SECTOR_ETFS, digest, finalize_coverage, normalize_observation
from market_brief.render import presentation, render
from market_brief.state import (
    BENCHMARK_SHIFT,
    MEDIAN_SHIFT,
    MIN_SEPARATION,
    SECTOR_FLOOR,
    SHARE_SHIFT,
    SNAPSHOT_SPREAD,
    divergence,
    snapshot,
)

DAY = "2026-11-16"
PRIOR = "2026-11-13"
OPEN_CLOCK, OPEN_NOW = f"{DAY}T15:01:00+00:00", f"{DAY}T15:01:30+00:00"
HOUR_CLOCK, HOUR_NOW = f"{DAY}T20:00:00+00:00", f"{DAY}T20:00:30+00:00"
CLOSE_NOW = f"{DAY}T21:01:00+00:00"
SECTORS = sorted(SECTOR_ETFS)
OPENING = dict(zip(SECTORS, (-0.9, -0.7, -0.5, -0.4, -0.3, -0.256, 0.1, 0.2, 0.3, 0.4, 0.5)),
               SPY=-1.1262545242, QQQ=-2.011)
HOURLY = dict(zip(SECTORS, (-2.1, -1.9, -1.7, -1.5, -1.4, -1.293, -1.0, -0.8, -0.5, -0.2, 0.3)),
              SPY=-1.8799634083, QQQ=-2.4)
CLOSING = dict(zip([s for s in SECTORS if s != "XLI"], (-2.0, -1.8, -1.5, -1.3, -1.117, -1.117, -0.9, -0.4, 0.2, 0.4)),
               SPY=-1.887)
AFTER_HOURS = {"XLI": -1.2, "QQQ": -2.3}


def prints(now, checkpoint, values, clock=None, clocks=None, prior=PRIOR, after_hours=None):
    """The sample packet on Nov 16 with these current prints admitted through the real normalizer."""
    now = utc(now)
    close = utc(f"{DAY}T21:00:00+00:00")
    packet = packet_at(now, last_history_date=PRIOR, checkpoint=checkpoint, intraday=False)
    rows = [(symbol, value, (clocks or {}).get(symbol, clock)) for symbol, value in values.items()]
    rows += [(symbol, value, f"{DAY}T21:00:30+00:00") for symbol, value in (after_hours or {}).items()]
    for symbol, value, observed in rows:
        raw = dict(id=f"{symbol}-intraday", topic=symbol, metric="premarket return", value=value, unit="%",
                   baseline="Alpaca IEX latest trade versus previous regular close", frequency="intraday",
                   observed_at=observed, retrieved_at=now.isoformat(), source_id="sample-prices",
                   status="AVAILABLE", reason=f"feed=IEX; latest trade timestamp; prior close {prior} regular session")
        row = normalize_observation(raw, now, close if checkpoint == "CLOSE_1M" else None, close)
        row["expected_freshness"] = "LIVE"
        packet["observations"] = [r for r in packet["observations"] if r["id"] != row["id"]] + [row]
    packet["run"]["run_id"] = f"live-{checkpoint.lower()}"
    return finalize_coverage(packet)


def opening_record(values=OPENING, **kwargs):
    packet = prints(OPEN_NOW, "OPEN_30M", values, OPEN_CLOCK, **kwargs)
    return interpretation_record(packet, narrative())


# At HOURLY_1500 XLB's latest trade was more than a minute old: admitted as DELAYED, never compared.
DELAYED_XLB = {"XLB": f"{DAY}T19:58:00+00:00"}


def hourly(values=HOURLY, clocks=DELAYED_XLB, **kwargs):
    return prints(HOUR_NOW, "HOURLY_1500", values, HOUR_CLOCK, clocks=clocks, **kwargs)


def closing():
    clocks = {symbol: f"{DAY}T20:59:{54 + n % 6:02d}+00:00" for n, symbol in enumerate(CLOSING)}
    return prints(CLOSE_NOW, "CLOSE_1M", CLOSING, clocks=clocks, after_hours=AFTER_HOURS)


# --- The rule ---------------------------------------------------------------------------------------------------------

def test_the_thresholds_are_conservative_and_stated():
    assert (SECTOR_FLOOR, MEDIAN_SHIFT, BENCHMARK_SHIFT, SHARE_SHIFT) == (9, 0.50, 0.50, 0.25)
    assert (SNAPSHOT_SPREAD, MIN_SEPARATION) == (timedelta(minutes=15), timedelta(minutes=60))


def test_nov_16_opening_to_afternoon_compares_the_ten_live_sector_etfs():
    record = opening_record()
    state = record["market_state"]
    assert set(state["rows"]) == SECTOR_ETFS | {"SPY", "QQQ"}
    assert {r["prior_close"] for r in state["rows"].values()} == {PRIOR}
    current = snapshot(hourly())
    # XLB's DELAYED print never enters a confident comparison; the other ten are enough.
    assert "XLB" not in current["rows"] and len(SECTOR_ETFS & set(current["rows"])) == 10
    result = divergence(state, current)
    assert (result["status"], result["direction"], result["kind"]) == ("shown", "lower", "broadening")
    assert result["cohort"] == sorted(SECTOR_ETFS - {"XLB"})
    before, after = result["before"], result["after"]
    # Both sides on the same ten: 5 of 10 lower then, 9 of 10 now; SPY −1.126% → −1.880% (−0.754 pp).
    assert (before["n"], before["down"], before["up"], round(before["median"], 3)) == (10, 5, 5, -0.078)
    assert (after["n"], after["down"], after["up"], round(after["median"], 4)) == (10, 9, 1, -1.1465)
    assert round(after["spy"] - before["spy"], 3) == -0.754


def test_with_all_eleven_live_the_nov_16_counts_are_the_verified_ones():
    result = divergence(opening_record()["market_state"], snapshot(hourly(clocks=None)))
    before, after = result["before"], result["after"]
    assert result["status"] == "shown"
    assert (before["n"], before["down"], before["up"], round(before["median"], 3)) == (11, 6, 5, -0.256)
    assert (after["n"], after["down"], after["up"], round(after["median"], 3)) == (11, 10, 1, -1.293)


def test_nov_16_close_prints_are_a_minute_old_so_the_close_shows_nothing():
    current = snapshot(closing())
    # XLI and QQQ traded after the regular close: refused by admission. The rest, stamped 20:59:54–20:59:59 and
    # collected at 21:01, are DELAYED: never a confident comparison.
    assert current["rows"] == {}
    assert divergence(opening_record()["market_state"], current) == dict(
        status="insufficient", reason="now: no live regular-session prints")


def test_a_quiet_session_shows_nothing():
    calm = {symbol: value - 0.1 for symbol, value in OPENING.items()}
    result = divergence(opening_record()["market_state"], snapshot(hourly(calm)))
    assert result == dict(status="quiet", reason="no material divergence")


@pytest.mark.parametrize("change", [
    dict(SPY=-1.3),                                   # SPY moved only 0.17 pp
    dict(SPY=-0.9),                                   # SPY moved the other way
    # the losers deepened (median −1.0 pp) but participation is unchanged at 6 of 11 lower
    dict(zip(SECTORS, (-1.9, -1.7, -1.5, -1.4, -1.3, -1.256, 0.1, 0.2, 0.3, 0.4, 0.5))),
])
def test_one_measure_alone_is_not_enough(change):
    current = dict(HOURLY, **change)
    assert divergence(opening_record()["market_state"], snapshot(hourly(current)))["status"] == "quiet"


def test_a_reversal_is_named_as_one():
    up = dict(zip(SECTORS, (-0.3, -0.2, -0.1, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1)), SPY=0.5, QQQ=0.7)
    down = dict(zip(SECTORS, (-1.2, -1.0, -0.9, -0.8, -0.7, -0.5, -0.4, -0.3, 0.1, 0.2, 0.3)), SPY=-0.4, QQQ=-0.6)
    result = divergence(opening_record(up)["market_state"], snapshot(hourly(down)))
    assert (result["status"], result["direction"], result["kind"]) == ("shown", "lower", "reversal")
    rally = divergence(opening_record(down)["market_state"], snapshot(hourly(up)))
    assert (rally["status"], rally["direction"], rally["kind"]) == ("shown", "higher", "reversal")


def drop(values, *symbols):
    return {s: v for s, v in values.items() if s not in symbols}


@pytest.mark.parametrize("opening,current,reason", [
    (OPENING, drop(HOURLY, "XLC", "XLE"), "8 sector ETFs held at both clocks (needs 9)"),  # XLB is DELAYED too
    (drop(OPENING, "XLC", "XLE"), HOURLY, "8 sector ETFs held at both clocks (needs 9)"),
    (OPENING, drop(HOURLY, "SPY"), "now: no SPY print"),
    (drop(OPENING, "SPY"), HOURLY, "at the analysis: no SPY print"),
])
def test_missing_coverage_shows_nothing(opening, current, reason):
    assert divergence(opening_record(opening)["market_state"], snapshot(hourly(current))) == dict(
        status="insufficient", reason=reason)


def test_participation_is_compared_on_the_common_cohort_only():
    """A symbol missing at one clock never moves the share: XLY (up at the analysis) is missing now and XLB is
    DELAYED, so both sides count the same nine."""
    flat = {symbol: value - 0.05 for symbol, value in OPENING.items()}
    result = divergence(opening_record()["market_state"], snapshot(hourly(drop(flat, "XLY"))))
    assert result == dict(status="quiet", reason="no material divergence")
    shown = divergence(opening_record()["market_state"], snapshot(hourly(drop(HOURLY, "XLY"))))
    assert shown["cohort"] == sorted(SECTOR_ETFS - {"XLB", "XLY"})
    assert shown["before"]["n"] == shown["after"]["n"] == 9


def test_stale_prints_never_count():
    # Prints forty minutes old are refused as stale by admission, so the snapshot has nothing to compare.
    stale = prints(HOUR_NOW, "HOURLY_1500", HOURLY, f"{DAY}T19:20:00+00:00")
    assert snapshot(stale)["rows"] == {}
    assert divergence(opening_record()["market_state"], snapshot(stale))["status"] == "insufficient"


def test_incomparable_snapshots_show_nothing():
    record = opening_record()
    other_close = snapshot(hourly(prior="2026-11-12"))
    assert divergence(record["market_state"], other_close)["reason"] == \
        "the two snapshots measure against different prior closes"
    # Live prints are at most a minute old, so a spread wider than fifteen minutes means a hand-built or corrupt
    # snapshot; it is still refused.
    mixed = snapshot(hourly())
    mixed["rows"]["XLC"] = dict(mixed["rows"]["XLC"], observed_at=f"{DAY}T19:40:00+00:00")
    assert divergence(record["market_state"], mixed)["reason"] == \
        "now: prints too far apart in time to compare as one snapshot"
    soon = snapshot(prints(f"{DAY}T15:31:30+00:00", "HOURLY_1100", HOURLY, f"{DAY}T15:31:00+00:00"))
    assert divergence(record["market_state"], soon)["reason"] == "too little time since the analysis's prints"


def test_a_premarket_analysis_has_no_comparable_snapshot():
    premarket = prints(f"{DAY}T14:00:00+00:00", "PREMARKET", OPENING, f"{DAY}T13:58:00+00:00")
    record = interpretation_record(premarket, narrative())
    assert record["market_state"]["rows"] == {}  # premarket prints are not regular-session participation
    assert divergence(record["market_state"], snapshot(hourly()))["status"] == "insufficient"


def test_an_analysis_frozen_before_the_check_shows_nothing():
    legacy = {k: v for k, v in opening_record().items() if k != "market_state"}
    assert divergence(legacy.get("market_state"), snapshot(hourly()))["reason"] == \
        "the analysis holds no market-state snapshot"
    assert presentation(hourly(), interpretation=legacy)["shift"] is None


def test_another_sessions_analysis_shows_nothing():
    state = dict(opening_record()["market_state"], session_date="2026-11-13")
    assert divergence(state, snapshot(hourly()))["reason"] == "the analysis belongs to another session"


# --- The page ---------------------------------------------------------------------------------------------------------

def test_the_refresh_page_shows_the_notice_with_clocks_measures_and_evidence():
    record = opening_record()
    view = presentation(hourly(), interpretation=record)
    shift = view["shift"]
    assert shift["window"] == "8:01 AM PT → 1:00 PM PT · vs prior close Fri, Nov 13"
    assert [(m["label"], m["earlier"], m["later"]) for m in shift["measures"]] == [
        ("Sector ETFs lower", "5 of 10", "9 of 10"), ("Median sector ETF", "−0.08%", "−1.15%"),
        ("SPY", "−1.13%", "−1.88%"), ("QQQ", "−2.01%", "−2.40%")]
    assert shift["note"] == ("Counts are of the 10 sector ETFs held at both clocks (of 11), not S&P 500 breadth. "
                             "Each row keeps its own clock.")
    # Evidence: both clocks' rows, each linked to its own ledger row (checked in the next test).
    clocks = {r["when"] for r in shift["refs"]}
    assert len(shift["refs"]) == 13 + 12 and len(clocks) == 2
    assert [r["anchor_id"] for r in shift["refs"][:2]] == ["analysis-state-SPY-intraday",
                                                           "analysis-state-QQQ-intraday"]
    assert all(r["anchor"] and r["anchor_id"].startswith("analysis-state-") for r in shift["refs"][:13])
    assert all(r["anchor"] and r["anchor_id"].startswith("evidence-") for r in shift["refs"][13:])
    markdown, page = render(hourly(), interpretation=record)
    assert '<div class="shift"><div class="caption">Since the analysis<span>8:01 AM PT → 1:00 PM PT' in page
    assert "Sector ETFs lower <b>5 of 10 → 9 of 10</b>" in page and "Median sector ETF <b>−0.08% → −1.15%</b>" in page
    assert "Sector ETFs have moved broadly lower since the analysis above was written." in page
    assert "**SINCE THE ANALYSIS** · 8:01 AM PT → 1:00 PM PT" in markdown
    assert "Sector ETFs lower 5 of 10 → 9 of 10" in markdown and "(#evidence-SPY-intraday)" in markdown
    # Never called comprehensive breadth anywhere on the page.
    assert "not S&amp;P 500 breadth" in page and "comprehensive" not in page.lower()


def test_both_clocks_link_to_their_own_ledger_rows_in_html_and_markdown():
    record = opening_record()
    markdown, page = render(hourly(), interpretation=record)
    ids = re.findall(r'\sid="([^"]+)"', page)
    assert len(ids) == len(set(ids)), "anchors must be unique"
    block = page[page.index('<div class="shift">'):]
    block = block[:block.index("</div><div") if "</div><div" in block else len(block)]
    hrefs = set(re.findall(r'href="#((?:analysis-state|evidence)-[^"]+)"', block))
    opening = {f"analysis-state-{s}-intraday" for s in record["market_state"]["rows"]}
    current = {f"evidence-{s}-intraday" for s in snapshot(hourly())["rows"]}
    assert opening <= hrefs and current <= hrefs
    for target in opening | current:
        assert target in ids
    # The analysis's SPY row: its own value, clock, baseline and source, never this run's.
    row = page[page.index('id="analysis-state-SPY-intraday"'):]
    row = row[:row.index("</div>")]
    assert "−1.13%" in row and "8:01 AM PT" in row and "sample-prices · SPY-intraday" in row
    current_row = page[page.index('id="evidence-SPY-intraday"'):]
    current_row = current_row[:current_row.index("</div>")]
    assert "−1.88%" in current_row and "1:00 PM PT" in current_row
    # Markdown: the same targets, as anchors in its ledger.
    md_ids = set(re.findall(r'<a id="([^"]+)"></a>', markdown))
    md_links = set(re.findall(r"\]\(#((?:analysis-state|evidence)-[^)]+)\)", markdown))
    assert opening <= md_links and current <= md_links and (opening | current) <= md_ids
    md_row = markdown[markdown.index('<a id="analysis-state-SPY-intraday"></a>'):].split("\n")[1]
    assert ("−1.13%" in md_row and f"observed {DAY}T15:01:00+00:00" in md_row
            and "source sample-prices" in md_row)


def test_the_notice_leaves_the_frozen_record_and_its_identity_alone():
    record = opening_record()
    before = (copy.deepcopy(record), record["origin"]["target_time"], record["origin"]["run_id"],
              record["content_hash"] if "content_hash" in record else None)
    render(hourly(), interpretation=record)
    assert (record, record["origin"]["target_time"], record["origin"]["run_id"],
            record.get("content_hash")) == before
    assert digest(record["narrative"]) == record["origin"]["narrative_hash"]


def test_the_close_page_shows_no_notice():
    assert presentation(closing(), interpretation=opening_record())["shift"] is None


def test_the_analysis_is_never_rewritten():
    record = opening_record()
    frozen = copy.deepcopy(record)
    refresh = hourly()
    view = presentation(refresh, interpretation=record)
    render(refresh, interpretation=record)
    assert record == frozen and digest(record["narrative"]) == record["origin"]["narrative_hash"]
    quiet = presentation(hourly({s: v - 0.1 for s, v in OPENING.items()}), interpretation=record)
    # The lead (headline, character, summary, take) is identical with and without the notice.
    for key in ("banner", "character", "summary", "take", "clocks"):
        assert json.dumps(view[key], default=str) == json.dumps(quiet[key], default=str)


def test_a_synthesis_page_never_shows_the_notice():
    packet = prints(OPEN_NOW, "OPEN_30M", OPENING, OPEN_CLOCK)
    assert presentation(packet, narrative())["shift"] is None


def test_the_snapshot_never_reaches_the_analyst():
    from market_brief.context import analyst_context, edition_profile
    context = analyst_context(prints(OPEN_NOW, "OPEN_30M", OPENING, OPEN_CLOCK), edition_profile("OPEN_30M"))
    assert "market_state" not in json.dumps(context) and "market-state" not in json.dumps(context)


def test_the_median_is_the_plain_median_of_the_cohort():
    result = divergence(opening_record()["market_state"], snapshot(hourly()))
    assert result["after"]["median"] == median(HOURLY[s] for s in result["cohort"])

"""The close keeps the last live "Since the analysis" evidence, at its own clock (WP-MB-TRUTH-01, item 2).

Nov 20, 2026: the 1:02 PM PT refresh showed sector ETFs reversing lower since the 8:02 AM PT analysis (1 → 8 of 10
lower, SPY −0.02% → −2.85%). At the close every print is the last regular-session trade, more than a minute old at
collection (DELAYED), so under WP-MB-STATE-01's AVAILABLE-only rule the close page had nothing to compare and the
notice vanished. The rule stays: DELAYED close prints are still never compared. Instead each refresh's edition state
keeps the last comparable live snapshot, and a page whose own prints cannot be compared shows that earlier
divergence with its own clock and its own ledger rows, saying the comparison stops there.
"""

import copy
import re

from test_cadence import TUE, Day
from test_history_admission import utc
from test_market_state import DAY, OPENING, closing, hourly, opening_record

from market_brief.continuity import (
    admit_prior_state,
    advance_bundle,
    carried_state,
    empty_bundle,
)
from market_brief.evidence import SECTOR_ETFS
from market_brief.render import presentation, render
from market_brief.state import live_snapshot, snapshot


def with_carried(packet, state):
    packet = copy.deepcopy(packet)
    packet["continuity"] = dict(status="available", reason="", anchors={}, comparisons=[], market_state=state)
    return packet


def test_a_refresh_keeps_its_own_live_snapshot_or_carries_the_last_one():
    live, stale = snapshot(hourly()), snapshot(closing())
    assert stale["rows"] == {}
    assert live_snapshot(live, None) is live
    assert live_snapshot(stale, live) is live                   # nothing comparable now: the last live one carries
    assert live_snapshot(live, snapshot(hourly(clocks=None))) is live   # comparable now: this run's own
    other = dict(live, session_date="2026-11-13")
    assert live_snapshot(stale, other) is stale                 # never another session's


def test_the_refresh_edition_state_records_the_snapshot_and_the_close_admits_it():
    record = opening_record()
    refresh = hourly()
    prior = dict(status="available", reason="", anchors={}, snapshots={}, watches=[], relationships=[])
    state = carried_state(refresh, prior, [], record)
    assert state["observed"]["market_state"] == snapshot(refresh)
    bundle = advance_bundle(empty_bundle(), state, None, utc(f"{DAY}T20:00:30+00:00"), record)
    admitted = admit_prior_state(bundle, closing())
    assert admitted["market_state"] == snapshot(refresh)
    # The close's own edition state carries that live snapshot forward, never its DELAYED prints.
    close_state = carried_state(closing(), admitted, [], record)
    assert close_state["observed"]["market_state"] == snapshot(refresh)


def test_the_close_page_shows_the_last_live_divergence_at_its_own_clock():
    record = opening_record()
    page = with_carried(closing(), snapshot(hourly()))
    shift = presentation(page, interpretation=record)["shift"]
    assert shift["window"] == "8:01 AM Vancouver time → 1:00 PM Vancouver time · vs prior close Fri, Nov 13"
    assert shift["sentence"] == (
        "By 1:00 PM Vancouver time, the last live prints, sector ETFs had moved broadly lower since the analysis "
        "above was written. This page's own prints, the last trades before the close, are not compared. "
        "The analysis is unchanged; these later prints are not part of it.")
    assert [(m["label"], m["earlier"], m["later"]) for m in shift["measures"]][:3] == [
        ("Sector ETFs lower", "5 of 10", "9 of 10"), ("Median sector ETF", "−0.08%", "−1.15%"),
        ("SPY", "−1.13%", "−1.88%")]
    # The later prints are the 1:00 PM ones, linked to their own rows, never to this page's close prints.
    later = [r for r in shift["refs"] if r["anchor_id"].startswith("live-state-")]
    assert len(later) == 12 and {r["when"] for r in later} == {"1:00 PM Vancouver time"}
    assert not [r for r in shift["refs"] if r["anchor_id"].startswith("evidence-")]


def test_both_clocks_link_to_their_own_ledger_rows_on_the_close_page():
    record = opening_record()
    markdown, page = render(with_carried(closing(), snapshot(hourly())), interpretation=record)
    ids = re.findall(r'\sid="([^"]+)"', page)
    assert len(ids) == len(set(ids)), "anchors must be unique"
    block = page[page.index('<div class="shift">'):page.index('id="evidence-ledger"')]
    hrefs = set(re.findall(r'href="#(live-state-[^"]+)"', block))
    later = {f"live-state-{s}-intraday" for s in snapshot(hourly())["rows"]}
    assert later <= hrefs and later <= set(ids)
    row = page[page.index('id="live-state-SPY-intraday"'):]
    row = row[:row.index("</div>")]
    assert "−1.88%" in row and "1:00 PM Vancouver time" in row
    assert "Last live prints · 1:00 PM Vancouver time" in page
    assert "By 1:00 PM Vancouver time, the last live prints" in markdown
    md_ids = set(re.findall(r'<a id="([^"]+)"></a>', markdown))
    md_links = set(re.findall(r"\]\(#(live-state-[^)]+)\)", markdown))
    assert later <= md_links and later <= md_ids


def test_live_prints_now_always_win_over_a_carried_snapshot():
    record = opening_record()
    calm = hourly({symbol: value - 0.1 for symbol, value in OPENING.items()})
    assert presentation(with_carried(calm, snapshot(hourly())), interpretation=record)["shift"] is None
    live = presentation(with_carried(hourly(), None), interpretation=record)["shift"]
    assert live["sentence"].startswith("Sector ETFs have moved broadly lower")


def test_without_a_carried_snapshot_the_close_still_shows_nothing():
    assert presentation(with_carried(closing(), None), interpretation=opening_record())["shift"] is None


# --- One day through the CLI --------------------------------------------------------------------------------------

SECTORS = sorted(SECTOR_ETFS)
OPEN_PRINTS = (("SPY", -0.02), ("QQQ", -0.77),
               *zip(SECTORS, (0.65, 0.74, 0.56, 0.40, 0.24, -1.67, 1.69, 0.78, 0.69, 1.17, 0.79)))
LATE_PRINTS = (("SPY", -2.85), ("QQQ", -4.56),
               *zip(SECTORS, (-1.55, -1.94, -0.11, -1.12, -2.48, -6.27, 1.05, -1.30, -0.22, 0.49, -2.30)))


def regular_session(raw, now):
    """The fixture's prints read like the collector's: measured against Friday's regular close."""
    for row in raw["observations"]:
        if row.get("frequency") == "intraday":
            row["reason"] = "feed=IEX; latest trade timestamp; prior close 2026-09-04 regular session"


def test_a_production_day_keeps_the_afternoon_divergence_on_the_close_page(monkeypatch, tmp_path):
    day = Day(monkeypatch, tmp_path)
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    assert day.run(f"{TUE}T14:00:00+00:00", "OPEN_30M", prints=OPEN_PRINTS, collect=regular_session) == 0
    assert day.run(f"{TUE}T19:00:00+00:00", "HOURLY_1500", prints=LATE_PRINTS, collect=regular_session) == 0
    assert "Since the analysis" in day.page("HOURLY_1500")
    assert day.bundle()["latest"]["observed"]["market_state"]["target_time"].startswith(f"{TUE}T19:00")
    # 4:01 PM ET: the last regular-session trades, a minute old at collection, DELAYED and never compared.
    assert day.run(f"{TUE}T20:01:00+00:00", "CLOSE_1M", prints=LATE_PRINTS, print_at=f"{TUE}T19:59:59+00:00",
                   collect=regular_session) == 0
    close = day.page("CLOSE_1M")
    assert "Since the analysis" in close and "the last live prints" in close
    assert "the last trades before the close, are not compared" in close
    assert day.calls == ["PREMARKET", "OPEN_30M"]
    # The analysis the close carries is still the opening one, unchanged.
    assert day.bundle()["interpretation"]["origin"]["checkpoint"] == "OPEN_30M"


def test_a_synthesis_run_never_carries_the_snapshot_into_its_evidence(monkeypatch, tmp_path):
    day = Day(monkeypatch, tmp_path)
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    assert day.run(f"{TUE}T13:31:00+00:00", "OPEN_1M", prints=OPEN_PRINTS, collect=regular_session) == 0
    assert day.run(f"{TUE}T14:00:00+00:00", "OPEN_30M", prints=OPEN_PRINTS, collect=regular_session) == 0
    evidence = (day.folder("OPEN_30M") / "evidence.json").read_text()
    assert "market_state" not in evidence


def test_interpretation_record_is_untouched_by_the_carried_notice():
    record = opening_record()
    frozen = copy.deepcopy(record)
    render(with_carried(closing(), snapshot(hourly())), interpretation=record)
    assert record == frozen


def test_a_sparse_refresh_never_discards_the_kept_divergence():
    """Review P2: an afternoon refresh with SPY alone (too few sector ETFs to compare) keeps the earlier snapshot, and
    the close still shows it."""
    record = opening_record()
    good = snapshot(hourly())
    sparse = hourly({"SPY": -2.0}, clocks=None)
    assert live_snapshot(snapshot(sparse), good, record["market_state"]) is good
    prior = dict(status="available", reason="", anchors={}, snapshots={}, watches=[], relationships=[],
                 market_state=good)
    assert carried_state(sparse, prior, [], record)["observed"]["market_state"] == good
    # The sparse refresh itself shows the kept divergence at its own clock.
    shift = presentation(with_carried(sparse, good), interpretation=record)["shift"]
    assert shift and shift["sentence"].endswith(
        "This page's own prints cannot be compared with the analysis. The analysis is unchanged; these later "
        "prints are not part of it.")
    # A genuinely comparable, quiet refresh still supersedes an earlier divergence.
    calm = snapshot(hourly({symbol: value - 0.1 for symbol, value in OPENING.items()}))
    assert live_snapshot(calm, good, record["market_state"]) is calm

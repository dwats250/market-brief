"""WP-MB-CLOSE-01: closing data integrity and how the close page tells its cells apart.

The regular-session boundary is unchanged: a trade stamped after the close is after-hours and never session evidence.
Alpaca's snapshot offers no other regular-session print with its own trade clock (`dailyBar` and `minuteBar` carry a
bar start, not a trade time), so nothing new is admitted; the page now says, under each table, which cells are the
last trades before the close (never official closing prices), which symbols traded only after it, and which columns
use daily history a session behind. Scenario: the 2026-11-16 close, where QQQ, XLI and AAPL traded only after the bell.
The sample fixture carries no AAPL, so `with_aapl` adds a TEST-ONLY dated AAPL close history (NVDA's fixture closes
under AAPL's name) for its row to render. Values are test-only.
"""

import copy
import re

import pytest
import test_history_admission
from test_market_state import CLOSING, DAY, opening_record, prints
from test_pipeline import narrative

from market_brief.continuity import carried_state, closing_data, interpretation_record, session_handoff
from market_brief.evidence import digest
from market_brief.render import presentation, render, table_notes

CLOSE_NOW = f"{DAY}T21:01:00+00:00"
FINAL = {symbol: f"{DAY}T20:59:{54 + n % 6:02d}+00:00" for n, symbol in enumerate(CLOSING)}
LAST_TRADE = "Each Change is the last trade seen before the 4:00 PM ET regular-session close, not an official " \
             "closing price."


@pytest.fixture
def with_aapl(monkeypatch):
    """The sample fixture plus a TEST-ONLY dated AAPL close history, so the mega-cap table has an AAPL row."""
    original = test_history_admission.read_json

    def read(path):
        value = original(path)
        if str(path).endswith("evidence.sample.json"):
            nvda = next(h for h in value["history"] if h["symbol"] == "NVDA")
            value["history"].append(dict(copy.deepcopy(nvda), id="history-AAPL", symbol="AAPL"))
        return value
    monkeypatch.setattr(test_history_admission, "read_json", read)


def close_page(values=CLOSING, clocks=FINAL, now=CLOSE_NOW, after_hours=None):
    return prints(now, "CLOSE_1M", values, clocks=clocks, after_hours=after_hours)


def intraday(packet, symbol):
    return next(r for r in packet["observations"] if r["id"] == f"{symbol}-intraday")


def sectors(packet, interpretation=None):
    return presentation(packet, interpretation=interpretation or opening_record())["sectors"]


# --- Admission: the regular-session boundary --------------------------------------------------------------------------

def test_valid_regular_session_prints_keep_their_own_clock_and_status():
    packet = close_page()
    spy = intraday(packet, "SPY")
    # Collected a minute after the bell: admitted, DELAYED, at the trade's own clock; never an official close.
    assert (spy["status"], spy["observed_at"], spy["value"]) == ("DELAYED", f"{DAY}T20:59:58+00:00", -1.887)
    view = sectors(packet)
    assert view["change_label"] == "Intraday vs prior close" and view["asof"] == "as of 1:59 PM Vancouver time"
    assert LAST_TRADE in view["notes"]


@pytest.mark.parametrize("observed,admitted", [
    (f"{DAY}T20:59:59.999000+00:00", True),
    (f"{DAY}T21:00:00+00:00", True),           # the bell itself is still the regular session
    (f"{DAY}T21:00:00.001000+00:00", False),   # one millisecond later is after-hours
    (f"{DAY}T21:00:59+00:00", False),
])
def test_the_close_boundary_is_exact(observed, admitted):
    row = intraday(close_page(dict(SPY=-1.9), clocks=dict(SPY=observed)), "SPY")
    assert (row["status"] != "UNAVAILABLE") is admitted
    if not admitted:
        assert row["value"] is None and row["reason"] == "after-hours trade, after the regular-session close"


def test_after_hours_only_symbols_are_named_under_their_table_and_never_shown_as_session_prices(with_aapl):
    # 2026-11-16: QQQ, XLI and AAPL had only after-hours IEX trades at the close snapshot.
    packet = close_page(after_hours={"XLI": -1.2, "QQQ": -2.3, "AAPL": -0.8})
    for symbol in ("XLI", "QQQ", "AAPL"):
        row = intraday(packet, symbol)
        assert (row["value"], row["status"]) == (None, "UNAVAILABLE")
        assert row["reason"] == "after-hours trade, after the regular-session close"
    view = presentation(packet, interpretation=opening_record())
    cells = {row["symbol"]: row["today"]["display"] for table in ("sectors", "equities")
             for row in view[table]["rows"]}
    assert cells["XLI"] == "no print" and cells["AAPL"] == "no print"
    after_hours = ("No print for {}: the latest trade came after the 4:00 PM ET regular-session close, and "
                   "after-hours trades never stand for the session.")
    assert after_hours.format("XLI") in view["sectors"]["notes"]
    assert after_hours.format("AAPL") in view["equities"]["notes"]
    # QQQ sits in no table; Coverage limitations still name it.
    assert not any("QQQ" in note for table in ("sectors", "equities", "cross_asset") for note in view[table]["notes"])
    markdown, page = render(packet, interpretation=opening_record())
    for text in (markdown, page):
        assert after_hours.format("XLI") in text and after_hours.format("AAPL") in text
        for symbol in ("QQQ", "XLI", "AAPL"):
            assert (f"{symbol}: no regular-session print, so no session value is shown; its latest trade was after "
                    "the regular-session close (after-hours)") in text
    # QQQ has no table row: the coverage line is its one reader statement, exact in both editions.
    qqq = ("QQQ: no regular-session print, so no session value is shown; its latest trade was after the "
           "regular-session close (after-hours)")
    assert f"- {qqq}" in markdown and f"<li>{qqq}</li>" in page
    assert "QQQ: after-hours trade" not in page and "QQQ: after-hours trade" not in markdown
    # The packet keeps its own reason and status.
    assert "QQQ: after-hours trade, after the regular-session close" in packet["coverage"]["limitations"]
    assert "| AAPL | no print |" in markdown
    # Nothing on the page claims an official close.
    assert not re.search(r"(?<![\w-])[Cc]lose vs prior close|official closing price(?!\.)|closing price:", page)


def test_partial_availability_names_only_what_is_missing_for_its_reason():
    values = {k: v for k, v in CLOSING.items() if k not in ("XLB", "XLC")}
    notes = sectors(close_page(values, after_hours={"XLI": -1.2}))["notes"]
    no_print = [note for note in notes if note.startswith("No print")]
    # XLB and XLC have no print for another reason (not returned here); only XLI is said to have traded after hours.
    assert no_print == ["No print for XLI: the latest trade came after the 4:00 PM ET regular-session close, and "
                        "after-hours trades never stand for the session."]


def test_a_table_whose_prints_are_not_all_from_the_final_minutes_makes_no_last_trade_claim():
    clocks = dict(FINAL, XLK=f"{DAY}T20:44:59+00:00")  # sixteen minutes before the bell
    notes = sectors(close_page(clocks=clocks))["notes"]
    assert not any(note.startswith("Each Change") for note in notes)
    clocks = dict(FINAL, XLK=f"{DAY}T20:45:00+00:00")  # the first second of the final fifteen minutes
    assert LAST_TRADE in sectors(close_page(clocks=clocks))["notes"]


def test_a_late_close_run_keeps_provisional_prints_and_says_so():
    packet = close_page(now=f"{DAY}T21:40:00+00:00")
    assert intraday(packet, "SPY")["status"] == "PROVISIONAL"
    view = sectors(packet)
    assert view["change_label"] == "Near-close vs prior close · provisional"
    assert LAST_TRADE + " Collected well after the close, so provisional." in view["notes"]


def test_delayed_daily_history_is_dated_where_its_columns_are_used():
    packet = close_page()
    assert packet["history_lag"] == {"completed_session": DAY, "history_through": "2026-11-13"}
    note = ("The 20-session and 50DMA columns use daily history through Fri, Nov 13; the Mon, Nov 16 daily bar is "
            "not published yet.")
    view = presentation(packet, interpretation=opening_record())
    assert note in view["sectors"]["notes"] and note in view["equities"]["notes"]
    markdown, _ = render(packet, interpretation=opening_record())
    # One paragraph per note in Markdown.
    assert f"\n\n{note}\n\n" in markdown


def test_the_close_label_is_the_exchange_clock_in_every_season():
    # Daylight time (Sep 8 close 20:00Z) and standard time (Nov 16 close 21:00Z) both read 4:00 PM ET, never a
    # Vancouver clock.
    nov = sectors(close_page())["notes"]
    assert LAST_TRADE in nov and not any(" PM PT" in note or "Vancouver time" in note for note in nov)
    row = dict(symbol="SPY", intraday=dict(id="SPY-intraday", observed_at="2026-09-08T19:59:30+00:00",
                                           status="AVAILABLE"))
    sep = table_notes([row], "Intraday vs prior close", dict(observations=[]),
                      dict(close="2026-09-08T20:00:00+00:00"))
    assert sep == [LAST_TRADE]


def test_missing_values_stay_missing_and_no_other_cell_changes():
    packet = close_page(after_hours={"XLI": -1.2})
    before = sectors(packet)
    rows = {row["symbol"]: row["today"]["display"] for row in before["rows"]}
    assert rows["XLI"] == "no print" and rows["XLK"] == "−1.12%"


# --- Continuity: the close hands off what was admitted, at its own clock ----------------------------------------------

def test_the_close_handoff_carries_only_regular_session_prints():
    packet = close_page(after_hours={"XLI": -1.2, "QQQ": -2.3})
    packet["run"]["run_id"] = "live-close"
    status = closing_data(packet)
    assert status == dict(status="PROVISIONAL_NEAR_CLOSE",
                          reason=f"timestamped session prints through {DAY}T20:59:59+00:00; "
                                 "not an official closing bar")
    record = opening_record()
    prior = dict(status="cold_start", reason="", anchors={}, snapshots={}, watches=[], relationships=[])
    state = carried_state(packet, prior, [], record)
    snapshots = state["observed"]["snapshots"]
    assert "XLI-intraday" not in snapshots and "QQQ-intraday" not in snapshots
    spy = snapshots["SPY-intraday"]
    assert (spy["value"], spy["observed_at"]) == (-1.887, f"{DAY}T20:59:58+00:00")
    handoff, reason = session_handoff(state)
    assert reason == "" and handoff["assessment"]["closing_character"]["provisional"] is True
    assert handoff["assessment"]["closing_character"]["text"] == record["narrative"]["character"]["text"]


def test_rendering_the_close_never_edits_the_interpretation():
    record = opening_record()
    frozen = copy.deepcopy(record)
    render(close_page(after_hours={"XLI": -1.2}), interpretation=record)
    assert record == frozen and digest(record["narrative"]) == record["origin"]["narrative_hash"]


def test_a_synthesis_page_with_live_prints_shows_no_close_notes():
    packet = prints(f"{DAY}T15:01:30+00:00", "OPEN_30M", CLOSING, f"{DAY}T15:01:00+00:00")
    view = presentation(packet, narrative())
    assert not any(note.startswith(("Each Change", "No print")) for note in view["sectors"]["notes"])
    assert interpretation_record(packet, narrative())["market_state"]["rows"]

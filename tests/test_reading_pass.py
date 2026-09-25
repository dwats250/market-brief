"""Reading pass: the header's three clocks (R1), the overdue page (R2), hierarchy and typography (R3), the light
palette (R4), the § evidence marker (R5) and the reading guide (R12)."""

import json
import re

import pytest
from test_cadence import TUE, Day
from test_pipeline import fixture_packet, narrative
from test_render import clock_lines

from market_brief.evidence import timestamp
from market_brief.render import OVERDUE_GRACE, presentation, render
from market_brief.schedule import next_checkpoint


@pytest.fixture
def day(monkeypatch, tmp_path):
    return Day(monkeypatch, tmp_path)


def live(packet, checkpoint="OPEN_1M"):
    packet["run"]["mode"] = "LIVE"
    packet["run"]["checkpoint"] = checkpoint
    packet["run"]["session"]["meaningful_premarket"] = True
    value = narrative()
    value["mode"] = "LIVE"
    return packet, value


def visible(page):
    body = page.split("<body>", 1)[1].split("<script>", 1)[0]
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))


def print_row(symbol, observed_at, value=0.4):
    return dict(id=f"{symbol}-intraday", topic=symbol, metric="premarket return", value=value, unit="%",
                baseline="latest trade versus previous regular close", frequency="intraday", observed_at=observed_at,
                retrieved_at=observed_at, source_id="sample-prices", status="AVAILABLE", reason="",
                freshness="LIVE", expected_freshness="LIVE", magnitude="NOTABLE")


# --- R1 status ------------------------------------------------------------------------------------------------

def test_live_is_silent_and_the_date_moves_to_the_masthead():
    _, page = render(*live(fixture_packet()))
    head = page.split("<h1>", 1)[0]
    assert '<div class="status-line">' not in head and "LIVE" not in visible(head)
    assert '<div class="masthead"><b class="masthead-title">Market Brief</b><span>Tuesday, Sep 8</span></div>' in head
    assert "Opening refresh" not in visible(page)  # the edition label at most once: here, not at all


@pytest.mark.parametrize("setup,status", [
    (lambda packet: None, "SAMPLE"),
    (lambda packet: packet["run"].update(display_status="LAST GOOD BRIEF"), "LAST GOOD BRIEF"),
    (lambda packet: packet["run"].update(mode="LIVE", commissioning=True), "LIVE COMMISSIONING"),
])
def test_other_statuses_stay_loud_with_the_edition_named_once(setup, status):
    packet = fixture_packet()
    setup(packet)
    value = narrative()
    value["mode"] = packet["run"]["mode"]
    md, page = render(packet, value)
    head = page.split("<h1>", 1)[0]
    assert f'<div class="status-line">{status} · Premarket edition</div>' in head
    assert visible(page).count("Premarket edition") == 1
    assert md.splitlines()[2] == f"{status} · Premarket edition · Tuesday, Sep 8"
    if status != "LAST GOOD BRIEF":
        assert '<div class="notice">' in head  # the truth notice stays beside it


# --- R1 the three clocks ---------------------------------------------------------------------------------------

def test_prices_is_the_latest_table_print_not_the_collection_clock():
    packet, value = live(fixture_packet(), "HOURLY_1300")
    packet["run"]["target_time"] = f"{TUE}T17:01:30+00:00"
    packet["run"]["actual_started_at"] = f"{TUE}T17:01:30+00:00"
    packet["observations"] += [print_row("XLI", f"{TUE}T17:00:10+00:00"), print_row("NVDA", f"{TUE}T17:00:55+00:00"),
                               print_row("GLD", f"{TUE}T16:40:00+00:00"),
                               print_row("SPY", f"{TUE}T17:01:20+00:00")]  # SPY is in no table
    _, page = render(packet, value)
    assert clock_lines(page)[0] == "Prices · 10:00 AM PT"  # NVDA's 17:00:55 print, the latest across the tables


def test_prices_without_current_prints_is_the_prior_close():
    view = presentation(*live(fixture_packet()))
    assert view["clocks"][0] == dict(label="Prices", text="prior close Fri, Sep 4", anchor=True)
    assert view["clocks"][1]["label"] == "Analysis"


def test_a_synthesis_combines_prices_and_analysis_only_when_they_share_a_clock():
    packet, value = live(fixture_packet(), "OPEN_30M")
    packet["run"]["target_time"] = packet["run"]["actual_started_at"] = f"{TUE}T14:01:40+00:00"
    packet["observations"].append(print_row("XLI", f"{TUE}T14:01:05+00:00"))
    view = presentation(packet, value)
    assert [c["label"] for c in view["clocks"]] == ["Prices & analysis", "Next"]
    assert view["clocks"][0]["text"] == "7:01 AM PT · opening structure"
    packet["observations"][-1] = print_row("XLI", f"{TUE}T13:59:50+00:00")  # the latest print is a minute behind
    view = presentation(packet, value)
    assert [c["text"] for c in view["clocks"][:2]] == ["6:59 AM PT", "7:01 AM PT · opening structure"]


def test_what_changed_on_a_carried_page_ends_at_its_analysis(day):
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    assert day.run(f"{TUE}T13:31:00+00:00", "OPEN_1M") == 0
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M") == 0
    assert day.run(f"{TUE}T17:00:00+00:00", "HOURLY_1300") == 0
    evidence = json.loads((day.folder("HOURLY_1300") / "evidence.json").read_text())
    view = presentation(evidence, interpretation=day.bundle()["interpretation"])
    assert view["since"]["label"] == "vs premarket and the 6:31 AM PT refresh · through the 7:01 AM PT analysis"
    structure = json.loads((day.folder("OPEN_30M") / "evidence.json").read_text())
    own = presentation(structure, interpretation=day.bundle()["interpretation"])
    assert own["since"]["label"] == "vs premarket and the 6:31 AM PT refresh"  # a synthesis page is its own end


# --- R2 the overdue page ---------------------------------------------------------------------------------------

def test_the_next_line_carries_its_absolute_time_and_its_own_pt_words():
    packet, value = live(fixture_packet(), "HOURLY_1300")
    packet["run"]["target_time"] = f"{TUE}T17:01:00+00:00"
    _, page = render(packet, value)
    info = next_checkpoint(timestamp(packet["run"]["target_time"]), "HOURLY_1300")
    match = re.search(r'<div class="clock" data-next-at="([^"]+)" data-next-when="([^"]+)" '
                      r'data-grace-minutes="(\d+)"><dt>Next</dt><dd>([^<]+)</dd></div>', page)
    assert match, page.split("<h1>", 1)[0]
    assert match[1] == info["scheduled_at"] and "+00:00" in match[1]  # absolute, not a wall clock
    assert match[3] == "15" and OVERDUE_GRACE.total_seconds() == 15 * 60
    assert match[4] == f"{match[2]} · price refresh"
    # The inline script reads those attributes and rewrites only the Next line, with the page's own words.
    script = page.split("<script>", 1)[1]
    assert "data-next-at" in script and "dataset.nextWhen" in script and "Update due ${" in script
    assert "has not published" in script and "visibilitychange" in script and "setInterval(overdue, 60000)" in script
    assert page.count("<script>") == 1 and "fetch(" not in script  # no network call
    # Without JavaScript nothing changes: the static page never says it is overdue.
    assert "has not published" not in page.split("<script>", 1)[0]


def test_the_next_session_premarket_carries_its_date_in_the_overdue_words():
    packet, value = live(fixture_packet(), "CLOSE_1M")
    packet["run"]["target_time"] = f"{TUE}T20:03:00+00:00"
    _, page = render(packet, value)
    assert 'data-next-when="Wed, Sep 9 · 6:00 AM PT"' in page
    assert "<dt>Next</dt><dd>Wed, Sep 9 · 6:00 AM PT · premarket analysis</dd>" in page

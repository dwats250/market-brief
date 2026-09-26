"""Reading pass: the header's three clocks (R1), the overdue page (R2), hierarchy and typography (R3), the light
palette (R4), the § evidence marker (R5) and the reading guide (R12)."""

import json
import re

import pytest
from test_cadence import TUE, Day
from test_pipeline import fixture_packet, freeze_clock, narrative
from test_render import clock_lines

from market_brief import cli
from market_brief.evidence import ROOT, read_json, timestamp
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


# --- R3 hierarchy and typography ------------------------------------------------------------------------------

def style():
    return (ROOT / "templates/brief.html.j2").read_text().split("<style>", 1)[1].split("</style>", 1)[0]


def test_section_headings_space_and_rules():
    css = style()
    desktop, mobile = css.split("@media(max-width:600px)", 1)
    assert "h2{font:600 28px/1.2 Georgia" in desktop and "h2{font-size:25px}" in mobile
    assert "section{border-top:2px solid var(--rule-strong);padding-top:26px;margin-top:52px}" in desktop
    assert "section{margin-top:40px;padding-top:22px}" in mobile
    assert "h1{font:500 52px/1.05 Georgia" in desktop and "h1{font-size:38px" in mobile  # h1 stays dominant
    for block in (":root{", 'html[data-theme="dark"]{', "html:not([data-theme]){"):
        assert "--rule-strong:#" in css.split(block, 1)[1].split("}", 1)[0], block


def replayed(tmp_path, monkeypatch, checkpoint="PREMARKET"):
    """The fictional replay the example editions come from: the sample continuity and its narrative."""
    raw = read_json(ROOT / "tests/fixtures/evidence.sample.json")
    freeze_clock(monkeypatch, raw["target_time"])
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(cli, "update_latest", lambda root, page: None)
    assert cli.main(["premarket", "--replay", "--checkpoint", checkpoint]) == 0
    folder = next(p for p in (tmp_path / "runs").glob("*/*") if (p / "brief.html").exists())
    return (folder / "brief.md").read_text(), (folder / "brief.html").read_text()


def test_what_changed_is_a_real_section_and_metals_is_named_plainly(tmp_path, monkeypatch):
    md, page = replayed(tmp_path, monkeypatch)
    assert '<section class="since"><h2>What changed</h2><p class="sub">vs the previous close · Fri, Sep 4</p>' in page
    assert page.index('<section class="since">') < page.index('<section class="next">')
    assert ".since h2" not in style()  # the standard heading, no override
    md, page = render(fixture_packet(), narrative())
    assert "<h2>Metals</h2>" in page and "<h2>Cross-asset structure</h2>" not in page
    assert "Metals structure" not in page and "METALS STRUCTURE" not in md and "\n## Metals\n" in md
    assert '<h2>Metals</h2><div class="caption">20-session return spread, ' in page
    assert "Energy" not in re.findall(r"<h2>([^<]*)</h2>", page)


# --- R4 no text dimmed by opacity -------------------------------------------------------------------------------

def test_no_text_is_dimmed_by_opacity():
    css = style()
    assert "opacity" not in css
    assert "td.secondary{font-size:13.5px;color:var(--faint)}" in css
    assert "details.cite summary{display:inline;list-style:none;cursor:pointer;color:var(--teal);" in css


# --- R5 § evidence ----------------------------------------------------------------------------------------------

LABELLED = '<summary aria-label="Supporting evidence" title="Supporting evidence">§ evidence</summary>'
BARE = '<summary aria-label="Supporting evidence" title="Supporting evidence">§</summary>'


def test_the_first_marker_in_document_order_is_labelled_once():
    _, page = render(fixture_packet(), narrative())
    assert page.count(LABELLED) == 1 and page.count(BARE) >= 3
    assert page.index(LABELLED) < page.index(BARE)
    assert page.index(LABELLED) > page.index('<div class="dek">')  # here the character line's own marker


def test_without_character_refs_the_label_moves_to_the_next_marker():
    value = narrative()
    value["character"]["evidence_ids"] = []
    _, page = render(fixture_packet(), value)
    dek = page.split('<div class="dek">', 1)[1].split("</div>", 1)[0]
    assert "<details" not in dek
    assert page.count(LABELLED) == 1
    assert page.index(LABELLED) > page.index('<div class="read">')


def test_the_marker_hit_area_is_padding_with_a_matching_negative_margin():
    rule = style().split("details.cite summary{", 1)[1].split("}", 1)[0]
    padding = re.search(r"padding:(\d+)px (\d+)px", rule)
    margin = re.search(r"margin:0 -(\d+)px", rule)
    assert padding and margin
    vertical, horizontal = int(padding[1]), int(padding[2])
    # A 14 px glyph line plus vertical padding clears 32 px; the net horizontal offset stays the old 3 px gap.
    assert 14 + 2 * vertical >= 32 and horizontal - int(margin[1]) == 3
    assert "white-space:nowrap" in rule  # "§ evidence" never breaks between the mark and its label
    assert "position:relative" in rule  # painted above a following block, so its padding stays tappable
    assert "details.proof summary{font-size:12px;padding:0;margin:0;" in style()  # the table proof keeps its place


# --- R12 how to read this brief -----------------------------------------------------------------------------------

def guide(page):
    return page.split('<details class="drawer guide">', 1)[1].split("<section>", 1)[0]


def test_the_guide_is_collapsed_above_sources_and_leads_with_the_latest_move():
    from test_rates_module import rates_packet
    md, page = render(rates_packet(), narrative())
    assert page.count('<details class="drawer guide">') == 1  # no `open`: collapsed by default
    assert page.index('<details class="drawer guide">') < page.index("<h2>Sources &amp; coverage</h2>")
    assert page.index("<h2>Metals</h2>") < page.index('<details class="drawer guide">')
    body = guide(page)
    assert body.startswith("<summary>How to read this brief</summary><dl><dt>Bear steepener</dt><dd>The latest "
                           "curve move. Long-end yields rose more than the front end.</dd><dt>2s10s</dt>")
    terms = re.findall(r"<dt>([^<]*)</dt>", body.split('<details class="guide-moves">', 1)[0])
    assert terms == ["Bear steepener", "2s10s", "5s30s", "Bull and bear", "The par curve", "Three clocks",
                     "§ evidence"]
    for text in re.findall(r"<dd>([^<]*)</dd>", body):
        assert 1 <= len(re.findall(r"[.!?](?:\s|$)", text.replace("&amp;", "&"))) <= 2, text  # one or two sentences
    moves = body.split('<details class="guide-moves"><summary>See all curve moves</summary>', 1)[1]
    assert len(re.findall(r"<dt>", moves)) == 13 and "<dt>Mixed curve move</dt>" in moves
    assert "How to read" not in md and "See all curve moves" not in md  # HTML only
    assert "today" not in body.lower()


def test_the_guide_skips_a_move_it_cannot_name():
    from test_rates_module import NOW, rates_packet
    stale = render(rates_packet(now=NOW.replace(day=11)), narrative())[1]
    assert guide(stale).startswith("<summary>How to read this brief</summary><dl><dt>2s10s</dt>")
    missing = render(rates_packet(levels={"10Y": 5.18}, changes={"10Y": 7}), narrative())[1]
    assert guide(missing).startswith("<summary>How to read this brief</summary><dl><dt>2s10s</dt>")
    fixture = fixture_packet()
    fixture["observations"] = [row for row in fixture["observations"] if not row["topic"].startswith("US ")]
    fixture["derived"] = [row for row in fixture["derived"] if not row["topic"].startswith("US ")]
    fixture.pop("curve")
    assert "<dt>2s10s</dt>" in guide(render(fixture, narrative())[1])


# --- review regressions ------------------------------------------------------------------------------------------

def test_a_commissioning_page_names_the_pending_scheduled_checkpoint_next():
    packet, value = live(fixture_packet(), "PREMARKET")  # 5:45 AM PT, before the 6:00 AM PT premarket synthesis
    assert presentation(packet, value)["clocks"][-1]["text"] == "6:31 AM PT · price refresh"  # its own slot is done
    packet["run"]["commissioning"] = True
    assert presentation(packet, value)["clocks"][-1]["text"] == "6:00 AM PT · analysis update"


def browser_next_line(page, tmp_path, now, fragment=""):
    """The Next line after the page's own script ran in headless Chrome with Date.now frozen at `now`."""
    import shutil
    import subprocess
    from datetime import datetime
    chrome = shutil.which("google-chrome") or shutil.which("google-chrome-stable") or shutil.which("chromium")
    if not chrome:
        pytest.skip("headless Chrome is not installed")
    frozen = int(datetime.fromisoformat(now).timestamp() * 1000)
    target = tmp_path / "brief.html"
    target.write_text(page.replace("<main>", f"<main><script>Date.now = () => {frozen};</script>", 1))
    result = subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--dump-dom",
                             target.as_uri() + fragment], capture_output=True, text=True, timeout=90, check=False)
    found = re.search(r'<div class="clock( overdue)?" data-next-at="[^"]+"[^>]*><dt>Next</dt><dd>([^<]*)</dd>',
                      result.stdout)
    assert found, result.stderr[-500:]
    return bool(found[1]), found[2]


def test_the_overdue_script_in_a_browser(tmp_path):
    packet, value = live(fixture_packet(), "HOURLY_1300")
    packet["run"]["target_time"] = f"{TUE}T17:01:00+00:00"  # the 10:01 AM PT refresh; next is 11:00 AM PT (18:00Z)
    _, page = render(packet, value)
    assert browser_next_line(page, tmp_path, f"{TUE}T18:14:59+00:00") == (False, "11:00 AM PT · price refresh")
    overdue = (True, "Update due 11:00 AM PT has not published")
    assert browser_next_line(page, tmp_path, f"{TUE}T18:15:01+00:00") == overdue
    # A malformed fragment in the URL cannot stop the check.
    assert browser_next_line(page, tmp_path, f"{TUE}T18:30:00+00:00", "#%E0%A4%A")[0] is True

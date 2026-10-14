"""The readability pass (2026-10-14): recognise a move before reading about it.

What changed shows each verdict's cited measurement as one before → after line with its window; an opened `§`
drawer draws the cited instrument's recent closes and this session's cited prints as isolated points; a compact
rates strip sits under the lead, read from the same module as Macro & rates; a watch names its state on its
gutter; the curve classifier no longer calls a one-leg move a parallel shift. Presentation only: the frozen
interpretation, the continuity records, the analyst context and the evidence ledger are unchanged.
"""

import re

import pytest
from test_cadence import TUE, Day, phone_layout_width
from test_continuity import carried_setup
from test_editorial_compression import opening, what_changed
from test_pipeline import fixture_packet, narrative

from market_brief.continuity import interpretation_record
from market_brief.curve import RULE_VERSION, classify
from market_brief.render import evidence_chart, move_words, movement_rows, presentation, render

TUE_1 = "watch-sample-premarket-124500-tue-1"
SAME = ["SPY-intraday", "premarket:SPY-intraday"]
SESSION = {"date": "2026-09-08", "previous_session": "2026-09-04",
           "open": "2026-09-08T13:30:00+00:00", "close": "2026-09-08T20:00:00+00:00"}


def print_row(ident, value, observed_at, topic="SPY"):
    return dict(id=ident, topic=topic, metric="premarket return", value=value, unit="%", frequency="intraday",
                observed_at=observed_at, status="AVAILABLE", baseline="latest trade versus previous regular close")


def verdict_page(**fields):
    packet, context, _ = carried_setup()
    value = opening(**fields)
    md, page = render(packet, value, context)
    return packet, value, context, md, page


# --- 1. before → after lines ---------------------------------------------------------------------------------

def test_a_verdict_citing_one_measurement_at_two_clocks_renders_one_before_after_line():
    _, _, _, md, page = verdict_page(watch_updates=[dict(
        carried_id=TUE_1, assessment="weakened", reason="The benchmark print turned positive.", evidence_ids=SAME)])
    since = what_changed(page)
    assert since.count('<span class="moves">') == 1
    block = since.split('<span class="moves">', 1)[1]
    assert '<span class="label">SPY</span>' in block
    assert '<span class="values">−0.53% → <a href="#evidence-SPY-intraday">+0.21%</a></span>' in block
    assert '<span class="verb"><span class="direction-positive">↑</span> turned higher</span>' in block
    # The window names both clocks and both phases, a premarket print never conflated with an intraday one, and
    # carries the evidence marker, so the provenance opens under the comparison, not over it.
    assert ('<span class="window">Premarket 5:41 AM → Intraday 12:06 PM PT · vs prior close '
            '<details class="cite">') in block
    assert "<b>Weakened</b> — If growth retains its lead" in since
    assert since.count('<details class="cite">') == 1 and 'href="#evidence-SPY-intraday"' in block
    assert "  - SPY · −0.53% → +0.21% · turned higher · Premarket 5:41 AM → Intraday 12:06 PM PT · vs prior close" in md


def test_a_record_citing_each_measurement_once_pairs_nothing():
    _, _, _, md, page = verdict_page(
        relationships=[dict(carried_id="rel-sample-premarket-124500-tue-2", instruments=["GLD", "GDX"],
                            statement="Miners trail gold.", assessment="strengthened",
                            reason="The gap widened again.", evidence_ids=["GDX-spread20"])])
    since = what_changed(page)
    assert "<b>Strengthened</b> — Miners trail gold." in since and 'class="moves"' not in since
    assert "  - " not in md.split("## What changed", 1)[1].split("## What matters next", 1)[0]


def test_pairs_are_earliest_against_latest_and_never_across_instruments_or_units():
    rows = [print_row("SPY-intraday", 0.21, "2026-09-08T19:06:00+00:00"),
            print_row("premarket:SPY-intraday", -0.53, "2026-09-08T12:41:00+00:00"),
            print_row("latest:SPY-intraday", -0.10, "2026-09-08T16:00:00+00:00"),
            print_row("QQQ-intraday", 0.40, "2026-09-08T19:06:00+00:00", topic="QQQ"),
            dict(id="SPY-daily", topic="SPY", metric="daily return", value=0.06, unit="%", frequency="daily",
                 observed_at="2026-09-04", status="BACKGROUND"),
            dict(id="treasury-2y", topic="US 2Y", metric="daily par yield", value=3.86, unit="% yield",
                 observed_at="2026-09-04", status="AVAILABLE")]
    moves = movement_rows(rows, SESSION)
    assert [(m["topic"], m["earlier"]["display"], m["later"]["display"]) for m in moves] == [
        ("SPY", "−0.53%", "+0.21%")]
    assert moves[0]["window"] == "Premarket 5:41 AM → Intraday 12:06 PM PT"
    # Two prints in one phase name the phase once.
    same_phase = movement_rows([print_row("a", -0.77, "2026-09-08T13:31:00+00:00"),
                                print_row("b", -0.99, "2026-09-08T14:01:00+00:00")], SESSION)
    assert same_phase[0]["window"] == "Intraday · 6:31 AM → 7:01 AM PT"
    assert same_phase[0]["verb"] == "extended its loss" and same_phase[0]["direction"] == "negative"
    # A dated measurement seen on two dates pairs by date.
    dated = movement_rows([dict(id="x", topic="GDX", metric="relative to GLD", value=-3.13, unit="pp",
                                observed_at="2026-09-04", status="BACKGROUND"),
                           dict(id="previous_close:x", topic="GDX", metric="relative to GLD", value=-2.50, unit="pp",
                                observed_at="2026-09-03", status="BACKGROUND")], SESSION)
    assert dated[0]["window"] == "Thu, Sep 3 → Fri, Sep 4" and dated[0]["measure"] == "vs GLD · 20 sessions"
    assert dated[0]["verb"] == "fell"


@pytest.mark.parametrize("earlier,later,words", [
    (1.80, -1.44, "turned lower"), (-0.53, 0.21, "turned higher"), (0.60, 0.00, "gave back its gain"),
    (-0.40, 0.00, "recovered to flat"), (0.00, -0.30, "turned lower"), (0.30, 0.50, "extended its gain"),
    (0.50, 0.30, "pared its gain"), (-0.77, -0.99, "extended its loss"), (-0.99, -0.77, "pared its loss"),
    (0.21, 0.21, "unchanged"), (0.214, 0.206, "unchanged"),
])
def test_the_verb_for_a_return_against_the_previous_close(earlier, later, words):
    assert move_words(earlier, later, True) == words


def test_the_verb_for_any_other_measurement_is_rose_or_fell():
    assert move_words(31, 36, False) == "rose" and move_words(5.21, 5.18, False) == "fell"


# --- 2. the evidence chart ------------------------------------------------------------------------------------

def test_an_opened_marker_draws_the_cited_instruments_closes_and_isolated_prints():
    _, _, _, _, page = verdict_page(watch_updates=[dict(
        carried_id=TUE_1, assessment="weakened", reason="The benchmark print turned positive.", evidence_ids=SAME)])
    since = what_changed(page)
    figure = since.split('<span class="evidence">', 1)[1].split("</svg>", 1)[0]
    assert '<span class="chart-head"><b>SPY</b><span>20 sessions to Fri, Sep 4 · Tue, Sep 8 prints</span>' in figure
    # The closes are one joined path; the prints are circles with no segment between them.
    closes = figure.split('<g class="closes">', 1)[1].split("</g>", 1)[0]
    prints = figure.split('<g class="prints">', 1)[1].split("</g>", 1)[0]
    assert closes.count("<line") == 20 and closes.count("<circle") == 1
    assert prints.count("<circle") == 2 and "<line" not in prints
    assert '<text x="' in prints and ">5:41 AM</text>" in prints and ">12:06 PM</text>" in prints
    # Direction colour on the prints carries the sign the text already states.
    assert 'class="direction-negative"' in prints and 'class="direction-positive"' in prints
    assert '<line class="zero"' in figure and '<line class="average"' in figure and '<line class="divider"' in figure
    # One legend per drawer, after its figures, naming only the marks the drawer uses.
    drawer = since.split('<details class="cite">', 1)[1].split("</details>", 1)[0]
    assert drawer.count('<span class="curve-legend evidence-legend">') == 1
    assert drawer.index("evidence-legend") > drawer.rindex("</svg>", 0, drawer.index("evidence-legend"))
    assert "prints vs previous close" in drawer and "50DMA" in drawer


def test_the_chart_is_geometry_over_real_closes_and_claims_no_path_between_prints():
    history = dict(symbol="SPY", dates=[f"2026-08-{d:02d}" for d in range(1, 26)],
                   closes=[100 + 0.2 * index for index in range(25)])
    prints = [print_row("a", -0.5, "2026-09-08T12:41:00+00:00"), print_row("b", 0.3, "2026-09-08T19:06:00+00:00")]
    chart = evidence_chart(history, prints, average=103.0, session_date="2026-09-08")
    assert len(chart["path"]["points"]) == 21 and len(chart["path"]["segments"]) == 20
    # The path ends at the previous close, which is zero and sits on the rule.
    assert chart["prior"]["y"] == chart["zero_y"]
    assert chart["path"]["points"][-1]["x"] == "62.0%" and chart["dots"][0]["x"] > chart["divider_x"]
    assert [d["label"] for d in chart["dots"]] == ["5:41 AM", "12:06 PM"]
    # A lower value plots lower; the 50DMA sits at its own distance from the previous close.
    assert chart["dots"][0]["y"] > chart["zero_y"] > chart["dots"][1]["y"]
    assert chart["reference_y"] > chart["zero_y"]  # 103 is below the 104.8 previous close
    # Without prints the path takes the whole width and there is no divider.
    alone = evidence_chart(history, [], None, "2026-09-08")
    assert alone["path"]["points"][-1]["x"] == "100.0%" and alone["divider_x"] is None and alone["dots"] == []
    assert alone["reference_y"] is None and alone["right_label"] == ""
    # Too little history draws nothing.
    assert evidence_chart(dict(symbol="SPY", dates=["2026-09-04"], closes=[100.0])) is None
    # More prints than labels fit label the first and the last only.
    many = [print_row(str(index), 0.1 * index, f"2026-09-08T1{index}:00:00+00:00") for index in range(4, 9)]
    labels = [d["label"] for d in evidence_chart(history, many, None, "2026-09-08")["dots"]]
    assert labels[0] and labels[-1] and not any(labels[1:-1])


def test_a_drawer_citing_no_close_history_draws_nothing_and_tables_draw_nothing():
    packet, value = fixture_packet(), narrative()
    value["sections"]["macro"][0]["evidence_ids"] = ["treasury-2y"]
    _, page = render(packet, value)
    macro = page.split("<h2>Macro &amp; rates</h2>", 1)[1].split("</section>", 1)[0]
    paragraph = macro.split('<div class="para">', 1)[1].split("</details>", 1)[0]
    assert 'class="evidence"' not in paragraph
    # Table proofs keep their lines only.
    assert 'class="evidence"' not in page.split("View exact values", 1)[1].split("</details>", 1)[0]


def test_a_carried_page_draws_the_prints_its_analyst_saw(day):
    """The chart reads the drawer's own rows, frozen with the interpretation, so a refresh draws the same prints its
    verdict was made on, under this run's unchanged prior sessions."""
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0

    def cite_the_print(live):
        live["character"]["evidence_ids"].append("SPY-intraday")
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M", mutate=cite_the_print) == 0
    assert day.run(f"{TUE}T17:00:00+00:00", "HOURLY_1300", prints=(("SPY", 0.9), ("QQQ", 0.8))) == 0
    structure, refresh = day.page("OPEN_30M"), day.page("HOURLY_1300")

    def figures(page):
        head = page.split("<h1>", 1)[1].split("<h2>Macro", 1)[0]
        return re.findall(r'<span class="evidence">.*?</svg>', head, re.S)
    assert figures(structure) and figures(structure) == figures(refresh)
    # The refresh's own SPY print is +0.90%; its chart still plots the −0.53% its analyst saw.
    spy = next(figure for figure in figures(refresh) if "<b>SPY</b>" in figure)
    assert '<g class="prints"><circle class="direction-negative"' in spy
    assert '<span class="value">+0.90%</span>' in refresh


# --- 3. the rates strip ---------------------------------------------------------------------------------------

def test_the_rates_strip_sits_under_the_lead_and_agrees_with_the_module():
    packet, value = fixture_packet(), narrative()
    md, page = render(packet, value)
    view = presentation(packet, value)
    strip = page.split('<section class="rates-strip">', 1)[1].split("</section>", 1)[0]
    assert page.index('<section class="rates-strip">') < page.index("<h2>What matters next</h2>")
    assert page.index("<h1>") < page.index('<section class="rates-strip">')
    # The same caption as the module: the curve's own date, never this session's clock.
    assert '<div class="caption">Rates<span>Fri, Sep 4 · latest official daily observation</span></div>' in strip
    tenors = re.findall(r"<dt>(\w+)</dt><dd[^>]*>(.*?)</dd>", strip)
    assert [t[0] for t in tenors] == ["2Y", "5Y", "10Y", "30Y"]
    for (maturity, text), row in zip(tenors, view["macro"]["yields"]):
        assert maturity == row["maturity"] and text.startswith(row["level"]["display"])
        if not row["change"]["absent"]:
            assert f"<small>{row['change']['display']}</small>" in text
    assert "2s10s 26 bp · 5 bp steeper" in strip and "<b>Bull steepener</b>" in strip
    assert '<a href="#treasury-curve">Full curve and chart</a>' in strip
    assert '<div class="caption" id="treasury-curve">U.S. Treasury par curve<span>' in page
    # A missing spread is the module's detail and stays there; a note that places the curve in time travels.
    assert "No 5s30s" not in strip and "No 5s30s: the latest curve has no 5Y or 30Y yield." in page
    assert md.index("**RATES** · Fri, Sep 4 · latest official daily observation · 2Y 3.86% −4 bp") < md.index(
        "## What matters next")


def test_no_curve_no_strip():
    packet, value = fixture_packet(), narrative()
    packet["observations"] = [row for row in packet["observations"] if not row["topic"].startswith("US ")]
    packet["derived"] = [row for row in packet["derived"] if not row["topic"].startswith("US ")]
    value["sections"]["macro"] = []
    md, page = render(packet, value)
    assert 'class="rates-strip"' not in page and "**RATES**" not in md


# --- 4. watch states -------------------------------------------------------------------------------------------

def test_watches_name_their_state_on_the_gutter():
    _, _, _, md, page = verdict_page(watch_updates=[dict(
        carried_id=TUE_1, assessment="strengthened", reason="Growth held its lead.", evidence_ids=SAME)])
    matters = page.split("<h2>What matters next</h2>", 1)[1].split("</section>", 1)[0]
    assert '<div class="watch state-active">' in matters   # the new watch
    judged = matters.split('<div class="watch state-judged">', 1)[1].split("</div></div>", 1)[0]
    assert "From an earlier read · into the close · verdict in What changed" in judged
    assert "strengthened" not in judged.lower() and "Growth held its lead." not in judged
    assert "- **FROM AN EARLIER READ · Into the close · verdict in What changed** — " in md
    # The expired Friday watch sits in the drawer with the grey state.
    drawer = matters.split('<details class="drawer retired">', 1)[1]
    assert '<div class="watch state-expired">' in drawer and "horizon passed" in drawer


def test_an_unresolved_live_watch_keeps_its_assessment_as_its_state():
    _, _, _, _, page = verdict_page(watch_updates=[dict(
        carried_id=TUE_1, assessment="unresolved", reason="No comparable print yet.", evidence_ids=SAME)])
    matters = page.split("<h2>What matters next</h2>", 1)[1].split("</section>", 1)[0]
    assert '<div class="watch state-unresolved">' in matters and "verdict in What changed" not in matters


def test_the_style_turns_only_passed_horizons_grey():
    _, page = render(fixture_packet(), narrative())
    style = re.search(r"<style>.*?</style>", page, re.S).group(0)
    assert ".watch.state-expired,.watch.state-completed{border-left-color:var(--rule-strong)}" in style
    assert "state-judged{" not in style and "state-unresolved{" not in style


# --- 5. the curve classifier ------------------------------------------------------------------------------------

def test_october_13_is_broadly_lower_with_mild_flattening_not_a_parallel_shift():
    assert RULE_VERSION == "curve-move.v2"
    move = classify(-1, -3, long_change=-2, threshold=3)
    assert move["label"] == "Broadly lower, mild flattening" and move["rule"] == "broad"
    assert move["sentence"] == "Yields fell across the curve, a little more at the long end." and move["note"] == ""
    # Both legs at the threshold in one direction is still a parallel shift; a trailing leg under it is not.
    assert classify(-3, -4, threshold=3)["label"] == "Parallel shift lower"
    assert classify(-4, -2, threshold=3)["label"] == "Broadly lower, mild steepening"
    assert classify(2, 3, threshold=3)["label"] == "Broadly higher, mild steepening"
    assert classify(3, 1, threshold=3)["label"] == "Broadly higher, mild flattening"
    # A broad move with a long-end move keeps the long-end note, as a parallel shift does.
    assert classify(-1, -3, long_change=-4, threshold=3)["note"] == "5s30s flattened 4 bp."


# --- 6. the audit section and the What changed note -------------------------------------------------------------

def test_coverage_limitations_stay_visible_and_the_ledger_stays_one_step_away():
    packet = fixture_packet()
    _, page = render(packet, narrative())
    audit = page.split('<section class="audit"><h2>Sources &amp; coverage</h2>', 1)[1]
    # The fixture records no deterministic limitation: a closed drawer says so without an empty list.
    assert ('<details class="drawer"><summary>Coverage limitations <span>· none recorded</span></summary>'
            '</details>') in audit
    assert '<details class="drawer" id="evidence-ledger"><summary>Evidence ledger' in audit
    assert 'href="#evidence-ledger">Full evidence ledger</a>' in page
    # A recorded limitation is open on arrival, counted in its summary.
    packet["coverage"]["limitations"] = ["Not automated: options positioning", "QQQ: no print"]
    _, page = render(packet, narrative())
    audit = page.split('<section class="audit"><h2>Sources &amp; coverage</h2>', 1)[1]
    assert '<details class="drawer" open><summary>Coverage limitations <span>· 2</span></summary><ul>' in audit
    assert "<li>Not collected: options positioning</li><li>QQQ: no print</li>" in audit


def test_the_no_change_note_reads_plainly():
    """A premarket over a carried close whose every comparison repeated the prior close."""
    packet, value = fixture_packet(), narrative()
    comparison = dict(id="cmp-previous_close-SPY-daily", anchor="previous_close", topic="SPY", metric="daily return",
                      unit="%", status="no_new_observation", prior_ref="previous_close:SPY-daily",
                      current_ref="SPY-daily", prior_value=0.06, current_value=0.06, delta=None,
                      prior_observed_at="2026-09-04", current_observed_at="2026-09-04")
    context = dict(prior_state=dict(status="available", reason="", watches=[], relationships=[], snapshots=[],
                                    anchors={"previous_close": dict(session_date="2026-09-04", run_id="r1")}),
                   comparisons=[comparison, dict(comparison, id="cmp-previous_close-QQQ-daily", topic="QQQ",
                                                 prior_ref="previous_close:QQQ-daily", current_ref="QQQ-daily")])
    md, page = render(packet, value, context)
    since = page.split('<section class="since">', 1)[1].split("</section>", 1)[0]
    note = re.search(r'<p class="fine">(.*?)</p>', since).group(1)
    assert note == ("Nothing comparable has moved: 2 prior-close observations repeated and no new session prints "
                    "have arrived.")
    assert "comparable measurement" not in page and "comparable measurement" not in md
    # A carried refresh dates the same note to the read that made the comparison.
    record = interpretation_record(packet, value, context, origin=dict(run_id="earlier-run"))
    carried = presentation(packet, interpretation=record)["since"]
    assert carried["label"] == "vs the previous close · Fri, Sep 4 · through the 5:45 AM PT analysis"
    assert carried["note"] == ("Nothing comparable had moved by the 5:45 AM PT read: 2 prior-close observations "
                               "repeated and no new session prints had arrived.")


def test_a_past_event_without_values_says_so():
    packet, value = fixture_packet(), narrative()
    event = next(e for e in packet["events"] if e.get("scheduled_at"))
    md, page = render(packet, value)
    assert "released · values not collected" not in page  # the fixture's event is still ahead of its run
    event["scheduled_at"] = "2026-09-08T12:00:00+00:00"  # before the 12:45 UTC premarket run
    md, page = render(packet, value)
    line = "Tuesday, Sep 8 · 5:00 AM PT · during session · released · values not collected"
    assert f"<b>{event['title']}</b>{line}" in page
    assert f"- **Event** — {event['title']} · {line}" in md


# --- 7. phones --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("width", [390, 360, 320])
def test_the_new_blocks_fit_a_phone_with_every_drawer_open(tmp_path, width):
    _, _, _, _, page = verdict_page(
        watch_updates=[dict(carried_id=TUE_1, assessment="weakened", reason="The benchmark print turned positive.",
                            evidence_ids=SAME)],
        changes=[dict(comparison_id="cmp-premarket-SPY-intraday",
                      text="SPY turned from {{premarket:SPY-intraday}} to {{SPY-intraday}}.",
                      evidence_ids=["SPY-intraday", "premarket:SPY-intraday", "QQQ-daily"])])
    opened = page.replace("<details", "<details open")
    assert phone_layout_width(opened, tmp_path, width) <= width


@pytest.fixture
def day(monkeypatch, tmp_path):
    return Day(monkeypatch, tmp_path)

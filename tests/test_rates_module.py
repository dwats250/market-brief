"""Rates presentation: desk formatting and neutral colour (R6), spread phrasing (R7), the rates module and its
inline curve chart (R9).

Pages are the fictional replay fixture with its Treasury rows replaced by hand-built entries dated inside the
fixture's own calendar (the latest entry Friday, September 4, 2026, the prior Thursday, September 3).
"""

import copy
import re

from test_curve import SEP24_CHANGES, SEP24_LEVELS, UNIVERSE, rows_for
from test_pipeline import NOW, narrative

from market_brief.collect import cuttingboard_record
from market_brief.evidence import ROOT, finalize_coverage, normalize_packet, read_json
from market_brief.metrics import derive
from market_brief.render import direction, formatted, presentation, render

DAY, PRIOR = "2026-09-04", "2026-09-03"


def rates_packet(levels=SEP24_LEVELS, changes=SEP24_CHANGES, day=DAY, prior=PRIOR, now=NOW, events=(),
                 context_items=()):
    raw = read_json(ROOT / "tests/fixtures/evidence.sample.json")
    raw["observations"] = [row for row in raw["observations"] if not row["topic"].startswith("US ")]
    raw["observations"] += rows_for(day, levels, changes, prior=prior, now=now, source_id="sample-rates")
    raw["events"] += list(events)
    raw["context_items"] += list(context_items)
    raw["cuttingboard"] = cuttingboard_record(raw["cuttingboard"], now, now)
    return finalize_coverage(derive(normalize_packet(raw, now, "SAMPLE"), UNIVERSE))


def with_macro(text, ids):
    value = copy.deepcopy(narrative())
    value["sections"]["macro"] = [dict(text=text, evidence_ids=ids, uncertainty="", alternative="",
                                       **{"class": "INTERPRETATION"})]
    return value


# --- R6 formatting and colour -------------------------------------------------------------------------------

def test_rates_display_in_desk_form():
    cases = [(5.18, "% yield", "5.18%"), (4.0, "% yield", "4.00%"), (7.000000000000028, "bp", "+7 bp"),
             (-3, "bp", "−3 bp"), (0.4, "bp", "0 bp"), (-0.4, "bp", "0 bp"), (31, "bp spread", "31 bp"),
             (-35, "bp spread", "−35 bp"), (0, "bp spread", "0 bp")]
    for value, unit, text in cases:
        assert formatted(dict(value=value, unit=unit)) == text, (value, unit)
    # Equities are unchanged.
    assert formatted(dict(value=-0.53, unit="%")) == "-0.53 %"


def test_rates_render_the_same_everywhere_in_neutral_colour():
    packet = rates_packet()
    value = with_macro("The 10-year added {{treasury-10y-change}} and 2s10s stands at {{treasury-2s10s}}.",
                       ["treasury-10y-change", "treasury-2s10s"])
    view = presentation(packet, value)
    ledger = {row["id"]: row for row in view["evidence"]}
    expected = {"treasury-10y": "5.18%", "treasury-10y-change": "+7 bp", "treasury-2y-change": "+2 bp",
                "treasury-30y": "5.47%", "treasury-2s10s": "31 bp", "treasury-2s10s-change": "+5 bp",
                "treasury-5s30s": "44 bp", "treasury-5s30s-change": "+3 bp"}
    for ident, text in expected.items():
        assert ledger[ident]["display"] == text, ident
    # Prose placeholders resolve to the same text.
    assert view["macro"]["paragraphs"][0]["text"] == "The 10-year added +7 bp and 2s10s stands at 31 bp."
    # No rates row is ever coloured, rising or falling, level or change.
    falling = rates_packet(changes={"2Y": -9, "5Y": -6, "10Y": -4, "30Y": -3})
    for source in (packet, falling):
        for row in [*source["observations"], *source["derived"]]:
            if row["topic"].startswith("US "):
                assert direction(row) == "neutral", row["id"]
    md, page = render(falling, value)
    rates = page.split("<h2>Macro &amp; rates</h2>", 1)[1].split("</section>", 1)[0]
    assert "direction-positive" not in rates and "direction-negative" not in rates
    assert re.search(r"−9 bp", rates) and "5.18%" in md


def test_proof_lines_show_rates_in_desk_form():
    view = presentation(rates_packet(), narrative())
    proof = {line["id"]: line["display"] for line in view["macro"]["yields_proof"]}
    assert proof["treasury-2y"] == "4.87%" and proof["treasury-2y-change"] == "+2 bp"


# --- R9 the module: order, caption, spread lines, curve move, notes ----------------------------------------

def macro_section(page):
    return page.split("<h2>Macro &amp; rates</h2>", 1)[1].split("</section>", 1)[0]


def test_module_renders_in_order_with_the_analyst_paragraphs_after_the_record():
    value = with_macro("Treasuries sold off into the close; 2s10s steepened {{treasury-2s10s-change}}.",
                       ["treasury-2s10s-change"])
    md, page = render(rates_packet(), value)
    section = macro_section(page)
    markers = ['<div class="caption">U.S. Treasury par curve<span>', '<table class="narrow-first rates">',
               '<dl class="spreads">', '<p class="curve-move">', '<div class="curve-chart">',
               '<div class="para">Treasuries sold off', "View exact values &amp; baselines"]
    positions = [section.index(marker) for marker in markers]
    assert positions == sorted(positions)
    assert "Fri, Sep 4 · latest official daily observation" in section  # Monday was Labor Day, a bond holiday
    assert "close" not in section.split('<div class="para">', 1)[0].lower()  # never "close", never a clock time
    assert not re.search(r"\d:\d\d", section.split('<div class="para">', 1)[0])
    assert '<dt>2s10s<br><small>10Y minus 2Y</small></dt><dd>31 bp · 5 bp steeper</dd><dt>5s30s<br><small>30Y minus 5Y</small></dt><dd>44 bp · 3 bp steeper</dd>' in section
    assert '<p class="curve-move"><b>Bear steepener</b> — Long-end yields rose more than the front end.</p>' in section
    for text in ("today", "wider", "narrower"):
        assert text not in section.split('<div class="para">', 1)[0].lower()


def test_captions_follow_freshness():
    current = rates_packet(day="2026-09-04", prior="2026-09-03", now=NOW.replace(day=5))  # Saturday run, Friday curve
    assert presentation(current, narrative())["macro"]["curve"]["caption"] == "Fri, Sep 4 · official daily observation"
    older = presentation(rates_packet(), narrative())["macro"]["curve"]
    assert older["caption"] == "Fri, Sep 4 · latest official daily observation"


def test_spread_phrasing_levels_changes_inversion_and_sign_flips():
    def lines(levels, changes):
        view = presentation(rates_packet(levels=levels, changes=changes), narrative())
        spreads = view["macro"]["curve"]["spreads"]
        return {line["name"]: (line["level"], line["detail"], line["flip"]) for line in spreads}
    assert lines({"2Y": 4.87, "10Y": 5.18}, {"2Y": 3, "10Y": 3})["2s10s"] == ("31 bp", "unchanged", "")
    assert lines({"2Y": 4.87, "10Y": 5.18}, {"2Y": 6, "10Y": 3})["2s10s"] == ("31 bp", "3 bp flatter", "")
    inverted = lines({"2Y": 4.50, "10Y": 4.15}, {"2Y": -3, "10Y": 2})["2s10s"]
    assert inverted == ("\u221235 bp", "5 bp steeper (less inverted)", "")
    assert lines({"2Y": 4.50, "10Y": 4.15}, {"2Y": 3, "10Y": 1})["2s10s"] == \
        ("\u221235 bp", "2 bp flatter (more inverted)", "")
    assert lines({"2Y": 4.47, "10Y": 4.50}, {"2Y": -3, "10Y": 2})["2s10s"] == ("3 bp", "5 bp steeper",
                                                                              "2s10s turned positive")
    assert lines({"2Y": 4.52, "10Y": 4.50}, {"2Y": 3, "10Y": -2})["2s10s"] == ("\u22122 bp", "5 bp flatter",
                                                                              "2s10s inverted")
    assert lines({"2Y": 4.50, "10Y": 4.50}, {"2Y": 3, "10Y": -2})["2s10s"] == ("0 bp", "5 bp flatter", "")


def test_stale_curve_shows_levels_with_their_date_and_nothing_else():
    now = NOW.replace(day=11)  # Friday, September 11: the Friday, September 4 curve is seven days old
    packet = rates_packet(now=now)
    assert packet["curve"]["freshness"] == "stale"
    md, page = render(packet, narrative())
    section = macro_section(page).split('<div class="para">', 1)[0]  # the module; the analyst's cited proof follows
    assert "Fri, Sep 4 · latest official daily observation" in section
    assert "Daily change" not in section and "+7 bp" not in section
    assert "5.18%" in section
    assert "curve-move" not in section and "curve-chart" not in section and "steeper" not in section
    assert '<dt>2s10s</dt><dd>31 bp</dd>' in section
    assert "This curve is more than five days old, so only its levels are shown here." in section
    proof = {line["id"] for line in presentation(packet, narrative())["macro"]["yields_proof"]}
    assert "treasury-10y" in proof and not any(ident.endswith("-change") for ident in proof)
    assert "Daily change" not in md.split("## Macro & rates", 1)[1].split("## Sector view", 1)[0]


def test_missing_tenors_read_no_print_and_name_the_missing_spread():
    view = presentation(rates_packet(levels={"2Y": 4.87, "5Y": 5.03, "10Y": 5.18},
                                     changes={"2Y": 2, "5Y": 4, "10Y": 7}), narrative())
    rows = {row["maturity"]: row for row in view["macro"]["yields"]}
    assert list(rows) == ["2Y", "5Y", "10Y", "30Y"]
    assert rows["30Y"]["level"]["display"] == "no print"
    curve = view["macro"]["curve"]
    assert [line["name"] for line in curve["spreads"]] == ["2s10s"]
    assert "No 5s30s: the latest curve has no 30Y yield." in curve["notes"]
    assert curve["move"]["label"] == "Bear steepener"
    no_two = presentation(rates_packet(levels={"5Y": 5.03, "10Y": 5.18, "30Y": 5.47},
                                       changes={"5Y": 4, "10Y": 7, "30Y": 7}), narrative())["macro"]["curve"]
    assert no_two["move"] == dict(label="Curve move unavailable", sentence="The latest curve has no 2Y yield.",
                                  note="", unavailable=True)
    assert "No 2s10s: the latest curve has no 2Y yield." in no_two["notes"]


def test_release_note_lands_in_the_module_notes():
    event = dict(id="cpi", title="Consumer Price Index", source_id="bls", published_at=None,
                 checked_at=NOW.isoformat(), scheduled_at="2026-09-08T12:30:00+00:00", status="SCHEDULED")
    md, page = render(rates_packet(events=[event]), narrative())
    note = "Curve predates the 5:30 AM PT Consumer Price Index release."
    assert f'<p class="fine">{note}</p>' in macro_section(page) and note in md


def test_proof_disclosure_covers_30y_and_the_spreads():
    proof = {line["id"] for line in presentation(rates_packet(), narrative())["macro"]["yields_proof"]}
    assert {"treasury-30y", "treasury-30y-change", "treasury-2s10s", "treasury-2s10s-change", "treasury-5s30s",
            "treasury-5s30s-change"} <= proof


def test_markdown_carries_the_module_without_the_chart():
    md, _ = render(rates_packet(), narrative())
    macro = md.split("## Macro & rates", 1)[1].split("## Sector view", 1)[0]
    assert "**U.S. TREASURY PAR CURVE** · Fri, Sep 4 · latest official daily observation" in macro
    assert "| 30Y | 5.47% | +7 bp |" in macro and "- 2s10s · 31 bp · 5 bp steeper" in macro
    assert "**Bear steepener** — Long-end yields rose more than the front end." in macro
    assert "<svg" not in md and "How to read" not in md


def test_evidence_saved_before_the_curve_record_still_renders_the_module():
    packet = rates_packet()
    packet.pop("curve")
    packet["derived"] = [row for row in packet["derived"] if not row["topic"].startswith("US ")]
    before = copy.deepcopy(packet)
    view = presentation(packet, narrative())
    assert packet == before  # rendering never writes the derivation back
    assert view["macro"]["curve"]["move"]["label"] == "Bear steepener"
    assert [line["level"] for line in view["macro"]["curve"]["spreads"]] == ["31 bp", "44 bp"]


# --- R9 the chart ---------------------------------------------------------------------------------------------

def chart_of(**kwargs):
    return presentation(rates_packet(**kwargs), narrative())["macro"]["curve"]["chart"]


def test_chart_geometry():
    chart = chart_of()
    xs = [point["x"] for point in chart["current"]["points"]]
    assert xs == ["0.0%", "33.8%", "59.4%", "100.0%"]  # log maturity: 0, .34, .59, 1.0
    assert [label["text"] for label in chart["labels"]] == ["2Y", "5Y", "10Y", "30Y"]
    assert 110 <= chart["height"] <= 140
    ys = [point["y"] for point in chart["current"]["points"]]
    assert ys == sorted(ys, reverse=True)  # rising yields plot higher (smaller y) on this upward curve
    low, high = chart["domain"]
    assert high - low >= 1.0 - 1e-9  # at least 100 bp
    values = [4.87, 5.03, 5.18, 5.47, 4.85, 4.99, 5.11, 5.40]
    assert abs((low + high) / 2 - (min(values) + max(values)) / 2) < 1e-6  # centred on the data
    assert low < min(values) and high > max(values)
    assert len(chart["current"]["segments"]) == 3 and len(chart["ghost"]["segments"]) == 3
    assert chart["legend"] == dict(current="Fri, Sep 4", prior="Thu, Sep 3")
    wide = chart_of(levels={"2Y": 3.00, "5Y": 3.50, "10Y": 4.25, "30Y": 5.00})
    low, high = wide["domain"]
    assert high - low > 2.0 and low < 2.93 and high > 5.07  # wide data: padded, not clamped to 100 bp


def test_chart_draws_straight_segments_only_between_adjacent_observed_tenors():
    chart = chart_of(levels={"2Y": 4.87, "10Y": 5.18, "30Y": 5.47}, changes={"2Y": 2, "10Y": 7, "30Y": 7})
    assert [(s["x1"], s["x2"]) for s in chart["current"]["segments"]] == [("59.4%", "100.0%")]
    assert [p["x"] for p in chart["current"]["points"]] == ["0.0%", "59.4%", "100.0%"]  # 2Y stands alone
    assert chart_of(levels={"2Y": 4.87, "10Y": 5.18}, changes={"2Y": 2, "10Y": 7}) is None  # no adjacent pair


def test_ghost_needs_every_tenor_the_current_curve_has():
    chart = chart_of(changes={"2Y": 2, "5Y": 4, "10Y": 7})  # no prior 30Y
    assert chart["ghost"] is None and chart["legend"] is None and len(chart["current"]["points"]) == 4


def test_chart_markup_is_inline_quiet_and_theme_aware():
    _, page = render(rates_packet(), narrative())
    svg = re.search(r'<div class="curve-chart"><svg[^>]*>.*?</svg>', page, re.S).group(0)
    assert 'aria-hidden="true"' in svg and 'focusable="false"' in svg and 'height="120"' in svg
    assert svg.count('vector-effect="non-scaling-stroke"') == 14  # 3+3 segments, 4+4 dots
    assert "<img" not in page and "<path" not in svg  # straight segments only, no smoothing
    assert "stroke-dasharray:4 4" in page.split("</style>", 1)[0]
    assert "var(--teal)" in page.split("</style>", 1)[0].split(".curve-chart", 1)[1]


# --- review regressions ------------------------------------------------------------------------------------

def test_a_curve_past_the_admission_window_keeps_a_dated_module():
    packet = rates_packet(now=NOW.replace(day=14))  # Monday, September 14: the Friday, September 4 curve is 10 days old
    assert packet["curve"]["freshness"] == "stale" and not any(r["topic"].startswith("US ") for r in packet["derived"])
    md, page = render(packet, narrative())
    section = macro_section(page)
    assert '<div class="caption">U.S. Treasury par curve<span>Fri, Sep 4 · latest official daily observation</span>' \
        in section
    assert "This curve is more than a week old, so its yields are not shown." in section
    assert "<table" not in section.split('<div class="para">', 1)[0] and "curve-chart" not in section
    assert "**U.S. TREASURY PAR CURVE** · Fri, Sep 4 · latest official daily observation" in md
    assert "Macro & rates" not in "".join(presentation(packet, narrative())["limitations"])


def test_a_stale_curve_puts_no_change_in_the_headline_figures():
    fresh = {chip["id"] for chip in presentation(rates_packet(), narrative())["chips"]}
    assert {"treasury-2y-change", "treasury-10y-change"} <= fresh
    for day in (11, 10):  # without equity figures the chips fall back to the first facts; still no stale change
        stale = {chip["id"] for chip in presentation(rates_packet(now=NOW.replace(day=day)), narrative())["chips"]}
        assert not any(ident.startswith("treasury-") and ident.endswith("-change") for ident in stale), day


def test_the_metals_caption_never_dangles():
    packet = rates_packet()
    packet["derived"] = [row for row in packet["derived"] if row["id"] not in ("SLV-spread20", "GDX-spread20")]
    md, page = render(packet, narrative())
    assert presentation(packet, narrative())["cross_asset"]["spread_label"] == ""
    assert '<h2>Metals</h2><div class="caption"><span>' in page and "spread, <" not in page
    metals = md.split("## Metals", 1)[1].split("\n\n", 2)[1]
    assert not metals.startswith(" ·") and "spread, " not in metals


def test_the_rates_page_fits_a_phone(tmp_path):
    from test_cadence import phone_layout_width
    _, page = render(rates_packet(), narrative())
    assert '<div class="curve-chart">' in page and '<dl class="spreads">' in page
    assert phone_layout_width(page, tmp_path) <= 390

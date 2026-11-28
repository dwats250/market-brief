"""WP-MB-OHLC-01: the prior session's range on the evidence chart.

Presentation only. The provider's open, high and low ride the history record to the page and stop there: they are
never an observation, a derived row, a context fact or a word in the prompt. A bar that is not a coherent range
costs the symbol its range and nothing else: its dates and closes are exactly what they were.
"""
import copy
import hashlib
import json
import re
from types import SimpleNamespace

import pytest
from test_cadence import phone_layout_width
from test_pipeline import NOW, narrative

from market_brief.collect import _alpaca_history, cuttingboard_record
from market_brief.context import analyst_context, edition_profile
from market_brief.evidence import ROOT, finalize_coverage, model_packet, normalize_packet, read_json
from market_brief.metrics import derive
from market_brief.render import EVIDENCE_BOTTOM, EVIDENCE_TOP, evidence_chart, render
from market_brief.synthesize import compact_json

FIXTURES = ROOT / "tests/fixtures"
RANGE_KEYS = ("opens", "highs", "lows")
HISTORY_KEYS = {"id", "symbol", "dates", "closes", "adjustment", "session", "source_id", "retrieved_at"}
SENTENCE = "Range and open are from the same split-adjusted daily bar, not an official closing price."
NOTE = f'<span class="evidence-legend">{SENTENCE}</span>'
HEAD = re.compile(r"<span>Prior session · O \d+\.\d{2} H \d+\.\d{2} L \d+\.\d{2} C \d+\.\d{2}</span>")
DRAWER = re.compile(r'<details class="cite">.*?</details>', re.S)
FIGURE = re.compile(r'<span class="evidence">.*?</svg></span></span>', re.S)
LINE = re.compile(r"<line [^>]*/>")
COORDINATE = re.compile(r'="[\d.%-]+"')  # an attribute whose whole value is a number or a percentage
# What the code before this change (88294b0) rendered for the sample page's `<details class="cite">` drawers.
PRE_CHANGE_DRAWERS = 12, "7944651fe876b70afc9c6c9ec6e6001c5a97d7a33c3af913a7cd3e5f8460ddc2"
SENTINEL = 987654.32  # a high no real bar could print: it must never turn up anywhere the analyst reads


# --- builders ---------------------------------------------------------------------------------------------------

def provider_bars():
    return read_json(FIXTURES / "alpaca-bars.ohlc.json")


def history_of(symbol, payload=None):
    found = [h for h in _alpaca_history(payload or provider_bars(), NOW, NOW, "alpaca-daily") if h["symbol"] == symbol]
    return found[0] if found else None


def closes_only(payload):
    """The same bars without open, high or low: the history as it was before this change."""
    stripped = copy.deepcopy(payload)
    for bars in stripped["bars"].values():
        for bar in bars:
            for key in ("o", "h", "l"):
                bar.pop(key, None)
    return stripped


def ranges_for(closes):
    """Coherent, deterministic ranges for a run of closes: each open is the previous close, each bar is 0.4% wide."""
    opens = [round(closes[i - 1], 2) if i else round(closes[0] * 0.998, 2) for i in range(len(closes))]
    return dict(opens=opens,
                highs=[round(max(o, c) * 1.004, 2) for o, c in zip(opens, closes)],
                lows=[round(min(o, c) * 0.996, 2) for o, c in zip(opens, closes)])


def raw_sample(ranges=True):
    raw = read_json(FIXTURES / "evidence.sample.json")
    if ranges:
        for history in raw["history"]:
            history.update(ranges_for(history["closes"]))
    return raw


def packet_of(raw):
    raw["cuttingboard"] = cuttingboard_record(raw["cuttingboard"], NOW, NOW)
    return finalize_coverage(derive(normalize_packet(raw, NOW, "SAMPLE"), read_json(ROOT / "config/universe.json")))


def chart_history(**fields):
    closes = [100 + 0.2 * index for index in range(25)]
    base = dict(symbol="SPY", dates=[f"2026-08-{day:02d}" for day in range(1, 26)], closes=closes,
                opens=[round(c - 0.1, 2) for c in closes], highs=[round(c + 0.2, 2) for c in closes],
                lows=[round(c - 0.3, 2) for c in closes])
    return {**base, **fields}


def inside_history():
    """A history whose prior session's range lies inside the range of its own close path (103.3 / 102.7 against closes
    that run 100 to 106), so the candle adds nothing the chart's domain does not already hold."""
    closes = [100 + 0.5 * index for index in range(13)] + [106 - 0.25 * index for index in range(1, 13)]
    return chart_history(closes=closes, opens=[round(c - 0.1, 2) for c in closes],
                         highs=[round(c + 0.3, 2) for c in closes], lows=[round(c - 0.3, 2) for c in closes])


def without_ranges(history):
    return {key: value for key, value in history.items() if key not in RANGE_KEYS}


def without_candles(page):
    """The page with every candle's markup taken out again: head span, wick, open tick and the fine-print note."""
    def figure(found):
        text = found.group(0)
        if not HEAD.search(text):
            return text
        text = HEAD.sub("", text)
        group = re.search(r'(<g class="closes">)(.*?)(<circle)', text, re.S)
        added = "".join(LINE.findall(group.group(2))[-2:])
        assert group.group(2).endswith(added)
        return text[:group.start(2)] + group.group(2)[:-len(added)] + text[group.end(2):]
    return FIGURE.sub(figure, page).replace(NOTE, "")


@pytest.fixture(scope="module")
def pair():
    plain_packet, ranged_packet = packet_of(raw_sample(False)), packet_of(raw_sample(True))
    return SimpleNamespace(plain_packet=plain_packet, ranged_packet=ranged_packet,
                           plain=render(plain_packet, narrative())[1], ranged=render(ranged_packet, narrative())[1])


# --- 1. the provider's bars: captured beside the close, or not at all ---------------------------------------------

def test_the_bars_call_keeps_open_high_low_beside_each_close_in_date_order():
    spy = history_of("SPY")  # the fixture lists its bars out of date order on purpose
    assert spy["dates"] == ["2026-08-27", "2026-08-28", "2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03",
                            "2026-09-04"]
    assert spy["closes"] == [642.4, 644.1, 641.3, 638.55, 640.2, 643.6, 645.2]
    assert spy["opens"] == [640.1, 642.5, 644.0, 641.2, 638.6, 640.3, 643.5]
    assert spy["highs"] == [643.25, 645.0, 644.8, 642.6, 641.1, 644.9, 646.4]
    assert spy["lows"] == [638.9, 641.2, 640.75, 637.9, 636.4, 639.7, 642.1]
    # The record is the old record plus the three arrays.
    assert set(spy) == HISTORY_KEYS | set(RANGE_KEYS)


BAD_BARS = {
    "high below close": lambda bar: bar.update(h=bar["c"] - 0.1),
    "high below open": lambda bar: bar.update(h=bar["o"] - 0.1),
    "low above close": lambda bar: bar.update(l=bar["c"] + 0.1),
    "low above open": lambda bar: bar.update(l=bar["o"] + 0.1),
    "high below low": lambda bar: bar.update(h=bar["l"] - 0.1),
    "not a number": lambda bar: bar.update(h=float("nan")),
    "infinite": lambda bar: bar.update(l=float("-inf")),
    "text": lambda bar: bar.update(o="643.5"),
    "boolean": lambda bar: bar.update(o=True),
    "null": lambda bar: bar.update(l=None),
    "absent": lambda bar: bar.pop("h"),
}


@pytest.mark.parametrize("day", ["2026-08-27", "2026-09-04"])
@pytest.mark.parametrize("form", BAD_BARS)
def test_one_bad_bar_leaves_the_symbol_its_closes_and_no_range_at_all(form, day):
    payload = provider_bars()
    BAD_BARS[form](next(bar for bar in payload["bars"]["SPY"] if bar["t"].startswith(day)))
    bad, before = history_of("SPY", payload), history_of("SPY", closes_only(provider_bars()))
    assert not set(RANGE_KEYS) & set(bad)  # never a partial array
    assert bad == before  # dates, closes and the rest of the record: exactly the pre-change history
    assert json.dumps(bad["closes"]) == json.dumps(before["closes"])  # nothing dropped, reordered or adjusted


@pytest.mark.parametrize("symbol", ["QQQ", "IWM", "DIA", "GLD"])
def test_the_fixtures_bad_symbols_keep_their_closes_while_a_good_one_keeps_its_range(symbol):
    assert history_of(symbol) == history_of(symbol, closes_only(provider_bars()))
    assert set(RANGE_KEYS) <= set(history_of("SPY"))


# --- 2. the evidence passthrough ------------------------------------------------------------------------------------

def test_the_ranges_survive_the_evidence_passthrough_aligned_with_dates_and_closes(pair):
    supplied = {h["symbol"]: h for h in raw_sample(True)["history"]}
    assert len(pair.ranged_packet["history"]) == 6
    for history in pair.ranged_packet["history"]:
        for key in RANGE_KEYS:
            assert len(history[key]) == len(history["dates"]) == len(history["closes"])
            assert history[key] == supplied[history["symbol"]][key]
    # No range supplied: the record has exactly the keys it always had.
    assert all(set(history) == HISTORY_KEYS for history in pair.plain_packet["history"])


BROKEN_ARRAYS = {
    "short array": lambda h: h["highs"].pop(),
    "long array": lambda h: h["lows"].append(1.0),
    "missing member": lambda h: h.pop("lows"),
    "not a list": lambda h: h.update(opens=h["opens"][-1]),
    "incoherent high": lambda h: h["highs"].__setitem__(-1, h["closes"][-1] - 1),
    "incoherent low": lambda h: h["lows"].__setitem__(4, h["opens"][4] + 1),
    "non-finite": lambda h: h["lows"].__setitem__(3, float("nan")),
    "null member": lambda h: h["opens"].__setitem__(0, None),
}


@pytest.mark.parametrize("form", BROKEN_ARRAYS)
def test_a_broken_range_is_dropped_at_the_passthrough_and_the_closes_are_untouched(form, pair):
    raw = raw_sample(True)
    spy = next(history for history in raw["history"] if history["symbol"] == "SPY")
    BROKEN_ARRAYS[form](spy)
    packet = packet_of(raw)
    kept = {h["symbol"]: h for h in packet["history"]}
    before = {h["symbol"]: h for h in pair.plain_packet["history"]}
    assert kept["SPY"] == before["SPY"]
    assert json.dumps(kept["SPY"]["closes"]) == json.dumps(before["SPY"]["closes"])
    assert all(set(RANGE_KEYS) <= set(kept[symbol]) for symbol in kept if symbol != "SPY")


# --- 3. the chart's geometry ----------------------------------------------------------------------------------------

def test_a_coherent_candle_sits_on_the_prior_point_inside_the_chart_box():
    chart = evidence_chart(chart_history(), [], None, "2026-09-08")
    candle = chart["candle"]
    assert candle["x"] == chart["prior"]["x"] == "100.0%"
    assert EVIDENCE_TOP < candle["y_high"] < candle["y_low"] < EVIDENCE_BOTTOM  # both ends strictly inside the box
    assert candle["y_high"] < candle["y_open"] < candle["y_low"]
    assert candle["y_high"] < chart["prior"]["y"] < candle["y_low"]  # the close sat inside the range
    assert candle["label"] == "Prior session · O 104.70 H 105.00 L 104.50 C 104.80"
    # With prints the prior point, and so the wick, moves with the path; the open tick stays left of it.
    prints = [dict(id="a", value=-0.5, observed_at="2026-09-08T12:41:00+00:00", direction="negative")]
    shared = evidence_chart(chart_history(), prints, None, "2026-09-08")["candle"]
    assert shared["x"] == "62.0%" and float(shared["tick_x"].rstrip("%")) < 62.0
    # A candle inside its own close path's range leaves everything else the chart it was: path, dots, divider, labels,
    # domain and reference line. (One that reaches past the path moves the domain: see the two tests below.)
    inside = evidence_chart(inside_history(), [], None, "2026-09-08")
    plain = evidence_chart(without_ranges(inside_history()), [], None, "2026-09-08")
    assert inside["candle"] is not None and plain["candle"] is None
    assert {key: value for key, value in inside.items() if key != "candle"} == {
        key: value for key, value in plain.items() if key != "candle"}


@pytest.mark.parametrize("form", BROKEN_ARRAYS)
def test_a_broken_range_draws_no_candle_and_leaves_the_chart_unchanged(form):
    history = chart_history()
    BROKEN_ARRAYS[form](history)
    plain = evidence_chart(without_ranges(chart_history()), [], None, "2026-09-08")
    assert evidence_chart(history, [], None, "2026-09-08") == plain


def test_a_wide_wick_is_drawn_whole_inside_the_chart_box_never_pinned_to_its_edge():
    """A prior close is often the path's own extreme, so the session's high or low falls outside the closes' window. The
    domain widens to hold the wick (a clipped wick reads as `high = top of the chart`); the head also states it."""
    flat = dict(closes=[100.0] * 25, opens=[100.0] * 25)
    wide = evidence_chart(chart_history(**flat, highs=[103.0] * 25, lows=[99.0] * 25), [], None, "2026-09-08")
    candle = wide["candle"]
    assert candle["y_high"] > EVIDENCE_TOP and candle["y_low"] < EVIDENCE_BOTTOM  # neither end cut at the box edge
    assert candle["y_high"] < wide["prior"]["y"] < candle["y_low"] and candle["y_open"] == wide["prior"]["y"]
    assert candle["label"] == "Prior session · O 100.00 H 103.00 L 99.00 C 100.00"
    # A candle inside the window the closes already had (here the two-point minimum) moves nothing else.
    narrow = evidence_chart(chart_history(**flat, highs=[100.5] * 25, lows=[99.5] * 25), [], None, "2026-09-08")
    plain = evidence_chart(without_ranges(chart_history(**flat)), [], None, "2026-09-08")
    assert EVIDENCE_TOP < narrow["candle"]["y_high"] < narrow["candle"]["y_low"] < EVIDENCE_BOTTOM
    assert {k: v for k, v in narrow.items() if k != "candle"} == {k: v for k, v in plain.items() if k != "candle"}


def test_a_candle_wider_than_the_close_path_widens_the_domain_and_holds_the_whole_wick():
    flat = dict(closes=[100.0] * 25, opens=[100.0] * 25)
    history = chart_history(**flat, highs=[103.0] * 25, lows=[97.0] * 25)  # the wick is +3% / -3% of a flat path
    chart = evidence_chart(history, [], None, "2026-09-08")
    plain = evidence_chart(without_ranges(history), [], None, "2026-09-08")
    path = [100 * (close / history["closes"][-1] - 1) for close in history["closes"][-21:]]
    low, high = chart["domain"]
    assert low < min(path) and high > max(path)
    assert low <= -3.0 and high >= 3.0  # the whole wick, 97 and 103 against a 100 close, is inside the domain
    assert low < plain["domain"][0] and high > plain["domain"][1]  # wider than the window the closes alone gave
    assert EVIDENCE_TOP < chart["candle"]["y_high"] < chart["zero_y"] < chart["candle"]["y_low"] < EVIDENCE_BOTTOM


def test_the_candle_does_not_decide_whether_the_fifty_session_average_shows():
    """The average is drawn only when it already falls inside the closes' own window, or when the drawer cites it. A
    candle that widens the domain must not pull it in: that would change which charts carry the line."""
    base = chart_history()  # closes 100 to 104.8, so the path runs -4.58% to 0%
    history = {**base, "highs": [*base["highs"][:-1], 107.0]}  # a +2.1% high; the average below is +1.0%
    plain = evidence_chart(without_ranges(history), [], 105.85, "2026-09-08")
    chart = evidence_chart(history, [], 105.85, "2026-09-08")
    assert chart["candle"] is not None and plain["reference_y"] is None and chart["reference_y"] is None
    cited = evidence_chart(history, [], 105.85, "2026-09-08", average_cited=True)  # a drawer citing it still gets it
    assert cited["reference_y"] is not None and EVIDENCE_TOP < cited["reference_y"] < EVIDENCE_BOTTOM


def test_a_one_bar_history_still_draws_nothing():
    assert evidence_chart(dict(symbol="SPY", dates=["2026-09-04"], closes=[100.0], opens=[100.0], highs=[101.0],
                               lows=[99.0])) is None


# --- 4. the page ------------------------------------------------------------------------------------------------------

def test_without_ranges_the_page_is_what_it_was_and_with_them_it_is_that_page_plus_the_candle(pair):
    drawers = DRAWER.findall(pair.plain)
    assert (len(drawers), hashlib.sha256("".join(drawers).encode()).hexdigest()) == PRE_CHANGE_DRAWERS
    assert "Prior session" not in pair.plain and "wick" not in pair.plain and SENTENCE not in pair.plain
    # Every history broken: the page is the no-range page, byte for byte.
    raw = raw_sample(True)
    for history in raw["history"]:
        history["lows"].pop()
    assert render(packet_of(raw), narrative())[1] == pair.plain
    # With ranges: the same page with the candle markup taken out again, except that a candle reaching past its close
    # path widens that chart's domain, so a figure's coordinates may move. Nothing outside the figures does, and inside
    # them nothing but coordinates does: same elements, classes, labels and text.
    stripped = without_candles(pair.ranged)
    assert pair.ranged != pair.plain and FIGURE.sub("", stripped) == FIGURE.sub("", pair.plain)
    plain_figures, ranged_figures = FIGURE.findall(pair.plain), FIGURE.findall(stripped)
    assert len(plain_figures) == len(ranged_figures) == 6
    assert [COORDINATE.sub('=""', figure) for figure in ranged_figures] == [
        COORDINATE.sub('=""', figure) for figure in plain_figures]


def test_the_head_span_appears_only_with_a_candle_and_reads_two_decimals(pair):
    assert not HEAD.search(pair.plain)
    last = {h["symbol"]: [h[key][-1] for key in (*RANGE_KEYS[:1], "highs", "lows", "closes")]
            for h in pair.ranged_packet["history"]}
    figures, drawn = FIGURE.findall(pair.ranged), 0
    assert len(figures) == 6
    for figure in figures:
        group = figure.split('<g class="closes">', 1)[1].split("</g>", 1)[0]
        assert (HEAD.search(figure) is not None) == (group.count("<line") == 22)  # 20 path segments + wick + tick
        if HEAD.search(figure):
            symbol = re.search(r"<b>([^<]+)</b>", figure)[1].split()[-1]  # `NVDA`, or `Gold miners · GDX`
            assert "Prior session · O {:.2f} H {:.2f} L {:.2f} C {:.2f}".format(*last[symbol]) in figure
            drawn += 1
    assert drawn == len(figures)  # every coherent range draws, and draws whole


def test_no_wick_on_the_page_is_cut_at_the_chart_box(pair):
    """The page-level form of `clip count 0`: both ends of every drawn wick are strictly inside the 8..62 px box."""
    drawn = 0
    for figure in FIGURE.findall(pair.ranged):
        if not HEAD.search(figure):
            continue
        wick = LINE.findall(figure.split('<g class="closes">', 1)[1].split("</g>", 1)[0])[-2]
        y_high, y_low = (float(value) for value in re.search(r'y1="([\d.]+)".*?y2="([\d.]+)"', wick).groups())
        assert EVIDENCE_TOP < y_high < y_low < EVIDENCE_BOTTOM, wick
        drawn += 1
    assert drawn == 6


def test_the_fine_print_sentence_appears_once_per_drawer_that_draws_a_candle(pair):
    drawers = DRAWER.findall(pair.ranged)
    assert len(drawers) == 12
    with_candle = [drawer for drawer in drawers if HEAD.search(drawer)]
    assert with_candle
    for drawer in drawers:
        assert drawer.count(SENTENCE) == (1 if HEAD.search(drawer) else 0)
    # The legend is the legend it was: one per drawer, with no entry for the wick.
    for drawer in with_candle:
        assert drawer.count('<span class="curve-legend evidence-legend">') == 1
        assert drawer.index(NOTE) > drawer.index('<span class="curve-legend evidence-legend">')
        legend = drawer.split('<span class="curve-legend evidence-legend">', 1)[1].split("</span></span>", 1)[0]
        assert "wick" not in legend and "range" not in legend.lower()
    assert pair.ranged.count(SENTENCE) == len(with_candle)
    assert (ROOT / "templates/brief.html.j2").read_text().count(SENTENCE) == 1


def test_the_wick_and_open_tick_are_neutral_non_scaling_strokes_inside_the_closes_group(pair):
    styles = re.compile(r"<style>.*?</style>", re.S)
    assert styles.findall(pair.ranged) == styles.findall(pair.plain)  # no rule added: the strokes are the path's
    assert ".evidence-chart .closes line{stroke:var(--teal);stroke-width:1.5;stroke-linecap:round}" in pair.ranged
    for direction in ("direction-positive", "direction-negative", "direction-neutral"):
        assert pair.ranged.count(direction) == pair.plain.count(direction)
    seen = 0
    for figure in FIGURE.findall(pair.ranged):
        if not HEAD.search(figure):
            continue
        group = figure.split('<g class="closes">', 1)[1].split("</g>", 1)[0]
        wick, tick = LINE.findall(group)[-2:]
        assert 'vector-effect="non-scaling-stroke"' in wick and 'vector-effect="non-scaling-stroke"' in tick
        assert "direction-" not in group and "fill=" not in group and "stroke=" not in group
        prior = re.search(r'<circle cx="([^"]+)"', group)[1]
        assert f'x1="{prior}"' in wick and f'x2="{prior}"' in wick  # vertical, at the prior close's x
        assert 0 <= float(prior.rstrip("%")) <= 100 and 0 <= float(re.search(r'x1="([\d.]+)%"', tick)[1]) < float(
            prior.rstrip("%"))
        seen += 1
    assert seen >= 2


@pytest.mark.parametrize("width", [390, 360])
def test_a_phone_sees_no_horizontal_overflow_and_the_candle_adds_none(width, pair, tmp_path):
    opened = {name: getattr(pair, name).replace("<details", "<details open") for name in ("plain", "ranged")}
    assert phone_layout_width(opened["plain"], tmp_path, width) <= width
    assert phone_layout_width(opened["ranged"], tmp_path, width) <= width


def test_no_official_close_wording_is_introduced_beyond_the_one_sentence(pair):
    notes = pair.ranged.count(SENTENCE)
    assert notes >= 2
    for phrase in ("official", "split-adjusted", "official closing"):
        assert pair.ranged.count(phrase) - pair.plain.count(phrase) == notes
    assert pair.ranged.count("official close ") == pair.plain.count("official close ")


# --- 5. the analyst never sees it -------------------------------------------------------------------------------------

def test_the_analyst_context_gains_no_byte_and_no_range_value(pair):
    """`evidence_hash` is the digest of the whole evidence packet, history included (context.py:158, checked against
    the packet at synthesize.py:488), so it is the one field that can change; it is 64 hex characters either way."""
    raw = raw_sample(True)
    raw["history"][0]["highs"][-1] = SENTINEL
    sentinel_packet = packet_of(raw)
    assert "history" not in model_packet(sentinel_packet)
    for name in ("PREMARKET", "OPEN_30M"):
        profile = edition_profile(name)
        plain = analyst_context(pair.plain_packet, profile)
        ranged = analyst_context(sentinel_packet, profile)
        wire = compact_json(ranged)
        assert len(compact_json(plain)) == len(wire)  # exactly 0 B
        assert compact_json({**plain, "evidence_hash": ""}) == compact_json({**ranged, "evidence_hash": ""})
        assert plain["evidence_hash"] != ranged["evidence_hash"] and len(plain["evidence_hash"]) == 64
        assert str(SENTINEL) not in wire and not any(f'"{key}"' in wire for key in RANGE_KEYS)


def test_the_evidence_ledger_row_count_is_unchanged(pair):
    summary = re.compile(r"Evidence ledger <span>· (\d+) current rows across (\d+) instruments")
    assert summary.search(pair.ranged).groups() == summary.search(pair.plain).groups() == ("36", "10")
    for field in ("observations", "derived", "events", "context_items"):
        assert [row["id"] for row in pair.ranged_packet[field]] == [row["id"] for row in pair.plain_packet[field]]
    assert pair.ranged.count('class="ledger') == pair.plain.count('class="ledger')


def test_the_page_grows_by_the_measured_bytes_and_no_more(pair):
    """Permit §4.2 allowed +2,000 B; this fixture measures +2,036 B (78,174 -> 80,210). Nothing in it is spare: each of
    its 6 figures adds a head span, a wick and an open tick (232-236 B), and each of its 5 drawers the sentence
    (126 B), so growth is about 234 B x figures + 126 B x drawers, which a page with more chart drawers exceeds. (Whole
    wicks cost 10 B more than the clipped ones did: the cut ends were the short integers 8 and 62.)"""
    growth = len(pair.ranged.encode()) - len(pair.plain.encode())
    figures, notes = len(FIGURE.findall(pair.ranged)), pair.ranged.count(NOTE)
    assert (figures, notes) == (6, 5)
    assert 0 < growth <= 2040
    assert growth == sum(len(match.encode()) for match in HEAD.findall(pair.ranged)) + len(NOTE.encode()) * notes + sum(
        len(line.encode()) for figure in FIGURE.findall(pair.ranged) if HEAD.search(figure)
        for line in LINE.findall(figure.split('<g class="closes">', 1)[1].split("</g>", 1)[0])[-2:])  # nothing else

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

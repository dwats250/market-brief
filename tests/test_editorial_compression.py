"""The editorial compression pass: one idea has one home.

The lead claims, a section proves, one watch tests. What changed carries every verdict on an earlier
hypothesis exactly once, What matters next carries only the questions continuity will keep asking, and a
flag renders only when it names something the story has not. Everything here is presentation: the frozen
interpretation, the continuity records and the evidence ledger are unchanged.
"""

import re
import shutil
import subprocess

import pytest
from test_cadence import phone_layout_width
from test_continuity import carried_setup, trimmed
from test_pipeline import fixture_packet, narrative

from market_brief.context import edition_profile
from market_brief.continuity import CARRY_LIMIT, admit_prior_state, compare_all, digest, edition_state
from market_brief.render import formatted, presentation, render

TUE_1 = "watch-sample-premarket-124500-tue-1"  # active, assessable
TUE_2 = "watch-sample-premarket-124500-tue-2"  # active, not comparable
FRI_1 = "watch-sample-close_1m-200300-fri-1"  # expired, not comparable


def opening(**fields):
    """A light opening-structure narrative over the carried fixture, with the given continuity records."""
    value = trimmed(narrative(), edition_profile("OPEN_30M"))
    value["watches"] = value["watches"][:1]
    value.update(fields)
    return value


def what_changed(page):
    if '<section class="since">' not in page:
        return ""
    return page.split('<section class="since">', 1)[1].split("</section>", 1)[0]


def matters(page):
    return page.split("<h2>What matters next</h2>", 1)[1].split("</section>", 1)[0]


def live_questions(page):
    block = matters(page).split('<details class="drawer retired">', 1)[0].split('<span class="eyebrow">Flagged', 1)[0]
    questions = re.findall(r'<div class="question">(.*?)</div>', block, re.S)
    return [re.sub(r"\s*<details.*", "", q, flags=re.S) for q in questions]


# --- what matters next: the live set is what continuity carries ------------------------------------------------

def test_live_watches_are_exactly_the_watches_continuity_carries_forward():
    packet, context, bundle = carried_setup()
    value = opening(watch_updates=[dict(carried_id=TUE_1, assessment="unresolved", reason="No comparable print yet.",
                                        evidence_ids=["SPY-intraday", "premarket:SPY-intraday"])])
    _, page = render(packet, value, context)
    prior = admit_prior_state(bundle, packet)
    state = edition_state(packet, value, prior, compare_all(prior, packet), digest(context), digest(value), "test")
    carried = [w["hypothesis"] for w in state["assessment"]["watches"]]
    assert len(carried) == CARRY_LIMIT and carried[0] == value["watches"][0]["condition"]
    assert live_questions(page) == carried
    # The expired Friday watch was never reassessed: it ended without a verdict, in the drawer, not in the path.
    assert '<details class="drawer retired"><summary>Earlier watches <span>· 1 ended without a verdict</span>' in page
    drawer = matters(page).split('<details class="drawer retired">', 1)[1]
    assert "From an earlier read · through the next opening hour · horizon passed" in drawer


def test_new_watches_displace_carried_ones_at_the_carry_limit():
    packet, context, bundle = carried_setup()
    value = opening()
    value["watches"] = [dict(value["watches"][0]), dict(value["watches"][0], condition="A second new question.")]
    _, page = render(packet, value, context)
    prior = admit_prior_state(bundle, packet)
    state = edition_state(packet, value, prior, compare_all(prior, packet), digest(context), digest(value), "test")
    assert live_questions(page) == [w["hypothesis"] for w in state["assessment"]["watches"]]
    assert len(live_questions(page)) == CARRY_LIMIT
    # The carried watch that overflowed is set aside without a verdict, so it sits in the drawer, and the
    # deterministic overflow record agrees.
    dropped = [w["hypothesis"] for w in state["assessment"]["retired"]
               if w["assessments"][-1]["reason"].startswith("dropped")]
    drawer = matters(page).split('<details class="drawer retired">', 1)[1]
    assert dropped and all(h in drawer for h in dropped)
    assert "· 2 ended without a verdict" in page  # the dropped one and the expired Friday one


def test_a_reversed_watch_leaves_the_live_set_and_its_verdict_goes_to_what_changed():
    packet, context, _ = carried_setup()
    value = opening(watch_updates=[dict(carried_id=TUE_1, assessment="reversed", reason="Growth lost its lead.",
                                        evidence_ids=["SPY-intraday", "premarket:SPY-intraday"])])
    md, page = render(packet, value, context)
    assert "<b>Reversed</b> — " in what_changed(page) and "Growth lost its lead." in what_changed(page)
    # The new watch reuses the fixture's wording, so the live set is that new question plus the other carried one.
    assert live_questions(page) == [value["watches"][0]["condition"],
                                    "Revisit the gold fund and miners on the same return horizon."]
    assert "- **Reversed** — " in md.split("## What changed", 1)[1].split("## What matters next", 1)[0]


# --- what changed: adjudication, once ----------------------------------------------------------------------------

def test_a_lower_priority_record_repeating_a_verdicts_evidence_is_not_shown_again():
    packet, context, _ = carried_setup()
    same = ["SPY-intraday", "premarket:SPY-intraday"]
    value = opening(
        watch_updates=[dict(carried_id=TUE_1, assessment="weakened", reason="The benchmark print turned positive.",
                            evidence_ids=same)],
        relationships=[dict(carried_id="rel-sample-premarket-124500-tue-1", instruments=["QQQ", "SPY"],
                            statement="Growth leads the broad benchmark.", assessment="weakened",
                            reason="The benchmark print turned positive.", evidence_ids=same)],
        changes=[dict(comparison_id="cmp-premarket-SPY-intraday",
                      text="SPY moved from {{premarket:SPY-intraday}} to {{SPY-intraday}}.", evidence_ids=same)])
    md, page = render(packet, value, context)
    since = what_changed(page)
    assert since.count("<li>") == 1 and "<b>Weakened</b> — If growth retains its lead" in since
    assert "Growth leads the broad benchmark." not in since and "SPY moved from" not in since
    # The suppressed records are still the analyst's records: nothing in the frozen narrative changed.
    view = presentation(packet, value, context)
    assert len(view["since"]["entries"]) == 1 and view["since"]["entries"][0]["kind"] == "watch"
    assert md.split("## What changed", 1)[1].split("## What matters next", 1)[0].count("\n- ") == 1


def test_a_record_that_cites_evidence_of_its_own_stays():
    packet, context, _ = carried_setup()
    value = opening(
        watch_updates=[dict(carried_id=TUE_1, assessment="weakened", reason="The benchmark print turned positive.",
                            evidence_ids=["SPY-intraday", "premarket:SPY-intraday"])],
        relationships=[dict(carried_id="rel-sample-premarket-124500-tue-2", instruments=["GLD", "GDX"],
                            statement="Miners trail gold.", assessment="strengthened",
                            reason="The gap widened again.", evidence_ids=["GDX-spread20"])],
        changes=[dict(comparison_id="cmp-premarket-SPY-intraday",
                      text="SPY turned from {{premarket:SPY-intraday}} to {{SPY-intraday}}.",
                      evidence_ids=["SPY-intraday", "premarket:SPY-intraday", "QQQ-daily"])])
    _, page = render(packet, value, context)
    since = what_changed(page)
    assert since.count("<li>") == 3
    order = [since.index(s) for s in ("<b>Weakened</b>", "<b>Strengthened</b>", "SPY turned from")]
    assert order == sorted(order)  # watch verdicts, then relationship verdicts, then changes


def test_unresolved_is_not_a_change():
    packet, context, _ = carried_setup()
    value = opening(
        watch_updates=[dict(carried_id=TUE_1, assessment="unresolved", reason="No comparable print yet.",
                            evidence_ids=["SPY-intraday", "premarket:SPY-intraday"])],
        relationships=[dict(carried_id="rel-sample-premarket-124500-tue-2", instruments=["GLD", "GDX"],
                            statement="Miners trail gold.", assessment="unresolved", reason="No new observation.",
                            evidence_ids=["GDX-spread20"])])
    md, page = render(packet, value, context)
    assert "Unresolved" not in what_changed(page) and "**Unresolved**" not in md
    # The still-live question keeps its one-line reason under What matters next, since the test is still open.
    assert '<span class="meta">From an earlier read · unresolved · into the close</span>' in matters(page)
    assert "No comparable print yet." in matters(page)


def test_a_live_watch_with_a_verdict_keeps_its_question_and_never_its_verdict_under_what_matters_next():
    packet, context, _ = carried_setup()
    value = opening(watch_updates=[dict(carried_id=TUE_1, assessment="strengthened", reason="Growth held its lead.",
                                        evidence_ids=["SPY-intraday", "premarket:SPY-intraday"])])
    md, page = render(packet, value, context)
    assert "<b>Strengthened</b> — If growth retains its lead" in what_changed(page)
    block = matters(page)
    assert "If growth retains its lead" in block and "strengthened" not in block.lower()
    assert "Growth held its lead." not in block
    assert '<span class="meta">From an earlier read · into the close</span>' in block
    assert "FROM AN EARLIER READ · Into the close" in md and "FROM AN EARLIER READ · strengthened" not in md


# --- flagged: only what the story has not said ---------------------------------------------------------------

def test_a_flag_renders_only_when_its_instrument_is_not_already_in_the_story():
    packet, value = fixture_packet(), narrative()
    _, page = render(packet, value)
    flags = re.findall(r'<ul class="flags">.*?</ul>', page, re.S)
    assert flags and "<b>NVDA</b>" not in flags[0]  # the summary cites NVDA's row
    assert "<b>Industrials · XLI</b>" in flags[0]
    # Cite the industrials row in the lead and nothing is left to flag: the eyebrow goes too.
    value["summary"][0]["evidence_ids"].append("XLI-daily")
    _, page = render(packet, value)
    assert '<span class="eyebrow">Flagged</span>' not in page and 'class="flags"' not in page
    # Naming the instrument in prose covers it as well as citing its row.
    value = narrative()
    value["summary"][0]["text"] = "Industrials led the fictional day while growth kept its lead."
    _, page = render(packet, value)
    assert 'class="flags"' not in page
    # The frozen selection is untouched: attention is still recorded in full.
    view = presentation(packet, value)
    assert view["technical"] and len(narrative()["attention"]) == 2


# --- the lead: no label pill, no figure strip ----------------------------------------------------------------

def test_the_label_is_recorded_but_not_displayed():
    packet, value = fixture_packet(), narrative()
    md, page = render(packet, value)
    assert value["banner"]["label"] == "MIXED"
    lead = page.split("<h1>", 1)[1].split("<section", 1)[0]
    assert "MIXED" not in lead and 'class="pill"' not in page and "**INTERPRETATION** ·" in md
    assert "INTERPRETATION — " not in md
    view = presentation(packet, value)
    assert view["banner"]["label"] == "MIXED"  # the field travels with the record


# --- numbers: one style everywhere ----------------------------------------------------------------------------

@pytest.mark.parametrize("value, unit, text", [
    (0.53, "%", "+0.53%"), (-3.4, "%", "−3.40%"), (0.004, "%", "0.00%"), (-0.004, "%", "0.00%"),
    (-0.006, "%", "−0.01%"), (28.34, "pp", "+28.34 pp"), (-5.3, "pp", "−5.30 pp"), (256.16, "USD", "256.16 USD"),
    (-6, "bp", "−6 bp"), (4.81, "% yield", "4.81%"),
])
def test_one_number_style(value, unit, text):
    assert formatted(dict(value=value, unit=unit)) == text


def test_placeholders_tables_and_ledger_share_the_style():
    packet, value = fixture_packet(), narrative()
    value["summary"][0]["text"] = "SPY printed {{SPY-daily}} and QQQ led by {{QQQ-spread20}}."
    md, page = render(packet, value)
    assert not re.search(r"[+\-−]?\d+\.\d+ %", page) and not re.search(r"[+\-−]?\d+\.\d+ %", md)
    assert not re.search(r"[>\s]-\d+\.\d+(?:%| pp)", page)  # no ASCII-hyphen negatives on the page
    assert "20D&nbsp;" in page and "50DMA&nbsp;" in page


# --- macro & rates holds the metals table; the order is fixed ------------------------------------------------

def test_metals_alone_still_render_under_macro_and_the_missing_half_is_named():
    packet = fixture_packet()
    packet["observations"] = [r for r in packet["observations"] if not r["topic"].startswith("US ")]
    packet["derived"] = [r for r in packet["derived"] if not r["topic"].startswith("US ")]
    packet["curve"] = None
    value = narrative()
    value["sections"]["macro"] = []
    for record in (value["banner"], value["character"], *value["summary"], value["take"]):
        record["evidence_ids"] = [i for i in record["evidence_ids"] if not i.startswith("treasury")] or ["SPY-daily"]
    view = presentation(packet, value)
    _, page = render(packet, value)
    assert "<h2>Macro &amp; rates</h2>" in page and '<div class="caption">Metals<span>' in page
    assert "Not in this edition: Treasury rates" in view["limitations"]
    assert not any("Macro & rates" in item for item in view["limitations"])


def cell_spill(page, tmp_path, width):
    """The largest distance any numeric cell's text runs past its own padding box, at a phone width, plus a
    sentinel when a number wraps onto a second line. Measured by the same headless Chrome as the width probe."""
    chrome = shutil.which("google-chrome") or shutil.which("google-chrome-stable") or shutil.which("chromium")
    if not chrome:
        pytest.skip("headless Chrome is not installed")
    probe = (f"<style>html,body{{width:{width}px!important;max-width:{width}px!important}}</style><script>"
             "let spill = 0; for (const td of document.querySelectorAll('td.number')) {"
             "  const cell = td.getBoundingClientRect(); if (cell.width === 0) continue;"  # hidden on phones
             "  const range = document.createRange(); range.selectNodeContents(td);"
             "  const text = range.getBoundingClientRect();"
             "  const style = getComputedStyle(td); const inner = cell.right - parseFloat(style.paddingRight);"
             "  if (text.height > 1.7 * parseFloat(style.fontSize)) spill = 999;"
             "  spill = Math.max(spill, text.right - inner); }"
             "document.title = 'SPILL=' + Math.ceil(spill);</script></body>")
    target = tmp_path / "spill.html"
    target.write_text(page.replace("</body>", probe, 1))
    result = subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
                             f"--window-size={width},1200", "--dump-dom", target.as_uri()],
                            capture_output=True, text=True, timeout=90, check=False)
    found = re.search(r"SPILL=(-?\d+)", result.stdout)
    assert found, result.stderr[-500:]
    return int(found.group(1))


# 601-721 px is where fixed-layout tables let no-wrap numbers spill (F1); 768 px is the desktop control.
@pytest.mark.parametrize("width", [390, 360, 320, 601, 640, 680, 720, 721, 768])
def test_the_phone_layout_keeps_every_number_inside_its_cell(tmp_path, width):
    """No unit wraps under its number and no numeric text spills past its column."""
    _, page = render(fixture_packet(), narrative())
    assert phone_layout_width(page, tmp_path, width) <= width
    assert cell_spill(page, tmp_path, width) <= 0
    # The probe itself detects a wrapped number: a value forced onto two lines is reported.
    wrapped = re.sub(r'(<td class="number primary[^>]*>)', r'\1<span style="display:block">+1.00</span>', page, count=1)
    assert cell_spill(wrapped, tmp_path, width) > 0

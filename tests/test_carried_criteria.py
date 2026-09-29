"""A carried watch keeps the criteria that give it operational meaning.

The frozen interpretation a deterministic refresh renders holds each carried watch's question, horizon and
confirm / changes-it criteria, at the values its author saw. Nothing else moves: the analyst reads the same
context, the live set and the verdicts are unchanged, a refresh never rewrites the record, a rejected
synthesis freezes nothing, and an interpretation frozen before the criteria were kept renders its question
alone.
"""

import copy
import json

import pytest
from test_cadence import TUE, Day
from test_continuity import (
    CLOSE_FRI,
    PREMARKET_TUE,
    accept,
    carried_setup,
    friday_close,
    run_packet,
    session_watch_narrative,
    trimmed,
)
from test_history_admission import utc
from test_light_context import LIGHT, OPEN_30M_TUE, light_edition
from test_pipeline import narrative

from market_brief.context import analyst_context, edition_profile
from market_brief.continuity import (
    admit_prior_state,
    advance_bundle,
    bundle_path,
    compare_all,
    continuity_context,
    empty_bundle,
    interpretation_record,
    load_bundle,
    session_handoff,
    write_bundle,
)
from market_brief.evidence import digest
from market_brief.render import presentation, render
from market_brief.synthesize import compact_json, construct_prompt

QUESTION = "If SPY holds its {{SPY-daily}} daily gain after the open, check whether participation broadens."
CONFIRM = "Breadth improves while SPY stays above its {{SPY-intraday}} print."
CHANGES_IT = "SPY slips back through its {{SPY-intraday}} print as leadership narrows."
SHOWN = "If SPY holds its +0.06% daily gain after the open"  # the question as rendered, at its author's value
TUE_1 = "watch-sample-premarket-124500-tue-1"  # carried_setup's live, assessable watch
# The analyst's view of a carried watch, plus `values` when a criterion quotes a row.
ANALYST_WATCH_KEYS = {"id", "lifecycle", "evaluability", "hypothesis", "confirmation", "contradiction", "horizon",
                      "evidence_refs", "origin_run_id", "latest_assessment"}


def criteria_at(value):
    """The criteria as rendered with SPY's print at `value`: the value their author saw, never a later one."""
    confirm, changes_it = (text.replace("{{SPY-intraday}}", value) for text in (CONFIRM, CHANGES_IT))
    return (f"<dl class=\"tests\"><dt>Confirm</dt><dd>{confirm}</dd><dt>Changes it</dt><dd>{changes_it}</dd></dl>",
            f"Confirm: {confirm} Changes it: {changes_it}")


@pytest.fixture
def day(monkeypatch, tmp_path):
    return Day(monkeypatch, tmp_path)


def with_criteria(value, horizon="SESSION"):
    """The narrative's first watch, quoting the daily row in its question and the current print in both criteria."""
    watch = value["watches"][0]
    watch.update(condition=QUESTION, confirmation=CONFIRM, contradiction=CHANGES_IT, horizon=horizon,
                 evidence_ids=[*watch["evidence_ids"], "SPY-intraday"])
    assert "SPY-daily" in watch["evidence_ids"]
    return value


def rehashed(record):
    body = {key: value for key, value in record.items() if key != "content_hash"}
    return dict(body, content_hash=digest(body))


def without_criteria(record):
    """An interpretation as the previous release froze it: carried watches without their criteria."""
    legacy = copy.deepcopy(record)
    for watch in legacy["prior_state"]["watches"]:
        watch.pop("confirmation", None)
        watch.pop("contradiction", None)
    return rehashed(legacy)


def live_watch(page, question):
    """The one live watch under What matters next whose question starts with `question`."""
    matters = page.split("<h2>What matters next</h2>", 1)[1]
    for end in ('<details class="drawer retired">', '<span class="eyebrow">Flagged', '<span class="eyebrow">Events',
                "</section>"):
        matters = matters.split(end, 1)[0]
    blocks = [b for b in matters.split('<div class="watch">')[1:] if b.startswith(f'<div class="question">{question}')]
    assert len(blocks) == 1, question
    return blocks[0]


def carried_line(markdown, question):
    lines = [line for line in markdown.splitlines() if line.startswith("- **FROM AN EARLIER READ") and question in line]
    assert len(lines) == 1, question
    return lines[0]


def markdown(day, checkpoint):
    return (day.folder(checkpoint) / "brief.md").read_text()


# --- carried watch -> frozen interpretation -> deterministic refresh ---------------------------------------------

def test_a_live_carried_watch_keeps_its_question_horizon_and_criteria_through_a_deterministic_refresh(day):
    """SPY prints −0.53% when the premarket writes the watch, +0.21% when the opening structure freezes it and
    +0.40% at the refresh: the carried criteria show the author's print on every page after."""
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", value=with_criteria(narrative())) == 0
    watches = day.bundle()["premarket"]["assessment"]["watches"]
    carried_id = next(w["id"] for w in watches if w["hypothesis"] == QUESTION)
    # 7:00 AM PT: the opening-structure synthesis carries the watch without reassessing it and freezes it whole.
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M", prints=(("SPY", 0.21), ("QQQ", 0.3))) == 0
    record = day.bundle()["interpretation"]  # hash-verified on load
    frozen = next(w for w in record["prior_state"]["watches"] if w["id"] == carried_id)
    assert (frozen["hypothesis"], frozen["confirmation"], frozen["contradiction"]) == (QUESTION, CONFIRM, CHANGES_IT)
    assert frozen["lifecycle"] == "active" and frozen["horizon"]["phrase"] == "Into the close"
    assert frozen["values"]["SPY-daily"]["value"] == pytest.approx(0.06, abs=0.005)
    assert frozen["values"]["SPY-intraday"]["value"] == -0.53
    assert record["evidence"]["SPY-intraday"]["value"] == 0.21  # the synthesis's own row, not the watch's
    # 10:00 AM PT: a deterministic refresh renders exactly that record; no analyst call, nothing rewritten.
    assert day.run(f"{TUE}T17:00:00+00:00", "HOURLY_1300", prints=(("SPY", 0.4), ("QQQ", 0.5))) == 0
    assert day.calls == ["PREMARKET", "OPEN_30M"]
    assert day.bundle()["interpretation"] == record
    html, text = criteria_at("−0.53%")
    for checkpoint in ("OPEN_30M", "HOURLY_1300"):
        page, md = day.page(checkpoint), markdown(day, checkpoint)
        block = live_watch(page, SHOWN)
        assert '<span class="meta">From an earlier read · into the close</span>' + html in block, checkpoint
        line = carried_line(md, SHOWN)
        assert line.startswith("- **FROM AN EARLIER READ · Into the close** — ")
        assert text in line, checkpoint
        assert "+0.21% print" not in page + md and "+0.40% print" not in page + md, checkpoint
        assert "{{" not in page and "{{" not in md, checkpoint
        assert day.metadata(checkpoint)["validation"] == "PASS"


def production_close(value):
    """Friday's synthesized fixture close, relabeled as the production handoff a Tuesday premarket restores.
    Written after Friday's close, its first watch runs into the next session, so it is live on Tuesday."""
    packet = run_packet(CLOSE_FRI, "live-close_1m-200300-fri", checkpoint="CLOSE_1M", intraday_value=-0.91)
    _, _, _, state = accept(packet, empty_bundle(), value)
    handoff, reason = session_handoff(state)
    assert handoff is not None, reason
    handoff["origin"]["mode"] = "LIVE"
    return dict(empty_bundle(), close=rehashed(handoff), updated_at=CLOSE_FRI, updated_by_run=packet["run"]["run_id"])


def test_a_rejected_synthesis_moves_no_pointer_and_the_refreshes_keep_the_frozen_criteria(day):
    """A day whose opening-structure synthesis fails: the premarket carries the previous close's live watch and
    freezes it with its criteria, the rejected update moves nothing, and the refreshes keep rendering the
    premarket's record, criteria included, at the −0.91% print Friday's author saw (Tuesday prints −0.53%)."""
    write_bundle(bundle_path(day.root), production_close(with_criteria(narrative(), "NEXT_CLOSE")))
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    record = day.bundle()["interpretation"]
    frozen = next(w for w in record["prior_state"]["watches"] if w["hypothesis"] == QUESTION)
    assert (frozen["confirmation"], frozen["contradiction"]) == (CONFIRM, CHANGES_IT)
    assert frozen["lifecycle"] == "active" and frozen["horizon"]["phrase"] == "Into the next session"
    before = bundle_path(day.root).read_bytes()
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M", fail_synthesis=True) == 2
    assert bundle_path(day.root).read_bytes() == before  # every pointer, the frozen record included
    assert day.metadata("OPEN_30M")["continuity"]["advanced"] is False
    assert day.published == ["PREMARKET"]
    assert day.run(f"{TUE}T15:00:00+00:00", "HOURLY_1100") == 0
    assert day.calls == ["PREMARKET", "OPEN_30M"]  # the refresh did not retry the analyst
    assert day.bundle()["interpretation"] == record
    assert day.metadata("HOURLY_1100")["interpretation"]["content_hash"] == record["content_hash"]
    html, text = criteria_at("−0.91%")
    for checkpoint in ("PREMARKET", "HOURLY_1100"):
        block = live_watch(day.page(checkpoint), SHOWN)
        assert '<span class="meta">From an earlier read · into the next session</span>' + html in block, checkpoint
        assert text in carried_line(markdown(day, checkpoint), SHOWN), checkpoint


def test_an_interpretation_frozen_before_criteria_were_kept_still_refreshes_with_the_question_alone(day):
    """A bundle written by the previous release: its frozen carried watches have no criteria. It loads, is
    admitted and renders the question and horizon as before; the refresh never migrates the record."""
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", value=with_criteria(narrative())) == 0
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M") == 0
    bundle = json.loads(bundle_path(day.root).read_text())
    legacy = without_criteria(bundle["interpretation"])
    assert legacy["prior_state"]["watches"] and legacy["content_hash"] != bundle["interpretation"]["content_hash"]
    write_bundle(bundle_path(day.root), dict(bundle, interpretation=legacy))
    loaded, note = load_bundle(bundle_path(day.root))
    assert note == "" and loaded["interpretation"] == legacy
    assert day.run(f"{TUE}T17:00:00+00:00", "HOURLY_1300") == 0
    assert day.metadata("HOURLY_1300")["validation"] == "PASS"
    block = live_watch(day.page("HOURLY_1300"), SHOWN)
    assert '<span class="meta">From an earlier read · into the close</span>' in block
    assert "<dl" not in block and "Confirm" not in block
    assert "Confirm:" not in carried_line(markdown(day, "HOURLY_1300"), SHOWN)
    assert day.bundle()["interpretation"] == legacy


# --- what the change does not touch -------------------------------------------------------------------------------

def test_the_frozen_criteria_never_reach_the_analyst_context():
    """The analyst's context is built from the admitted edition states, never from the frozen record. The light
    opening-structure context is byte for byte the same whether the bundle holds no interpretation, one that
    keeps its carried watches' criteria, or one frozen before criteria were kept; the analyst already reads
    those criteria from the carried state."""
    state, handoff = friday_close()
    bundle = advance_bundle(empty_bundle(), state, handoff, utc(CLOSE_FRI))
    premarket = run_packet(PREMARKET_TUE, "sample-premarket-124500-tue", intraday_value=-0.53)
    value = session_watch_narrative()
    _, _, context, premarket_state = accept(premarket, bundle, value)
    bundle = advance_bundle(bundle, premarket_state, None, utc(PREMARKET_TUE))
    record = interpretation_record(premarket, value, context, "test", digest(context))
    assert len(record["prior_state"]["watches"]) == 2
    assert all(w["confirmation"] and w["contradiction"] for w in record["prior_state"]["watches"])
    reference_packet, _, _, reference = light_edition(OPEN_30M_TUE, "OPEN_30M", "sample-open_30m-tue",
                                                      intraday_value=0.21)
    sent = construct_prompt(reference_packet, context=reference)[1]
    for interpretation in (None, record, without_criteria(record)):
        packet = run_packet(OPEN_30M_TUE, "sample-open_30m-tue", checkpoint="OPEN_30M", intraday_value=0.21)
        prior = admit_prior_state(dict(bundle, interpretation=interpretation), packet)
        comparisons = compare_all(prior, packet)
        packet["continuity"] = dict(status=prior["status"], reason=prior["reason"], anchors=prior["anchors"],
                                    comparisons=comparisons)
        light = dict(analyst_context(packet, LIGHT, comparisons, prior),
                     **continuity_context(prior, comparisons, LIGHT))
        assert compact_json(light) == compact_json(reference)
        assert construct_prompt(packet, context=light)[1] == sent
    # The analyst already reads each carried watch's criteria, and its view of a watch gains nothing here.
    assert all(w["confirmation"] and w["contradiction"] for w in reference["prior_state"]["watches"])
    assert all(set(w) - {"values"} == ANALYST_WATCH_KEYS for w in reference["prior_state"]["watches"])


def test_the_live_set_and_the_verdicts_are_unchanged_by_the_criteria():
    """Criteria are presentation of the live watches continuity carries: the same watches are live, in the same
    order, verdicts still render once in What changed, and the drawer shows questions only."""
    packet, context, _ = carried_setup()
    value = trimmed(narrative(), edition_profile("OPEN_30M"))
    value["watches"] = value["watches"][:1]
    value["watch_updates"] = [dict(carried_id=TUE_1, assessment="strengthened",
                                   reason="Growth held its lead.",
                                   evidence_ids=["SPY-intraday", "premarket:SPY-intraday"])]
    record = interpretation_record(packet, value, context)
    view = presentation(packet, interpretation=record)
    legacy = presentation(packet, interpretation=without_criteria(record))
    for key in ("carried", "retired"):
        assert [c["id"] for c in view["next"][key]] == [c["id"] for c in legacy["next"][key]]
    assert view["since"]["entries"] == legacy["since"]["entries"]
    assert [c["assessment"] for c in view["next"]["carried"]] == ["", ""]  # the verdict is not repeated here
    assert all(c["confirmation"] and c["contradiction"] for c in view["next"]["carried"])
    assert all(not c["confirmation"] for c in legacy["next"]["carried"])


def test_a_reassessment_reason_stays_apart_from_the_criteria_and_the_drawer_shows_questions_only():
    """A live carried watch reads question, criteria, then the reason it is still open; the Markdown keeps that
    reason beside the question, ahead of the labeled criteria, so neither reads as the other. An earlier watch
    in the drawer shows its question alone, although the frozen record holds its criteria too."""
    packet, context, _ = carried_setup()
    value = trimmed(narrative(), edition_profile("OPEN_30M"))
    value["watches"] = [dict(value["watches"][0], condition="A new question for the opening structure.")]
    value["watch_updates"] = [dict(carried_id=TUE_1, assessment="unresolved", reason="No comparable print yet.",
                                   evidence_ids=["SPY-intraday", "premarket:SPY-intraday"])]
    md, page = render(packet, value, context)
    carried = next(w for w in context["prior_state"]["watches"] if w["id"] == TUE_1)
    question, confirm, changes_it = carried["hypothesis"], carried["confirmation"], carried["contradiction"]
    assert ('<span class="meta">From an earlier read · unresolved · into the close</span>'
            f'<dl class="tests"><dt>Confirm</dt><dd>{confirm}</dd><dt>Changes it</dt><dd>{changes_it}</dd></dl>'
            '<p class="fine">No comparable print yet.</p></div>') in live_watch(page, question)
    assert carried_line(md, question).endswith(
        f"— {question} No comparable print yet. Confirm: {confirm} Changes it: {changes_it}")
    # The drawer: the expired Friday watch holds the same criteria in the view, and shows none of them.
    view = presentation(packet, value, context)
    assert [c["confirmation"] for c in view["next"]["retired"]] == [confirm]
    drawer = page.split('<details class="drawer retired">', 1)[1].split('<span class="eyebrow">', 1)[0]
    assert question in drawer and "<dl" not in drawer and confirm not in drawer and changes_it not in drawer
    earlier = next(line for line in md.splitlines() if line.startswith("- Earlier watches"))
    assert question in earlier and "Confirm:" not in earlier and confirm not in earlier

def test_a_live_carried_criterion_covers_a_flag_as_a_new_watch_criterion_does():
    """A flag renders only when its instrument is not already in a live watch, by cited row or by name. A live
    carried watch now shows its criteria, so they count like a new watch's; a watch in the drawer never does."""
    packet, context, _ = carried_setup()
    value = trimmed(narrative(), edition_profile("OPEN_30M"))
    value["watches"] = value["watches"][:1]
    record = interpretation_record(packet, value, context)

    def flags(interpretation):
        return [a["symbol"] for a in presentation(packet, interpretation=interpretation)["next"]["attention"]]

    view = presentation(packet, interpretation=record)
    assert flags(record) == ["XLI"]
    live, retired = view["next"]["carried"][0]["id"], view["next"]["retired"][0]["id"]
    for ident, expected in ((retired, ["XLI"]), (live, [])):
        naming = copy.deepcopy(record)
        next(w for w in naming["prior_state"]["watches"] if w["id"] == ident)["confirmation"] = (
            "Industrials join the advance.")
        assert flags(naming) == expected, ident

"""E1: evidence-integrity hardening.

Rule 1. Once the Treasury curve is stale (`curve_freshness`: more than five calendar days old), the page stops
presenting its daily changes as current (the rates module blanks them and names no move), but the authority filter
still admitted the tenor and spread change rows, so the analyst could cite a week-old move as today's and the
deterministic comparisons could offer it as a `changed` measurement. `stale_movement` withholds exactly those rows
from both; dated levels stay citable and every row stays in the evidence record.

Rule 2. A `change` named a changed comparison and cited admitted evidence, but nothing tied the two: a claim about
comparison X could cite unrelated admitted evidence Y. A change now cites only its comparison's own operands
(`current_ref`, `prior_ref`). Relationships and watch updates keep citing any admitted evidence.
"""

import copy

import pytest
from test_continuity import carried_setup, trimmed
from test_curve import SEP24_CHANGES, SEP24_LEVELS, UNIVERSE, rates_packet, rows_for, utc
from test_pipeline import NOW, narrative

from market_brief.collect import cuttingboard_record
from market_brief.context import analyst_context, edition_profile, supplied_ids
from market_brief.continuity import _snapshot, compare_anchor
from market_brief.evidence import ROOT, evidence_catalog, finalize_coverage, model_packet, normalize_packet, read_json
from market_brief.metrics import derive
from market_brief.render import presentation
from market_brief.synthesize import validate_narrative

MOVES = {"treasury-2y-change", "treasury-5y-change", "treasury-10y-change", "treasury-30y-change",
         "treasury-2s10s-change", "treasury-5s30s-change"}
LEVELS = {"treasury-2y", "treasury-5y", "treasury-10y", "treasury-30y", "treasury-2s10s", "treasury-5s30s"}


# --- Rule 1: a stale curve's changes are not current movement ------------------------------------------------

BOUNDARY = [
    ("2026-09-25T13:00:00+00:00", "2026-09-24", "2026-09-23", "current", 1),
    ("2026-09-22T13:00:00+00:00", "2026-09-17", "2026-09-16", "older", 5),  # the last day its changes count
    ("2026-09-22T13:00:00+00:00", "2026-09-16", "2026-09-15", "stale", 6),
    ("2026-09-24T13:00:00+00:00", "2026-09-17", "2026-09-16", "stale", 7),  # normalization still keeps values
]


@pytest.mark.parametrize(("now", "day", "prior", "freshness", "age"), BOUNDARY,
                         ids=[f"{row[3]}-{row[4]}d" for row in BOUNDARY])
def test_the_analyst_and_the_page_agree_on_movement_at_the_stale_boundary(now, day, prior, freshness, age):
    packet = rates_packet(utc(now), rows_for(day, SEP24_LEVELS, SEP24_CHANGES, prior=prior))
    assert (packet["curve"]["freshness"], packet["curve"]["age_days"]) == (freshness, age)
    admitted = set(evidence_catalog(model_packet(packet)))
    shown = supplied_ids(analyst_context(packet, edition_profile("PREMARKET")))
    shown_light = supplied_ids(analyst_context(packet, edition_profile("OPEN_30M")))
    page = {line["id"] for line in presentation(packet, narrative())["macro"]["yields_proof"]}
    # Dated levels stay everywhere.
    assert LEVELS <= admitted and LEVELS <= shown and LEVELS <= shown_light and LEVELS <= page
    # Movement is current evidence exactly when the page presents it as current.
    expected = set() if freshness == "stale" else MOVES
    assert admitted & MOVES == shown & MOVES == page & MOVES == expected
    assert shown_light & MOVES == expected
    # Nothing is hidden from the record: every change row keeps its value in the evidence.
    recorded = {row["id"]: row for row in [*packet["observations"], *packet["derived"]]}
    assert all(isinstance(recorded[ident]["value"], int) for ident in MOVES)


def fixture_with_curve(day):
    """The fixture evidence at its own clock with every Treasury row moved to `day`."""
    raw = read_json(ROOT / "tests/fixtures/evidence.sample.json")
    raw["cuttingboard"] = cuttingboard_record(raw["cuttingboard"], NOW, NOW)
    for row in raw["observations"]:
        if row["topic"].startswith("US "):
            row["observed_at"] = day
    return finalize_coverage(derive(normalize_packet(raw, NOW, "SAMPLE"), UNIVERSE))


def with_levels_only(value):
    """The same narrative with every Treasury change it cites, and every placeholder of one, re-pointed to the dated
    level of the same tenor."""
    value = copy.deepcopy(value)
    records = [value["banner"], *value["summary"], value["take"], value["character"],
               *[p for section in value["sections"].values() for p in section]]
    for record in records:
        record["evidence_ids"] = list(dict.fromkeys(
            ident.removesuffix("-change") if ident.startswith("treasury-") else ident
            for ident in record["evidence_ids"]))
        for key in ("title", "text", "limitation", "uncertainty", "alternative"):
            if isinstance(record.get(key), str):
                record[key] = record[key].replace("-change}}", "}}")
    return value


def test_a_stale_change_cannot_back_the_narrative_but_a_dated_level_can():
    fresh, stale = fixture_with_curve("2026-09-04"), fixture_with_curve("2026-09-02")
    assert (fresh["curve"]["freshness"], stale["curve"]["freshness"]) == ("older", "stale")
    # The fixture narrative cites the 2Y and 10Y daily changes: valid on today's curve, rejected on a stale one.
    assert validate_narrative(narrative(), fresh)
    with pytest.raises(ValueError, match=r"unsupplied evidence reference: treasury-(2|10)y-change"):
        validate_narrative(narrative(), stale)
    context = analyst_context(stale, edition_profile("PREMARKET"))
    with pytest.raises(ValueError, match=r"unsupplied evidence reference: treasury-(2|10)y-change"):
        validate_narrative(narrative(), stale, context)
    assert validate_narrative(with_levels_only(narrative()), stale, context)
    # The curve record still reaches the analyst and says why no move is named.
    assert (context["curve"]["freshness"], context["curve"]["reason"]) == ("stale", "stale_observation")


def comparisons_of(packet, day):
    """Deterministic comparisons of the 10Y level and daily change against snapshots of an earlier entry."""
    rows = {row["id"]: row for row in packet["observations"]}
    snapshots = {ident: dict(_snapshot(rows[ident], "earlier-run"), observed_at=day)
                 for ident in ("treasury-10y", "treasury-10y-change")}
    return {c["prior_ref"]: c for c in compare_anchor("previous_close", snapshots, packet)}


def test_no_comparison_offers_a_stale_change_as_a_current_move():
    fresh = comparisons_of(fixture_with_curve("2026-09-04"), "2026-08-31")
    stale = comparisons_of(fixture_with_curve("2026-09-02"), "2026-08-31")
    assert fresh["previous_close:treasury-10y-change"]["status"] == "changed"
    assert stale["previous_close:treasury-10y-change"]["status"] == "unavailable"
    assert stale["previous_close:treasury-10y-change"]["current_ref"] is None
    # A dated level is still a level: its comparison stands on either curve.
    assert fresh["previous_close:treasury-10y"]["status"] == stale["previous_close:treasury-10y"]["status"] == "changed"


# --- Rule 2: a change is bound to its comparison ----------------------------------------------------------------

def with_change(evidence_ids, **records):
    value = trimmed(narrative(), edition_profile("OPEN_30M"))
    value["changes"] = [dict(comparison_id="cmp-premarket-SPY-intraday", text="SPY turned against its premarket read.",
                             evidence_ids=evidence_ids)]
    value.update(records)
    return value


@pytest.mark.parametrize("evidence_ids", [
    ["SPY-intraday", "premarket:SPY-intraday"],
    ["SPY-intraday"],
    ["premarket:SPY-intraday"],
])
def test_a_change_citing_its_own_comparison_passes(evidence_ids):
    packet, context, _ = carried_setup()
    comparison = next(c for c in context["comparisons"] if c["id"] == "cmp-premarket-SPY-intraday")
    assert (comparison["current_ref"], comparison["prior_ref"]) == ("SPY-intraday", "premarket:SPY-intraday")
    assert validate_narrative(with_change(evidence_ids), packet, context)


@pytest.mark.parametrize(("evidence_ids", "foreign"), [
    (["SPY-intraday", "premarket:SPY-intraday", "QQQ-daily"], "QQQ-daily"),  # an unrelated current fact rides along
    (["QQQ-daily"], "QQQ-daily"),  # only unrelated evidence
    (["SPY-daily", "premarket:SPY-intraday"], "SPY-daily"),  # the same instrument, another measurement
    (["SPY-intraday", "premarket:QQQ-daily"], "premarket:QQQ-daily"),  # an admitted prior ref of something else
])
def test_a_change_citing_admitted_evidence_outside_its_comparison_fails(evidence_ids, foreign):
    packet, context, _ = carried_setup()
    shown = supplied_ids(context) | {row["ref"] for row in context["prior_state"]["snapshots"]}
    assert set(evidence_ids) <= shown  # admitted: only the binding can reject it
    with pytest.raises(ValueError, match=f"change cites evidence outside its comparison: {foreign}$"):
        validate_narrative(with_change(evidence_ids), packet, context)


def test_relationships_and_watch_updates_still_cite_any_admitted_evidence():
    packet, context, _ = carried_setup()
    value = with_change(
        ["SPY-intraday", "premarket:SPY-intraday"],
        watch_updates=[dict(carried_id="watch-sample-premarket-124500-tue-1", assessment="weakened",
                            reason="The print turned positive against the premarket read.",
                            evidence_ids=["SPY-intraday", "premarket:SPY-intraday", "QQQ-daily"])],
        relationships=[dict(carried_id="rel-sample-premarket-124500-tue-2", instruments=["GLD", "GDX"],
                            statement="Miners trail gold.", assessment="strengthened", reason="The gap widened again.",
                            evidence_ids=["GDX-spread20", "premarket:GDX-spread20", "SPY-intraday"])])
    assert validate_narrative(value, packet, context)

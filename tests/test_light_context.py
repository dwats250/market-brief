"""Light editions read a genuinely bounded continuity projection; rich editions are unchanged."""

import copy
import json

import pytest
from test_continuity import (
    AFTERNOON_TUE,
    CLOSE_FRI,
    PREMARKET_TUE,
    accept,
    friday_close,
    run_packet,
    session_watch_narrative,
    trimmed,
)
from test_history_admission import utc
from test_pipeline import narrative

from market_brief.context import analyst_context, edition_profile
from market_brief.continuity import (
    admit_prior_state,
    advance_bundle,
    compare_all,
    continuity_context,
    empty_bundle,
)
from market_brief.synthesize import compact_json, construct_prompt, validate_narrative

OPEN_1M_TUE = "2026-09-08T13:32:00+00:00"
OPEN_30M_TUE = "2026-09-08T14:01:00+00:00"
LIGHT = edition_profile("OPEN_30M")
RICH = edition_profile("PREMARKET")


def premarket_bundle():
    """Friday close handed off, Tuesday premarket accepted: the state every intraday edition restores."""
    state, handoff = friday_close()
    bundle = advance_bundle(empty_bundle(), state, handoff, utc(CLOSE_FRI))
    premarket = run_packet(PREMARKET_TUE, "sample-premarket-124500-tue", intraday_value=-0.53)
    _, _, _, state = accept(premarket, bundle, session_watch_narrative())
    return advance_bundle(bundle, state, None, utc(PREMARKET_TUE))


def light_edition(now, checkpoint, run_id, **kwargs):
    packet = run_packet(now, run_id, checkpoint=checkpoint, **kwargs)
    prior = admit_prior_state(premarket_bundle(), packet)
    comparisons = compare_all(prior, packet)
    packet["continuity"] = dict(status=prior["status"], reason=prior["reason"], anchors=prior["anchors"],
                                comparisons=comparisons)
    profile = edition_profile(checkpoint)
    context = dict(analyst_context(packet, profile, comparisons, prior),
                   **continuity_context(prior, comparisons, profile))
    return packet, prior, comparisons, context


def synthetic_prior():
    snapshot = lambda ident, topic, metric, unit, value, observed: dict(  # noqa: E731
        id=ident, topic=topic, metric=metric, unit=unit, value=value, observed_at=observed, run_id="run-prior",
        identity=dict(key=f"{topic}|{metric}"))
    return dict(status="available", reason="", anchors={"premarket": dict(run_id="run-prior")},
                closing_character=None,
                watches=[dict(id="w-assessable", lifecycle="active", hypothesis="h", confirmation="c",
                              contradiction="x", horizon={"declared": "SESSION"}, evidence_refs=["SPY-intraday"],
                              origin_run_id="run-prior", assessments=[{"status": "open"}]),
                         dict(id="w-missing", lifecycle="active", hypothesis="h", confirmation="c",
                              contradiction="x", horizon={"declared": "SESSION"}, evidence_refs=["GLD-intraday"],
                              origin_run_id="run-prior", assessments=[{"status": "open"}]),
                         dict(id="w-not-comparable", lifecycle="active", hypothesis="h", confirmation="c",
                              contradiction="x", horizon={"declared": "SESSION"}, evidence_refs=["XLU-dma50"],
                              origin_run_id="run-prior", assessments=[{"status": "open"}])],
                relationships=[dict(id="r-1", instruments=["SPY", "QQQ"], statement="s",
                                    evidence_refs=["QQQ-r20"], assessments=[{"status": "new"}])],
                snapshots={"premarket": {
                    "SPY-intraday": snapshot("SPY-intraday", "SPY", "premarket return", "%", -0.5,
                                             "2026-09-08T12:40:00+00:00"),
                    "GLD-intraday": snapshot("GLD-intraday", "GLD", "premarket return", "%", 0.1,
                                             "2026-09-08T12:40:00+00:00"),
                    "XLU-dma50": snapshot("XLU-dma50", "XLU", "distance from 50DMA", "%", 1.0, "2026-09-04"),
                    "QQQ-r20": snapshot("QQQ-r20", "QQQ", "twenty-session return", "%", 2.0, "2026-09-04"),
                    "AAPL-spread20": snapshot("AAPL-spread20", "AAPL", "relative to QQQ", "pp", 3.0, "2026-09-04"),
                    "XLE-r20": snapshot("XLE-r20", "XLE", "twenty-session return", "%", 4.0, "2026-09-04")}})


def synthetic_comparisons():
    def cmp(ident, status, **fields):
        record = dict(id=f"cmp-premarket-{ident}", anchor="premarket", metric_key="k", topic=ident.split("-")[0],
                      metric="m", unit="%", prior_ref=f"premarket:{ident}", prior_run_id="run-prior",
                      prior_value=1.0, prior_observed_at="2026-09-04", current_ref=None, current_value=None,
                      current_observed_at=None, delta=None, status=status, reason="")
        return dict(record, **fields)
    return [cmp("SPY-intraday", "changed", current_ref="SPY-intraday", current_value=0.2,
                current_observed_at="2026-09-08T14:00:00+00:00", delta=0.7),
            cmp("GLD-intraday", "unavailable", reason="no usable current observation of this measurement"),
            cmp("XLU-dma50", "no_new_observation", current_ref="XLU-dma50", current_value=1.0,
                current_observed_at="2026-09-04"),
            cmp("QQQ-r20", "not_comparable", current_ref="QQQ-r20", current_value=2.0,
                current_observed_at="2026-09-04", reason="unit differs"),
            cmp("AAPL-spread20", "no_new_observation", current_ref="AAPL-spread20", current_value=3.0,
                current_observed_at="2026-09-04"),
            cmp("XLE-r20", "changed", current_ref="XLE-r20", current_value=5.0, current_observed_at="2026-09-08",
                delta=1.0)]


# --- projection shape --------------------------------------------------------------------------

def test_light_projection_keeps_full_records_only_for_changed_comparisons():
    context = continuity_context(synthetic_prior(), synthetic_comparisons(), LIGHT)
    by_id = {c["id"]: c for c in context["comparisons"]}
    assert set(by_id) == {c["id"] for c in synthetic_comparisons()}  # every comparison keeps its identity
    full = {i for i, c in by_id.items() if "prior_value" in c}
    assert full == {"cmp-premarket-SPY-intraday", "cmp-premarket-XLE-r20"}
    for ident, record in by_id.items():
        if ident not in full:
            assert set(record) == {"id", "status"}
    assert context["comparison_summary"] == dict(
        mode="changed", counts=dict(changed=2, no_new_observation=2, unavailable=1, not_comparable=1),
        note=context["comparison_summary"]["note"])
    assert "values" in context["comparison_summary"]["note"]


def test_light_projection_supplies_only_citable_prior_snapshots():
    context = continuity_context(synthetic_prior(), synthetic_comparisons(), LIGHT)
    refs = {s["ref"] for s in context["prior_state"]["snapshots"]}
    # Watch and relationship dependencies plus the prior side of every changed comparison.
    assert refs == {"premarket:SPY-intraday", "premarket:GLD-intraday", "premarket:XLU-dma50",
                    "premarket:QQQ-r20", "premarket:XLE-r20"}
    assert "premarket:AAPL-spread20" not in refs


def test_light_projection_supplies_a_repeated_observation_once_across_anchors():
    prior, comparisons = synthetic_prior(), synthetic_comparisons()
    prior["anchors"]["latest"] = dict(run_id="run-latest")
    prior["snapshots"]["latest"] = copy.deepcopy(prior["snapshots"]["premarket"])
    prior["snapshots"]["latest"]["SPY-intraday"].update(value=-0.1, observed_at="2026-09-08T13:40:00+00:00")
    latest = []
    for record in comparisons:
        twin = dict(record, id=record["id"].replace("premarket", "latest"), anchor="latest",
                    prior_ref=record["prior_ref"].replace("premarket", "latest"))
        latest.append(twin)
    context = continuity_context(prior, comparisons + latest, LIGHT)
    refs = {s["ref"] for s in context["prior_state"]["snapshots"]}
    # A later anchor's identical dated background is not repeated; its distinct print and the prior
    # side of every changed comparison remain citable.
    assert "latest:XLU-dma50" not in refs and "latest:GLD-intraday" not in refs
    assert {"latest:SPY-intraday", "latest:XLE-r20", "premarket:XLE-r20", "premarket:SPY-intraday"} <= refs
    assert len(context["comparisons"]) == 12 and context["comparison_summary"]["counts"]["changed"] == 4
    rich = continuity_context(prior, comparisons + latest)
    assert len(rich["prior_state"]["snapshots"]) == 12


def test_carried_watch_evaluability_survives_the_light_projection():
    context = continuity_context(synthetic_prior(), synthetic_comparisons(), LIGHT)
    evaluability = {w["id"]: w["evaluability"] for w in context["prior_state"]["watches"]}
    assert evaluability == {"w-assessable": "assessable", "w-missing": "missing_evidence",
                            "w-not-comparable": "not_comparable"}
    rich = continuity_context(synthetic_prior(), synthetic_comparisons())
    assert evaluability == {w["id"]: w["evaluability"] for w in rich["prior_state"]["watches"]}


def test_rich_projection_is_unchanged():
    prior, comparisons = synthetic_prior(), synthetic_comparisons()
    default = continuity_context(copy.deepcopy(prior), copy.deepcopy(comparisons))
    rich = continuity_context(prior, comparisons, RICH)
    assert rich == default
    assert all("prior_value" in c for c in rich["comparisons"])
    assert len(rich["prior_state"]["snapshots"]) == 6
    assert "comparison_summary" not in rich


def test_light_catalog_limits_large_magnitude_to_current_window_rows():
    packet, prior, comparisons, context = light_edition(OPEN_30M_TUE, "OPEN_30M", "sample-open_30m-tue",
                                                       intraday_value=0.21)
    rows = {row["id"]: row for group in context["catalog"] for row in group["rows"]}
    kept_refs = set()
    for record in prior["watches"] + prior["relationships"]:
        kept_refs |= set(record["evidence_refs"])
    kept_refs |= {c["current_ref"] for c in comparisons if c["status"] == "changed" and c["current_ref"]}
    for trigger in packet["attention"]:
        kept_refs |= set(trigger["evidence_ids"])
    admitted = {r["id"] for r in [*packet["observations"], *packet["derived"]]}
    assert {ref for ref in kept_refs if ref in admitted} <= set(rows)
    background = {"twenty-session return", "distance from 50DMA", "fifty-session average", "relative to SPY",
                  "relative to QQQ"}
    stray = [i for i, r in rows.items() if r["metric"] in background and r.get("magnitude") == "LARGE"
             and i not in kept_refs and not any(g["topic"] in ("SPY", "QQQ", "GLD") and r in g["rows"]
                                                for g in context["catalog"])
             and i not in {row["id"] for rows_ in packet.get("sector_leadership", {}).values() for row in rows_}]
    assert stray == []


# --- continuity chains from the premarket anchor ------------------------------------------------

@pytest.mark.parametrize("now, checkpoint", [(OPEN_1M_TUE, "OPEN_1M"), (OPEN_30M_TUE, "OPEN_30M"),
                                             (AFTERNOON_TUE, "AFTERNOON")])
def test_light_editions_fit_their_budget_with_headroom(now, checkpoint):
    packet, prior, comparisons, context = light_edition(now, checkpoint, f"sample-{checkpoint.lower()}-tue",
                                                       intraday_value=0.21)
    assert context["prior_state"]["status"] == "available" and "premarket" in context["prior_state"]["anchors"]
    assert comparisons and any(c["status"] == "no_new_observation" for c in comparisons)
    _, user = construct_prompt(packet, context=context)
    assert len(user.encode()) <= 34_000
    assert len(compact_json(context["comparisons"]).encode()) < len(compact_json(comparisons).encode()) / 2


def test_a_change_can_name_a_retained_changed_comparison_but_not_an_omitted_one():
    packet, prior, comparisons, context = light_edition(AFTERNOON_TUE, "AFTERNOON", "sample-afternoon-tue",
                                                       intraday_value=0.21)
    value = trimmed(narrative(), edition_profile("AFTERNOON"))
    value["changes"] = [dict(comparison_id="cmp-premarket-SPY-intraday",
                             text="SPY moved from {{premarket:SPY-intraday}} to {{SPY-intraday}} since the premarket.",
                             evidence_ids=["SPY-intraday", "premarket:SPY-intraday"])]
    assert validate_narrative(value, packet, context)
    omitted = next(c for c in comparisons if c["status"] != "changed")
    value["changes"] = [dict(comparison_id=omitted["id"], text="Changed.", evidence_ids=["SPY-intraday"])]
    with pytest.raises(ValueError, match="changed comparison"):
        validate_narrative(value, packet, context)
    value["changes"] = [dict(comparison_id="cmp-invented", text="Changed.", evidence_ids=["SPY-intraday"])]
    with pytest.raises(ValueError, match="unknown"):
        validate_narrative(value, packet, context)


def test_saved_light_context_round_trips_through_json():
    _, _, _, context = light_edition(OPEN_1M_TUE, "OPEN_1M", "sample-open_1m-tue", intraday_value=0.21)
    assert json.loads(compact_json(context)) == context

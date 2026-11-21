"""An expired watch is judged on the last regular-session prints at its horizon (WP-MB-TRUTH-01, item 1).

Nov 19, 2026: the 8:02 AM PT analysis carried "XLK stays above its prior close at the close" and "XLP and XLU stay
below prior closes into the close". The close handoff held XLK, XLP and XLU's 3:59:59 PM ET last trades, yet the Nov 20
premarket marked the first `not_comparable` and the second `missing_evidence`, because evaluability looked only at
that morning's premarket prints (a different session, or none at all). Both watches ended "unresolved". The prints
that decide a close test are the ones at the close, so those snapshots now make the watch assessable, and a verdict
must cite them. After-hours trades, prints from the watch's own run and prints far from the horizon never count.
"""

import copy

import pytest

from market_brief.continuity import continuity_context, evaluability, horizon_refs, validate_state

WRITTEN = "2026-11-19T15:02:38.000000+00:00"
CLOSE = "2026-11-19T21:00:00+00:00"
ORIGIN = "live-open_30m-150238"
CLOSE_RUN = "live-close_1m-210223"


def row(ident, value, observed, run_id=CLOSE_RUN, status="DELAYED", frequency="intraday"):
    topic = ident.split("-")[0]
    return dict(id=ident, topic=topic, metric="premarket return", value=value, unit="%", observed_at=observed,
                frequency=frequency, status=status, run_id=run_id,
                identity=dict(key=f"{topic}|premarket return|intraday|-|IEX"))


def watch(ident, refs, expires=CLOSE, phrase="Into the close", declared="SESSION"):
    return dict(id=ident, lifecycle="expired", hypothesis="h", confirmation="c", contradiction="x",
                horizon=dict(declared=declared, expires_at=expires, expires_session="2026-11-19", phrase=phrase),
                evidence_refs=refs, origin_run_id=ORIGIN, first_observed_at=WRITTEN,
                assessments=[dict(status="new")])


def premarket_prior(snapshots, watches):
    return dict(status="available", reason="", anchors={"previous_close": dict(run_id=CLOSE_RUN)},
                closing_character=None, watches=watches, relationships=[],
                snapshots={"previous_close": snapshots})


def unavailable(ref):
    """This morning's comparison of a closing print: no premarket counterpart."""
    return dict(id=f"cmp-previous_close-{ref}", anchor="previous_close", prior_ref=f"previous_close:{ref}",
                status="unavailable", topic=ref.split("-")[0], metric="premarket return", unit="%",
                current_ref=None, prior_value=None, current_value=None, delta=None, prior_observed_at=None,
                current_observed_at=None)


def other_session(ref):
    return dict(unavailable(ref), status="not_comparable", current_ref=ref)


NOV19 = {
    "XLK-intraday": row("XLK-intraday", -1.85, "2026-11-19T20:59:59.716028129Z"),
    "SPY-intraday": row("SPY-intraday", -1.98, "2026-11-19T20:59:59.410000000Z"),
    "XLP-intraday": row("XLP-intraday", -1.41, "2026-11-19T20:59:59.742106219Z"),
    "XLU-intraday": row("XLU-intraday", -1.19, "2026-11-19T20:59:57.235930531Z"),
}
TECH = watch("watch-tech", ["XLK-intraday", "SPY-intraday"])
DEFENSIVE = watch("watch-defensive", ["XLP-intraday", "XLU-intraday"])
COMPARISONS = [unavailable("XLK-intraday"), other_session("SPY-intraday"), unavailable("XLP-intraday"),
               unavailable("XLU-intraday")]


def test_nov19_close_tests_are_assessable_on_their_closing_prints():
    prior = premarket_prior(NOV19, [TECH, DEFENSIVE])
    assert horizon_refs(TECH, prior) == ["previous_close:XLK-intraday", "previous_close:SPY-intraday"]
    assert horizon_refs(DEFENSIVE, prior) == ["previous_close:XLP-intraday", "previous_close:XLU-intraday"]
    # Before this fix: not_comparable and missing_evidence.
    assert evaluability(TECH, prior, COMPARISONS) == "assessable"
    assert evaluability(DEFENSIVE, prior, COMPARISONS) == "assessable"


@pytest.mark.parametrize("snapshot, why", [
    (row("XLP-intraday", 0.2, "2026-11-19T21:00:30Z", status="UNAVAILABLE"), "after-hours rows are never usable"),
    (row("XLP-intraday", 0.2, "2026-11-19T21:00:30Z"), "a trade after the horizon is after-hours"),
    (row("XLP-intraday", -1.86, "2026-11-19T15:02:30Z", run_id=ORIGIN), "the watch's own evidence"),
    (row("XLP-intraday", -1.40, "2026-11-19T20:02:30Z", status="AVAILABLE"), "an hour before the close"),
    (row("XLP-intraday", -1.40, "2026-11-18T20:59:59Z"), "another session"),
    (row("XLP-intraday", -1.40, "2026-11-19", frequency="daily"), "an untimed daily row"),
])
def test_only_regular_session_prints_at_the_horizon_decide(snapshot, why):
    prior = premarket_prior({"XLP-intraday": snapshot}, [watch("w", ["XLP-intraday"])])
    assert horizon_refs(prior["watches"][0], prior) == [], why
    assert evaluability(prior["watches"][0], prior, [unavailable("XLP-intraday")]) == "missing_evidence"


def test_a_close_print_never_decides_an_opening_hour_watch():
    opening = watch("w-open", ["XLK-intraday"], expires="2026-11-19T15:30:00+00:00",
                    phrase="Through the opening hour", declared="OPENING_HOUR")
    prior = premarket_prior(NOV19, [opening])
    assert horizon_refs(opening, prior) == []


def test_a_live_watch_is_judged_on_current_evidence_only():
    live = dict(TECH, lifecycle="active")
    assert horizon_refs(live, premarket_prior(NOV19, [live])) == []


def test_the_analyst_sees_which_snapshots_decide_and_unaffected_contexts_are_unchanged():
    prior = premarket_prior(NOV19, [TECH, DEFENSIVE])
    context = continuity_context(prior, COMPARISONS)
    watches = {w["id"]: w for w in context["prior_state"]["watches"]}
    assert watches["watch-tech"]["evaluability"] == "assessable"
    assert watches["watch-tech"]["horizon_refs"] == ["previous_close:XLK-intraday", "previous_close:SPY-intraday"]
    assert "horizon_refs" in context["prior_state"]["note"]
    refs = {row["ref"] for row in context["prior_state"]["snapshots"]}
    assert {"previous_close:XLK-intraday", "previous_close:XLP-intraday"} <= refs
    # A context with no expired watch decided at its horizon is byte for byte what it was.
    quiet = premarket_prior(NOV19, [dict(TECH, lifecycle="active")])
    record = continuity_context(quiet, COMPARISONS)["prior_state"]
    assert "horizon_refs" not in record["watches"][0]
    assert "horizon_refs" not in record["note"]


def narrative(update):
    return dict(character=dict(evidence_ids=[]), watch_updates=[update], relationships=[], changes=[])


def update(assessment, evidence_ids):
    return dict(carried_id="watch-tech", assessment=assessment, evidence_ids=evidence_ids, reason="r")


def test_a_verdict_on_an_expired_watch_cites_its_horizon_prints():
    context = continuity_context(premarket_prior(NOV19, [TECH]), COMPARISONS)
    shown = {"XLK-intraday", "SPY-intraday"}
    validate_state(narrative(update("weakened", ["previous_close:XLK-intraday"])), context, shown, set())
    validate_state(narrative(update("weakened", ["previous_close:XLK-intraday", "previous_close:SPY-intraday"])),
                   context, shown, set())
    validate_state(narrative(update("unresolved", [])), context, shown, set())
    # This morning's premarket print is another session: it never stands for yesterday's close.
    with pytest.raises(ValueError, match="horizon"):
        validate_state(narrative(update("weakened", ["XLK-intraday"])), context, shown, set())


def test_a_watch_without_horizon_prints_still_needs_current_evidence():
    prior = premarket_prior(copy.deepcopy(NOV19), [dict(TECH, lifecycle="active")])
    comparisons = [dict(unavailable("XLK-intraday"), status="changed", current_ref="XLK-intraday")]
    context = continuity_context(prior, comparisons)
    with pytest.raises(ValueError, match="must cite current evidence"):
        validate_state(narrative(update("weakened", ["previous_close:XLK-intraday"])), context, {"XLK-intraday"},
                       set())


def test_partial_horizon_coverage_stays_unresolved():
    """Review P1: with SPY's close print alone, XLK's close test is not decided at its horizon, and this morning's
    XLK print (another session) can never fill the gap."""
    partial = premarket_prior({"SPY-intraday": NOV19["SPY-intraday"]}, [TECH])
    assert horizon_refs(TECH, partial) == []
    assert evaluability(TECH, partial, COMPARISONS) == "not_comparable"


def test_a_daily_background_ref_does_not_block_the_horizon():
    """Only the watch's timestamped prints need a horizon print; a daily row it also cites is prior-day context."""
    mixed = dict(TECH, evidence_refs=["XLK-intraday", "SPY-intraday", "XLK-dma50"])
    prior = premarket_prior(dict(NOV19, **{"XLK-dma50": row("XLK-dma50", -3.0, "2026-11-18", frequency="daily")}),
                            [mixed])
    assert horizon_refs(mixed, prior) == ["previous_close:XLK-intraday", "previous_close:SPY-intraday"]


def test_a_verdict_on_an_expired_watch_cites_only_its_horizon_prints():
    """Review P1: a current ref (and so a placeholder quoting it) beside a horizon ref is still another session."""
    context = continuity_context(premarket_prior(NOV19, [TECH]), COMPARISONS)
    shown = {"XLK-intraday", "SPY-intraday"}
    for cited in (["previous_close:SPY-intraday", "XLK-intraday"], ["previous_close:XLK-intraday", "SPY-intraday"]):
        with pytest.raises(ValueError, match="horizon"):
            validate_state(narrative(update("strengthened", cited)), context, shown, set())

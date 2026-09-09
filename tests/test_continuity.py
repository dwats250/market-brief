"""Structured session state: deterministic identity, valid comparisons, fail-closed admission."""

import copy
import json

import pytest
from test_history_admission import packet_at, utc
from test_pipeline import narrative

from market_brief import continuity
from market_brief.context import analyst_context, edition_profile
from market_brief.continuity import (
    BUNDLE_SCHEMA,
    admit_prior_state,
    advance_bundle,
    compare_all,
    continuity_context,
    edition_state,
    empty_bundle,
    load_bundle,
    resolve_horizon,
    session_handoff,
    write_bundle,
)
from market_brief.evidence import digest
from market_brief.synthesize import validate_narrative

CLOSE_FRI = "2026-09-04T20:03:00+00:00"
PREMARKET_TUE = "2026-09-08T12:45:00+00:00"
AFTERNOON_TUE = "2026-09-08T19:10:00+00:00"
CLOSE_TUE = "2026-09-08T20:03:00+00:00"
PREMARKET_WED = "2026-09-09T12:45:00+00:00"
NEXT_EVENT = dict(id="wed-release", title="Fictional Wednesday release", source_id="bls", published_at=None,
                  checked_at=CLOSE_TUE, scheduled_at="2026-09-09T12:30:00+00:00", status="SCHEDULED")


def run_packet(now, run_id, **kwargs):
    packet = packet_at(utc(now), **kwargs)
    packet["run"]["run_id"] = run_id
    return packet


def accept(packet, bundle, value=None):
    """One edition end to end: admit, compare, build context, validate, package."""
    prior = admit_prior_state(bundle, packet)
    comparisons = compare_all(prior, packet)
    packet["continuity"] = dict(status=prior["status"], reason=prior["reason"], anchors=prior["anchors"],
                                comparisons=comparisons)
    profile = edition_profile(packet["run"]["checkpoint"])
    context = dict(analyst_context(packet, profile, comparisons, prior), **continuity_context(prior, comparisons))
    value = trimmed(value or narrative(), profile)
    validate_narrative(value, packet, context)
    state = edition_state(packet, value, prior, comparisons, digest(context), digest(value), "test")
    return prior, comparisons, context, state


def trimmed(value, profile):
    """A light edition answers with fewer paragraphs, watches, and attention items."""
    value["summary"] = value["summary"][:profile["summary_paragraphs"]]
    value["watches"] = value["watches"][:profile["watches"]]
    value["attention_ids"] = value["attention_ids"][:profile["attention_items"]]
    value["attention"] = [a for a in value["attention"] if a["id"] in value["attention_ids"]]
    return value


def friday_close():
    packet = run_packet(CLOSE_FRI, "sample-close_1m-200300-fri", checkpoint="CLOSE_1M")
    prior, comparisons, context, state = accept(packet, empty_bundle())
    handoff, reason = session_handoff(state)
    assert handoff is not None, reason
    return state, handoff


# --- packaging and identity ------------------------------------------------------------------

def test_close_edition_packages_code_owned_identities_and_separates_observed_from_assessment():
    state, handoff = friday_close()
    assert state["kind"] == "edition_state" and handoff["kind"] == "session_handoff"
    assert state["origin"]["run_id"] == "sample-close_1m-200300-fri"
    assert state["observed"]["data_status"]["status"] == "COMPLETED_SESSION"
    assert state["observed"]["continuity"] == "cold_start"
    assert state["assessment"]["class"] == "INTERPRETATION" and state["assessment"]["origin"] == "analyst"
    watches = state["assessment"]["watches"]
    assert [w["id"] for w in watches] == ["watch-sample-close_1m-200300-fri-1", "watch-sample-close_1m-200300-fri-2"]
    assert all(w["assessments"] == [dict(status="new", run_id="sample-close_1m-200300-fri",
                                         assessed_at=packet_time(state), current_refs=w["evidence_refs"],
                                         previous_refs=[], reason="", origin="analyst")] for w in watches)
    assert watches[0]["horizon"]["declared"] == "OPENING_HOUR"
    assert watches[0]["horizon"]["expires_session"] == "2026-09-08"  # after Friday's close, the next session
    assert watches[0]["metric_keys"] and watches[0]["instruments"] == ["NVDA", "QQQ", "SPY"]
    assert [r["id"] for r in state["assessment"]["relationships"]] == [
        "rel-sample-close_1m-200300-fri-1", "rel-sample-close_1m-200300-fri-2"]
    assert handoff["assessment"]["closing_character"]["provisional"] is False
    assert handoff["observed"]["closing_data"]["status"] == "COMPLETED_SESSION"
    # Snapshots keep the anchors and every watch dependency with their own run identity.
    snapshots = handoff["observed"]["snapshots"]
    assert {"SPY-daily", "QQQ-daily", "GLD-daily", "GDX-daily", "GDX-spread20", "treasury-2y-change"} <= set(snapshots)
    assert all(row["run_id"] == "sample-close_1m-200300-fri" and row["identity"]["key"] for row in snapshots.values())
    assert handoff["content_hash"] == digest({k: v for k, v in handoff.items() if k != "content_hash"})


def packet_time(state):
    return state["origin"]["target_time"]


# --- the fixture chain: close -> next premarket -> close ----------------------------------------

def test_premarket_admits_only_the_previous_exchange_sessions_close_across_the_holiday():
    _, handoff = friday_close()
    bundle = advance_bundle(empty_bundle(), _, handoff, utc(CLOSE_FRI))
    packet = run_packet(PREMARKET_TUE, "sample-premarket-124500-tue", intraday=False)
    prior = admit_prior_state(bundle, packet)
    assert prior["status"] == "available"
    assert set(prior["anchors"]) == {"previous_close"}
    assert prior["anchors"]["previous_close"]["session_date"] == "2026-09-04"
    assert [w["id"] for w in prior["watches"]] == [
        "watch-sample-close_1m-200300-fri-1", "watch-sample-close_1m-200300-fri-2"]
    # Wednesday's premarket may not see Friday's close: Tuesday sits between them.
    later = run_packet(PREMARKET_WED, "sample-premarket-124500-wed", last_history_date="2026-09-08", intraday=False)
    stale = admit_prior_state(bundle, later)
    assert stale["status"] == "cold_start"
    assert "2026-09-04" in stale["reason"] and "2026-09-08" in stale["reason"]


def test_no_prior_prose_reaches_the_analyst_context():
    _, handoff = friday_close()
    bundle = advance_bundle(empty_bundle(), _, handoff, utc(CLOSE_FRI))
    packet = run_packet(PREMARKET_TUE, "sample-premarket-124500-tue", intraday=False)
    prior, comparisons, context, state = accept(packet, bundle)
    text = json.dumps(context)
    for prose in (narrative()["banner"]["title"], narrative()["summary"][0]["text"][:40],
                  narrative()["sections"]["macro"][0]["text"][:40]):
        assert prose not in text
    assert context["prior_state"]["status"] == "available"
    assert context["prior_state"]["class"] == "INTERPRETATION"
    assert {w["id"] for w in context["prior_state"]["watches"]} == {w["id"] for w in prior["watches"]}
    assert all(":" in row["ref"] for row in context["prior_state"]["snapshots"])
    # Carried hypotheses are the structured criteria, never yesterday's headline.
    assert context["prior_state"]["watches"][0]["hypothesis"] == narrative()["watches"][0]["condition"]


def test_repeated_observations_do_not_imply_change_and_missing_facts_are_explicit():
    _, handoff = friday_close()
    bundle = advance_bundle(empty_bundle(), _, handoff, utc(CLOSE_FRI))
    packet = run_packet(PREMARKET_TUE, "sample-premarket-124500-tue", intraday=False)
    prior = admit_prior_state(bundle, packet)
    comparisons = {c["id"]: c for c in compare_all(prior, packet)}
    # Friday's completed bars are Tuesday's prior close: the same observation, not a change.
    assert comparisons["cmp-previous_close-SPY-daily"]["status"] == "no_new_observation"
    assert comparisons["cmp-previous_close-SPY-daily"]["delta"] is None
    # Friday's session print has no premarket counterpart.
    assert comparisons["cmp-previous_close-SPY-intraday"]["status"] == "unavailable"
    assert all(c["status"] != "changed" for c in comparisons.values())
    context = continuity_context(prior, list(comparisons.values()))
    watches = {w["id"]: w for w in context["prior_state"]["watches"]}
    assert watches["watch-sample-close_1m-200300-fri-2"]["evaluability"] == "not_comparable"


def test_intraday_editions_compare_against_the_premarket_anchor_and_latest_edition():
    _, handoff = friday_close()
    bundle = advance_bundle(empty_bundle(), _, handoff, utc(CLOSE_FRI))
    premarket = run_packet(PREMARKET_TUE, "sample-premarket-124500-tue", intraday_value=-0.53)
    _, _, _, state = accept(premarket, bundle)
    bundle = advance_bundle(bundle, state, None, utc(PREMARKET_TUE))
    assert bundle["close"] is handoff and bundle["premarket"] is state and bundle["latest"] is state
    afternoon = run_packet(AFTERNOON_TUE, "sample-afternoon-191000-tue", checkpoint="AFTERNOON",
                           intraday_value=0.21)
    prior, comparisons, context, state = accept(afternoon, bundle)
    assert set(prior["anchors"]) == {"premarket"}  # latest is the same run as premarket: not duplicated
    by_id = {c["id"]: c for c in comparisons}
    spy = by_id["cmp-premarket-SPY-intraday"]
    assert spy["status"] == "changed" and spy["delta"] == pytest.approx(0.74)
    assert spy["prior_ref"] == "premarket:SPY-intraday" and spy["current_ref"] == "SPY-intraday"
    assert by_id["cmp-premarket-SPY-daily"]["status"] == "no_new_observation"
    assert set(prior["anchors"]["premarket"]) >= {"run_id", "evidence_hash", "evidence_cutoff", "data_status"}


def test_post_close_without_session_observations_does_not_advance_close_state():
    _, handoff = friday_close()
    bundle = advance_bundle(empty_bundle(), _, handoff, utc(CLOSE_FRI))
    premarket = run_packet(PREMARKET_TUE, "sample-premarket-124500-tue", intraday=False)
    _, _, _, state = accept(premarket, bundle)
    bundle = advance_bundle(bundle, state, None, utc(PREMARKET_TUE))
    close = run_packet(CLOSE_TUE, "sample-close_1m-200300-tue", checkpoint="CLOSE_1M", intraday=False)
    assert close["history_lag"]
    prior, comparisons, context, state = accept(close, bundle)
    assert state["observed"]["data_status"]["status"] == "EARLIER_HISTORY_ONLY"
    handoff_tue, reason = session_handoff(state)
    assert handoff_tue is None and "closing character unavailable" in reason
    bundle = advance_bundle(bundle, state, handoff_tue, utc(CLOSE_TUE))
    assert bundle["close"] is handoff and bundle["latest"] is state
    wednesday = run_packet(PREMARKET_WED, "sample-premarket-124500-wed", last_history_date="2026-09-08",
                           intraday=False)
    prior = admit_prior_state(bundle, wednesday)
    assert prior["status"] == "cold_start" and "close continuity unavailable" in prior["reason"]


def test_provisional_near_close_prints_advance_a_labeled_handoff_and_carry_next_session_events():
    _, handoff = friday_close()
    bundle = advance_bundle(empty_bundle(), _, handoff, utc(CLOSE_FRI))
    premarket = run_packet(PREMARKET_TUE, "sample-premarket-124500-tue", intraday_value=-0.53)
    _, _, _, state = accept(premarket, bundle)
    bundle = advance_bundle(bundle, state, None, utc(PREMARKET_TUE))
    close = run_packet(CLOSE_TUE, "sample-close_1m-200300-tue", checkpoint="CLOSE_1M", intraday_value=0.4,
                       events=[NEXT_EVENT])
    assert next(e for e in close["events"] if e["id"] == "wed-release")["session_relation"] == "NEXT SESSION"
    prior, comparisons, context, state = accept(close, bundle)
    assert state["observed"]["data_status"]["status"] == "PROVISIONAL_NEAR_CLOSE"
    handoff_tue, reason = session_handoff(state)
    assert handoff_tue["assessment"]["closing_character"]["provisional"] is True
    assert handoff_tue["next_events"] == [dict(id="wed-release", title="Fictional Wednesday release",
                                               scheduled_at="2026-09-09T12:30:00+00:00", session_date="2026-09-09")]
    bundle = advance_bundle(bundle, state, handoff_tue, utc(CLOSE_TUE))
    assert bundle["close"] is handoff_tue and bundle["premarket"]["origin"]["checkpoint"] == "PREMARKET"
    wednesday = run_packet(PREMARKET_WED, "sample-premarket-124500-wed", last_history_date="2026-09-08",
                           intraday=False, events=[dict(NEXT_EVENT, checked_at=PREMARKET_WED)])
    prior = admit_prior_state(bundle, wednesday)
    assert prior["status"] == "available"
    assert prior["next_events"][0]["id"] == "wed-release"
    # The event is rechecked from current collection and keeps its own date.
    assert next(e for e in wednesday["events"] if e["id"] == "wed-release")["session_relation"] == "BEFORE OPEN"
    comparisons = {c["id"]: c for c in compare_all(prior, wednesday)}
    assert comparisons["cmp-previous_close-SPY-daily"]["status"] == "changed"
    assert comparisons["cmp-previous_close-SPY-daily"]["prior_observed_at"] == "2026-09-04"
    assert comparisons["cmp-previous_close-SPY-daily"]["current_observed_at"] == "2026-09-08"
    assert comparisons["cmp-previous_close-SPY-intraday"]["status"] == "unavailable"


def session_watch_narrative():
    """A premarket note whose first watch runs into the close and cites the current print."""
    value = narrative()
    value["watches"][0].update(horizon="SESSION", evidence_ids=["SPY-intraday", "QQQ-daily", "NVDA-spread20"])
    return value


def test_carried_watch_assessment_and_lifecycle():
    _, handoff = friday_close()
    bundle = advance_bundle(empty_bundle(), _, handoff, utc(CLOSE_FRI))
    premarket = run_packet(PREMARKET_TUE, "sample-premarket-124500-tue", intraday_value=-0.53)
    _, _, _, state = accept(premarket, bundle, session_watch_narrative())
    bundle = advance_bundle(bundle, state, None, utc(PREMARKET_TUE))
    afternoon = run_packet(AFTERNOON_TUE, "sample-afternoon-191000-tue", checkpoint="AFTERNOON", intraday_value=0.21)
    prior = admit_prior_state(bundle, afternoon)
    lifecycles = {w["id"]: w["lifecycle"] for w in prior["watches"]}
    # Premarket's session watch is still live; its opening-hour sibling from Friday has expired.
    assert lifecycles["watch-sample-premarket-124500-tue-1"] == "active"
    assert lifecycles["watch-sample-close_1m-200300-fri-1"] == "expired"
    assert "watch-sample-close_1m-200300-fri-2" not in lifecycles  # dropped at the three-watch cap
    value = narrative()
    value["watch_updates"] = [dict(carried_id="watch-sample-premarket-124500-tue-1", assessment="weakened",
                                   reason="The benchmark print turned positive against the premarket read.",
                                   evidence_ids=["SPY-intraday", "premarket:SPY-intraday"])]
    value["watches"] = value["watches"][:1]
    prior, comparisons, context, state = accept(afternoon, bundle, value)
    carried = next(w for w in state["assessment"]["watches"] if w["id"] == "watch-sample-premarket-124500-tue-1")
    assert carried["assessments"][-1]["status"] == "weakened"
    assert carried["assessments"][-1]["previous_refs"] == ["premarket:SPY-intraday"]
    assert carried["assessments"][-1]["origin"] == "analyst" and carried["version"] == 1
    assert len(state["assessment"]["watches"]) <= 3
    assert all(w["lifecycle"] == "active" for w in state["assessment"]["watches"])
    assert any(w["id"] == "watch-sample-close_1m-200300-fri-1" for w in state["assessment"]["retired"])


# --- invalid new state is rejected without touching prior state -----------------------------

def carried_setup():
    _, handoff = friday_close()
    bundle = advance_bundle(empty_bundle(), _, handoff, utc(CLOSE_FRI))
    premarket = run_packet(PREMARKET_TUE, "sample-premarket-124500-tue", intraday_value=-0.53)
    _, _, _, state = accept(premarket, bundle, session_watch_narrative())
    bundle = advance_bundle(bundle, state, None, utc(PREMARKET_TUE))
    afternoon = run_packet(AFTERNOON_TUE, "sample-afternoon-191000-tue", checkpoint="AFTERNOON", intraday_value=0.21)
    prior = admit_prior_state(bundle, afternoon)
    comparisons = compare_all(prior, afternoon)
    profile = edition_profile("AFTERNOON")
    context = dict(analyst_context(afternoon, profile, comparisons, prior), **continuity_context(prior, comparisons))
    return afternoon, context, bundle


@pytest.mark.parametrize("mutation", ["unknown-watch", "survived-without-evidence", "prior-ref-in-summary",
                                      "unknown-relationship", "carried-as-new", "change-without-change",
                                      "renamed-carried-watch"])
def test_invalid_continuity_records_are_rejected(mutation):
    packet, context, bundle = carried_setup()
    value = trimmed(narrative(), edition_profile("AFTERNOON"))
    if mutation == "unknown-watch":
        value["watch_updates"] = [dict(carried_id="watch-invented", assessment="weakened", reason="x",
                                       evidence_ids=["SPY-intraday"])]
    elif mutation == "survived-without-evidence":
        # GLD/GDX daily rows repeat Friday's observation: not comparable, so not "strengthened".
        value["watch_updates"] = [dict(carried_id="watch-sample-premarket-124500-tue-2", assessment="strengthened",
                                       reason="x", evidence_ids=["GLD-daily"])]
    elif mutation == "prior-ref-in-summary":
        value["summary"][0]["evidence_ids"].append("premarket:SPY-intraday")
    elif mutation == "unknown-relationship":
        value["relationships"][0].update(carried_id="rel-invented", assessment="strengthened")
    elif mutation == "carried-as-new":
        value["relationships"][0].update(carried_id="rel-sample-premarket-124500-tue-1", assessment="new")
    elif mutation == "change-without-change":
        value["changes"] = [dict(comparison_id="cmp-premarket-SPY-daily", text="Changed.",
                                 evidence_ids=["SPY-daily"])]
    else:
        value["watch_updates"] = [dict(carried_id="watch-sample-premarket-124500-tue-1 (renamed)",
                                       assessment="weakened", reason="x", evidence_ids=["SPY-intraday"])]
    before = copy.deepcopy(bundle)
    with pytest.raises(ValueError):
        validate_narrative(value, packet, context)
    assert bundle == before


def test_valid_change_interpretation_cites_the_deterministic_comparison():
    packet, context, bundle = carried_setup()
    value = trimmed(narrative(), edition_profile("AFTERNOON"))
    value["changes"] = [dict(comparison_id="cmp-premarket-SPY-intraday",
                             text="SPY moved from {{premarket:SPY-intraday}} to {{SPY-intraday}} since the premarket.",
                             evidence_ids=["SPY-intraday", "premarket:SPY-intraday"])]
    assert validate_narrative(value, packet, context)
    prior = admit_prior_state(bundle, packet)
    comparisons = compare_all(prior, packet)
    state = edition_state(packet, value, prior, comparisons, digest(context), digest(value), "test")
    assert state["assessment"]["changes"][0]["comparison_id"] == "cmp-premarket-SPY-intraday"
    assert next(c for c in state["observed"]["comparisons"] if c["id"] == "cmp-premarket-SPY-intraday")["delta"] == \
        pytest.approx(0.74)


# --- missing, stale, corrupt, or foreign state means an explicit cold start -------------------

def test_missing_stale_corrupt_and_foreign_bundles_cold_start(tmp_path):
    packet = run_packet(PREMARKET_TUE, "sample-premarket-124500-tue", intraday=False)
    bundle, note = load_bundle(tmp_path / "missing.json")
    assert note == "no continuity bundle" and admit_prior_state(bundle, packet)["status"] == "cold_start"
    (tmp_path / "corrupt.json").write_text("{not json")
    assert load_bundle(tmp_path / "corrupt.json")[1] == "continuity bundle unreadable"
    (tmp_path / "schema.json").write_text(json.dumps({"schema_version": "other"}))
    assert load_bundle(tmp_path / "schema.json")[1] == "continuity bundle schema mismatch"
    _, handoff = friday_close()
    good = advance_bundle(empty_bundle(), _, handoff, utc(CLOSE_FRI))
    write_bundle(tmp_path / "bundle.json", good)
    loaded, note = load_bundle(tmp_path / "bundle.json")
    assert note == "" and loaded["close"]["content_hash"] == handoff["content_hash"]
    tampered = json.loads(json.dumps(good))
    tampered["close"]["assessment"]["watches"][0]["hypothesis"] = "rewritten after the fact"
    write_bundle(tmp_path / "tampered.json", tampered)
    loaded, note = load_bundle(tmp_path / "tampered.json")
    assert loaded["close"] is None and "corrupt" in note
    for change, reason in ((dict(mode="LIVE"), "mode mismatch"),
                           (dict(experiment=True), "commissioning or experiment origin"),
                           (dict(commissioning=True), "commissioning or experiment origin"),
                           (dict(target_time="2026-09-08T13:00:00+00:00"), "not earlier")):
        foreign = json.loads(json.dumps(good))
        foreign["close"]["origin"].update(change)
        foreign["close"] = continuity._hashed(foreign["close"])
        prior = admit_prior_state(foreign, packet)
        assert prior["status"] == "cold_start" and reason in prior["reason"]
    assert empty_bundle()["schema_version"] == BUNDLE_SCHEMA


# --- horizons ----------------------------------------------------------------------------------

def test_horizons_resolve_from_the_exchange_calendar_not_from_tomorrow():
    premarket = utc(PREMARKET_TUE)
    assert resolve_horizon("OPENING_HOUR", premarket)["expires_at"] == "2026-09-08T14:30:00+00:00"
    assert resolve_horizon("SESSION", premarket)["expires_at"] == "2026-09-08T20:00:00+00:00"
    assert resolve_horizon("NEXT_BRIEF", premarket, current_checkpoint="PREMARKET")["next_checkpoint"] == "OPEN_1M"
    assert resolve_horizon("NEXT_BRIEF", premarket)["expires_session"] == "2026-09-08"
    after_close = utc(CLOSE_TUE)
    assert resolve_horizon("NEXT_BRIEF", after_close)["expires_session"] == "2026-09-09"
    assert resolve_horizon("SESSION", after_close)["phrase"] == "Into the next session"
    friday = utc(CLOSE_FRI)
    assert resolve_horizon("OPENING_HOUR", friday)["expires_session"] == "2026-09-08"  # Labor Day skipped
    # After today's opening hour but before the close, the horizon is the next session's opening hour.
    afternoon = resolve_horizon("OPENING_HOUR", utc(AFTERNOON_TUE))
    assert afternoon["expires_session"] == "2026-09-09" and afternoon["expires_at"] == "2026-09-09T14:30:00+00:00"
    holiday = resolve_horizon("OPENING_HOUR", utc("2026-09-07T15:00:00+00:00"))
    assert holiday["expires_session"] == "2026-09-08"
    early = utc("2026-11-27T15:00:00+00:00")
    assert resolve_horizon("SESSION", early)["expires_at"] == "2026-11-27T18:00:00+00:00"
    assert resolve_horizon("NEXT_BRIEF", utc("2026-11-27T17:30:00+00:00"))["next_checkpoint"] == "CLOSE_1M"
    event = resolve_horizon("EVENT(wed-release)", after_close, [NEXT_EVENT])
    assert event["expires_session"] == "2026-09-09" and event["phrase"].startswith("Around ")
    with pytest.raises(ValueError):
        resolve_horizon("EVENT(unknown)", after_close, [NEXT_EVENT])


# --- the CLI: only accepted production state advances the bundle -------------------------------

def live_cli(monkeypatch, tmp_path, now, checkpoint, intraday=True, value=None):
    from test_pipeline import freeze_clock

    from market_brief import cli
    from market_brief.evidence import ROOT, read_json
    freeze_clock(monkeypatch, now)
    raw = read_json(ROOT / "tests/fixtures/evidence.sample.json")
    raw["mode"] = "LIVE"
    for row in raw["history"] + raw["observations"] + raw["events"]:
        row["retrieved_at"] = now
        if "checked_at" in row:
            row["checked_at"] = now
    if intraday:
        raw["observations"].append(dict(
            id="SPY-intraday", topic="SPY", metric="premarket return", value=-0.53, unit="%",
            baseline="latest trade versus previous regular close", frequency="intraday",
            observed_at=now, retrieved_at=now, source_id="sample-prices", status="AVAILABLE", reason=""))
    monkeypatch.setattr(cli, "collect_live", lambda target, include_cuttingboard=False: raw)
    live = value or narrative()
    live["mode"] = "LIVE"
    monkeypatch.setattr(cli, "synthesize", lambda packet, **kwargs: (live, {"route": "test"}))
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(cli, "update_latest", lambda root, page: None)
    monkeypatch.setattr(cli, "publish_latest", lambda root: None)
    return cli


def test_live_chain_advances_the_bundle_only_for_accepted_production_runs(tmp_path, monkeypatch):
    from market_brief.continuity import bundle_path
    cli = live_cli(monkeypatch, tmp_path, "2026-09-04T20:03:00+00:00", "CLOSE_1M")
    assert cli.main(["premarket", "--checkpoint", "CLOSE_1M"]) == 0
    bundle, note = load_bundle(bundle_path(tmp_path))
    assert note == "" and bundle["close"]["kind"] == "session_handoff"
    assert bundle["close"]["session"]["date"] == "2026-09-04"
    close_hash = bundle["close"]["content_hash"]
    folder = next(p for p in (tmp_path / "runs/2026-09-04").iterdir() if (p / "evidence.json").exists())
    assert (folder / "edition_state.json").exists() and (folder / "session_handoff.json").exists()
    metadata = json.loads((folder / "metadata.json").read_text())
    assert metadata["continuity"]["advanced"] is True and metadata["continuity"]["handoff"] == "written"
    # Tuesday premarket admits Friday's close and advances the edition pointers, not the close.
    cli = live_cli(monkeypatch, tmp_path, "2026-09-08T12:45:00+00:00", "PREMARKET", intraday=False)
    assert cli.main(["premarket", "--checkpoint", "PREMARKET"]) == 0
    bundle, _ = load_bundle(bundle_path(tmp_path))
    assert bundle["close"]["content_hash"] == close_hash
    assert bundle["premarket"]["origin"]["checkpoint"] == "PREMARKET" and bundle["latest"] is not None
    folder = next(p for p in (tmp_path / "runs/2026-09-08").iterdir() if (p / "evidence.json").exists())
    context = json.loads((folder / "analyst_context.json").read_text())
    assert context["prior_state"]["status"] == "available"
    evidence = json.loads((folder / "evidence.json").read_text())
    assert evidence["continuity"]["anchors"]["previous_close"]["session_date"] == "2026-09-04"
    # A failed synthesis leaves every pointer untouched.
    cli = live_cli(monkeypatch, tmp_path, "2026-09-08T19:10:00+00:00", "AFTERNOON")
    monkeypatch.setattr(cli, "synthesize", lambda packet, **kwargs: (_ for _ in ()).throw(ValueError("invalid")))
    before = bundle_path(tmp_path).read_text()
    assert cli.main(["premarket", "--checkpoint", "AFTERNOON"]) == 2
    assert bundle_path(tmp_path).read_text() == before


@pytest.mark.parametrize("flags", [["--replay"], ["--commissioning"], ["--experiment"]])
def test_sample_commissioning_and_experiment_runs_never_advance_state(tmp_path, monkeypatch, flags):
    from market_brief.continuity import bundle_path
    cli = live_cli(monkeypatch, tmp_path, "2026-09-04T20:03:00+00:00", "CLOSE_1M")
    assert cli.main(["premarket", *flags]) == 0
    assert not bundle_path(tmp_path).exists()
    folder = next(p for p in (tmp_path / "runs").glob("*/*") if (p / "evidence.json").exists())
    metadata = json.loads((folder / "metadata.json").read_text())
    assert metadata["continuity"]["advanced"] is False
    assert (folder / "edition_state.json").exists()  # the record exists; production pointers do not move


def test_replay_admits_the_sample_close_fixture_and_assesses_carried_watches(tmp_path, monkeypatch):
    from market_brief import cli
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(cli, "update_latest", lambda root, page: None)
    assert cli.main(["premarket", "--replay"]) == 0
    folder = next(p for p in (tmp_path / "runs").glob("*/*") if (p / "evidence.json").exists())
    context = json.loads((folder / "analyst_context.json").read_text())
    assert context["prior_state"]["status"] == "available"
    assert context["prior_state"]["anchors"]["previous_close"]["run_id"] == "sample-close_1m-200300-fixture"
    state = json.loads((folder / "edition_state.json").read_text())
    carried = [w for w in state["assessment"]["watches"] if w["origin_run_id"] == "sample-close_1m-200300-fixture"]
    assert carried and carried[0]["assessments"][-1]["status"] == "unresolved"
    assert carried[0]["assessments"][-1]["previous_refs"] == ["previous_close:QQQ-daily"]
    assert state["observed"]["continuity"] == "available"
    # Cold start is explicit when no bundle is supplied.
    assert cli.main(["premarket", "--replay", "--continuity", str(tmp_path / "absent.json")]) == 0
    contexts = [json.loads((p / "analyst_context.json").read_text())
                for p in (tmp_path / "runs").glob("*/*") if (p / "evidence.json").exists()]
    cold = next(c for c in contexts if c["prior_state"]["status"] == "cold_start")
    assert cold["prior_state"]["reason"] == "close continuity unavailable: absent; no continuity bundle"

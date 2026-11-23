"""A1: the daily session record written by an accepted production close (WP-MB-WEEK-AHEAD-01).

Plan of record: docs/2026-09-27-market-memory-week-ahead.md §3 A1, §4 and §9 A1.
"""

import json

import pytest
from test_cadence import TUE, Day

from market_brief import cli, memory
from market_brief.continuity import bundle_path
from market_brief.evidence import digest
from market_brief.memory import MEMORY_FILE, MEMORY_SCHEMA, session_memory, verify

CLOSE = f"{TUE}T20:03:00+00:00"
PRINT_AT = f"{TUE}T19:59:58+00:00"


@pytest.fixture
def day(monkeypatch, tmp_path):
    return Day(monkeypatch, tmp_path)


def full_day(day, close_prints=(("SPY", -0.9), ("QQQ", -1.2), ("XLI", 0.1)), flags=(), **close):
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M") == 0
    return day.run(CLOSE, "CLOSE_1M", print_at=PRINT_AT, prints=close_prints, flags=flags, **close)


def record(day):
    return json.loads((day.folder("CLOSE_1M") / MEMORY_FILE).read_text())


def test_an_accepted_production_close_writes_a_hashed_record_with_no_model_call(day):
    assert full_day(day) == 0
    assert day.calls == ["PREMARKET", "OPEN_30M"]  # the record adds no call
    meta = day.metadata("CLOSE_1M")
    assert meta["synthesis"] == dict(kind="close", calls=0) and meta["validation"] == "PASS"
    assert meta["continuity"]["advanced"] is True
    saved = record(day)
    assert verify(saved) and saved["schema_version"] == MEMORY_SCHEMA and saved["kind"] == "session_memory"
    assert meta["memory"] == dict(file=MEMORY_FILE, content_hash=saved["content_hash"], gaps=len(saved["gaps"]))
    source = saved["source"]
    assert source["mode"] == "LIVE" and source["commissioning"] is False and source["experiment"] is False
    assert source["checkpoint"] == "CLOSE_1M" and source["run_id"] == day.folder("CLOSE_1M").name
    assert source["evidence_hash"] == meta["evidence_hash"] and source["written_by"] == "close"
    handoff = json.loads((day.folder("CLOSE_1M") / "session_handoff.json").read_text())
    assert source["handoff_hash"] == handoff["content_hash"] and source["closing_data"] == "PROVISIONAL_NEAR_CLOSE"
    assert saved["session"]["date"] == TUE
    # The read is the session's accepted interpretation, labeled interpretation.
    assert saved["retained"]["class"] == "INTERPRETATION"
    assert saved["retained"]["read"]["checkpoint"] == "OPEN_30M"
    assert saved["retained"]["read"]["hash"] == day.bundle()["interpretation"]["content_hash"]


def test_every_observed_value_keeps_its_original_lineage(day):
    assert full_day(day) == 0
    saved = record(day)
    evidence = json.loads((day.folder("CLOSE_1M") / "evidence.json").read_text())
    rows = {r["id"]: r for r in evidence["observations"] + evidence["derived"]}
    returns = {s: r for s, r in saved["observed"]["returns"].items() if r}
    assert set(returns) == {"SPY", "QQQ", "XLI"}
    kept = [*returns.values(), *saved["observed"]["leadership"]["top"], *saved["observed"]["leadership"]["bottom"],
            *saved["observed"]["rates"]["rows"]]
    assert kept
    for row in kept:
        original = rows[row["id"]]
        for key in ("source_id", "observed_at", "retrieved_at", "value", "metric", "formula_version", "input_ids"):
            assert row.get(key) == original.get(key), (row["id"], key)
        assert row["source_id"] in saved["sources"]  # attribution survives the close run
        assert row["source_id"] != "session-memory"
    # Only this session's prints stand for the session; a trade is never labeled a close.
    assert {r["basis"] for r in returns.values()} == {"last_trade"}
    assert returns["SPY"]["value"] == -0.9


def test_missing_symbols_are_named_gaps_not_silence(day):
    assert full_day(day) == 0
    saved = record(day)
    assert saved["observed"]["returns"]["XLK"] is None
    assert f"observed.returns.XLK: no observation from session {TUE}" in saved["gaps"]


def test_a_settled_session_bar_is_the_return_after_the_bar_is_published(day):
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    assert day.run(f"{TUE}T23:30:00+00:00", "CLOSE_1M", intraday=False, last_history_date=TUE) == 0
    saved = record(day)
    assert saved["source"]["closing_data"] == "COMPLETED_SESSION"
    returns = {s: r for s, r in saved["observed"]["returns"].items() if r}
    assert returns and {r["basis"] for r in returns.values()} == {"daily_bar"}
    assert all(r["observed_at"] == TUE and r["id"].endswith("-daily") for r in returns.values())


def test_frozen_facts_come_from_what_the_analyst_saw_never_the_close_catalog(day):
    # The close's own daily rows differ from those the analyst saw (a restated last bar).
    assert full_day(day, scale_last_close={"SPY": 1.02, "QQQ": 1.02, "GLD": 1.02, "GDX": 1.02}) == 0
    saved = record(day)
    interpretation = day.bundle()["interpretation"]
    evidence = json.loads((day.folder("CLOSE_1M") / "evidence.json").read_text())
    close_rows = {r["id"]: r for r in evidence["observations"] + evidence["derived"]}
    facts = memory.retained_facts(saved["retained"])
    assert facts, "the day's watches cite rows"
    differs = 0
    for _, ident, fact in facts:
        frozen = interpretation["evidence"].get(ident)
        if frozen is not None:
            assert fact["value"] == frozen["value"]
        if ident in close_rows and close_rows[ident]["value"] != fact["value"]:
            differs += 1
    assert differs, "a close print moved, and the retained fact kept the analyst's value"


def test_a_page_less_close_still_records_the_observed_session_with_a_named_read_gap(day):
    assert day.run(CLOSE, "CLOSE_1M", print_at=PRINT_AT) == 0
    saved = record(day)
    assert verify(saved) and saved["retained"]["read"] is None
    assert "retained.read: no accepted interpretation for this session" in saved["gaps"]
    assert saved["observed"]["returns"]["SPY"]["basis"] == "last_trade"


def test_a_failing_optional_part_becomes_a_gap_and_the_close_still_passes(day, monkeypatch):
    def broken(*args):
        raise KeyError("assessments")
    monkeypatch.setattr(memory, "_watches", broken)
    assert full_day(day) == 0
    saved = record(day)
    assert day.metadata("CLOSE_1M")["validation"] == "PASS"
    assert "retained.watches: not recorded (KeyError)" in saved["gaps"]
    assert saved["retained"]["watches"] == [] and saved["observed"]["returns"]["SPY"]


def test_a_projection_defect_fails_the_close_closed(day, monkeypatch):
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    before = bundle_path(day.root).read_text()

    def broken(*args):
        raise TypeError("defect")
    monkeypatch.setattr(memory, "_observed", broken)
    assert day.run(CLOSE, "CLOSE_1M", print_at=PRINT_AT) == 2
    meta = day.metadata("CLOSE_1M")
    assert meta["validation"] == "FAILED" and "session memory projection failed (TypeError" in meta["error"]
    assert meta["continuity"]["advanced"] is False and bundle_path(day.root).read_text() == before
    assert not (day.folder("CLOSE_1M") / MEMORY_FILE).exists()


def test_a_close_with_no_session_observation_writes_nothing(day):
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    assert day.run(CLOSE, "CLOSE_1M", intraday=False) == 2
    assert not (day.folder("CLOSE_1M") / MEMORY_FILE).exists()


@pytest.mark.parametrize("flags", [("--experiment",), ("--commissioning",)])
def test_experiment_and_commissioning_closes_never_write(day, flags):
    assert day.run(CLOSE, "CLOSE_1M", print_at=PRINT_AT, flags=flags) in (0, 2)
    folders = list((day.root / "runs").rglob(MEMORY_FILE))
    assert folders == []


def test_a_replayed_close_never_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(cli, "update_latest", lambda root, page: None)
    cli.main(["premarket", "--replay", "--checkpoint", "CLOSE_1M"])
    assert list(tmp_path.rglob(MEMORY_FILE)) == []


def test_refreshes_and_syntheses_never_write(day):
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    assert day.run(f"{TUE}T13:31:00+00:00", "OPEN_1M") == 0
    assert list((day.root / "runs").rglob(MEMORY_FILE)) == []


def test_the_projection_is_deterministic_and_bounded(day):
    assert full_day(day) == 0
    folder = day.folder("CLOSE_1M")
    packet = json.loads((folder / "evidence.json").read_text())
    handoff = json.loads((folder / "session_handoff.json").read_text())
    saved = record(day)
    run = dict(run_id=saved["source"]["run_id"], target_time=saved["source"]["target_time"],
               code_revision=saved["source"]["code_revision"], evidence_hash=saved["source"]["evidence_hash"])
    universe = json.loads((cli.ROOT / "config/universe.json").read_text())
    # The bundle the close admitted from: before this close advanced it, its premarket and interpretation slots are
    # the same session's, which is all the projection reads.
    bundle = day.bundle()
    again = session_memory(packet, handoff, bundle, universe, run)
    assert again == saved and again["content_hash"] == digest({k: v for k, v in saved.items() if k != "content_hash"})
    # Size: each kept row carries its full lineage; a real W47 close projects to about 30 KB compact.
    assert len(json.dumps(saved)) < 40_000


def test_verdicts_count_only_same_session_analyst_assessments():
    runs = {"live-premarket-1": "PREMARKET", "live-open_30m-1": "OPEN_30M"}
    watch = dict(lifecycle="retired", assessments=[
        dict(status="new", run_id="live-premarket-1", origin="analyst"),
        dict(status="strengthened", run_id="live-premarket-0", origin="analyst"),  # yesterday's
        dict(status="weakened", run_id="live-open_30m-1", origin="analyst"),
        dict(status="unresolved", run_id="live-open_30m-1", origin="deterministic",
             reason="dropped: at most three watches are carried")])
    assert memory._verdicts(watch, runs) == [dict(status="weakened", by="OPEN_30M")]
    assert memory._end(watch) == "dropped"
    assert memory._end(dict(watch, assessments=[dict(status="reversed")])) == "reversed"
    assert memory._end(dict(lifecycle="expired")) == "expired" and memory._end(dict(lifecycle="active")) == "open"


def test_a_tampered_record_does_not_verify(day):
    assert full_day(day) == 0
    saved = record(day)
    saved["observed"]["returns"]["SPY"]["value"] = 9.9
    assert not verify(saved)
    assert not verify(dict(saved, schema_version="other"))


# --- review regressions (Codex Astra, A1 round 1) ---------------------------------------------------

def _inputs(day):
    folder = day.folder("CLOSE_1M")
    packet = json.loads((folder / "evidence.json").read_text())
    handoff = json.loads((folder / "session_handoff.json").read_text())
    saved = record(day)
    run = dict(run_id=saved["source"]["run_id"], target_time=saved["source"]["target_time"],
               code_revision=saved["source"]["code_revision"], evidence_hash=saved["source"]["evidence_hash"])
    universe = json.loads((cli.ROOT / "config/universe.json").read_text())
    return packet, handoff, day.bundle(), universe, run


def test_retained_facts_keep_their_original_lineage_and_name_who_froze_them(day):
    assert full_day(day) == 0
    saved = record(day)
    runs = {day.bundle()["premarket"]["origin"]["run_id"], day.bundle()["interpretation"]["origin"]["run_id"]}
    assert memory.retained_facts(saved["retained"])
    for _, ident, fact in memory.retained_facts(saved["retained"]):
        assert fact["source_id"] and fact["retrieved_at"] and fact["frozen_by"]["run_id"] in runs, ident
        if fact.get("frequency") == "daily":
            assert fact["formula_version"] and fact["input_ids"], ident
    assert not [g for g in saved["gaps"] if "without its source or retrieval clock" in g]


def test_a_watch_value_filled_at_the_close_never_stands_for_what_the_analyst_saw(day):
    assert full_day(day, scale_last_close={"QQQ": 1.05}) == 0
    packet, handoff, bundle, universe, run = _inputs(day)
    close_value = next(r["value"] for r in packet["derived"] if r["id"] == "QQQ-daily")
    for watch in handoff["assessment"]["watches"]:
        # As `carried_state` fills a legacy watch's values from the close's own catalog.
        watch["values"] = {ref: dict(id=ref, value=close_value, metric="daily return", source_id="alpaca-daily",
                                     retrieved_at=run["target_time"]) for ref in watch["evidence_refs"]}
    saved = session_memory(packet, handoff, bundle, universe, run)
    facts = [fact for _, ident, fact in memory.retained_facts(saved["retained"]) if ident == "QQQ-daily"]
    assert facts and all(fact["value"] != close_value for fact in facts)


def _rehashed(record_):
    from market_brief.continuity import _hashed
    return _hashed({k: v for k, v in record_.items() if k != "content_hash"})


def test_an_interpretation_the_close_would_not_admit_is_never_quoted(day):
    assert full_day(day) == 0
    packet, handoff, bundle, universe, run = _inputs(day)
    late = json.loads(json.dumps(bundle["interpretation"]))
    late["origin"]["target_time"] = f"{TUE}T21:00:00+00:00"  # after this close's evidence cutoff
    saved = session_memory(packet, handoff, dict(bundle, interpretation=_rehashed(late)), universe, run)
    assert saved["retained"]["read"] is None
    assert "retained.read: no accepted interpretation for this session" in saved["gaps"]


def test_a_malformed_premarket_record_is_a_gap_not_a_failed_close(day):
    assert full_day(day) == 0
    packet, handoff, bundle, universe, run = _inputs(day)
    broken = json.loads(json.dumps(bundle["premarket"]))
    del broken["origin"]["target_time"], broken["origin"]["run_id"]
    saved = session_memory(packet, handoff, dict(bundle, premarket=_rehashed(broken)), universe, run)
    assert verify(saved) and saved["observed"]["returns"]["SPY"]
    assert "retained.premarket: no accepted premarket edition for this session" in saved["gaps"]


def test_a_failing_retained_setup_still_leaves_the_observed_record(day, monkeypatch):
    def broken(*args):
        raise KeyError("origin")
    monkeypatch.setattr(memory, "_admitted", broken)
    assert full_day(day) == 0
    saved = record(day)
    assert "retained: not recorded (KeyError)" in saved["gaps"] and saved["retained"]["watches"] == []
    assert day.metadata("CLOSE_1M")["validation"] == "PASS"


def test_watches_retired_by_the_later_synthesis_are_kept_with_how_they_ended(day):
    assert full_day(day) == 0
    saved = record(day)
    premarket_watches = {w["id"] for w in day.bundle()["premarket"]["assessment"]["watches"]}
    kept = {w["id"]: w for w in saved["retained"]["watches"]}
    assert premarket_watches <= set(kept), "every watch the premarket wrote is in the session record"
    assert any(w["end"] in ("dropped", "reversed") for w in kept.values())


def test_relationship_statuses_are_this_sessions_own_analyst_assessments():
    runs = {"live-premarket-1": "PREMARKET"}
    handoff = dict(assessment=dict(relationships=[dict(
        id="rel-live-premarket-0-1", origin_run_id="live-premarket-0", statement="s", evidence_refs=["SPY-daily"],
        assessments=[dict(status="new", run_id="live-premarket-0", origin="analyst"),
                     dict(status="strengthened", run_id="live-premarket-0", origin="analyst"),
                     dict(status="weakened", run_id="live-premarket-1", origin="analyst")])]))
    (relationship,) = memory._relationships(handoff, runs)
    assert relationship["statuses"] == [dict(status="weakened", by="PREMARKET")] and relationship["carried"] is True


def test_a_close_rejected_after_the_record_was_written_leaves_no_record(day):
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    pointer = day.root / "runs" / "latest-success.json"
    pointer.unlink()
    pointer.symlink_to(day.root / "elsewhere.json")
    assert day.run(CLOSE, "CLOSE_1M", print_at=PRINT_AT) == 2
    meta = day.metadata("CLOSE_1M")
    assert meta["validation"] == "FAILED" and "memory" not in meta
    assert not (day.folder("CLOSE_1M") / MEMORY_FILE).exists()


# --- review regressions (Codex Astra, A1 round 2) ---------------------------------------------------

PM, OPEN = "live-premarket-130000-aaaa", "live-open_30m-140100-bbbb"
RUNS = {PM: "PREMARKET", OPEN: "OPEN_30M"}


def _row(value, run_hint, **extra):
    return dict(dict(id="QQQ-daily", topic="QQQ", metric="daily return", value=value, unit="%",
                     observed_at="2026-09-04", source_id="alpaca-daily", retrieved_at=f"2026-09-08T{run_hint}Z",
                     formula_version="v0", input_ids=["history-QQQ"]), **extra)


def _session(premarket_watches=(), premarket_retired=(), prior=(), updates=(), evidence=None, snapshots=None):
    premarket = dict(origin=dict(run_id=PM, checkpoint="PREMARKET"),
                     assessment=dict(watches=list(premarket_watches), retired=list(premarket_retired)),
                     observed=dict(snapshots=snapshots or {}))
    interpretation = dict(origin=dict(run_id=OPEN, checkpoint="OPEN_30M"), evidence=evidence or {},
                          prior_state=dict(watches=list(prior)), narrative=dict(watch_updates=list(updates)))
    return premarket, interpretation


def _watch(ident, origin, values=None, lifecycle="active", assessments=()):
    return dict(id=ident, checkpoint=RUNS.get(origin), origin_run_id=origin, hypothesis="h", confirmation="c",
                contradiction="x", horizon=dict(declared="SESSION"), evidence_refs=["QQQ-daily"],
                values=values or {}, lifecycle=lifecycle, assessments=list(assessments))


def test_two_watches_citing_one_id_keep_the_observation_each_author_saw():
    early = _watch(f"watch-{PM}-1", PM, values={"QQQ-daily": _row(0.148368, "13:00:00")})
    late = _watch(f"watch-{OPEN}-1", OPEN, values={"QQQ-daily": _row(2.151335, "14:01:00")})
    premarket, interpretation = _session(premarket_watches=[early],
                                         evidence={"QQQ-daily": _row(2.151335, "14:01:00")})
    handoff = dict(assessment=dict(watches=[late, early], relationships=[]))
    gaps = []
    watches = {w["id"]: w for w in memory._watches(handoff, premarket, interpretation, RUNS, gaps)}
    assert watches[early["id"]]["facts"]["QQQ-daily"]["value"] == 0.148368
    assert watches[early["id"]]["facts"]["QQQ-daily"]["frozen_by"]["run_id"] == PM
    assert watches[late["id"]]["facts"]["QQQ-daily"]["value"] == 2.151335
    assert watches[late["id"]]["facts"]["QQQ-daily"]["frozen_by"]["run_id"] == OPEN
    assert not gaps


def test_a_frozen_row_missing_lineage_takes_it_only_from_the_same_observation():
    bare = {k: v for k, v in _row(0.148368, "13:00:00").items() if k not in ("retrieved_at", "formula_version",
                                                                           "input_ids")}
    early = _watch(f"watch-{PM}-1", PM, values={"QQQ-daily": bare})
    premarket, interpretation = _session(premarket_watches=[early],
                                         snapshots={"QQQ-daily": _row(0.148368, "13:00:00")})
    gaps = []
    (watch,) = memory._watches(dict(assessment=dict(watches=[early], relationships=[])), premarket, interpretation,
                               RUNS, gaps)
    fact = watch["facts"]["QQQ-daily"]
    assert fact["retrieved_at"] == "2026-09-08T13:00:00Z" and fact["input_ids"] == ["history-QQQ"] and not gaps
    # A different observation never lends its lineage.
    premarket, interpretation = _session(premarket_watches=[early], snapshots={"QQQ-daily": _row(9.9, "13:00:00")})
    gaps = []
    (watch,) = memory._watches(dict(assessment=dict(watches=[early], relationships=[])), premarket, interpretation,
                               RUNS, gaps)
    assert "retrieved_at" not in watch["facts"]["QQQ-daily"]
    assert any("without its source or retrieval clock" in g for g in gaps)


def test_a_watch_that_expired_before_the_later_synthesis_ends_expired_not_dropped():
    gone = _watch(f"watch-{PM}-2", PM, values={"QQQ-daily": _row(0.1, "13:00:00")})
    premarket, interpretation = _session(premarket_watches=[gone], prior=[dict(gone, lifecycle="expired")])
    (watch,) = memory._watches(dict(assessment=dict(watches=[], relationships=[])), premarket, interpretation,
                               RUNS, [])
    assert watch["end"] == "expired" and watch["from"] == "PREMARKET"


def test_a_watch_the_later_synthesis_retired_keeps_its_earlier_same_session_verdicts():
    carried = _watch("watch-live-open_30m-0-1", "live-open_30m-0", values={"QQQ-daily": _row(0.1, "13:00:00")},
                     assessments=[dict(status="new", run_id="live-open_30m-0", origin="analyst"),
                                  dict(status="strengthened", run_id=PM, origin="analyst")])
    premarket, interpretation = _session(premarket_watches=[carried], prior=[carried],
                                         updates=[dict(carried_id=carried["id"], assessment="reversed")])
    (watch,) = memory._watches(dict(assessment=dict(watches=[], relationships=[])), premarket, interpretation,
                               RUNS, [])
    assert watch["verdicts"] == [dict(status="strengthened", by="PREMARKET"), dict(status="reversed", by="OPEN_30M")]
    assert watch["end"] == "reversed"


def test_a_filesystem_failure_after_the_record_is_a_truthful_rejection_with_no_record(day, monkeypatch):
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    before = bundle_path(day.root).read_text()

    def failing(path, bundle):
        raise OSError("disk full")
    monkeypatch.setattr(cli, "write_bundle", failing)
    assert day.run(CLOSE, "CLOSE_1M", print_at=PRINT_AT) == 2
    meta = day.metadata("CLOSE_1M")
    assert meta["validation"] == "FAILED" and meta["continuity"]["advanced"] is False and "memory" not in meta
    assert not (day.folder("CLOSE_1M") / MEMORY_FILE).exists() and bundle_path(day.root).read_text() == before


# --- review regressions (Codex Astra, A1 round 3) ---------------------------------------------------

def test_a_failed_final_metadata_write_leaves_no_record(day, monkeypatch):
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    original = cli.write_json

    def failing(path, value):
        if path.name == "metadata.json" and value.get("checkpoint") == "CLOSE_1M":
            raise OSError("disk full")
        return original(path, value)
    monkeypatch.setattr(cli, "write_json", failing)
    assert day.run(CLOSE, "CLOSE_1M", print_at=PRINT_AT) == 2
    assert not (day.folder("CLOSE_1M") / MEMORY_FILE).exists()


def test_a_carried_relationship_never_takes_todays_snapshots_as_its_facts():
    premarket, interpretation = _session(snapshots={"QQQ-daily": _row(5.155786, "13:00:00")})
    carried = dict(id="rel-live-premarket-0-1", origin_run_id="live-premarket-0", statement="s",
                   evidence_refs=["QQQ-daily"],
                   assessments=[dict(status="new", run_id="live-premarket-0", origin="analyst",
                                     current_refs=["QQQ-daily"])])
    gaps = []
    (rel,) = memory._relationships(dict(assessment=dict(relationships=[carried])), RUNS, interpretation, premarket,
                                   gaps)
    assert rel["facts"] == {} and rel["statuses"] == []
    assert any("carried from an earlier session" in g for g in gaps)
    assessed = dict(carried, assessments=[*carried["assessments"], dict(
        status="strengthened", run_id=PM, origin="analyst", current_refs=["QQQ-daily"])])
    (rel,) = memory._relationships(dict(assessment=dict(relationships=[assessed])), RUNS, interpretation, premarket,
                                   [])
    assert rel["facts"]["QQQ-daily"]["value"] == 5.155786 and rel["facts"]["QQQ-daily"]["frozen_by"]["run_id"] == PM


def test_without_a_premarket_a_watch_keeps_its_own_frozen_values():
    carried = _watch("watch-live-open_30m-0-1", "live-open_30m-0", values={"QQQ-daily": _row(0.1, "13:00:00")})
    _, interpretation = _session(prior=[carried], evidence={"QQQ-daily": _row(9.9, "14:01:00")})
    gaps = []
    (watch,) = memory._watches(dict(assessment=dict(watches=[carried], relationships=[])), None, interpretation,
                               {OPEN: "OPEN_30M"}, gaps)
    # Never today's evidence: without a same-session assessment citing it, the value is a named gap.
    assert watch["facts"] == {} and any("carried from an earlier session" in g for g in gaps)


def test_lineage_is_enriched_only_from_the_run_that_froze_the_value():
    bare = {k: v for k, v in _row(0.148368, "13:00:00").items() if k not in ("retrieved_at", "formula_version",
                                                                           "input_ids")}
    early = _watch(f"watch-{PM}-1", PM, values={"QQQ-daily": bare})
    later = _row(0.148368, "14:01:00", input_ids=["history-QQQ", "later"])
    premarket, interpretation = _session(premarket_watches=[early], evidence={"QQQ-daily": later},
                                         snapshots={"QQQ-daily": _row(0.148368, "13:00:00")})
    (watch,) = memory._watches(dict(assessment=dict(watches=[early], relationships=[])), premarket, interpretation,
                               RUNS, [])
    fact = watch["facts"]["QQQ-daily"]
    assert fact["retrieved_at"] == "2026-09-08T13:00:00Z" and fact["input_ids"] == ["history-QQQ"]


# --- review regressions (independent review, A1 round 4) --------------------------------------------

def test_a_watch_carried_from_an_earlier_session_never_shows_todays_values_as_its_facts():
    carried = _watch("watch-live-premarket-0-1", "live-premarket-0")
    premarket, interpretation = _session(premarket_watches=[carried],
                                         snapshots={"QQQ-daily": _row(7.7, "13:00:00")})
    gaps = []
    (watch,) = memory._watches(dict(assessment=dict(watches=[carried], relationships=[])), premarket,
                               interpretation, RUNS, gaps)
    assert watch["facts"] == {} and any("carried from an earlier session" in g for g in gaps)
    valued = _watch("watch-live-premarket-0-1", "live-premarket-0", values={"QQQ-daily": _row(0.1, "13:00:00")})
    premarket, interpretation = _session(premarket_watches=[valued], snapshots={"QQQ-daily": _row(7.7, "13:00:00")})
    gaps = []
    (watch,) = memory._watches(dict(assessment=dict(watches=[valued], relationships=[])), premarket,
                               interpretation, RUNS, gaps)
    # Its values may have been filled by any later carry, so none are credited; they are in that session's record.
    assert watch["facts"] == {} and any("carried from an earlier session" in g for g in gaps)
    # A verdict the later synthesis gave it, held only on the close's copy, keeps the row it cited.
    late = _watch("watch-live-premarket-0-1", "live-premarket-0", assessments=[
        dict(status="strengthened", run_id=OPEN, origin="analyst", current_refs=["QQQ-daily"])])
    premarket, interpretation = _session(premarket_watches=[_watch("watch-live-premarket-0-1", "live-premarket-0")],
                                         evidence={"QQQ-daily": _row(3.3, "14:01:00")})
    (watch,) = memory._watches(dict(assessment=dict(watches=[late], relationships=[])), premarket, interpretation,
                               RUNS, [])
    assert watch["facts"]["QQQ-daily"]["value"] == 3.3 and watch["facts"]["QQQ-daily"]["frozen_by"]["run_id"] == OPEN
    judged = _watch("watch-live-premarket-0-1", "live-premarket-0", assessments=[
        dict(status="strengthened", run_id=PM, origin="analyst", current_refs=["QQQ-daily"])])
    premarket, interpretation = _session(premarket_watches=[judged], snapshots={"QQQ-daily": _row(7.7, "13:00:00")})
    (watch,) = memory._watches(dict(assessment=dict(watches=[judged], relationships=[])), premarket,
                               interpretation, RUNS, [])
    assert watch["facts"]["QQQ-daily"]["value"] == 7.7 and watch["facts"]["QQQ-daily"]["frozen_by"]["run_id"] == PM


def test_any_interruption_after_the_record_leaves_no_record(day, monkeypatch):
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0

    def broken(*args):
        raise KeyError("slot")
    monkeypatch.setattr(cli, "advance_bundle", broken)
    assert day.run(CLOSE, "CLOSE_1M", print_at=PRINT_AT) == 2  # main() reports the KeyError
    meta = day.metadata("CLOSE_1M")
    assert "memory" not in meta and meta["continuity"]["advanced"] is False
    assert not (day.folder("CLOSE_1M") / MEMORY_FILE).exists()

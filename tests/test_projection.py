"""Full record stays local; the model receives only what it needs to edit."""

import copy
import json

import pytest
from test_history_admission import packet_at, utc
from test_pipeline import fixture_packet, freeze_clock, narrative

from market_brief import cli
from market_brief.context import CONTEXT_SCHEMA, analyst_context, supplied_ids
from market_brief.evidence import ROOT, digest, evidence_catalog, model_packet, read_json
from market_brief.synthesize import (
    construct_prompt,
    synthesis_packet,
    synthesize_openrouter,
    validate_narrative,
)


def payload(packet):
    _, user = construct_prompt(packet)
    return json.loads(user)


def test_default_payload_is_the_compact_projection():
    data = payload(fixture_packet())
    assert "evidence" not in data and "lookback" not in data
    assert {"catalog", "baselines", "output_schema", "attention", "coverage"} <= set(data)


def test_full_payload_remains_available_for_diagnostics():
    packet = fixture_packet()
    data = json.loads(construct_prompt(packet, full=True)[1])
    assert set(data) == {"evidence", "catalog", "output_schema"}
    assert data["evidence"] == json.loads(json.dumps(model_packet(packet)))
    assert set(data["catalog"]) == set(evidence_catalog(model_packet(packet)))


def test_compact_payload_keeps_the_output_schema_in_context():
    data = payload(fixture_packet())
    banner = data["output_schema"]["properties"]["banner"]
    assert banner["required"] == ["title", "label", "class", "evidence_ids", "limitation"]


def test_full_normalized_packet_is_untouched_by_projection():
    packet = fixture_packet()
    before = copy.deepcopy(packet)
    construct_prompt(packet)
    construct_prompt(packet, full=True)
    synthesis_packet(packet)
    assert packet == before
    assert model_packet(packet) == model_packet(before)


def test_projection_is_deterministic_and_smaller():
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"))
    assert synthesis_packet(packet) == synthesis_packet(packet)
    _, user = construct_prompt(packet)
    assert len(user.encode()) < len(construct_prompt(packet, full=True)[1].encode()) * 0.5


def test_each_evidence_row_is_sent_once_without_schema_or_renderer_fields():
    packet = fixture_packet()
    _, user = construct_prompt(packet)
    data = payload(packet)
    assert "lookback" not in data and "evidence" not in data
    assert user.count('"SPY-daily"') == 1
    assert user.count('"treasury-2y"') == 1
    assert "sample-prices" not in user or user.count("sample-prices") <= 2  # source listed, not per row
    for row in data["catalog"]:
        for item in row["rows"]:
            assert set(item) <= {"id", "metric", "value", "unit", "magnitude", "status", "observed_at"}


def test_every_permitted_evidence_id_remains_available():
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"))
    user = construct_prompt(packet)[1]
    permitted = set(evidence_catalog(model_packet(packet)))
    assert permitted
    for ident in permitted:
        assert f'"{ident}"' in user
    data = payload(packet)
    sent = {item["id"] for group in data["catalog"] for item in group["rows"]}
    sent |= {e["id"] for e in data.get("events", [])} | {c["id"] for c in data.get("context_items", [])}
    assert sent == permitted
    topics = {group["topic"] for group in data["catalog"]}
    assert {"SPY", "QQQ", "XLI", "NVDA", "GLD", "GDX", "US 2Y", "US 10Y"} <= topics
    assert data["sector_leadership"]["top"] and data["attention"]
    assert "Basis" in data["coverage"]["basis"]
    assert data["baselines"]["daily return"].startswith("prior regular close")


def test_repeated_history_errors_are_aggregated():
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"), last_history_date="2026-09-03")
    assert len(packet["history_errors"]) == 6
    data = payload(packet)
    summary = data["history_errors"]
    assert summary["affected_count"] == 6
    assert set(summary["affected_symbols"]) == {"SPY", "QQQ", "NVDA", "GLD", "GDX", "XLI"}
    assert summary["expected_session"] == "2026-09-08"
    assert summary["latest_provider_session"] == "2026-09-03"
    assert "last completed exchange session" in summary["reasons"][0]
    assert "invalid/incomplete historical context" not in construct_prompt(packet)[1]


def test_history_lag_and_unavailable_sources_are_kept_compactly():
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"))
    data = payload(packet)
    assert data["history_lag"] == {"completed_session": "2026-09-08", "history_through": "2026-09-04"}
    for source in data["sources"]:
        assert set(source) <= {"id", "name", "kind", "status", "reason", "feed", "data_delay", "coverage_date"}
    assert packet["run"]["session"]["previous_session"] == data["run"]["session"]["previous_session"]


def test_validator_still_uses_the_full_model_packet():
    packet = fixture_packet()
    assert validate_narrative(narrative(), packet)
    bad = narrative()
    bad["summary"][0]["evidence_ids"] = ["invented"]
    try:
        validate_narrative(bad, packet)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown evidence must still be rejected")


def test_successful_usage_and_cost_are_recorded_when_present(capsys):
    def requester(payload_, api_key):
        return {"id": "r1", "model": "anthropic/claude-fable-5.1", "provider": "Azure",
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(narrative())}}],
                "usage": {"prompt_tokens": 8435, "completion_tokens": 2794, "total_tokens": 11229, "cost": 0.22405}}
    _, meta = synthesize_openrouter(fixture_packet(), api_key="k", requester=requester)
    assert meta["usage"] == {"prompt_tokens": 8435, "completion_tokens": 2794, "total_tokens": 11229, "cost": 0.22405}
    assert meta["finish_reason"] == "stop" and meta["provider_route"] == "Azure"
    assert meta["resolved_model"] == "anthropic/claude-fable-5.1"
    out = capsys.readouterr().out
    assert "Synthesis usage" in out and "8435" in out and "0.22405" in out
    assert "k" not in out.split("Synthesis usage", 1)[1].split()[:1]


def test_absent_usage_metadata_does_not_fail_synthesis():
    def requester(payload_, api_key):
        return {"choices": [{"message": {"content": json.dumps(narrative())}}]}
    output, meta = synthesize_openrouter(fixture_packet(), api_key="k", requester=requester)
    assert output["mode"] == "SAMPLE"
    assert meta["usage"] == {} and meta["finish_reason"] == "unknown"


def test_experiment_run_never_publishes_or_records_success(tmp_path, monkeypatch):
    freeze_clock(monkeypatch, "2026-09-08T12:45:00+00:00")
    raw = read_json(ROOT / "tests/fixtures/evidence.sample.json")
    raw["mode"] = "LIVE"
    monkeypatch.setattr(cli, "collect_live", lambda target, include_cuttingboard=False: raw)
    value = narrative()
    value["mode"] = "LIVE"
    monkeypatch.setattr(cli, "synthesize", lambda packet, **kwargs: (value, {"route": "test"}))
    original = cli.output_directory
    monkeypatch.setattr(cli, "output_directory", lambda root, *rest: original(tmp_path, *rest))
    monkeypatch.setattr(cli, "update_latest", lambda root, page: None)
    published = []
    monkeypatch.setattr(cli, "publish_latest", lambda root: published.append(root))
    written = []
    original_write = cli.write_json
    monkeypatch.setattr(cli, "write_json", lambda path, value: written.append(str(path)) or original_write(path, value))
    assert cli.main(["premarket", "--commissioning", "--experiment"]) == 0
    assert published == []
    assert not any(path.endswith("latest-success.json") or "latest-" in path.rsplit("/", 1)[-1] for path in written)
    folder = next((tmp_path / "runs").glob("*/*"))
    assert (folder / "brief.html").exists()
    assert json.loads((folder / "metadata.json").read_text())["experiment"] is True


# The saved analyst boundary: one versioned, hashed context per run, and references checked against it.
def test_context_carries_schema_version_evidence_hash_and_run_identity():
    packet = fixture_packet()
    packet["run"]["run_id"] = "sample-premarket-124500-fixture"
    context = analyst_context(packet)
    assert context["schema_version"] == CONTEXT_SCHEMA
    assert context["evidence_hash"] == digest(packet)
    assert context["run"]["run_id"] == "sample-premarket-124500-fixture"
    assert context["run"]["mode"] == "SAMPLE" and context["run"]["checkpoint"] == "PREMARKET"
    assert analyst_context(packet) == context
    assert digest(analyst_context(packet)) == digest(context)


def test_saved_context_is_the_exact_payload_sent():
    packet = fixture_packet()
    context = analyst_context(packet)
    _, user = construct_prompt(packet, context=context)
    data = json.loads(user)
    del data["output_schema"]
    assert data == json.loads(json.dumps(context))


def test_denied_sources_are_absent_from_the_saved_context():
    packet = fixture_packet()
    next(s for s in packet["sources"] if s["id"] == "sample-rates")["llm_allowed"] = False
    context = analyst_context(packet)
    assert "treasury-2y" not in supplied_ids(context)
    assert "sample-rates" not in json.dumps(context)
    assert "SPY-daily" in supplied_ids(context)


def test_references_must_have_been_supplied_in_the_context():
    packet = fixture_packet()
    context = analyst_context(packet)
    assert validate_narrative(narrative(), packet, context)
    trimmed = json.loads(json.dumps(context))
    trimmed["catalog"] = [group for group in trimmed["catalog"] if group["topic"] != "QQQ"]
    with pytest.raises(ValueError, match="unsupplied"):
        validate_narrative(narrative(), packet, trimmed)


def test_context_for_a_different_evidence_record_is_rejected():
    packet = fixture_packet()
    context = analyst_context(packet)
    packet["coverage"]["limitations"].append("evidence changed after the context was saved")
    with pytest.raises(ValueError, match="does not match"):
        validate_narrative(narrative(), packet, context)


def test_replay_persists_context_that_hashes_to_the_saved_evidence(tmp_path, monkeypatch):
    original = cli.output_directory
    monkeypatch.setattr(cli, "output_directory", lambda root, *rest: original(tmp_path, *rest))
    monkeypatch.setattr(cli, "update_latest", lambda root, page: None)
    assert cli.main(["premarket", "--replay"]) == 0
    folder = next((tmp_path / "runs").glob("*/*"))
    evidence = json.loads((folder / "evidence.json").read_text())
    context = json.loads((folder / "analyst_context.json").read_text())
    metadata = json.loads((folder / "metadata.json").read_text())
    assert evidence["run"]["run_id"] == folder.name == metadata["run_id"]
    assert context["evidence_hash"] == digest(evidence) == metadata["evidence_hash"]
    assert metadata["context_hash"] == digest(context)
    assert metadata["context_schema"] == CONTEXT_SCHEMA
    # Replaying the saved evidence rebuilds the identical current-evidence projection; the
    # continuity portion is reproduced from the saved evidence's deterministic comparisons.
    rebuilt = analyst_context(evidence)
    assert {k: v for k, v in context.items() if k not in ("prior_state", "comparisons")} == rebuilt
    assert [c["id"] for c in context["comparisons"]] == [c["id"] for c in evidence["continuity"]["comparisons"]]
    assert set(supplied_ids(context)) == set(evidence_catalog(model_packet(evidence)))

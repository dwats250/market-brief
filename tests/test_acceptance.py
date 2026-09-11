"""Acceptance separates product correctness (fatal) from editorial shaping (backstop with headroom).

Run 34548270198 generated a grounded CLOSE_1M brief on Azure (finish=stop, 2,931 completion tokens)
and was discarded at `sections.macro.0.text` because one paragraph ran past its 240-character
editorial target. The same class recurs locally (281 characters at `sections.equities.0.text`), and
the same replay wrote "Nasdaq-100", which the digit rule read as a numeric claim.
"""

import json

import pytest
from test_pipeline import fixture_packet, narrative

from market_brief import cli
from market_brief import synthesize as synthesize_module
from market_brief.context import analyst_context, edition_profile
from market_brief.synthesize import (
    EDITORIAL_HEADROOM,
    acceptance_schema,
    editorial_notes,
    narrative_schema,
    synthesize_openrouter,
    validate_narrative,
)

PROSE = ("Index-level closes crossing below their trailing averages point to broadening technical "
         "weakness, yet the move is uneven: financials and health care joined the break while "
         "communication services and energy still sit atop the leaderboard, and one mega-cap bucked it. ")


def prose(length):
    return (PROSE * (length // len(PROSE) + 1))[:length].rstrip() + "."


def set_path(value, path, text):
    target = value
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = text


@pytest.mark.parametrize("path", [
    ("sections", "macro", 0, "text"), ("sections", "equities", 0, "text"), ("summary", 0, "text"),
    ("watches", 0, "condition"), ("character", "text"), ("relationships", 0, "statement"),
    ("banner", "limitation"), ("attention", 0, "why"),
])
def test_prose_modestly_over_its_editorial_target_is_accepted_and_noted(path):
    packet = fixture_packet()
    schema = narrative_schema(edition_profile("PREMARKET"))
    node = schema
    for key in path:
        node = node["properties"][key] if isinstance(key, str) else node["items"]
    target = node["maxLength"]
    value = narrative()
    set_path(value, path, prose(target + 41))
    assert validate_narrative(value, packet)
    notes = editorial_notes(value, schema)
    assert notes == [dict(path=".".join(map(str, path)), keyword="maxLength", limit=target,
                          actual=len(prose(target + 41)))]


@pytest.mark.parametrize("path", [
    ("sections", "macro", 0, "text"), ("summary", 0, "text"), ("watches", 0, "condition"),
    ("character", "text"), ("banner", "title"),
])
def test_prose_beyond_the_backstop_is_still_fatal(path):
    schema = narrative_schema(edition_profile("PREMARKET"))
    node = schema
    for key in path:
        node = node["properties"][key] if isinstance(key, str) else node["items"]
    value = narrative()
    set_path(value, path, prose(node["maxLength"] * EDITORIAL_HEADROOM + 1))
    with pytest.raises(ValueError, match="malformed narrative at " + ".".join(map(str, path)).replace(".", r"\.")):
        validate_narrative(value, fixture_packet())


def test_headroom_applies_to_counts_but_never_to_cuttingboard_or_structure():
    local = narrative_schema(edition_profile("PREMARKET"))
    hard = acceptance_schema(local)
    assert hard["properties"]["summary"]["maxItems"] == 2 * EDITORIAL_HEADROOM
    assert hard["properties"]["watches"]["maxItems"] == 3 * EDITORIAL_HEADROOM
    assert hard["properties"]["sections"]["properties"]["cuttingboard"]["maxItems"] == 0
    refs = hard["properties"]["summary"]["items"]["properties"]["evidence_ids"]
    assert refs["minItems"] == 1 and refs["uniqueItems"] is True and refs["maxItems"] == 4 * EDITORIAL_HEADROOM
    assert hard["properties"]["summary"]["minItems"] == 1
    assert hard["properties"]["summary"]["items"]["properties"]["text"]["minLength"] == 1
    assert hard["properties"]["banner"]["properties"]["class"] == {"const": "INTERPRETATION"}
    # The editorial contract itself is untouched: the model still reads the targets.
    assert local["properties"]["summary"]["maxItems"] == 2
    assert local["properties"]["sections"]["properties"]["macro"]["items"]["properties"]["text"]["maxLength"] == 240


def test_light_edition_counts_get_the_same_bounded_headroom():
    from test_contract import edition_response
    from test_history_admission import packet_at, utc
    packet = packet_at(utc("2026-09-08T19:10:00+00:00"), checkpoint="AFTERNOON")
    profile = edition_profile("AFTERNOON")
    context = analyst_context(packet, profile)
    value = edition_response(profile, context)
    assert profile["summary_paragraphs"] == 1
    value["summary"].append(dict(value["summary"][0]))
    assert validate_narrative(value, packet, context)
    assert [n["path"] for n in editorial_notes(value, narrative_schema(profile))] == ["summary"]
    value["summary"].append(dict(value["summary"][0]))
    with pytest.raises(ValueError, match="malformed narrative at summary"):
        validate_narrative(value, packet, context)


@pytest.mark.parametrize("text", [
    "The Nasdaq-100 broke below its own average while the S&P 500 held.",
    "Russell 2000 laggards versus Nasdaq 100 leaders widen the small-cap gap.",
])
def test_index_names_are_labels_not_numeric_claims(text):
    value = narrative()
    value["relationships"][0]["statement"] = text
    assert validate_narrative(value, fixture_packet())
    value["attention"][0]["why"] = text[:120]
    assert validate_narrative(value, fixture_packet())


@pytest.mark.parametrize("text", [
    "The Nasdaq-100 fell 2 percent into the close.",
    "S&P 500 closed at 5000 for the first time.",
    "Breadth was 3 to 1 negative.",
])
def test_literal_numbers_next_to_index_names_remain_fatal(text):
    value = narrative()
    value["relationships"][0]["statement"] = text
    with pytest.raises(ValueError, match="literal numeric claim"):
        validate_narrative(value, fixture_packet())


def test_rejected_openrouter_narrative_travels_with_the_error():
    value = narrative()
    value["summary"][0]["evidence_ids"] = ["not-an-evidence-id"]

    def requester(payload, key):
        return {"id": "gen-1", "provider": "Azure", "model": "anthropic/claude-fable-5.1",
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
    with pytest.raises(ValueError, match="unknown, unavailable, or unsupplied evidence reference") as excinfo:
        synthesize_openrouter(fixture_packet(), api_key="secret", requester=requester)
    assert excinfo.value.narrative == value


def test_rejected_cli_narrative_is_archived_with_the_run(tmp_path, monkeypatch):
    original = cli.output_directory
    monkeypatch.setattr(cli, "output_directory", lambda root, *rest: original(tmp_path, *rest))
    monkeypatch.setattr(cli, "update_latest", lambda root, page: None)
    monkeypatch.setattr("market_brief.synthesize.shutil.which", lambda _: "/usr/bin/claude")
    value = narrative()
    value["watches"][0]["horizon"] = "EVENT(not-an-admitted-event)"

    class Result:
        returncode, stderr = 0, ""
        stdout = json.dumps({"structured_output": value, "modelUsage": {"claude-test": {}}})
    monkeypatch.setattr(cli, "synthesize", lambda packet, **kwargs: synthesize_module.synthesize(
        packet, runner=lambda *a, **k: Result(), **kwargs))
    assert cli.main(["premarket", "--replay", "--synthesize"]) == 2
    folder = next((tmp_path / "runs").glob("*/*"))
    metadata = json.loads((folder / "metadata.json").read_text())
    assert metadata["validation"] == "FAILED" and "unknown event horizon" in metadata["error"]
    assert json.loads((folder / "narrative.rejected.json").read_text()) == value
    assert not (folder / "narrative.json").exists() and not (folder / "brief.html").exists()


def test_accepted_run_records_editorial_notes_without_failing(tmp_path, monkeypatch):
    original = cli.output_directory
    monkeypatch.setattr(cli, "output_directory", lambda root, *rest: original(tmp_path, *rest))
    monkeypatch.setattr(cli, "update_latest", lambda root, page: None)
    value = narrative()
    value["sections"]["macro"][0]["text"] = prose(281)
    monkeypatch.setattr(cli, "synthesize", lambda packet, **kwargs: (value, {"route": "test"}))
    assert cli.main(["premarket", "--replay", "--synthesize"]) == 0
    folder = next((tmp_path / "runs").glob("*/*"))
    metadata = json.loads((folder / "metadata.json").read_text())
    assert metadata["validation"] == "PASS"
    assert metadata["editorial"] == [dict(path="sections.macro.0.text", keyword="maxLength", limit=240,
                                          actual=len(prose(281)))]
    assert (folder / "brief.html").exists() and not (folder / "narrative.rejected.json").exists()

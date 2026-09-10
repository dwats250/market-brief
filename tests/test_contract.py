"""The synthesis contract: a smaller, sharper editorial job with unchanged factual authority."""

import json
import re

import pytest
from test_pipeline import fixture_packet, narrative

from market_brief.evidence import ROOT
from market_brief.synthesize import NARRATIVE_SCHEMA, construct_prompt, validate_narrative

PROMPT = (ROOT / "prompts/synthesis.md").read_text()


def test_headline_contract_is_one_short_claim():
    headline = PROMPT.split("HEADLINE", 1)[1].split("\n\n", 1)[0].lower()
    assert "one" in headline and "claim" in headline
    assert "8" in headline and "12" in headline and "words" in headline
    assert "no caveat" in headline
    assert "no semicolon" in headline
    assert NARRATIVE_SCHEMA["properties"]["banner"]["properties"]["title"]["maxLength"] <= 160


def test_overlong_or_compound_headlines_are_rejected():
    value = narrative()
    value["banner"]["title"] = "Most equity prints sit below the prior close with health care and megacap " \
        "tech weakest and energy and utilities the exceptions, but the tape comes from a single delayed feed " \
        "whose trades are stamped at the session close rather than in premarket"
    with pytest.raises(ValueError):
        validate_narrative(value, fixture_packet())
    value = narrative()
    value["banner"]["title"] = "Energy leads while tech lags; the feed is single-venue"
    with pytest.raises(ValueError, match="headline"):
        validate_narrative(value, fixture_packet())
    value = narrative()
    value["banner"]["title"] = "Energy leads while mega-cap tech participation weakens"
    assert validate_narrative(value, fixture_packet())


def test_contract_forbids_table_restatement():
    lowered = PROMPT.lower()
    assert "do not enumerate" in lowered or "never enumerate" in lowered
    assert "tables are the record" in lowered
    assert "relationships" in lowered and "contradiction" in lowered
    assert "what would change the read" in lowered or "change the read" in lowered


def test_contract_states_coverage_caveat_once():
    lowered = PROMPT.lower()
    assert "once" in lowered and "limitation" in lowered
    assert "do not repeat" in lowered
    system, _ = construct_prompt(fixture_packet())
    assert system == PROMPT


def test_contract_preserves_analytical_scope():
    lowered = PROMPT.lower()
    for keep in ("divergence", "cross-asset", "alternative", "timing"):
        assert keep in lowered


def test_numeric_grounding_and_fail_closed_rules_unchanged():
    value = narrative()
    value["banner"]["title"] = "SPY rose 8 percent"
    with pytest.raises(ValueError, match="literal numeric"):
        validate_narrative(value, fixture_packet())
    value = narrative()
    value["summary"][0]["text"] = "Yield is {{invented-id}}."
    value["summary"][0]["evidence_ids"].append("invented-id")
    with pytest.raises(ValueError):
        validate_narrative(value, fixture_packet())
    value = narrative()
    value["watches"][0]["horizon"] = "TOMORROW"
    with pytest.raises(ValueError):
        validate_narrative(value, fixture_packet())
    assert re.search(r"\bNUMBERS\b", PROMPT)


# Edition profiles: one contract, five checkpoints, smaller bounds for light editions.
EDITIONS = (("2026-09-08T12:45:00+00:00", "PREMARKET"), ("2026-09-08T13:35:00+00:00", "OPEN_1M"),
            ("2026-09-08T14:05:00+00:00", "OPEN_30M"), ("2026-09-08T19:10:00+00:00", "AFTERNOON"),
            ("2026-09-08T20:03:00+00:00", "CLOSE_1M"))


def edition_response(profile, context=None):
    """A complete response for the edition: trimmed to its bounds and citing only supplied facts."""
    from test_continuity import trimmed

    from market_brief.context import supplied_ids
    value = trimmed(narrative(), profile)
    if context is None:
        return value
    shown = supplied_ids(context)
    topics = {group["topic"] for group in context["catalog"]}
    records = [value["banner"], value["character"], *value["summary"], *value["watches"], *value["relationships"]]
    records += [p for section in value["sections"].values() for p in section]
    for record in records:
        record["evidence_ids"] = [i for i in record["evidence_ids"] if i in shown] or ["SPY-daily"]
    value["relationships"] = [r for r in value["relationships"] if set(r["instruments"]) <= topics]
    return value


@pytest.mark.parametrize("now,checkpoint", EDITIONS)
def test_each_edition_validates_a_complete_response_within_its_budget(now, checkpoint):
    from test_history_admission import packet_at, utc

    from market_brief.context import analyst_context, edition_profile
    from market_brief.evidence import canonical
    packet = packet_at(utc(now), checkpoint=checkpoint)
    profile = edition_profile(checkpoint)
    context = analyst_context(packet, profile)
    value = edition_response(profile, context)
    assert validate_narrative(value, packet, context)
    system, user = construct_prompt(packet, context=context)
    assert system == PROMPT
    payload = json.loads(user)
    assert payload["edition"]["checkpoint"] == checkpoint and payload["edition"]["profile"] == profile["profile"]
    assert payload["output_schema"]["properties"]["watches"]["maxItems"] == profile["watches"]
    assert len(user.encode()) <= profile["input_limit_bytes"]
    # A measured visible-output estimate; adaptive reasoning has no fixed reservation.
    assert len(canonical(value).encode()) / 3 < profile["max_output_tokens"] * .65
    if profile["profile"] == "light":
        assert payload["selection"]["mode"] == "changed" and payload["selection"]["omitted_count"] > 0


def test_light_edition_rejects_responses_beyond_its_bounds():
    from test_history_admission import packet_at, utc

    from market_brief.context import analyst_context, edition_profile
    packet = packet_at(utc("2026-09-08T19:10:00+00:00"), checkpoint="AFTERNOON")
    context = analyst_context(packet, edition_profile("AFTERNOON"))
    with pytest.raises(ValueError, match="summary"):
        validate_narrative(narrative(), packet, context)


def test_omitted_light_context_rows_cannot_be_cited():
    from test_history_admission import packet_at, utc

    from market_brief.context import analyst_context, edition_profile, supplied_ids
    from market_brief.evidence import evidence_catalog, model_packet
    packet = packet_at(utc("2026-09-08T19:10:00+00:00"), checkpoint="AFTERNOON")
    context = analyst_context(packet, edition_profile("AFTERNOON"))
    shown = supplied_ids(context)
    every = set(evidence_catalog(model_packet(packet)))
    omitted = every - shown
    assert omitted and {"SPY-daily", "QQQ-daily", "SPY-intraday", "treasury-2y-change"} <= shown
    assert set(context["selection"]["omitted_topics"]) & {"GDX", "GLD", "NVDA", "XLI"}
    value = edition_response(edition_profile("AFTERNOON"))
    value["summary"][0]["evidence_ids"] = [sorted(omitted)[0]]
    with pytest.raises(ValueError, match="unsupplied"):
        validate_narrative(value, packet, context)


def test_a_context_over_its_edition_budget_fails_with_diagnostics_not_truncation(monkeypatch):
    from market_brief import context as context_module
    from market_brief import synthesize
    packet = fixture_packet()
    tight = dict(synthesize.edition_profile("PREMARKET"), input_limit_bytes=1000)
    monkeypatch.setattr(synthesize, "edition_profile", lambda checkpoint: tight)
    monkeypatch.setattr(context_module, "edition_profile", lambda checkpoint, config=None: tight)
    with pytest.raises(ValueError, match="exceeds the rich edition budget"):
        construct_prompt(packet)


def test_contract_states_concise_output_is_enforced_not_stylistic():
    """The prompt must say that over-budget or truncated output is discarded with no second attempt."""
    lowered = PROMPT.lower()
    assert "hard" in lowered and "no second attempt" in lowered
    assert "discarded" in lowered or "rejected" in lowered

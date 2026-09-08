"""The synthesis contract: a smaller, sharper editorial job with unchanged factual authority."""

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

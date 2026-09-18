"""One model-owned attention selection surface: `attention[]`, validated against admitted triggers.

Production run 35347858192 (PREMARKET, 2026-09-18) returned a schema-valid narrative that named three
admitted triggers in `attention_ids` and an empty `attention` list. The provider's strict grammar
cannot express equality between two fields, so the old contract admitted a response the local
validator could never accept. The repair removes the duplicated authority: the analyst selects
triggers only by writing `attention` items, and deterministic code derives the selected IDs.
"""

import json

import pytest
from jsonschema import Draft202012Validator
from test_pipeline import fixture_packet, narrative
from test_schema_factoring import expand_local_refs

from market_brief.context import analyst_context, edition_profile
from market_brief.continuity import interpretation_record
from market_brief.evidence import evidence_catalog, model_packet
from market_brief.synthesize import (
    NARRATIVE_SCHEMA,
    acceptance_schema,
    factored_transport_schema,
    narrative_schema,
    transport_schema,
    validate_narrative,
)

# The exact selection the analyst returned on 2026-09-18 (artifact market-brief-run-PREMARKET-35347858192).
INCIDENT_ATTENTION_IDS = ["attention-SPY-cross", "attention-GLD-cross", "attention-META-spread"]
INCIDENT_TRIGGERS = [
    dict(id="attention-SPY-cross", symbol="SPY", reason="Daily close crossed up through its moving average"),
    dict(id="attention-GLD-cross", symbol="GLD", reason="Daily close crossed up through its moving average"),
    dict(id="attention-META-spread", symbol="META", reason="Material twenty-session return spread versus QQQ"),
]


def incident_packet():
    """The fixture packet with the three real incident triggers admitted, cited to fixture rows."""
    packet = fixture_packet()
    catalog = evidence_catalog(model_packet(packet))
    for trigger in INCIDENT_TRIGGERS:
        rows = ([ident for ident in catalog if ident.startswith(trigger["symbol"] + "-")]
                or [ident for ident in catalog if ident.startswith("QQQ-")])  # META is outside the fixture
        packet["attention"].append(dict(trigger, evidence_ids=rows[:1], horizon="daily", date="2026-09-17"))
    admitted = {a["id"] for a in model_packet(packet)["attention"]}
    assert set(INCIDENT_ATTENTION_IDS) <= admitted
    return packet


def every_contract():
    for profile in (None, edition_profile("PREMARKET"), edition_profile("OPEN_30M")):
        local = NARRATIVE_SCHEMA if profile is None else narrative_schema(profile)
        yield local, transport_schema(local), factored_transport_schema(local)


def test_attention_ids_is_not_a_model_authored_field_anywhere():
    for local, inline, factored in every_contract():
        for schema in (local, acceptance_schema(local), inline, expand_local_refs(factored)):
            assert "attention_ids" not in schema["properties"]
            assert "attention_ids" not in schema["required"]
        assert "attention_ids" not in json.dumps(factored)
        assert "attention" in local["properties"] and "attention" in local["required"]


def test_sep_18_incident_shape_is_not_expressible_by_any_contract():
    """The real failure: three admitted IDs in `attention_ids`, nothing in `attention`."""
    value = narrative()
    value["attention"] = []
    value["attention_ids"] = list(INCIDENT_ATTENTION_IDS)
    for local, inline, factored in every_contract():
        for schema in (local, acceptance_schema(local), inline, factored):
            errors = list(Draft202012Validator(schema).iter_errors(value))
            assert errors and any(e.validator == "additionalProperties" for e in errors)
    with pytest.raises(ValueError, match="malformed narrative"):
        validate_narrative(value, incident_packet())


def test_selecting_none_is_the_only_way_to_return_no_attention():
    value = narrative()
    value["attention"] = []
    accepted = validate_narrative(value, incident_packet())
    assert accepted["attention"] == []
    for local, inline, factored in every_contract():
        for schema in (acceptance_schema(local), inline, factored):
            assert not list(Draft202012Validator(schema).iter_errors(value))


def test_incident_ids_selected_through_attention_items_are_accepted():
    packet = incident_packet()
    value = narrative()
    value["attention"] = [dict(id=ident, why="Confirms whether the broad tape leads or lags gold.")
                          for ident in INCIDENT_ATTENTION_IDS]
    accepted = validate_narrative(value, packet)
    assert [a["id"] for a in accepted["attention"]] == INCIDENT_ATTENTION_IDS


@pytest.mark.parametrize("ids, message", [
    (["attention-SPY-cross", "invented"], "unknown attention trigger"),
    (["attention-NVDA-spread", "attention-NVDA-spread"], "repeated attention trigger"),
])
def test_unknown_or_repeated_trigger_ids_fail_closed(ids, message):
    value = narrative()
    value["attention"] = [dict(id=ident, why="Matters for participation.") for ident in ids]
    with pytest.raises(ValueError, match=message):
        validate_narrative(value, incident_packet())


@pytest.mark.parametrize("checkpoint, over", [("PREMARKET", 7), ("OPEN_30M", 5)])
def test_attention_bounds_still_hold_past_editorial_headroom(checkpoint, over):
    packet = incident_packet()
    packet["run"]["checkpoint"] = checkpoint
    profile = edition_profile(checkpoint)
    assert acceptance_schema(narrative_schema(profile))["properties"]["attention"]["maxItems"] == over - 1
    value = narrative()
    value["attention"] = [dict(id=f"attention-{i}", why="Matters.") for i in range(over)]
    with pytest.raises(ValueError, match="malformed narrative at attention"):
        validate_narrative(value, packet)


@pytest.mark.parametrize("why, message", [
    ("SPY is 2 percent above its average.", "literal numeric claim in attention reason"),
    ("Buy SPY on the cross.", "trade language in attention reason"),
])
def test_attention_reason_language_rules_are_unchanged(why, message):
    value = narrative()
    value["attention"] = [dict(id="attention-NVDA-spread", why=why)]
    with pytest.raises(ValueError, match=message):
        validate_narrative(value, incident_packet())


def test_continuity_freezes_exactly_the_selected_deterministic_trigger_records():
    packet = incident_packet()
    context = analyst_context(packet)
    value = narrative()
    value["attention"] = [dict(id="attention-GLD-cross", why="Tests whether the metal confirms risk appetite."),
                          dict(id="attention-NVDA-spread", why="Tests whether leadership broadens.")]
    validate_narrative(value, packet, context)
    record = interpretation_record(packet, value, context, "test")
    by_id = {a["id"]: a for a in packet["attention"]}
    assert [a["id"] for a in record["attention"]] == ["attention-NVDA-spread", "attention-GLD-cross"]
    for frozen in record["attention"]:
        source = by_id[frozen["id"]]
        assert frozen == dict(id=source["id"], symbol=source["symbol"], reason=source["reason"],
                              date=source["date"], evidence_ids=list(source["evidence_ids"]))
        assert set(frozen["evidence_ids"]) <= set(record["evidence"])
    assert "attention_ids" not in record["narrative"]

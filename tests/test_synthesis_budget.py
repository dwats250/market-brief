"""Offline size stress tests. Byte/3 is a planning estimate, never a native token count."""

import json
import math

import pytest
from jsonschema import Draft202012Validator
from test_pipeline import fixture_packet, narrative

from market_brief.context import edition_profile
from market_brief.synthesize import narrative_schema, transport_schema, validate_narrative


def maximum_shape(schema):
    """Fill every array and prose field; use four realistic distinct reference IDs.

    This stresses prose lengths, not pathological Unicode or maximum-length identifiers.
    It is schema-valid, intentionally not a claim of semantic grounding.
    """
    def fill(node, key="", index=0):
        if "const" in node:
            return node["const"]
        if "enum" in node:
            return max(node["enum"], key=len)
        if "oneOf" in node:
            return fill(node["oneOf"][0], key, index)
        if node.get("type") == "object":
            return {k: fill(v, k) for k, v in node["properties"].items()}
        if node.get("type") == "array":
            return [fill(node["items"], key, i) for i in range(node["maxItems"])]
        if key in {"evidence_ids", "attention_ids", "id", "comparison_id", "carried_id"}:
            return f"previous_close:QQQ-{index}"
        if key == "instruments":
            return ["SPY", "QQQ", "US 2Y", "US 10Y"][index]
        length = node["maxLength"]
        return ("Leadership remains mixed while confirmation depends on synchronized observations. "
                * (length // 80 + 1))[:length]
    return fill(schema)


@pytest.mark.parametrize("checkpoint", ["PREMARKET", "OPEN_30M"])
def test_maximum_shape_has_measured_serialized_headroom(checkpoint):
    profile = edition_profile(checkpoint)
    schema = narrative_schema(profile)
    Draft202012Validator.check_schema(schema)
    value = maximum_shape(schema)
    Draft202012Validator(schema).validate(value)
    transport = transport_schema(schema)
    Draft202012Validator.check_schema(transport)
    Draft202012Validator(transport).validate(value)
    size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
    estimate = math.ceil(size / 3)
    # Leave at least a quarter of the TOTAL ceiling for adaptive reasoning and variance.
    assert estimate <= profile["max_output_tokens"] * .75
    print(f"{checkpoint}: maximum-shape bytes={size}; byte/3 estimate={estimate}; "
          f"total ceiling={profile['max_output_tokens']}")


def test_light_maximum_shape_is_smaller_than_rich():
    rich = maximum_shape(narrative_schema(edition_profile("PREMARKET")))
    light = maximum_shape(narrative_schema(edition_profile("OPEN_30M")))
    assert len(json.dumps(light)) < len(json.dumps(rich)) * .85


@pytest.mark.parametrize("path", [
    ("banner", "limitation"), ("summary", 0, "text"), ("summary", 0, "uncertainty"),
    ("sections", "macro", 0, "text"), ("attention", 0, "why"),
    ("watches", 0, "condition"), ("watches", 0, "confirmation"),
    ("watches", 0, "contradiction"), ("character", "text"),
    ("relationships", 0, "statement"), ("relationships", 0, "reason"),
])
def test_rich_schema_rejects_excess_prose_without_relying_on_prompt(path):
    value = narrative()
    target = value
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = "x" * 501
    with pytest.raises(ValueError, match="malformed narrative"):
        validate_narrative(value, fixture_packet())


def test_all_schema_strings_are_bounded_including_reference_ids():
    def visit(node):
        if "string" in node.get("type", []):
            assert "maxLength" in node or "enum" in node or "const" in node
        for value in node.values():
            if isinstance(value, dict):
                visit(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        visit(item)
    visit(narrative_schema(edition_profile("PREMARKET")))


# Anthropic's documented structured-output subset (2026-09-10): no minLength/maxLength, no array
# constraints beyond minItems 0 or 1, no uniqueItems/pattern/oneOf. Unsupported keywords are a 400.
PROVIDER_KEYWORDS = {"type", "properties", "required", "additionalProperties", "enum", "const",
                     "anyOf", "items", "minItems", "description"}


def keywords(node, found=None):
    found = set() if found is None else found
    if isinstance(node, dict):
        for key, value in node.items():
            found.add(key)
            if key == "minItems":
                assert value in (0, 1)
            if key != "properties":
                keywords(value, found)
            else:
                for child in value.values():
                    keywords(child, found)
    elif isinstance(node, list):
        for item in node:
            keywords(item, found)
    return found


@pytest.mark.parametrize("checkpoint", ["PREMARKET", "OPEN_30M"])
def test_transport_schema_uses_only_documented_provider_keywords(checkpoint):
    from market_brief.synthesize import NARRATIVE_SCHEMA, transport_schema
    for local in (narrative_schema(edition_profile(checkpoint)), NARRATIVE_SCHEMA):
        transport = transport_schema(local)
        Draft202012Validator.check_schema(transport)
        assert keywords(transport) <= PROVIDER_KEYWORDS
        # Fully inlined: no schema references of any spelling reach the provider.
        assert not any(token in json.dumps(transport) for token in ("$ref", "$defs", "definitions", "oneOf"))
        # Same shape: a complete bounded response satisfies both contracts.
        Draft202012Validator(transport).validate(maximum_shape(local))


def test_every_local_bound_is_described_in_transport_and_enforced_only_locally():
    """Cost safety never depends on the provider honoring a bound: the model reads it in a
    description, the local validator enforces it, and the transport schema alone would accept excess."""
    from market_brief.synthesize import transport_schema
    local = narrative_schema(edition_profile("PREMARKET"))
    transport = transport_schema(local)
    value = maximum_shape(local)
    lenient = Draft202012Validator(transport)
    strict = Draft202012Validator(local)
    checked = 0

    def visit(node, current, wire):
        nonlocal checked
        if isinstance(current, dict):
            for key, child in node["properties"].items():
                visit(child, current[key], wire["properties"][key])
                current_child = current[key]
                if "maxLength" in child:
                    assert str(child["maxLength"]) in wire["properties"][key].get("description", "")
                    current[key] = "x" * (child["maxLength"] + 1)
                    assert not strict.is_valid(value) and lenient.is_valid(value)
                    current[key] = current_child
                    checked += 1
        elif isinstance(current, list):
            if "maxItems" in node:
                assert str(node["maxItems"]) in wire.get("description", "")
                current.append(current[0] if current else maximum_shape(node["items"]))
                assert not strict.is_valid(value) and lenient.is_valid(value)
                current.pop()
                checked += 1
            for index, child in enumerate(current):
                if "maxLength" in node["items"]:
                    current[index] = "x" * (node["items"]["maxLength"] + 1)
                    assert not strict.is_valid(value) and lenient.is_valid(value)
                    current[index] = child
                    checked += 1
                else:
                    visit(node["items"], child, wire["items"])
    visit(local, value, transport)
    assert strict.is_valid(value) and lenient.is_valid(value)
    assert checked >= 40


def test_watch_horizon_and_nullable_carried_id_survive_transport_translation():
    from market_brief.synthesize import transport_schema
    local = narrative_schema(edition_profile("PREMARKET"))
    transport = transport_schema(local)
    horizon = transport["properties"]["watches"]["items"]["properties"]["horizon"]
    assert horizon["type"] == "string" and "EVENT(" in horizon["description"] and "NEXT_BRIEF" in horizon["description"]
    carried = transport["properties"]["relationships"]["items"]["properties"]["carried_id"]
    assert {"type": "null"} in carried["anyOf"] and "type" not in carried
    value = maximum_shape(local)
    value["relationships"][0]["carried_id"] = None
    value["watches"][0]["horizon"] = "EVENT(fomc)"
    assert Draft202012Validator(transport).is_valid(value)
    value["watches"][0]["horizon"] = "TOMORROW"
    assert Draft202012Validator(transport).is_valid(value)  # transport cannot express the pattern
    assert not Draft202012Validator(local).is_valid(value)  # the local contract still rejects it

"""Offline size stress tests. Byte/3 is a planning estimate, never a native token count."""

import json
import math

import pytest
from jsonschema import Draft202012Validator
from test_pipeline import fixture_packet, narrative

from market_brief.context import edition_profile
from market_brief.synthesize import compact_schema, narrative_schema, validate_narrative


def maximum_shape(schema):
    """Fill every array and prose field; use four realistic distinct reference IDs.

    This stresses prose lengths, not pathological Unicode or maximum-length identifiers.
    It is schema-valid, intentionally not a claim of semantic grounding.
    """
    def fill(node, key="", index=0):
        if "$ref" in node:
            return fill(schema["$defs"][node["$ref"].split("/")[-1]], key, index)
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
    transport = compact_schema(schema)
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


def test_every_prose_and_identifier_bound_is_enforced_in_transport_schema():
    schema = narrative_schema(edition_profile("PREMARKET"))
    value = maximum_shape(schema)
    transport = Draft202012Validator(compact_schema(schema))

    def visit(node, current):
        if isinstance(current, dict):
            for key, child in node["properties"].items():
                if "maxLength" in child:
                    original = current[key]
                    current[key] = "x" * (child["maxLength"] + 1)
                    assert not transport.is_valid(value)
                    current[key] = original
                else:
                    visit(child, current[key])
        elif isinstance(current, list):
            for index, child in enumerate(current):
                if "maxLength" in node["items"]:
                    current[index] = "x" * (node["items"]["maxLength"] + 1)
                    assert not transport.is_valid(value)
                    current[index] = child
                else:
                    visit(node["items"], child)
    visit(schema, value)
    assert transport.is_valid(value)

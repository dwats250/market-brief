"""The provider-enforced schema is a `$defs`/`$ref` factoring of the fully inlined transport schema.

Anthropic's strict-grammar compiler rejects the fully inlined Market Brief schema as too large
("The compiled grammar is too large", HTTP 400). The fix factors repeated genuine schema nodes into
local `$defs` for the OpenRouter `response_format` only; the prompt keeps advertising the fully
inlined form. These tests prove the two are the same contract by expanding every local ref back to
the inlined schema exactly — no field, type, requirement, or enum may differ.
"""

import json

import pytest
from jsonschema import Draft202012Validator
from test_pipeline import fixture_packet, narrative

from market_brief.context import edition_profile
from market_brief.evidence import digest
from market_brief.synthesize import (
    NARRATIVE_SCHEMA,
    factored_transport_schema,
    narrative_schema,
    synthesize_openrouter,
    transport_schema,
)


def compact(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def expand_local_refs(schema):
    """Recursively inline every local `#/$defs` ref and drop the `$defs` container.

    The exact inverse of the factoring: proves the factored provider schema and the fully inlined
    transport schema are byte-for-byte the same contract once the refs are expanded.
    """
    definitions = schema.get("$defs", {})

    def expand(node):
        if isinstance(node, list):
            return [expand(item) for item in node]
        if not isinstance(node, dict):
            return node
        reference = node.get("$ref")
        if set(node) == {"$ref"} and isinstance(reference, str) and reference.startswith("#/$defs/"):
            return expand(definitions[reference.rsplit("/", 1)[-1]])
        return {key: expand(value) for key, value in node.items() if key != "$defs"}

    return expand(schema)


@pytest.mark.parametrize("checkpoint", [None, "PREMARKET", "OPEN_30M"])
def test_factored_provider_schema_reconstructs_inline_transport_exactly(checkpoint):
    contract = NARRATIVE_SCHEMA if checkpoint is None else narrative_schema(edition_profile(checkpoint))
    inline = transport_schema(contract)
    factored = factored_transport_schema(contract)

    # A valid Draft 2020-12 schema that genuinely factors: real `$defs` and more refs than defs.
    Draft202012Validator.check_schema(factored)
    assert factored.get("$defs")
    assert compact(factored).count('"$ref":"#/$defs/') > len(factored["$defs"])

    # Strictly smaller on the wire than the fully inlined form the grammar compiler rejected.
    assert len(compact(factored)) < len(compact(inline))

    # The strongest proof: expanding every local ref reconstructs the inline transport schema exactly.
    assert expand_local_refs(factored) == inline

    # Nothing loosened: the fully inlined form already omits the unsupported bound keywords, and
    # factoring reintroduces none of them.
    wire = compact(factored)
    for keyword in ("minLength", "maxLength", "maxItems", "uniqueItems", "pattern"):
        assert keyword not in wire


def test_inline_and_factored_accept_and_reject_the_same_narratives():
    contract = narrative_schema(edition_profile("PREMARKET"))
    inline = transport_schema(contract)
    factored = factored_transport_schema(contract)

    valid = narrative()
    assert not list(Draft202012Validator(inline).iter_errors(valid))
    assert not list(Draft202012Validator(factored).iter_errors(valid))

    invalid = json.loads(json.dumps(valid))
    del invalid["banner"]["limitation"]  # a required property removed must still be rejected by both
    assert list(Draft202012Validator(inline).iter_errors(invalid))
    assert list(Draft202012Validator(factored).iter_errors(invalid))


def test_openrouter_prompt_copy_and_response_format_are_one_factored_schema():
    """Proof on the actual emitted request: the prompt copy and `response_format` are the same factored
    schema, and it is the inlined contract. Owner ruling 2026-09-25 reversed PR #30's inlined prompt copy
    because it put the 2026-09-24 light context 26 bytes over its input budget."""
    seen = {}

    def requester(payload, api_key):
        seen["payload"] = payload
        return {"id": "response-test", "choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(narrative())}}]}

    _, metadata = synthesize_openrouter(fixture_packet(), api_key="secret", requester=requester)
    payload = seen["payload"]
    advertised = json.loads(payload["messages"][1]["content"])["output_schema"]
    enforced = payload["response_format"]["json_schema"]["schema"]

    Draft202012Validator.check_schema(enforced)
    assert enforced.get("$defs")
    assert compact(advertised) == compact(enforced)  # one form: the prompt copy is the enforced schema
    inline = transport_schema(narrative_schema(edition_profile("PREMARKET")))
    assert expand_local_refs(advertised) == inline  # same contract, factored representation
    assert len(compact(advertised)) < len(compact(inline))
    assert metadata["schema_hash"] == digest(enforced)  # provenance records what was sent on the wire


@pytest.mark.parametrize("checkpoint, full", [("PREMARKET", False), ("OPEN_30M", False), ("PREMARKET", True)])
def test_the_prompt_schema_copy_expands_exactly_to_the_inline_transport_schema(checkpoint, full):
    """The schema the analyst reads in the user message, for every synthesis profile and the diagnostic
    full packet: structurally the inlined transport contract, only factored. No field, bound description,
    enum, requirement or validation rule differs, and it is the exact schema `response_format` enforces."""
    from test_history_admission import packet_at, utc

    from market_brief.context import analyst_context
    from market_brief.synthesize import construct_prompt
    packet = packet_at(utc("2026-09-08T12:45:00+00:00" if checkpoint == "PREMARKET" else "2026-09-08T14:05:00+00:00"),
                       checkpoint=checkpoint)
    profile = edition_profile(checkpoint)
    context = None if full else analyst_context(packet, profile)
    _, user = construct_prompt(packet, full=full, context=context)
    prompt_schema = json.loads(user)["output_schema"]
    contract = NARRATIVE_SCHEMA if full else narrative_schema(profile)
    assert prompt_schema.get("$defs")
    assert expand_local_refs(prompt_schema) == transport_schema(contract)
    assert compact(prompt_schema) == compact(factored_transport_schema(contract))
    for value in (narrative(), json.loads(json.dumps(narrative())) | {"extra": 1}):
        assert Draft202012Validator(prompt_schema).is_valid(value) == \
            Draft202012Validator(transport_schema(contract)).is_valid(value)

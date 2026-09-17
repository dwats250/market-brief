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


def test_openrouter_advertises_inline_schema_and_enforces_factored_equivalent():
    """Proof on the actual emitted request: prompt inline, `response_format` factored, same contract."""
    seen = {}

    def requester(payload, api_key):
        seen["payload"] = payload
        return {"id": "response-test", "choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(narrative())}}]}

    _, metadata = synthesize_openrouter(fixture_packet(), api_key="secret", requester=requester)
    payload = seen["payload"]
    advertised = json.loads(payload["messages"][1]["content"])["output_schema"]
    enforced = payload["response_format"]["json_schema"]["schema"]

    Draft202012Validator.check_schema(advertised)
    Draft202012Validator.check_schema(enforced)
    assert "$defs" not in advertised           # the prompt keeps the fully inlined schema
    assert enforced.get("$defs")               # only the provider-enforced schema is factored
    assert advertised != enforced
    assert len(compact(enforced)) < len(compact(advertised))
    assert expand_local_refs(enforced) == advertised  # same contract, factored representation
    assert metadata["schema_hash"] == digest(enforced)  # provenance records what was sent on the wire

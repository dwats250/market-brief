import json

from test_pipeline import fixture_packet, narrative

from market_brief.synthesize import OPENROUTER_MODEL, _TransientOpenRouterError, synthesize_openrouter


def test_openrouter_structured_transport_preserves_validator_contract():
    calls = []

    def requester(payload, api_key):
        calls.append((payload, api_key))
        return {"id": "response-test", "choices": [{"message": {
            "content": json.dumps(narrative())}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}

    output, metadata = synthesize_openrouter(fixture_packet(), api_key="secret", requester=requester)
    payload, key = calls[0]
    assert key == "secret"
    assert payload["model"] == OPENROUTER_MODEL
    assert payload["reasoning"] == {"exclude": True}
    assert payload["response_format"]["type"] == "json_schema"
    assert output["mode"] == "SAMPLE"
    assert metadata["provider"] == "OpenRouter"
    assert metadata["usage"]["total_tokens"] == 30


def test_openrouter_retries_only_transient_transport_failures():
    calls = []

    def requester(payload, api_key):
        calls.append(1)
        if len(calls) < 3:
            raise _TransientOpenRouterError("temporary")
        return {"id": "response-test", "choices": [{"message": {
            "content": json.dumps(narrative())}}]}

    output, _ = synthesize_openrouter(fixture_packet(), api_key="secret", requester=requester,
                                      sleeper=lambda _: None)
    assert output["mode"] == "SAMPLE" and len(calls) == 3

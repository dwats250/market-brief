import json

from test_pipeline import fixture_packet, narrative

from market_brief.synthesize import (
    OPENROUTER_MODEL,
    _openrouter_narrative,
    _TransientOpenRouterError,
    synthesize_openrouter,
)


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
    assert payload["plugins"] == [{"id": "response-healing"}]
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


def test_openrouter_accepts_fenced_json_transport_wrapper():
    def requester(payload, api_key):
        return {"id": "response-test", "choices": [{"message": {
            "content": "```json\n" + json.dumps(narrative()) + "\n```"}}]}

    output, _ = synthesize_openrouter(fixture_packet(), api_key="secret", requester=requester)
    assert output["mode"] == "SAMPLE"


def test_openrouter_rejects_truncated_invalid_json_with_sanitized_diagnostic():
    response = {"id": "response-test", "provider": "test-provider", "choices": [{
        "finish_reason": "length", "message": {"content": '{"schema_version":'}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 6000}}
    try:
        _openrouter_narrative(response)
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("malformed response was accepted")
    assert "finish_reason" in message and "content_bytes" in message
    assert "schema_version" not in message


def test_openrouter_rejects_validator_invalid_json_without_second_call():
    calls = []
    invalid = narrative()
    del invalid["banner"]["limitation"]

    def requester(payload, api_key):
        calls.append(1)
        return {"id": "response-test", "choices": [{"message": {
            "content": json.dumps(invalid)}}]}

    try:
        synthesize_openrouter(fixture_packet(), api_key="secret", requester=requester)
    except ValueError:
        pass
    else:
        raise AssertionError("validator-invalid response was accepted")
    assert calls == [1]

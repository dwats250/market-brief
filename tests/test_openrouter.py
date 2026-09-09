import json

import pytest
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
    assert payload["reasoning"] == {"exclude": True, "max_tokens": 1024}
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


def test_analyst_identity_and_edition_budget_are_configured_and_recorded(monkeypatch):
    from market_brief.synthesize import analyst_model
    assert analyst_model(environ={})["model"] == OPENROUTER_MODEL
    assert analyst_model(environ={})["source"] == "config/editions.json"
    assert analyst_model(environ={"MARKET_BRIEF_MODEL": "vendor/other-analyst"}) == dict(
        model="vendor/other-analyst", source="environment", cli_model="sonnet")
    monkeypatch.setenv("MARKET_BRIEF_MODEL", "vendor/other-analyst")
    calls = []

    def requester(payload, api_key):
        calls.append(payload)
        return {"id": "r", "model": "vendor/other-analyst:resolved", "provider": "Test",
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(narrative())}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}
    _, meta = synthesize_openrouter(fixture_packet(), api_key="k", requester=requester)
    assert calls[0]["model"] == "vendor/other-analyst" and calls[0]["max_tokens"] == 5524
    assert meta["model"] == "vendor/other-analyst" and meta["model_source"] == "environment"
    assert meta["resolved_model"] == "vendor/other-analyst:resolved" and meta["profile"] == "rich"
    assert meta["max_output_tokens"] == 5524 and meta["attempts"] == 1
    assert meta["input_bytes"] > 1000 and meta["output_bytes"] > 100


@pytest.mark.parametrize("checkpoint,total", [
    ("PREMARKET", 5524), ("CLOSE_1M", 5524),
    ("OPEN_1M", 3524), ("OPEN_30M", 3524), ("AFTERNOON", 3524),
])
def test_each_edition_sends_bounded_reasoning_and_reserved_json_capacity(checkpoint, total):
    from test_contract import edition_response

    from market_brief.context import analyst_context, edition_profile
    packet = fixture_packet()
    packet["run"]["checkpoint"] = checkpoint
    profile = edition_profile(checkpoint)
    context = analyst_context(packet, profile)
    value = edition_response(profile, context)
    calls = []

    def requester(payload, api_key):
        calls.append(payload)
        return {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(value), "reasoning": "PRIVATE_REASONING_SENTINEL",
            "reasoning_details": [{"text": "PRIVATE_REASONING_SENTINEL"}]}}]}

    output, metadata = synthesize_openrouter(packet, api_key="test", requester=requester, context=context)
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == total
    assert calls[0]["reasoning"] == {"max_tokens": 1024, "exclude": True}
    assert metadata["reasoning_max_tokens"] == 1024
    assert metadata["max_output_tokens"] == total
    assert total - metadata["reasoning_max_tokens"] == (4500 if profile["profile"] == "rich" else 2500)
    assert "PRIVATE_REASONING_SENTINEL" not in json.dumps([output, metadata])


@pytest.mark.parametrize("content", ['{"schema_version":', json.dumps(narrative())])
def test_length_is_output_budget_failure_before_validation_without_retry(monkeypatch, content):
    import importlib
    module = importlib.import_module("market_brief.synthesize")
    calls = []

    def must_not_validate(*args, **kwargs):
        pytest.fail("length completion reached semantic validation")

    monkeypatch.setattr(module, "validate_narrative", must_not_validate)

    def requester(payload, api_key):
        calls.append(payload)
        return {"provider": "Test", "choices": [{"finish_reason": "length", "message": {
            "content": content, "reasoning": "PRIVATE_REASONING_SENTINEL"}}],
            "usage": {"completion_tokens": 5524, "cost": 0.1}}

    with pytest.raises(ValueError, match="output budget exhausted") as exc:
        synthesize_openrouter(fixture_packet(), api_key="test", requester=requester,
                             sleeper=lambda _: pytest.fail("length completion retried"))
    assert len(calls) == 1
    assert '"finish_reason": "length"' in str(exc.value)
    assert '"completion_tokens": 5524' in str(exc.value)
    assert "PRIVATE_REASONING_SENTINEL" not in str(exc.value)
    assert "schema_version" not in str(exc.value)

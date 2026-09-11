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
    assert len(calls) == 1
    payload, key = calls[0]
    assert key == "secret"
    assert payload["model"] == OPENROUTER_MODEL
    assert payload["reasoning"] == {"exclude": True, "effort": "low"}
    assert payload["plugins"] == [{"id": "response-healing"}]
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["strict"] is True
    # Fable 5.1 endpoints advertise no sampling parameters; with require_parameters a stray
    # temperature could leave no eligible provider. The wire schema carries only documented keywords.
    assert "temperature" not in payload and "top_p" not in payload
    wire = json.dumps(payload["response_format"]["json_schema"]["schema"])
    assert "maxLength" not in wire and "maxItems" not in wire and "uniqueItems" not in wire
    assert payload["provider"] == {"order": ["azure"], "allow_fallbacks": False, "require_parameters": True}
    assert output["mode"] == "SAMPLE"
    assert metadata["provider"] == "OpenRouter"
    assert metadata["usage"]["total_tokens"] == 30
    from market_brief.evidence import digest
    assert metadata["schema_hash"] == digest(payload["response_format"]["json_schema"]["schema"])


def test_openrouter_never_retries_transient_transport_failures():
    calls = []

    def requester(payload, api_key):
        calls.append(payload)
        raise _TransientOpenRouterError("temporary")

    with pytest.raises(ValueError, match="no automatic paid retry"):
        synthesize_openrouter(fixture_packet(), api_key="secret", requester=requester,
                             sleeper=lambda _: pytest.fail("paid retry"))
    assert len(calls) == 1
    assert calls[0]["provider"] == {"order": ["azure"], "allow_fallbacks": False, "require_parameters": True}


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
    assert calls[0]["model"] == "vendor/other-analyst" and calls[0]["max_tokens"] == 7000
    assert meta["model"] == "vendor/other-analyst" and meta["model_source"] == "environment"
    assert meta["resolved_model"] == "vendor/other-analyst:resolved" and meta["profile"] == "rich"
    assert meta["max_output_tokens"] == 7000 and meta["attempts"] == 1
    assert meta["input_bytes"] > 1000 and meta["output_bytes"] > 100


@pytest.mark.parametrize("checkpoint,total", [
    ("PREMARKET", 7000), ("CLOSE_1M", 7000),
    ("OPEN_1M", 4500), ("OPEN_30M", 4500), ("AFTERNOON", 4500),
])
def test_each_edition_sends_low_adaptive_effort_and_unchanged_total_ceiling(checkpoint, total):
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
    assert calls[0]["reasoning"] == {"effort": "low", "exclude": True}
    assert metadata["reasoning_effort"] == "low"
    assert metadata["max_output_tokens"] == total
    assert "reasoning_max_tokens" not in metadata
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
            "usage": {"completion_tokens": 7000, "cost": 0.1}}

    with pytest.raises(ValueError, match="output budget exhausted") as exc:
        synthesize_openrouter(fixture_packet(), api_key="test", requester=requester,
                             sleeper=lambda _: pytest.fail("length completion retried"))
    assert len(calls) == 1
    assert '"finish_reason": "length"' in str(exc.value)
    assert '"completion_tokens": 7000' in str(exc.value)
    assert "PRIVATE_REASONING_SENTINEL" not in str(exc.value)
    assert "schema_version" not in str(exc.value)


@pytest.mark.parametrize("finish", ["stop", "length"])
def test_nested_accounting_and_healing_survive_without_private_content(finish):
    from market_brief.synthesize import _openrouter_diagnostic

    response = {"id": "gen-test", "model": "resolved-model", "provider": "Azure", "choices": [{
        "finish_reason": finish, "native_finish_reason": "max_tokens" if finish == "length" else "end_turn",
        "message": {"content": json.dumps(narrative()), "reasoning_details": [
            {"text": "PRIVATE_SENTINEL", "data": "PRIVATE_SENTINEL"}]}}],
        "usage": {"completion_tokens": 4000, "cost": .2,
                  "completion_tokens_details": {"reasoning_tokens": 1700, "text": "PRIVATE_SENTINEL"},
                  "prompt_tokens_details": {"cached_tokens": 12},
                  "cost_details": {"upstream_inference_cost": .2}},
        "openrouter_metadata": {"pipeline": [{"type": "response_healing", "data": {
            "improved": True, "original_length": 5001, "healed_length": 5000,
            "content": "PRIVATE_SENTINEL"}}]}}
    diagnostic = json.loads(_openrouter_diagnostic(response))
    assert diagnostic["response_id"] == "gen-test"
    assert diagnostic["resolved_model"] == "resolved-model"
    assert diagnostic["usage"]["completion_tokens_details"] == {"reasoning_tokens": 1700}
    assert diagnostic["usage"]["non_reasoning_completion_tokens"] == 2300
    assert diagnostic["response_healing"][0]["data"] == {
        "improved": True, "original_length": 5001, "healed_length": 5000}
    if finish == "length":
        with pytest.raises(ValueError) as exc:
            synthesize_openrouter(fixture_packet(), api_key="fake", requester=lambda *_: response)
        recorded = str(exc.value)
    else:
        _, metadata = synthesize_openrouter(fixture_packet(), api_key="fake", requester=lambda *_: response)
        assert metadata["diagnostic"] == diagnostic
        assert metadata["usage"] == diagnostic["usage"]
        recorded = json.dumps(metadata)
    assert "reasoning_tokens" in recorded
    assert "PRIVATE_SENTINEL" not in recorded


def test_missing_nested_accounting_is_unknown_not_zero():
    from market_brief.synthesize import _openrouter_diagnostic
    diagnostic = json.loads(_openrouter_diagnostic({"choices": [{"message": {"content": "{}"}}]}))
    assert "completion_tokens_details" not in diagnostic["usage"]
    assert "non_reasoning_completion_tokens" not in diagnostic["usage"]
    assert diagnostic["response_healing"] is None


@pytest.mark.parametrize("failure", ["timeout", "http", "malformed"])
def test_transport_failure_makes_one_http_request_and_enables_metadata(monkeypatch, failure):
    import importlib
    from urllib.error import HTTPError

    module = importlib.import_module("market_brief.synthesize")
    calls = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            return b"not json"

    def fake_urlopen(request, timeout):
        calls.append(request)
        assert request.get_header("X-openrouter-metadata") == "enabled"
        payload = json.loads(request.data)
        assert payload["provider"] == {"order": ["azure"], "allow_fallbacks": False, "require_parameters": True}
        assert payload["reasoning"] == {"effort": "low", "exclude": True}
        assert payload["response_format"]["json_schema"]["schema"] == json.loads(
            payload["messages"][1]["content"])["output_schema"]
        if failure == "timeout":
            raise TimeoutError()
        if failure == "http":
            raise HTTPError(request.full_url, 503, "unavailable", {}, None)
        return Response()

    monkeypatch.setattr(module, "urlopen", fake_urlopen)
    with pytest.raises(ValueError, match="no automatic paid retry"):
        synthesize_openrouter(fixture_packet(), api_key="fake")
    assert len(calls) == 1


@pytest.mark.parametrize("reasoning", [True, "1700", -1, 4001])
def test_invalid_reasoning_split_does_not_invent_content_accounting(reasoning):
    from market_brief.synthesize import _safe_usage
    usage = _safe_usage({"usage": {"completion_tokens": 4000,
                                  "completion_tokens_details": {"reasoning_tokens": reasoning}}})
    assert "non_reasoning_completion_tokens" not in usage


def test_suite_guard_blocks_live_openrouter_and_cli_calls(monkeypatch):
    """The autouse guard in conftest.py: a leaked key or installed CLI cannot make a paid call."""
    import os

    from market_brief.synthesize import synthesize
    assert "OPENROUTER_API_KEY" not in os.environ
    with pytest.raises(AssertionError, match="never open a connection"):
        synthesize_openrouter(fixture_packet(), api_key="leaked")
    with pytest.raises(ValueError, match="not installed"):
        synthesize(fixture_packet())


@pytest.mark.parametrize("status,expected", [(400, "OpenRouter HTTP 400"), (503, "no automatic paid retry")])
def test_http_error_body_is_recorded_sanitized_without_retry(monkeypatch, status, expected):
    """Run 34486249474 lost its cause: `OpenRouter HTTP 400` with no body. Keep the safe parts."""
    import importlib
    import io
    from urllib.error import HTTPError

    module = importlib.import_module("market_brief.synthesize")
    calls = []
    body = json.dumps({"error": {"code": status, "message": "Provider returned error: PARAM_REJECTED",
                                 "metadata": {"provider_name": "Azure", "error_type": "invalid_request",
                                              "raw": "PRIVATE_RAW_SENTINEL", "headers": {"authorization": "x"}}},
                       "user_id": "PRIVATE_USER_SENTINEL"}).encode()

    def fake_urlopen(request, timeout):
        calls.append(request)
        raise HTTPError(request.full_url, status, "error", {}, io.BytesIO(body))

    monkeypatch.setattr(module, "urlopen", fake_urlopen)
    with pytest.raises(ValueError, match=expected) as exc:
        synthesize_openrouter(fixture_packet(), api_key="fake")
    recorded = str(exc.value)
    assert len(calls) == 1
    assert '"http_status": %d' % status in recorded
    assert "PARAM_REJECTED" in recorded and "Azure" in recorded and "invalid_request" in recorded
    for private in ("PRIVATE_RAW_SENTINEL", "PRIVATE_USER_SENTINEL", "authorization", "fake"):
        assert private not in recorded


def test_unreadable_http_error_body_is_unknown_not_invented(monkeypatch):
    import importlib
    import io
    from urllib.error import HTTPError

    module = importlib.import_module("market_brief.synthesize")

    def fake_urlopen(request, timeout):
        raise HTTPError(request.full_url, 400, "error", {}, io.BytesIO(b"<html>not json"))

    monkeypatch.setattr(module, "urlopen", fake_urlopen)
    with pytest.raises(ValueError, match="OpenRouter HTTP 400") as exc:
        synthesize_openrouter(fixture_packet(), api_key="fake")
    assert '"error": "unknown"' in str(exc.value) and "html" not in str(exc.value)

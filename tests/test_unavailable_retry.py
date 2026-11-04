"""One delayed retry for Anthropic's transient "Service temporarily unavailable", relayed by OpenRouter as HTTP 503.

On 2026-11-04 the PREMARKET synthesis's only request came back as that 503 and the checkpoint failed closed. That one
response, every field matched whole and exactly, earns one retry of the identical request after a ten-second wait,
accounted durably before it is sent: at most two provider requests per synthesis, shared with the grammar-rate-limit
retry (never both), no model fallback. Every other 5xx, 429, 408, transport, auth or malformed failure keeps its
single request. All offline: the transport and the sleeper are fakes.
"""

import io
import json
from urllib.error import HTTPError, URLError

import pytest
from test_cadence import TUE
from test_grammar_retry import EXACT, Wire, accepted, rate_limited, record_of
from test_pipeline import narrative
from test_synthesis_attempts import COST, PaidActions

from market_brief.evidence import canonical
from market_brief.synthesize import (
    OPENROUTER_FALLBACK_MODEL,
    OPENROUTER_MODEL,
    NarrativeRejected,
    _safe_error,
)

UNAVAILABLE = "Service temporarily unavailable"


def unavailable_body(provider_message=UNAVAILABLE, provider="Anthropic", code=503, message="Provider returned error",
                     raw=None):
    """OpenRouter's relay of Anthropic's 503, its `raw` carrying Anthropic's own JSON error."""
    if raw is None:
        raw = json.dumps({"type": "error", "error": {"type": "api_error", "message": provider_message}})
    return json.dumps({"error": {"code": code, "message": message,
                                 "metadata": {"provider_name": provider, "raw": raw}},
                       "user_id": "PRIVATE_USER_SENTINEL"}).encode()


def unavailable(request, body=None, status=503):
    return HTTPError(request.full_url, status, "error", {}, io.BytesIO(body or unavailable_body()))


SANITIZED = {"code": 503, "message": "Provider returned error",
             "metadata": {"provider_name": "Anthropic", "provider_message": UNAVAILABLE}}


# --- the exact 503: one retry, ten seconds later, of the identical request ---------------------------------------

def test_the_exact_503_is_retried_once_after_ten_seconds_with_the_identical_request(monkeypatch):
    described = []
    wire = Wire(monkeypatch, [unavailable, lambda request: accepted()], hook=described.append)
    output, model = wire.synthesize()
    assert output["mode"] == "SAMPLE"
    # Accounted before each request; the retry's accounting precedes the wait, the wait precedes the request.
    assert wire.events == [("hook", 1), "request", ("hook", 2), ("sleep", 10), "request"]
    assert wire.slept == [10]
    first, second = wire.requests
    assert first.data == second.data  # byte for byte: the same model, prompt, schema, evidence and route
    assert json.loads(second.data)["model"] == OPENROUTER_MODEL
    assert first.full_url == second.full_url and first.headers == second.headers
    assert model["attempts"] == 2 and model["fallback_used"] is False and model["primary_failure"] is None
    assert model["requested_model"] == OPENROUTER_MODEL and model["usage"] == COST
    assert "grammar_retry" not in model
    retry = model["unavailable_retry"]
    assert retry == dict(first=dict(model=OPENROUTER_MODEL, status=503, error=SANITIZED), delay_seconds=10,
                         model=OPENROUTER_MODEL, outcome="response")
    assert "PRIVATE_USER_SENTINEL" not in json.dumps(model)
    # The hook saw the first request, then the second request's intent with the first's diagnostic.
    assert [d["attempts"] for d in described] == [1, 2]
    assert "unavailable_retry" not in described[0] and "grammar_retry" not in described[1]
    assert described[1]["unavailable_retry"] == dict(retry, outcome="pending")


def test_an_invalid_second_narrative_is_rejected_by_the_same_validator_and_accounted_as_two(monkeypatch):
    invalid = narrative()
    del invalid["banner"]["limitation"]
    wire = Wire(monkeypatch, [unavailable, lambda request: accepted(invalid)])
    with pytest.raises(NarrativeRejected, match="malformed narrative") as caught:
        wire.synthesize()
    assert len(wire.requests) == 2 and wire.slept == [10]
    accounted = caught.value.attempt
    assert accounted["attempts"] == 2 and accounted["usage"] == COST
    assert accounted["unavailable_retry"]["outcome"] == "response"


# --- the retry is the last request ------------------------------------------------------------------------------

NO_ENDPOINT = json.dumps({"error": {"code": 404, "message": f"No endpoints found for {OPENROUTER_MODEL}"}}).encode()


@pytest.mark.parametrize(("second", "expected"), [
    (unavailable, "no automatic paid retry"),
    (rate_limited, "OpenRouter HTTP 400"),  # the grammar retry never chains after this one
    (lambda request: HTTPError(request.full_url, 404, "error", {}, io.BytesIO(NO_ENDPOINT)),
     "OpenRouter HTTP 404"),  # never a model fallback after the retry
    (lambda request: unavailable(request, status=500), "no automatic paid retry"),
    (lambda request: TimeoutError(), "no automatic paid retry"),
], ids=["503-again", "grammar-400", "no-endpoints", "500", "timeout"])
def test_a_failed_retry_stops_at_two_requests_with_both_accounted(monkeypatch, second, expected):
    wire = Wire(monkeypatch, [unavailable, second])
    with pytest.raises(ValueError, match=expected) as caught:
        wire.synthesize()
    assert len(wire.requests) == 2 and wire.slept == [10]
    assert wire.events == [("hook", 1), "request", ("hook", 2), ("sleep", 10), "request"]
    assert [json.loads(r.data)["model"] for r in wire.requests] == [OPENROUTER_MODEL, OPENROUTER_MODEL]
    accounted = caught.value.attempt
    assert accounted["attempts"] == 2 and accounted["requested_model"] == OPENROUTER_MODEL
    assert "fallback_used" not in accounted and "primary_failure" not in accounted
    assert "grammar_retry" not in accounted
    assert accounted["unavailable_retry"]["outcome"] == "failed"


# --- accounting first: no record, no retry ----------------------------------------------------------------------

def test_a_retry_that_cannot_be_accounted_is_never_sent_and_never_waited_for(monkeypatch):
    def recorder(described):
        if described["attempts"] == 2:
            raise ValueError("the paid synthesis attempt could not be recorded; no provider request was sent")
    wire = Wire(monkeypatch, [unavailable], hook=recorder)
    with pytest.raises(ValueError, match="could not be recorded") as caught:
        wire.synthesize()
    assert len(wire.requests) == 1 and wire.slept == []
    assert wire.events == [("hook", 1), "request", ("hook", 2)]
    assert caught.value.attempt["attempts"] == 1
    assert caught.value.attempt["unavailable_retry"]["outcome"] == "not sent"


def test_without_an_accounting_hook_the_exact_503_fails_exactly_as_before(monkeypatch):
    wire = Wire(monkeypatch, [unavailable])
    with pytest.raises(ValueError) as caught:
        wire.synthesize(hooked=False)
    assert len(wire.requests) == 1 and wire.slept == []
    diagnostic = canonical({"http_status": 503, "error": SANITIZED})
    assert str(caught.value) == ("OpenRouter transport failure; no automatic paid retry; "
                                 f"cause=OpenRouter transient HTTP 503; diagnostic={diagnostic}")
    assert "unavailable_retry" not in caught.value.attempt


# --- everything else keeps its one request ----------------------------------------------------------------------

NEAR_MISSES = {
    "trailing period": unavailable_body(UNAVAILABLE + "."),
    "padded": unavailable_body(" " + UNAVAILABLE + " "),
    "double space": unavailable_body(UNAVAILABLE.replace(" ", "  ", 1)),
    "tab": unavailable_body(UNAVAILABLE.replace(" ", "\t", 1)),
    "zero-width space": unavailable_body(UNAVAILABLE.replace(" ", " ​", 1)),
    "lowercase lead": unavailable_body(UNAVAILABLE.lower()),
    "overloaded": unavailable_body("Overloaded"),
    "prefixed": unavailable_body("Error: " + UNAVAILABLE),
    "other provider": unavailable_body(provider="Claude Platform on AWS"),
    "provider case": unavailable_body(provider="anthropic"),
    "provider with lone surrogate": unavailable_body(provider="Anthropic\ud800"),
    "provider padded": unavailable_body(provider="Anthropic "),
    "other OpenRouter message": unavailable_body(message="Provider returned error: unavailable"),
    "OpenRouter message case": unavailable_body(message="provider returned error"),
    "string code": unavailable_body(code="503"),
    "float code": unavailable_body(code=503.0),
    "boolean code": unavailable_body(code=True),
    "502 code in body": unavailable_body(code=502),
    "raw not JSON": unavailable_body(raw=UNAVAILABLE),
    "raw message not a string": unavailable_body(raw=json.dumps({"error": {"message": [UNAVAILABLE]}})),
    "no raw": json.dumps({"error": {"code": 503, "message": "Provider returned error",
                                    "metadata": {"provider_name": "Anthropic"}}}).encode(),
    "no metadata": json.dumps({"error": {"code": 503, "message": "Provider returned error"}}).encode(),
    "text outside the provider message": json.dumps({"error": {"code": 503, "message": UNAVAILABLE}}).encode(),
    "OpenRouter's own 503": json.dumps({"error": {"code": 503, "message": "No available providers"}}).encode(),
    "empty object": b"{}",
    "unreadable": b"<html>" + UNAVAILABLE.encode(),
    "empty": b" ",
}
missing_code = json.loads(unavailable_body())
del missing_code["error"]["code"]
NEAR_MISSES["missing code"] = json.dumps(missing_code).encode()


@pytest.mark.parametrize("body", NEAR_MISSES.values(), ids=NEAR_MISSES.keys())
def test_an_unrecognized_or_near_503_is_one_request_and_no_wait(monkeypatch, body):
    wire = Wire(monkeypatch, [lambda request: unavailable(request, body)])
    with pytest.raises(ValueError, match="OpenRouter transport failure; no automatic paid retry; "
                                         "cause=OpenRouter transient HTTP 503") as caught:
        wire.synthesize()
    assert len(wire.requests) == 1 and wire.slept == [] and wire.events == [("hook", 1), "request"]
    assert caught.value.attempt["attempts"] == 1 and "unavailable_retry" not in caught.value.attempt


@pytest.mark.parametrize(("failure", "expected"), [
    # The exact 503 body under any other status is that status's failure, unchanged.
    *[(lambda request, status=status: unavailable(request, status=status), "no automatic paid retry")
      for status in (408, 429, 500, 502, 504)],
    (lambda request: unavailable(request, status=400), "OpenRouter HTTP 400"),
    (lambda request: unavailable(request, status=401), "authentication failed"),
    (lambda request: TimeoutError(), "no automatic paid retry"),
    (lambda request: URLError("connection reset"), "no automatic paid retry"),
    (lambda request: OSError("network unreachable"), "no automatic paid retry"),
], ids=["408", "429", "500", "502", "504", "400", "401", "timeout", "urlerror", "oserror"])
def test_other_statuses_and_transport_failures_are_still_never_retried(monkeypatch, failure, expected):
    wire = Wire(monkeypatch, [failure])
    with pytest.raises(ValueError, match=expected) as caught:
        wire.synthesize()
    assert len(wire.requests) == 1 and wire.slept == [] and ("hook", 2) not in wire.events
    assert "unavailable_retry" not in getattr(caught.value, "attempt", {})


def test_the_diagnostic_sanitizer_is_unchanged_for_the_exact_503():
    error = _safe_error(HTTPError("u", 503, "x", {}, io.BytesIO(unavailable_body())))
    assert error == SANITIZED


# --- the grammar retry and this one share a two-request budget --------------------------------------------------

def test_a_503_after_the_grammar_retry_is_the_last_request(monkeypatch):
    wire = Wire(monkeypatch, [rate_limited, unavailable])
    with pytest.raises(ValueError, match="no automatic paid retry") as caught:
        wire.synthesize()
    assert len(wire.requests) == 2 and wire.slept == [60]
    assert wire.events == [("hook", 1), "request", ("hook", 2), ("sleep", 60), "request"]
    accounted = caught.value.attempt
    assert accounted["attempts"] == 2 and accounted["grammar_retry"]["outcome"] == "failed"
    assert accounted["grammar_retry"]["first"]["error"]["metadata"]["provider_message"] == EXACT
    assert "unavailable_retry" not in accounted


def test_a_503_on_the_model_fallback_is_not_retried(monkeypatch):
    """The no-endpoints fallback already spent the second request: the exact 503 there fails closed."""
    wire = Wire(monkeypatch, [lambda request: HTTPError(request.full_url, 404, "error", {}, io.BytesIO(NO_ENDPOINT)),
                              unavailable])
    with pytest.raises(ValueError, match="no automatic paid retry") as caught:
        wire.synthesize()
    assert [json.loads(r.data)["model"] for r in wire.requests] == [OPENROUTER_MODEL, OPENROUTER_FALLBACK_MODEL]
    assert wire.slept == [] and ("hook", 2) not in wire.events
    assert "unavailable_retry" not in caught.value.attempt and "grammar_retry" not in caught.value.attempt


def test_the_grammar_retry_still_answers_only_its_exact_400_with_its_own_minute(monkeypatch):
    wire = Wire(monkeypatch, [rate_limited, lambda request: accepted()])
    _, model = wire.synthesize()
    assert len(wire.requests) == 2 and wire.slept == [60]
    assert model["grammar_retry"]["outcome"] == "response" and "unavailable_retry" not in model


# --- the scheduled day: PREMARKET retried and accepted, OPEN_1M refreshes under it -------------------------------

class UnavailableActions(PaidActions):
    """`PaidActions`, with the first request of the first synthesis answered by the exact 503 and the real production
    sleeper recording its wait instead of taking it."""

    def __init__(self, monkeypatch, tmp_path, first=unavailable):
        super().__init__(monkeypatch, tmp_path)
        self.bodies, self.slept, self.first = [], [], first
        monkeypatch.setattr("market_brief.synthesize.sleep", self.slept.append)

    def openrouter(self, request, timeout):
        self.bodies.append(request.data)
        if len(self.bodies) == 1:
            payload = json.loads(request.data)
            self.requests.append(json.loads(payload["messages"][1]["content"])["run"]["checkpoint"])
            raise self.first(request)
        return super().openrouter(request, timeout)


def test_a_premarket_503_is_retried_accepted_and_open_1m_refreshes_under_it_without_a_call(monkeypatch, tmp_path):
    day = UnavailableActions(monkeypatch, tmp_path)
    premarket = day.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)
    metadata = day.metadata(premarket, "PREMARKET")
    assert metadata["validation"] == "PASS" and metadata["synthesis"] == dict(kind="synthesis", calls=2)
    assert metadata["model"]["attempts"] == 2 and metadata["model"]["unavailable_retry"]["outcome"] == "response"
    assert "grammar_retry" not in metadata["model"]
    assert day.slept == [10] and day.requests == ["PREMARKET", "PREMARKET"] and day.bodies[0] == day.bodies[1]
    record = record_of(day, premarket)
    assert record["attempts"] == 2 and record["unavailable_retry"]["outcome"] == "pending"
    assert record["unavailable_retry"]["first"]["error"] == SANITIZED and "grammar_retry" not in record
    accepted_state = day.bundle(premarket)
    assert accepted_state["interpretation"]

    opening = day.run(f"{TUE}T13:31:00+00:00", "OPEN_1M")
    refreshed = day.metadata(opening, "OPEN_1M")
    assert refreshed["validation"] == "PASS" and refreshed["synthesis"]["calls"] == 0
    assert refreshed["model_route"] == "none" and "attempt_record" not in refreshed
    assert day.bundle(opening)["interpretation"] == accepted_state["interpretation"]
    # Nothing beyond PREMARKET's two requests, and no wait but its one.
    assert day.requests == ["PREMARKET", "PREMARKET"] and len(day.bodies) == 2 and day.slept == [10]
    assert day.published == ["PREMARKET", "OPEN_1M"]


def test_an_unrecognized_premarket_503_is_still_one_request_and_publishes_nothing(monkeypatch, tmp_path):
    day = UnavailableActions(monkeypatch, tmp_path,
                             first=lambda request: unavailable(request, unavailable_body("Overloaded")))
    premarket = day.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)
    metadata = day.metadata(premarket, "PREMARKET")
    assert metadata["validation"] == "FAILED" and metadata["synthesis"]["calls"] == 1
    assert day.requests == ["PREMARKET"] and day.slept == [] and day.published == []
    record = record_of(day, premarket)
    assert "attempts" not in record and "unavailable_retry" not in record  # the original record shape

"""One delayed retry for Anthropic's grammar-compilation rate limit, relayed by OpenRouter as an HTTP 400.

Anthropic refuses to compile the strict-output grammar when the organization has compiled fifty in a minute; nothing
is generated. That one diagnostic, matched exactly, earns one retry of the identical request after a sixty-second
wait, accounted durably before it is sent: at most two provider requests per synthesis, no model fallback. Every
other failure keeps its existing single-request path. All offline: the transport and the sleeper are fakes.
"""

import io
import json
import os
from urllib.error import HTTPError, URLError

import pytest
from test_cadence import TUE
from test_pipeline import fixture_packet, narrative
from test_synthesis_attempts import COST, PaidActions, Served, attempt

from market_brief import cli
from market_brief.synthesize import (
    OPENROUTER_FALLBACK_MODEL,
    OPENROUTER_MODEL,
    NarrativeRejected,
    synthesize_openrouter,
)

ORGANIZATION = "3f2a9c4e-1b7d-4e8a-9c2f-5d6e7a8b9c0d"
EXACT = (f"Grammar compilation rate limit exceeded. Organization {ORGANIZATION} exceeded limit: "
         "50 compilations per minute. You can try again later.")


def provider_body(provider_message=EXACT, provider="Anthropic", code=400, message="Provider returned error"):
    """OpenRouter's relay of a provider error, its `raw` carrying the provider's own JSON error."""
    raw = json.dumps({"type": "error", "error": {"type": "rate_limit_error", "message": provider_message}})
    return json.dumps({"error": {"code": code, "message": message,
                                 "metadata": {"provider_name": provider, "raw": raw}},
                       "user_id": "PRIVATE_USER_SENTINEL"}).encode()


def rate_limited(request, body=None, status=400):
    return HTTPError(request.full_url, status, "error", {}, io.BytesIO(body or provider_body()))


def accepted(value=None):
    return Served({"id": "gen-2", "model": OPENROUTER_MODEL, "provider": "Anthropic", "usage": COST,
                   "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value or narrative())}}]})


class Wire:
    """The real `_openrouter_post` over a fake `urlopen` that answers from `answers` in order, with the hook, the
    sleeper and the requests recorded on one timeline."""

    def __init__(self, monkeypatch, answers, hook=None):
        self.answers, self.requests, self.events, self.slept = list(answers), [], [], []
        self.hook = hook
        monkeypatch.setattr("market_brief.synthesize.urlopen", self.urlopen)

    def urlopen(self, request, timeout):
        self.requests.append(request)
        self.events.append("request")
        answer = self.answers.pop(0)(request)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def on_request(self, described):
        self.events.append(("hook", described["attempts"]))
        if self.hook:
            self.hook(described)

    def sleeper(self, seconds):
        self.events.append(("sleep", seconds))
        self.slept.append(seconds)

    def synthesize(self, hooked=True):
        return synthesize_openrouter(fixture_packet(), api_key="fake", sleeper=self.sleeper,
                                     on_request=self.on_request if hooked else None)


# --- the exact diagnostic: one retry, sixty seconds later, of the identical request ------------------------------

def test_the_exact_diagnostic_is_retried_once_after_sixty_seconds_with_the_identical_request(monkeypatch):
    wire = Wire(monkeypatch, [rate_limited, lambda request: accepted()])
    output, model = wire.synthesize()
    assert output["mode"] == "SAMPLE"
    # Accounted before each request; the retry's accounting precedes the wait, the wait precedes the request.
    assert wire.events == [("hook", 1), "request", ("hook", 2), ("sleep", 60), "request"]
    assert wire.slept == [60]
    first, second = wire.requests
    assert first.data == second.data  # byte for byte: the same model, prompt, schema and route
    assert json.loads(second.data)["model"] == OPENROUTER_MODEL
    assert first.full_url == second.full_url and first.headers == second.headers
    assert model["attempts"] == 2 and model["fallback_used"] is False and model["primary_failure"] is None
    assert model["requested_model"] == OPENROUTER_MODEL and model["usage"] == COST
    retry = model["grammar_retry"]
    assert retry["delay_seconds"] == 60 and retry["model"] == OPENROUTER_MODEL and retry["outcome"] == "response"
    assert retry["first"] == dict(model=OPENROUTER_MODEL, status=400, error={
        "code": 400, "message": "Provider returned error",
        "metadata": {"provider_name": "Anthropic", "provider_message": EXACT}})
    assert "PRIVATE_USER_SENTINEL" not in json.dumps(model)


def test_the_retry_hook_describes_the_first_diagnostic_and_the_second_requests_intent(monkeypatch):
    described = []
    wire = Wire(monkeypatch, [rate_limited, lambda request: accepted()], hook=described.append)
    wire.synthesize()
    assert [d["attempts"] for d in described] == [1, 2]
    assert "grammar_retry" not in described[0]
    retry = described[1]["grammar_retry"]
    assert retry["outcome"] == "pending" and retry["delay_seconds"] == 60 and retry["model"] == OPENROUTER_MODEL
    assert retry["first"]["error"]["metadata"]["provider_message"] == EXACT


def test_an_uppercase_organization_id_is_the_same_diagnostic(monkeypatch):
    body = provider_body(EXACT.replace(ORGANIZATION, ORGANIZATION.upper()))
    wire = Wire(monkeypatch, [lambda request: rate_limited(request, body), lambda request: accepted()])
    _, model = wire.synthesize()
    assert len(wire.requests) == 2 and wire.slept == [60] and model["attempts"] == 2


# --- the retry is the last request ------------------------------------------------------------------------------

@pytest.mark.parametrize(("second", "expected"), [
    (rate_limited, "OpenRouter HTTP 400"),
    (lambda request: HTTPError(request.full_url, 404, "error", {}, io.BytesIO(json.dumps(
        {"error": {"code": 404, "message": f"No endpoints found for {OPENROUTER_MODEL}"}}).encode())),
     "OpenRouter HTTP 404"),  # never a model fallback after the retry
    (lambda request: TimeoutError(), "no automatic paid retry"),
    (lambda request: HTTPError(request.full_url, 503, "error", {}, io.BytesIO(b"{}")), "no automatic paid retry"),
    (lambda request: HTTPError(request.full_url, 400, "error", {}, io.BytesIO(b"{}")), "OpenRouter HTTP 400"),
], ids=["rate-limited-again", "no-endpoints", "timeout", "503", "other-400"])
def test_a_failed_retry_stops_at_two_requests_with_both_accounted(monkeypatch, second, expected):
    wire = Wire(monkeypatch, [rate_limited, second])
    with pytest.raises(ValueError, match=expected) as caught:
        wire.synthesize()
    assert len(wire.requests) == 2 and wire.slept == [60]
    assert [json.loads(r.data)["model"] for r in wire.requests] == [OPENROUTER_MODEL, OPENROUTER_MODEL]
    accounted = caught.value.attempt
    assert accounted["attempts"] == 2 and accounted["requested_model"] == OPENROUTER_MODEL
    assert "fallback_used" not in accounted and "primary_failure" not in accounted
    assert accounted["grammar_retry"]["outcome"] == "failed"
    assert accounted["grammar_retry"]["first"]["error"]["metadata"]["provider_message"] == EXACT


def test_an_invalid_second_narrative_is_rejected_and_accounted_as_two_requests(monkeypatch):
    invalid = narrative()
    del invalid["banner"]["limitation"]
    wire = Wire(monkeypatch, [rate_limited, lambda request: accepted(invalid)])
    with pytest.raises(NarrativeRejected, match="malformed narrative") as caught:
        wire.synthesize()
    assert len(wire.requests) == 2 and wire.slept == [60]
    accounted = caught.value.attempt
    assert accounted["attempts"] == 2 and accounted["usage"] == COST and accounted["response_id"] == "gen-2"
    assert accounted["grammar_retry"]["outcome"] == "response"


def test_a_rate_limited_fallback_is_not_retried(monkeypatch):
    """The no-endpoints fallback already spent the second request: a rate limit there fails closed."""
    no_endpoint = json.dumps({"error": {"code": 404, "message": f"No endpoints found for {OPENROUTER_MODEL}"}})
    wire = Wire(monkeypatch, [lambda request: HTTPError(request.full_url, 404, "error", {},
                                                        io.BytesIO(no_endpoint.encode())), rate_limited])
    with pytest.raises(ValueError, match="OpenRouter HTTP 400") as caught:
        wire.synthesize()
    assert [json.loads(r.data)["model"] for r in wire.requests] == [OPENROUTER_MODEL, OPENROUTER_FALLBACK_MODEL]
    assert wire.slept == [] and wire.events.count(("hook", 1)) == 1 and ("hook", 2) not in wire.events
    assert "grammar_retry" not in caught.value.attempt


# --- accounting first: no record, no retry ----------------------------------------------------------------------

def test_a_retry_that_cannot_be_accounted_is_never_sent(monkeypatch):
    def recorder(described):
        if described["attempts"] == 2:
            raise ValueError("the paid synthesis attempt could not be recorded; no provider request was sent")
    wire = Wire(monkeypatch, [rate_limited], hook=recorder)
    with pytest.raises(ValueError, match="could not be recorded") as caught:
        wire.synthesize()
    assert len(wire.requests) == 1 and wire.slept == []
    assert wire.events == [("hook", 1), "request", ("hook", 2)]
    assert caught.value.attempt["attempts"] == 1 and caught.value.attempt["grammar_retry"]["outcome"] == "not sent"


def test_without_an_accounting_hook_the_diagnostic_fails_closed_as_before(monkeypatch):
    wire = Wire(monkeypatch, [rate_limited])
    with pytest.raises(ValueError, match="OpenRouter HTTP 400") as caught:
        wire.synthesize(hooked=False)
    assert len(wire.requests) == 1 and wire.slept == []
    assert EXACT in str(caught.value) and "grammar_retry" not in caught.value.attempt


# --- everything else keeps its one request ----------------------------------------------------------------------

NEAR_MISSES = {
    "49 per minute": provider_body(EXACT.replace("50 compilations", "49 compilations")),
    "500 per minute": provider_body(EXACT.replace("50 compilations", "500 compilations")),
    "per hour": provider_body(EXACT.replace("per minute", "per hour")),
    "prefixed": provider_body("Error: " + EXACT),
    "suffixed": provider_body(EXACT + " Retry-After: 60"),
    "no final period": provider_body(EXACT[:-1]),
    "lowercase lead": provider_body(EXACT[0].lower() + EXACT[1:]),
    "short organization": provider_body(EXACT.replace(ORGANIZATION, ORGANIZATION[:-1])),
    "named organization": provider_body(EXACT.replace(ORGANIZATION, "acme-trading")),
    "message only": provider_body("Grammar compilation rate limit exceeded."),
    "other provider": provider_body(provider="Claude Platform on AWS"),
    "provider case": provider_body(provider="anthropic"),
    "other OpenRouter message": provider_body(message="Provider returned error: rate limited"),
    "string code": provider_body(code="400"),
    "429 code in body": provider_body(code=429),
    "grammar too large": provider_body("The compiled grammar is too large, which would cause performance issues."),
    "no provider body": json.dumps({"error": {"code": 400, "message": "Provider returned error",
                                              "metadata": {"provider_name": "Anthropic"}}}).encode(),
    "exact text outside the provider message": json.dumps({"error": {"code": 400, "message": EXACT}}).encode(),
    "unreadable": b"<html>" + EXACT.encode(),
}


@pytest.mark.parametrize("body", NEAR_MISSES.values(), ids=NEAR_MISSES.keys())
def test_an_unrelated_or_near_400_is_one_request_and_no_wait(monkeypatch, body):
    wire = Wire(monkeypatch, [lambda request: rate_limited(request, body)])
    with pytest.raises(ValueError, match="OpenRouter HTTP 400") as caught:
        wire.synthesize()
    assert len(wire.requests) == 1 and wire.slept == [] and wire.events == [("hook", 1), "request"]
    assert caught.value.attempt["attempts"] == 1 and "grammar_retry" not in caught.value.attempt


@pytest.mark.parametrize(("failure", "expected"), [
    (lambda request: TimeoutError(), "no automatic paid retry"),
    (lambda request: URLError("connection reset"), "no automatic paid retry"),
    (lambda request: OSError("network unreachable"), "no automatic paid retry"),
    # The exact rate-limit body under any status but 400 is that status's failure, unchanged.
    *[(lambda request, status=status: rate_limited(request, status=status), "no automatic paid retry")
      for status in (408, 429, 500, 502, 503, 504)],
    (lambda request: rate_limited(request, status=401), "authentication failed"),
    (lambda request: rate_limited(request, status=403), "authentication failed"),
    (lambda request: rate_limited(request, status=422), "OpenRouter HTTP 422"),
    (lambda request: Served(["not", "an", "object"]), "malformed response"),
], ids=["timeout", "urlerror", "oserror", "408", "429", "500", "502", "503", "504", "401", "403", "422",
        "malformed-200"])
def test_transport_auth_transient_and_malformed_failures_are_still_never_retried(monkeypatch, failure, expected):
    wire = Wire(monkeypatch, [failure])
    with pytest.raises(ValueError, match=expected):
        wire.synthesize()
    assert len(wire.requests) == 1 and wire.slept == [] and ("hook", 2) not in wire.events


# --- the scheduled run: accounted, guarded, and continuity/publication unchanged --------------------------------

class GrammarActions(PaidActions):
    """`PaidActions`, with the first request of each synthesis answered by the exact rate-limit 400 and the real
    production sleeper recording its wait instead of taking it."""

    def __init__(self, monkeypatch, tmp_path):
        super().__init__(monkeypatch, tmp_path)
        self.bodies, self.slept, self.limited = [], [], True
        monkeypatch.setattr("market_brief.synthesize.sleep", self.slept.append)

    def openrouter(self, request, timeout):
        self.bodies.append(request.data)
        if self.limited and len(self.bodies) % 2 == 1:
            payload = json.loads(request.data)
            self.requests.append(json.loads(payload["messages"][1]["content"])["run"]["checkpoint"])
            raise rate_limited(request)
        return super().openrouter(request, timeout)


@pytest.fixture
def grammar(monkeypatch, tmp_path):
    return GrammarActions(monkeypatch, tmp_path)


def record_of(actions, run_id, session=TUE, checkpoint="PREMARKET"):
    return json.loads((actions.days[run_id].root / f"runs/attempts/{session}-{checkpoint}.json").read_text())


def test_a_scheduled_retry_publishes_once_and_the_checkpoint_stays_spent(grammar, capsys):
    run_id = grammar.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)
    metadata = grammar.metadata(run_id, "PREMARKET")
    assert metadata["validation"] == "PASS" and metadata["synthesis"] == dict(kind="synthesis", calls=2)
    assert metadata["model"]["attempts"] == 2 and metadata["model"]["grammar_retry"]["outcome"] == "response"
    assert grammar.slept == [60] and grammar.requests == ["PREMARKET", "PREMARKET"]
    assert grammar.bodies[0] == grammar.bodies[1]
    assert grammar.published == ["PREMARKET"]
    record = record_of(grammar, run_id)
    assert record["attempts"] == 2 and record["grammar_retry"]["outcome"] == "pending"
    assert record["grammar_retry"]["first"]["error"]["metadata"]["provider_message"] == EXACT
    capsys.readouterr()
    # A fresh runner inside the window finds the record and sends nothing.
    grammar.run(f"{TUE}T13:11:00+00:00", "PREMARKET", intraday=False)
    assert "SKIP / PREMARKET / already completed" in capsys.readouterr().out
    assert len(grammar.bodies) == 2 and grammar.slept == [60]


def test_a_rejected_retry_publishes_nothing_advances_nothing_and_is_never_paid_again(grammar, capsys):
    run_id = grammar.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False, verdict="reject")
    assert grammar.runs[run_id]["conclusion"] == "failure" and grammar.published == []
    root = grammar.days[run_id].root
    assert not (root / "continuity").exists() or not any((root / "continuity").iterdir())
    folder = grammar.days[run_id].folder("PREMARKET")
    assert not (folder / "narrative.json").exists() and not (folder / "edition_state.json").exists()
    metadata = json.loads((folder / "metadata.json").read_text())
    assert metadata["validation"] == "FAILED" and metadata["synthesis"]["calls"] == 2
    assert "unsupplied evidence reference" in metadata["error"]
    assert metadata["model"]["attempts"] == 2 and metadata["model"]["usage"] == COST
    assert metadata["model"]["grammar_retry"]["outcome"] == "response"
    assert record_of(grammar, run_id)["attempts"] == 2
    assert attempt(TUE, "PREMARKET") in grammar.names(run_id)
    capsys.readouterr()
    second = grammar.run(f"{TUE}T13:11:00+00:00", "PREMARKET", intraday=False)
    assert "SKIP / PREMARKET / paid synthesis already attempted" in capsys.readouterr().out
    assert len(grammar.bodies) == 2 and grammar.slept == [60] and not grammar.days[second].attempts("PREMARKET")


def test_a_rejected_opening_retry_keeps_the_premarket_interpretation(grammar):
    grammar.limited = False
    premarket = grammar.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)
    accepted_state = grammar.bundle(premarket)
    grammar.limited, grammar.bodies = True, []
    rejected = grammar.run(f"{TUE}T14:01:00+00:00", "OPEN_30M", verdict="reject")
    metadata = grammar.metadata(rejected, "OPEN_30M")
    assert metadata["validation"] == "DEGRADED" and metadata["synthesis"]["calls"] == 2
    assert grammar.bundle(rejected)["interpretation"] == accepted_state["interpretation"]
    assert len(grammar.bodies) == 2 and grammar.slept == [60]


def test_a_retry_record_that_cannot_be_written_sends_no_second_request(grammar, monkeypatch, capsys):
    real = cli.record_attempt

    def failing_second(root, session_date, checkpoint, run_id, described):
        if described.get("attempts") == 2:
            # A genuine refusal by the real writer: the record is now a symlink (to a copy, so it still counts).
            path = cli.attempt_path(root, session_date, checkpoint)
            copy = path.with_suffix(".copy")
            copy.write_bytes(path.read_bytes())
            path.unlink()
            os.symlink(copy, path)
        return real(root, session_date, checkpoint, run_id, described)
    monkeypatch.setattr(cli, "record_attempt", failing_second)
    run_id = grammar.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)
    assert len(grammar.bodies) == 1 and grammar.slept == [] and grammar.published == []
    metadata = grammar.metadata(run_id, "PREMARKET")
    assert metadata["validation"] == "FAILED" and "cannot be a symlink" in metadata["error"]
    assert metadata["model"]["attempts"] == 1 and metadata["model"]["grammar_retry"]["outcome"] == "not sent"
    capsys.readouterr()
    grammar.run(f"{TUE}T13:11:00+00:00", "PREMARKET", intraday=False)
    assert "SKIP / PREMARKET / paid synthesis already attempted" in capsys.readouterr().out
    assert len(grammar.bodies) == 1



def test_an_interrupted_wait_leaves_the_second_request_accounted_and_it_is_never_paid_again(grammar, monkeypatch,
                                                                                            capsys):
    """The runner is cancelled during the sixty-second wait, after the second request's intent was recorded: only
    the first request was sent, the record already counts two, and the workflow's always() upload of it stops any
    later run from synthesizing the checkpoint."""
    def interrupted(seconds):
        grammar.slept.append(seconds)
        raise KeyboardInterrupt
    monkeypatch.setattr("market_brief.synthesize.sleep", interrupted)
    with pytest.raises(KeyboardInterrupt):
        grammar.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)
    run_id = max(grammar.runs)
    assert len(grammar.bodies) == 1 and grammar.requests == ["PREMARKET"] and grammar.slept == [60]
    assert grammar.published == []
    record = record_of(grammar, run_id)
    assert record["attempts"] == 2 and record["grammar_retry"]["outcome"] == "pending"
    assert record["grammar_retry"]["first"]["error"]["metadata"]["provider_message"] == EXACT
    # The cancelled run's always() step still uploads its attempt record.
    root = grammar.days[run_id].root
    for path in sorted((root / "runs/attempts").glob("*.json")):
        grammar.upload(run_id, f"{cli.ATTEMPT_ARTIFACT}-{path.stem}", f"{TUE}T13:01:00+00:00", 5, path.read_bytes())
    grammar.runs[run_id].update(status="completed", conclusion="cancelled")
    monkeypatch.setattr("market_brief.synthesize.sleep", grammar.slept.append)
    capsys.readouterr()
    second = grammar.run(f"{TUE}T13:11:00+00:00", "PREMARKET", intraday=False)
    assert "SKIP / PREMARKET / paid synthesis already attempted" in capsys.readouterr().out
    assert len(grammar.bodies) == 1 and grammar.slept == [60] and grammar.published == []
    assert not grammar.days[second].attempts("PREMARKET")

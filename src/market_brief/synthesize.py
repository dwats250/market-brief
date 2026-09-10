"""One Claude CLI route and bounded, mechanically grounded narrative validation."""

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from jsonschema import Draft202012Validator

from .context import analyst_context, edition_profile, supplied_ids
from .continuity import ASSESSMENTS, CARRIED_ASSESSMENTS, prior_values, validate_state
from .evidence import ROOT, canonical, compact_model_record, digest, evidence_catalog, model_packet, read_json


def text_field(limit, optional=False):
    return {"type": "string", "minLength": 0 if optional else 1, "maxLength": limit}


TEXT = text_field(360)
IDENTIFIER = text_field(96)
# One claim of roughly eight to twelve words; the bound is a backstop, not the target.
HEADLINE = {"type": "string", "minLength": 1, "maxLength": 160}
REFS = {"type": "array", "items": IDENTIFIER, "minItems": 1,
        "maxItems": 4, "uniqueItems": True}
HORIZONS = ["OPENING_HOUR", "SESSION", "NEXT_CLOSE", "NEXT_BRIEF"]
EVENT_HORIZON = re.compile(r"^EVENT\([a-zA-Z][\w-]{0,79}\)$")
ALLOWED_LABELS = re.compile(r"\b(?:2Y|5Y|10Y|30Y|5-session|20-session|50-day|50-session)\b")
TRADE_LANGUAGE = re.compile(r"\b(entry|target|sizing|buy|sell|execute|execution|order)\b", re.I)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "anthropic/claude-fable-5.1"
TRANSIENT_OPENROUTER_STATUS = {408, 429, 500, 502, 503, 504}


def obj(properties):
    return dict(type="object", properties=properties, required=list(properties),
                additionalProperties=False)


PARAGRAPH = obj({"text": TEXT, "class": {"enum": ["OBSERVED", "INTERPRETATION"]},
                 "evidence_ids": REFS, "uncertainty": text_field(80, optional=True),
                 "alternative": text_field(100, optional=True)})
SECTION_PARAGRAPH = obj(dict(PARAGRAPH["properties"], text=text_field(240)))
NARRATIVE_SCHEMA = obj({
    "schema_version": {"const": "market-brief.narrative.v1"},
    "mode": {"enum": ["LIVE", "SAMPLE"]},
    "banner": obj({"title": HEADLINE, "label": {"enum": ["RISK-ON", "RISK-OFF", "MIXED", "INDETERMINATE"]},
                   "class": {"const": "INTERPRETATION"}, "evidence_ids": REFS,
                   "limitation": text_field(200)}),
    "summary": {"type": "array", "items": PARAGRAPH, "minItems": 1, "maxItems": 2},
    "sections": obj({k: {"type": "array", "items": SECTION_PARAGRAPH,
                         "maxItems": 0 if k == "cuttingboard" else 1}
                     for k in ("macro", "equities", "attention", "cuttingboard", "events")}),
    "attention_ids": {"type": "array", "items": IDENTIFIER,
                      "maxItems": 3, "uniqueItems": True},
    "attention": {"type": "array", "maxItems": 3, "items": obj({
        "id": IDENTIFIER, "why": text_field(120)})},
    "watches": {"type": "array", "minItems": 1, "maxItems": 3, "items": obj({
        "class": {"const": "WATCH"}, "condition": text_field(140), "confirmation": text_field(100),
        "contradiction": text_field(100), "horizon": {"oneOf": [{"enum": HORIZONS},
            {"type": "string", "maxLength": 87, "pattern": EVENT_HORIZON.pattern}]}, "evidence_ids": REFS})},
    # Continuity records. The analyst assesses; deterministic code owns every persistent ID.
    "character": obj({"text": text_field(180), "evidence_ids": REFS}),
    "relationships": {"type": "array", "maxItems": 3, "items": obj({
        "carried_id": dict(IDENTIFIER, type=["string", "null"]),
        "instruments": {"type": "array", "items": text_field(40), "minItems": 1, "maxItems": 4,
                        "uniqueItems": True},
        "statement": text_field(140), "assessment": {"enum": list(ASSESSMENTS)},
        "reason": text_field(100, optional=True), "evidence_ids": REFS})},
    "watch_updates": {"type": "array", "maxItems": 3, "items": obj({
        "carried_id": IDENTIFIER, "assessment": {"enum": list(CARRIED_ASSESSMENTS)},
        "reason": text_field(120), "evidence_ids": REFS})},
    "changes": {"type": "array", "maxItems": 3, "items": obj({
        "comparison_id": IDENTIFIER, "text": text_field(140), "evidence_ids": REFS})},
})
TOKEN = re.compile(r"\{\{([a-zA-Z][\w-]*(?::[a-zA-Z][\w-]*)?)\}\}")


def narrative_schema(profile=None):
    """The one contract, with the edition's smaller bounds applied for light checkpoints."""
    if not profile:
        return NARRATIVE_SCHEMA
    schema = json.loads(json.dumps(NARRATIVE_SCHEMA))
    schema["properties"]["summary"]["maxItems"] = profile["summary_paragraphs"]
    schema["properties"]["attention_ids"]["maxItems"] = profile["attention_items"]
    schema["properties"]["attention"]["maxItems"] = profile["attention_items"]
    schema["properties"]["watches"]["maxItems"] = profile["watches"]
    if profile["profile"] == "light":
        # All carried watches can still be assessed; select fewer relationships/changes.
        for key in ("relationships", "changes"):
            schema["properties"][key]["maxItems"] = 2
        schema["properties"]["summary"]["items"]["properties"]["text"] = text_field(300)
        watch = schema["properties"]["watches"]["items"]["properties"]
        watch.update(condition=text_field(130), confirmation=text_field(80), contradiction=text_field(80))
        schema["properties"]["watch_updates"]["items"]["properties"]["reason"] = text_field(100)
        schema["properties"]["character"]["properties"]["text"] = text_field(140)
    return schema


def compact_json(value):
    """Wire serialization only; do not change canonical evidence/continuity hashes."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def compact_schema(schema):
    """Share repeated definitions; the context and transport still carry the SAME contract."""
    definitions = {"evidence_refs": REFS, "section_paragraph": SECTION_PARAGRAPH}

    def visit(value, replace=True):
        if replace:
            for name, definition in definitions.items():
                if value == definition:
                    return {"$ref": f"#/$defs/{name}"}
        if isinstance(value, dict):
            return {key: visit(item) for key, item in value.items()}
        if isinstance(value, list):
            return [visit(item) for item in value]
        return value

    result = visit(schema)
    result["$defs"] = {name: visit(value, replace=False) for name, value in definitions.items()}
    return result


def analyst_model(config=None, environ=None):
    """Configured analyst identity: one model for every edition, overridable by environment."""
    environ = os.environ if environ is None else environ
    config = config or read_json(ROOT / "config/editions.json")
    configured = config["analyst"]["model"]
    override = environ.get("MARKET_BRIEF_MODEL")
    return dict(model=override or configured, source="environment" if override else "config/editions.json",
                cli_model=config["analyst"].get("cli_model", "sonnet"))


def validate_narrative(narrative, packet, context=None, schema=None):
    """Mechanical grounding: schema, mode, references that exist in admitted evidence and were
    actually supplied in the analyst context, numeric placeholders, and trade/current-language rules.

    `schema` is the contract actually advertised to the model; by default the edition's profile-bounded one.
    """
    profile = (context or {}).get("edition") or edition_profile(packet["run"]["checkpoint"])
    errors = list(Draft202012Validator(schema or narrative_schema(profile)).iter_errors(narrative))
    if errors:
        raise ValueError("malformed narrative at " + ".".join(map(str, errors[0].absolute_path)))
    if narrative["mode"] != packet["run"]["mode"]:
        raise ValueError("sample/live narrative mode mismatch")
    if ";" in narrative["banner"]["title"]:
        raise ValueError("headline must be one claim without a semicolon")
    catalog = evidence_catalog(model_packet(packet))
    if context is not None:
        if context.get("evidence_hash") != digest(packet):
            raise ValueError("analyst context does not match the evidence record")
        shown = supplied_ids(context)
        catalog = {ident: row for ident, row in catalog.items() if ident in shown}
    # Current-condition records cite current evidence only; continuity records may add prior refs.
    records = [narrative["banner"], *narrative["summary"], *narrative["watches"], narrative["character"]]
    records += [p for section in narrative["sections"].values() for p in section]
    for record in records:
        refs = set(record["evidence_ids"])
        if not refs <= catalog.keys():
            raise ValueError("unknown, unavailable, or unsupplied evidence reference")
    validate_state(narrative, context, set(catalog), {row["topic"] for row in catalog.values() if "topic" in row})
    values = {ident: row for ident, row in catalog.items()}
    values.update(prior_values(context))
    records += [*narrative["relationships"], *narrative["watch_updates"], *narrative["changes"]]
    for record in records:
        refs = set(record["evidence_ids"])
        for key in ("title", "text", "limitation", "uncertainty", "alternative", "condition",
                    "confirmation", "contradiction", "horizon", "statement", "reason"):
            text = record.get(key, "")
            for ident in TOKEN.findall(text):
                if ident not in refs or not isinstance(values.get(ident, {}).get("value"), (int, float)):
                    raise ValueError("numeric placeholder not grounded in cited observation")
            plain = TOKEN.sub("", text)
            plain = ALLOWED_LABELS.sub("", plain)
            if re.search(r"\d|[{}]", plain):
                raise ValueError("literal numeric claim or malformed evidence placeholder")
            if packet["run"]["mode"] == "SAMPLE" and re.search(
                    r"\b(today|now|currently|this morning|live market)\b", plain, re.I):
                raise ValueError("current-language claim in sample narrative")
    if narrative["sections"]["cuttingboard"]:
        raise ValueError("Cuttingboard is quoted only by the deterministic renderer")
    admitted = {a["id"] for a in model_packet(packet)["attention"]}
    if not set(narrative["attention_ids"]) <= admitted:
        raise ValueError("unknown attention trigger")
    attention = narrative.get("attention", [])
    if {item["id"] for item in attention} != set(narrative["attention_ids"]):
        raise ValueError("attention reasons must match selected triggers")
    for item in attention:
        if re.search(r"\d", ALLOWED_LABELS.sub("", item["why"])):
            raise ValueError("literal numeric claim in attention reason")
        if TRADE_LANGUAGE.search(item["why"]):
            raise ValueError("trade language in attention reason")
    for watch in narrative["watches"]:
        if watch["horizon"].startswith("EVENT("):
            event_id = watch["horizon"][6:-1]
            if event_id not in {row["id"] for row in model_packet(packet)["events"]}:
                raise ValueError("watch references unknown event horizon")
        records = [catalog[ident] for ident in watch["evidence_ids"] if ident in catalog]
        if records and all(row.get("magnitude") == "SMALL" for row in records):
            raise ValueError("SMALL observations cannot anchor a watch")
    return narrative


def synthesis_packet(packet):
    """Compatibility name: the bounded projection now lives in `context.analyst_context`."""
    return analyst_context(packet)


def construct_prompt(packet, full=False, context=None, include_schema=True):
    """Default payload is the saved analyst context (bounded projection accepted on 2026-09-08).

    `context` is the exact saved artifact when the caller persisted one; otherwise it is built here.
    `full` reproduces the original evidence-plus-catalog payload for diagnostics.
    OpenRouter retains the factored schema copy: run 34278983083 failed banner schema
    validation with strict response_format but no copy. The isolated CLI supplies its
    contract through --json-schema and opts out of the duplicate user-message schema.
    """
    instructions = (ROOT / "prompts/synthesis.md").read_text()
    profile = edition_profile(packet["run"]["checkpoint"])
    if full:
        model_view = model_packet(packet)
        catalog = {ident: compact_model_record(row) for ident, row in evidence_catalog(model_view).items()}
        projected = dict(evidence=model_view, catalog=catalog)
        limit = 120_000
    else:
        context = context if context is not None else analyst_context(packet, profile)
        projected = dict(context)
        limit = min(120_000, profile["input_limit_bytes"])
    if include_schema:
        schema = NARRATIVE_SCHEMA if full else narrative_schema(context.get("edition") or profile)
        projected["output_schema"] = compact_schema(schema)
    user = compact_json(projected)
    size = len(user.encode())
    sections = {key: len(compact_json(value).encode()) for key, value in projected.items()}
    largest = ", ".join(f"{key}={value}" for key, value in
                         sorted(sections.items(), key=lambda item: item[1], reverse=True)[:3])
    print(f"Synthesis packet: {size} bytes; largest sections: {largest}", flush=True)
    if size > limit:
        # Never truncate: a context that cannot fit its edition budget fails with diagnostics.
        raise ValueError(f"bounded synthesis packet exceeds the {profile['profile']} edition budget: "
                         f"{size} bytes > {limit}; largest sections: {largest}")
    return instructions, user


class _TransientOpenRouterError(ValueError):
    pass


def _openrouter_post(payload, api_key, timeout=180):
    request = Request(OPENROUTER_URL, data=compact_json(payload).encode("utf-8"), headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://dwats250.github.io/market-brief/",
        "X-Title": "Market Brief",
        "X-OpenRouter-Metadata": "enabled",
    }, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(2_000_001)
            result = json.loads(body.decode("utf-8"))
            if isinstance(result, dict):
                result["_market_brief_transport"] = {
                    "http_status": response.status,
                    "response_bytes": len(body),
                }
            return result
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise ValueError("OpenRouter authentication failed") from None
        diagnostic = canonical({"http_status": exc.code, "error": _safe_error(exc)})
        if exc.code in TRANSIENT_OPENROUTER_STATUS:
            raise _TransientOpenRouterError(f"OpenRouter transient HTTP {exc.code}; "
                                            f"diagnostic={diagnostic}") from None
        raise ValueError(f"OpenRouter HTTP {exc.code}; diagnostic={diagnostic}") from None
    except (URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError):
        raise _TransientOpenRouterError("OpenRouter network or response failure") from None


def _safe_error(exc, limit=20_000):
    """Run 34486249474 recorded only `OpenRouter HTTP 400`. Keep the documented error code,
    a bounded message and provider labels; never the raw provider body, headers or IDs."""
    try:
        body = json.loads(exc.read(limit).decode("utf-8"))
        error = body["error"]
        code, message, metadata = error.get("code"), error.get("message"), error.get("metadata")
    except (AttributeError, OSError, UnicodeError, ValueError, KeyError, TypeError):
        return "unknown"
    result = {}
    if type(code) in (int, float):
        result["code"] = code
    if isinstance(message, str):
        result["message"] = message[:300]
    if isinstance(metadata, dict):
        result["metadata"] = {key: metadata[key][:80] for key in ("provider_name", "error_type", "provider_code")
                              if isinstance(metadata.get(key), str)}
    return result or "unknown"


def _safe_usage(response):
    """Keep accounting, including reasoning counts; never infer missing detail as zero."""
    usage = response.get("usage") or {}
    if not isinstance(usage, dict):
        return {}
    result = {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cost")
              if type(usage.get(key)) in (int, float)}
    for group, keys in {
        "completion_tokens_details": ("reasoning_tokens", "text_tokens",
                                      "accepted_prediction_tokens", "rejected_prediction_tokens"),
        "prompt_tokens_details": ("cached_tokens", "cache_write_tokens"),
        "cost_details": ("upstream_inference_cost", "upstream_inference_prompt_cost",
                         "upstream_inference_completions_cost"),
    }.items():
        details = usage.get(group)
        if isinstance(details, dict):
            result[group] = {key: details[key] for key in keys if type(details.get(key)) in (int, float)}
    total = result.get("completion_tokens")
    reasoning = result.get("completion_tokens_details", {}).get("reasoning_tokens")
    if total is not None and reasoning is not None and 0 <= reasoning <= total:
        # An accounting residual, not a tokenizer measurement of message.content.
        result["non_reasoning_completion_tokens"] = total - reasoning
    return result


def _healing_diagnostic(response):
    metadata = response.get("openrouter_metadata") or {}
    pipeline = metadata.get("pipeline") if isinstance(metadata, dict) else None
    if not isinstance(pipeline, list):
        return None
    return [{"type": "response_healing", "data": {
        key: value for key, value in (stage.get("data") or {}).items()
        if key in {"healed", "improved", "original_length", "healed_length", "input_length", "output_length",
                   "original_content_length", "healed_content_length"} and type(value) in (int, float, bool)}}
        for stage in pipeline if isinstance(stage, dict) and stage.get("type") == "response_healing"
        and isinstance(stage.get("data"), dict)]


def _openrouter_diagnostic(response, message=None, content=None):
    choice = (response.get("choices") or [{}])[0] if isinstance(response, dict) else {}
    message = message if isinstance(message, dict) else (choice.get("message") or {})
    if content is None and isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    transport = response.get("_market_brief_transport", {}) if isinstance(response, dict) else {}
    return canonical({
        "http_status": transport.get("http_status", "unknown"),
        "response_bytes": transport.get("response_bytes", "unknown"),
        "response_keys": sorted(key for key in response if not key.startswith("_"))
        if isinstance(response, dict) else [],
        "choice_keys": sorted(choice) if isinstance(choice, dict) else [],
        "message_keys": sorted(message) if isinstance(message, dict) else [],
        "finish_reason": choice.get("finish_reason", "unknown") if isinstance(choice, dict) else "unknown",
        "content_type": type(content).__name__ if content is not None else "missing",
        "content_bytes": len(content.encode("utf-8")) if isinstance(content, str) else 0,
        "usage": _safe_usage(response) if isinstance(response, dict) else {},
        "response_id": response.get("id"),
        "resolved_model": response.get("model", "unknown"),
        "native_finish_reason": choice.get("native_finish_reason", "unknown"),
        "response_healing": _healing_diagnostic(response),
        "provider": response.get("provider", "unknown") if isinstance(response, dict) else "unknown",
        "metadata_keys": sorted(response.get("openrouter_metadata", {}))
        if isinstance(response, dict) and isinstance(response.get("openrouter_metadata"), dict) else [],
    })


def _openrouter_narrative(response):
    if response.get("error"):
        raise ValueError("OpenRouter returned an error")
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("OpenRouter returned no synthesis choice")
    if choices[0].get("finish_reason") == "length":
        diagnostic = _openrouter_diagnostic(response)
        raise ValueError("OpenRouter synthesis output budget exhausted (finish_reason=length); "
                         f"structured output rejected before validation; diagnostic={diagnostic}")
    message = choices[0].get("message") or {}
    if message.get("refusal"):
        raise ValueError("OpenRouter refused synthesis")
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not isinstance(content, str) or not content:
        diagnostic = _openrouter_diagnostic(response, message)
        raise ValueError("OpenRouter returned no structured synthesis; "
                         f"diagnostic={diagnostic}")
    content = content.strip()
    if content.startswith("```") and content.endswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        diagnostic = _openrouter_diagnostic(response, message, content)
        raise ValueError("OpenRouter returned malformed structured synthesis; "
                         f"diagnostic={diagnostic}") from None


def synthesize_openrouter(packet, api_key=None, requester=_openrouter_post, sleeper=None, full=False,
                          context=None):
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OpenRouter credentials are not configured")
    system, user = construct_prompt(packet, full=full, context=context)
    profile = edition_profile(packet["run"]["checkpoint"])
    analyst = analyst_model()
    schema = compact_schema(NARRATIVE_SCHEMA if full else narrative_schema((context or {}).get("edition") or profile))
    requested_at = datetime.now(timezone.utc).isoformat()
    payload = dict(model=analyst["model"], temperature=0, max_tokens=profile["max_output_tokens"],
                   messages=[{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                   plugins=[{"id": "response-healing"}],
                   provider={"allow_fallbacks": False, "require_parameters": True},
                   response_format={"type": "json_schema", "json_schema": {
                       "name": "market_brief_narrative", "strict": True, "schema": schema}},
                   reasoning={"effort": profile["reasoning_effort"], "exclude": True})
    # A timeout or malformed transport envelope may follow a billable generation.
    # One request only, including on transport failure. `sleeper` is retained for callers.
    try:
        response = requester(payload, api_key)
    except _TransientOpenRouterError as exc:
        raise ValueError(f"OpenRouter transport failure; no automatic paid retry; cause={exc}") from None
    narrative = _openrouter_narrative(response)
    try:
        narrative = validate_narrative(narrative, packet, None if full else context, NARRATIVE_SCHEMA if full else None)
    except ValueError as exc:
        diagnostic = _openrouter_diagnostic(response)
        raise ValueError(f"{exc}; diagnostic={diagnostic}") from None
    safe_usage = _safe_usage(response)
    choice = (response.get("choices") or [{}])[0]
    finish_reason = choice.get("finish_reason", "unknown") if isinstance(choice, dict) else "unknown"
    provider_route = response.get("provider", "unknown")
    resolved_model = response.get("model", analyst["model"])
    if safe_usage:
        print("Synthesis usage: " + " ".join(f"{key}={value}" for key, value in safe_usage.items())
              + f" finish={finish_reason} provider={provider_route} model={resolved_model}", flush=True)
    else:
        print(f"Synthesis usage: unavailable finish={finish_reason} provider={provider_route}", flush=True)
    return narrative, dict(route="openrouter", provider="OpenRouter", model=analyst["model"],
                           model_source=analyst["source"], profile=profile["profile"],
                           max_output_tokens=profile["max_output_tokens"],
                           reasoning_effort=profile["reasoning_effort"], attempts=1,
                           input_bytes=len(user.encode()), output_bytes=len(canonical(narrative).encode()),
                           resolved_model=resolved_model, provider_route=provider_route,
                           finish_reason=finish_reason,
                           diagnostic=json.loads(_openrouter_diagnostic(response)),
                           requested_at=requested_at, response_id=response.get("id"), usage=safe_usage,
                           schema_hash=digest(schema),
                           prompt_hash=digest(dict(system=system, user=user)), evidence_hash=digest(packet))


def synthesize(packet, runner=subprocess.run, full=False, context=None):
    if os.environ.get("OPENROUTER_API_KEY"):
        return synthesize_openrouter(packet, full=full, context=context)
    executable = shutil.which("claude")
    if not executable:
        raise ValueError("Claude CLI is not installed")
    system, user = construct_prompt(packet, full=full, context=context, include_schema=False)
    analyst = analyst_model()
    profile = edition_profile(packet["run"]["checkpoint"])
    schema = NARRATIVE_SCHEMA if full else narrative_schema((context or {}).get("edition") or profile)
    argv = [executable, "--print", "--safe-mode", "--tools", "", "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}', "--disable-slash-commands",
            "--no-session-persistence", "--setting-sources", "", "--output-format", "json",
            "--model", analyst["cli_model"], "--system-prompt", system,
            "--json-schema", compact_json(compact_schema(schema))]
    # Retain existing auth location, not unrelated provider credentials or project environment.
    env = {k: v for k, v in os.environ.items() if k in
           {"HOME", "PATH", "LANG", "USER", "SHELL", "XDG_CONFIG_HOME", "SSL_CERT_FILE"}}
    with tempfile.TemporaryDirectory(prefix="market-brief-model-") as isolated:
        try:
            result = runner(argv, input=user, text=True, capture_output=True,
                            timeout=180, cwd=isolated, env=env, check=False)
        except (subprocess.TimeoutExpired, OSError):
            raise ValueError("Claude invocation unavailable or timed out") from None
    if result.returncode != 0 or len(result.stdout) > 2_000_000:
        raise ValueError("Claude invocation failed; check existing login/network outside the report")
    try:
        envelope = json.loads(result.stdout)
        if envelope.get("is_error"):
            raise ValueError("Claude returned an error")
        narrative = envelope.get("structured_output")
        if narrative is None:
            narrative = json.loads(envelope.get("result", ""))
        validated = validate_narrative(narrative, packet, None if full else context, schema)
    except (json.JSONDecodeError, TypeError, AttributeError):
        raise ValueError("Claude did not return a structured narrative") from None
    models = list(envelope.get("modelUsage", {}).keys())
    return validated, dict(route="claude-cli", requested_model=analyst["cli_model"], profile=profile["profile"],
                           resolved_models=models or ["not exposed"],
                           schema_hash=digest(compact_schema(schema)),
                           prompt_hash=digest(dict(system=system, user=user)),
                           evidence_hash=digest(packet))

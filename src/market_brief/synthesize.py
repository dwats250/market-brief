"""One Claude CLI route and bounded, mechanically grounded narrative validation."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from jsonschema import Draft202012Validator

from .evidence import ROOT, canonical, digest, evidence_catalog, model_packet

TEXT = {"type": "string", "minLength": 1, "maxLength": 1800}
REFS = {"type": "array", "items": {"type": "string"}, "minItems": 1,
        "maxItems": 12, "uniqueItems": True}
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
                 "evidence_ids": REFS, "uncertainty": {"type": "string", "maxLength": 500},
                 "alternative": {"type": "string", "maxLength": 500}})
NARRATIVE_SCHEMA = obj({
    "schema_version": {"const": "market-brief.narrative.v0"},
    "mode": {"enum": ["LIVE", "SAMPLE"]},
    "banner": obj({"title": TEXT, "label": {"enum": ["RISK-ON", "RISK-OFF", "MIXED", "INDETERMINATE"]},
                   "class": {"const": "INTERPRETATION"}, "evidence_ids": REFS,
                   "limitation": TEXT}),
    "summary": {"type": "array", "items": PARAGRAPH, "minItems": 1, "maxItems": 2},
    "sections": obj({k: {"type": "array", "items": PARAGRAPH, "maxItems": 1}
                     for k in ("macro", "equities", "attention", "cuttingboard", "events")}),
    "attention_ids": {"type": "array", "items": {"type": "string"},
                      "maxItems": 3, "uniqueItems": True},
    "attention": {"type": "array", "maxItems": 3, "items": obj({
        "id": {"type": "string"}, "why": TEXT})},
    "watches": {"type": "array", "minItems": 1, "maxItems": 3, "items": obj({
        "class": {"const": "WATCH"}, "condition": TEXT, "confirmation": TEXT,
        "contradiction": TEXT, "horizon": {"oneOf": [{"enum": HORIZONS},
            {"pattern": EVENT_HORIZON.pattern}]}, "evidence_ids": REFS})},
    "changes": {"type": "array", "maxItems": 0},
})
TOKEN = re.compile(r"\{\{([a-zA-Z][\w-]*)\}\}")


def validate_narrative(narrative, packet):
    errors = list(Draft202012Validator(NARRATIVE_SCHEMA).iter_errors(narrative))
    if errors:
        raise ValueError("malformed narrative at " + ".".join(map(str, errors[0].absolute_path)))
    if narrative["mode"] != packet["run"]["mode"]:
        raise ValueError("sample/live narrative mode mismatch")
    catalog = evidence_catalog(model_packet(packet))
    records = [narrative["banner"], *narrative["summary"], *narrative["watches"]]
    records += [p for section in narrative["sections"].values() for p in section]
    for record in records:
        refs = set(record["evidence_ids"])
        if not refs <= catalog.keys():
            raise ValueError("unknown or unavailable evidence reference")
        for key in ("title", "text", "limitation", "uncertainty", "alternative", "condition",
                    "confirmation", "contradiction", "horizon"):
            text = record.get(key, "")
            for ident in TOKEN.findall(text):
                if ident not in refs or not isinstance(catalog[ident].get("value"), (int, float)):
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


def construct_prompt(packet):
    projected = model_packet(packet)
    instructions = (ROOT / "prompts/synthesis.md").read_text()
    user = canonical(dict(evidence=projected, catalog=evidence_catalog(projected),
                          output_schema=NARRATIVE_SCHEMA))
    if len(user.encode()) > 120_000:
        raise ValueError("bounded synthesis packet exceeds size limit")
    return instructions, user


class _TransientOpenRouterError(ValueError):
    pass


def _openrouter_post(payload, api_key, timeout=180):
    request = Request(OPENROUTER_URL, data=json.dumps(payload).encode("utf-8"), headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://dwats250.github.io/market-brief/",
        "X-Title": "Market Brief",
    }, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read(2_000_001).decode("utf-8"))
    except HTTPError as exc:
        if exc.code in TRANSIENT_OPENROUTER_STATUS:
            raise _TransientOpenRouterError(f"OpenRouter transient HTTP {exc.code}") from None
        if exc.code in {401, 403}:
            raise ValueError("OpenRouter authentication failed") from None
        raise ValueError(f"OpenRouter HTTP {exc.code}") from None
    except (URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError):
        raise _TransientOpenRouterError("OpenRouter network or response failure") from None


def _openrouter_narrative(response):
    if response.get("error"):
        raise ValueError("OpenRouter returned an error")
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("OpenRouter returned no synthesis choice")
    message = choices[0].get("message") or {}
    if message.get("refusal"):
        raise ValueError("OpenRouter refused synthesis")
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not isinstance(content, str) or not content:
        raise ValueError("OpenRouter returned no structured synthesis")
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        raise ValueError("OpenRouter returned malformed structured synthesis") from None


def synthesize_openrouter(packet, api_key=None, requester=_openrouter_post, sleeper=time.sleep):
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OpenRouter credentials are not configured")
    system, user = construct_prompt(packet)
    requested_at = datetime.now(timezone.utc).isoformat()
    payload = dict(model=OPENROUTER_MODEL, temperature=0, max_tokens=6000,
                   messages=[{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                   response_format={"type": "json_schema", "json_schema": {
                       "name": "market_brief_narrative", "strict": True, "schema": NARRATIVE_SCHEMA}},
                   reasoning={"exclude": True})
    response = None
    for attempt in range(3):
        try:
            response = requester(payload, api_key)
            break
        except _TransientOpenRouterError:
            if attempt == 2:
                raise ValueError("OpenRouter transient failure after bounded retries") from None
            sleeper(2 ** attempt)
    narrative = validate_narrative(_openrouter_narrative(response), packet)
    usage = response.get("usage") if isinstance(response, dict) else None
    safe_usage = {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                  if isinstance(usage, dict) and key in usage}
    return narrative, dict(route="openrouter", provider="OpenRouter", model=OPENROUTER_MODEL,
                           requested_at=requested_at, response_id=response.get("id"), usage=safe_usage,
                           prompt_hash=digest(dict(system=system, user=user)), evidence_hash=digest(packet))


def synthesize(packet, runner=subprocess.run):
    if os.environ.get("OPENROUTER_API_KEY"):
        return synthesize_openrouter(packet)
    executable = shutil.which("claude")
    if not executable:
        raise ValueError("Claude CLI is not installed")
    system, user = construct_prompt(packet)
    argv = [executable, "--print", "--safe-mode", "--tools", "", "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}', "--disable-slash-commands",
            "--no-session-persistence", "--setting-sources", "", "--output-format", "json",
            "--model", "sonnet", "--system-prompt", system,
            "--json-schema", canonical(NARRATIVE_SCHEMA)]
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
        validated = validate_narrative(narrative, packet)
    except (json.JSONDecodeError, TypeError, AttributeError):
        raise ValueError("Claude did not return a structured narrative") from None
    models = list(envelope.get("modelUsage", {}).keys())
    return validated, dict(route="claude-cli", requested_model="sonnet",
                           resolved_models=models or ["not exposed"],
                           prompt_hash=digest(dict(system=system, user=user)),
                           evidence_hash=digest(packet))

"""One Claude CLI route and bounded, mechanically grounded narrative validation."""

import json
import os
import re
import shutil
import subprocess
import tempfile

from jsonschema import Draft202012Validator

from .evidence import ROOT, canonical, digest, evidence_catalog, model_packet

TEXT = {"type": "string", "minLength": 1, "maxLength": 1800}
REFS = {"type": "array", "items": {"type": "string"}, "minItems": 1,
        "maxItems": 12, "uniqueItems": True}
HORIZONS = ["OPENING_HOUR", "SESSION", "NEXT_CLOSE", "NEXT_BRIEF"]
EVENT_HORIZON = re.compile(r"^EVENT\([a-zA-Z][\w-]{0,79}\)$")
ALLOWED_LABELS = re.compile(r"\b(?:2Y|5Y|10Y|30Y|5-session|20-session|50-day|50-session)\b")
TRADE_LANGUAGE = re.compile(r"\b(entry|target|sizing|buy|sell|execute|execution|order)\b", re.I)


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


def synthesize(packet, runner=subprocess.run):
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

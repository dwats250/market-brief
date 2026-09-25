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
from .evidence import (
    PLACEHOLDER,
    ROOT,
    canonical,
    compact_model_record,
    digest,
    evidence_catalog,
    model_packet,
    read_json,
)


def text_field(limit, optional=False):
    return {"type": "string", "minLength": 0 if optional else 1, "maxLength": limit}


TEXT = text_field(360)
IDENTIFIER = text_field(96)
# One claim of roughly eight to ten words; the character bound is a backstop, not the target, and the
# word target is editorial: a grounded eleven-word headline is kept and noted, never discarded.
HEADLINE = {"type": "string", "minLength": 1, "maxLength": 160}
HEADLINE_WORD_TARGET = 10
REFS = {"type": "array", "items": IDENTIFIER, "minItems": 1,
        "maxItems": 4, "uniqueItems": True}
HORIZONS = ["OPENING_HOUR", "SESSION", "NEXT_CLOSE", "NEXT_BRIEF"]
EVENT_HORIZON = re.compile(r"^EVENT\([a-zA-Z][\w-]{0,79}\)$")
# Bounded labels carry digits without stating a measurement: the Treasury tenors, the configured
# five/twenty/fifty-session windows in their grammatical forms ("20-session", "over 20 sessions",
# "50-day", "50DMAs", "SMA50"), and index names. Any other digit in prose is a literal numeric claim.
# Run 34554487893 was rejected on "over 20 sessions" and run 34556169474 on plural "50DMAs" while
# every placeholder was grounded; singular, plural and abbreviated forms of one label are one label.
ALLOWED_LABELS = re.compile(
    r"\b(?:(?:2|5|10|30)[- ]?(?:Y|yr|year)s?"
    r"|(?:5|20|50)[- ]?(?:trading[- ])?(?:sessions?|days?|d)"
    r"|50[- ]?[SD]?MAs?|[SD]?MA[- ]?50s?"
    r"|S&P[ -]?500|Nasdaq[- ]100|Russell [12]000|Dow 30)\b", re.IGNORECASE)  # Title Case headlines
TRADE_LANGUAGE = re.compile(r"\b(entry|target|sizing|buy|sell|execute|execution|order)\b", re.I)
# The one exemption, for the take only: "sell-off" names a market move, not an instruction. Removed before the
# trade-language check runs, so "sell" as an action still rejects there.
SELL_OFF = re.compile(r"\bsell-?offs?\b", re.I)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "anthropic/claude-fable-5.1"
OPENROUTER_FALLBACK_MODEL = "anthropic/claude-fable-5"
TRANSIENT_OPENROUTER_STATUS = {408, 429, 500, 502, 503, 504}
# The one 404 routing condition eligible for a bounded model failover: OpenRouter reports the
# requested model has no eligible endpoint (the observed production failure, run 35099346593). A
# bare or otherwise-classified 404 is not this condition and fails closed with no fallback.
NO_ENDPOINT_404 = re.compile(r"no endpoints found", re.I)


def obj(properties):
    return dict(type="object", properties=properties, required=list(properties),
                additionalProperties=False)


PARAGRAPH = obj({"text": TEXT, "class": {"enum": ["OBSERVED", "INTERPRETATION"]},
                 "evidence_ids": REFS, "uncertainty": text_field(80, optional=True),
                 "alternative": text_field(100, optional=True)})
SECTION_PARAGRAPH = obj(dict(PARAGRAPH["properties"], text=text_field(240)))
NARRATIVE_SCHEMA = obj({
    "schema_version": {"const": "market-brief.narrative.v2"},
    "mode": {"enum": ["LIVE", "SAMPLE"]},
    "banner": obj({"title": HEADLINE, "label": {"enum": ["RISK-ON", "RISK-OFF", "MIXED", "INDETERMINATE"]},
                   "class": {"const": "INTERPRETATION"}, "evidence_ids": REFS,
                   "limitation": text_field(200)}),
    "summary": {"type": "array", "items": PARAGRAPH, "minItems": 1, "maxItems": 2},
    # The take: the one interpretation this brief could turn out to be wrong about. Always present, and
    # empty (no text, no evidence) when the evidence is too thin to commit; never both one and the other.
    "take": obj({"text": text_field(160, optional=True), "class": {"const": "INTERPRETATION"},
                 "evidence_ids": {"type": "array", "items": IDENTIFIER, "maxItems": 4, "uniqueItems": True}}),
    "sections": obj({k: {"type": "array", "items": SECTION_PARAGRAPH,
                         "maxItems": 0 if k == "cuttingboard" else 1}
                     for k in ("macro", "equities", "attention", "cuttingboard", "events")}),
    # The one model-owned attention selection: admitted trigger IDs with a reason each. Deterministic
    # code derives the selected IDs from these items; a second ID list cannot contradict them.
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
TOKEN = PLACEHOLDER


def narrative_schema(profile=None):
    """The one contract, with the edition's smaller bounds applied for light checkpoints."""
    if not profile:
        return NARRATIVE_SCHEMA
    schema = json.loads(json.dumps(NARRATIVE_SCHEMA))
    schema["properties"]["summary"]["maxItems"] = profile["summary_paragraphs"]
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


# Editorial targets (maxLength/maxItems) shape the brief; the model reads them as limits. Acceptance
# enforces them with fixed headroom so a grounded brief that runs modestly long is kept and noted,
# while structure, grounding, counts of zero, minimums and uniqueness stay exact. Run 34548270198
# discarded a complete, grounded generation for one paragraph past its 240-character target.
EDITORIAL_HEADROOM = 2
EDITORIAL_KEYWORDS = ("maxLength", "maxItems")


def acceptance_schema(schema):
    """The local contract with editorial bounds widened by EDITORIAL_HEADROOM; zero stays zero."""
    def visit(node):
        if isinstance(node, list):
            return [visit(item) for item in node]
        if not isinstance(node, dict):
            return node
        return {key: (value * EDITORIAL_HEADROOM if key in EDITORIAL_KEYWORDS else visit(value))
                for key, value in node.items()}
    return visit(schema)


def editorial_notes(narrative, schema):
    """Where an accepted narrative ran past an editorial target: recorded, never fatal."""
    notes = []
    for error in Draft202012Validator(schema).iter_errors(narrative):
        if error.validator in EDITORIAL_KEYWORDS:
            notes.append(dict(path=".".join(map(str, error.absolute_path)), keyword=error.validator,
                              limit=error.validator_value, actual=len(error.instance)))
    title = (narrative.get("banner") or {}).get("title") if isinstance(narrative, dict) else None
    if isinstance(title, str) and len(title.split()) > HEADLINE_WORD_TARGET:
        notes.append(dict(path="banner.title", keyword="words", limit=HEADLINE_WORD_TARGET, actual=len(title.split())))
    return sorted(notes, key=lambda note: note["path"])


# Voice telemetry: contract and filler words that read as machinery in reader prose. The prompt steers away
# from them; they are counted per accepted synthesis and never gate acceptance, publication or a retry.
AVOID_PHRASES = ("admitted", "packet", "notably", "evident", "suggesting", "rather than", "broad but not",
                 "character")
AVOID_PATTERNS = {phrase: re.compile(r"\b" + r"\s+".join(map(re.escape, phrase.split())) + r"\b", re.I)
                  for phrase in AVOID_PHRASES}


def reader_prose(narrative):
    """The analyst's reader-facing sentences. Identifiers, instruments, horizons, labels, classes, modes and the
    schema version are not prose; neither is anything the renderer writes."""
    banner, character = narrative["banner"], narrative["character"]
    paragraphs = [*narrative["summary"], *(p for section in narrative["sections"].values() for p in section)]
    texts = [banner["title"], banner["limitation"], character["text"]]
    texts += [p[key] for p in paragraphs for key in ("text", "uncertainty", "alternative")]
    texts.append((narrative.get("take") or {}).get("text", ""))  # absent from a v1 narrative
    texts += [item["why"] for item in narrative["attention"]]
    texts += [w[key] for w in narrative["watches"] for key in ("condition", "confirmation", "contradiction")]
    texts += [r[key] for r in narrative["relationships"] for key in ("statement", "reason")]
    texts += [u["reason"] for u in narrative["watch_updates"]] + [c["text"] for c in narrative["changes"]]
    return texts


def style_notes(narrative):
    """Advisory voice telemetry for one accepted synthesis: observation only, never fatal."""
    prose = reader_prose(narrative)
    plain = [TOKEN.sub("", text) for text in prose]
    counts = {phrase: sum(len(pattern.findall(text)) for text in plain) for phrase, pattern in AVOID_PATTERNS.items()}
    take = (narrative.get("take") or {}).get("text", "").strip()

    def normalized(text):
        return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())
    return dict(avoid_phrases=counts, avoid_phrase_total=sum(counts.values()),
                numeric_placeholders=sum(len(TOKEN.findall(text)) for text in prose),
                take=dict(present=bool(take), characters=len(take),
                          repeats_headline=bool(take) and normalized(take) == normalized(narrative["banner"]["title"])))


class NarrativeRejected(ValueError):
    """A parsed narrative that failed acceptance; carried so the run can archive it for diagnosis."""

    def __init__(self, message, narrative):
        super().__init__(message)
        self.narrative = narrative


def compact_json(value):
    """Wire serialization only; do not change canonical evidence/continuity hashes."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


# Anthropic's structured-output subset (documented 2026-09-10) rejects string length, array
# length beyond minItems 0/1, uniqueItems, pattern and oneOf with HTTP 400. Whether OpenRouter
# strips them before Azure is not documented; three production requests carrying them returned
# 200, but nothing shows they were enforced. So the wire contract carries only documented
# keywords, every bound becomes a description the model can read, and `validate_narrative`
# enforces the full local contract after generation. Cost exposure is bounded by max_tokens alone.
UNSUPPORTED_WIRE_KEYWORDS = ("minLength", "maxLength", "maxItems", "uniqueItems", "pattern")


def transport_schema(schema):
    """The provider-compatible shape of the same contract, fully inlined: types, required, enums.

    The isolated CLI enforces this inlined form directly. The OpenRouter route sends
    `factored_transport_schema` instead, both as the user-message copy and as `response_format` — an
    isomorphic `$defs`/`$ref` factoring of this same output — because Anthropic's strict-grammar compiler
    rejects the fully inlined form as too large and the inlined copy cost the light edition its input
    budget; the two are proven identical by expanding every local ref back to this schema."""

    def describe(node):
        notes = []
        if "maxLength" in node:
            notes.append(f"At most {node['maxLength']} characters"
                         + (", non-empty" if node.get("minLength") else "") + ".")
        if "maxItems" in node:
            least = node.get("minItems", 0)
            notes.append(f"At most {node['maxItems']} items" + (f", at least {least}" if least > 1 else "")
                         + (", no duplicates" if node.get("uniqueItems") else "") + ".")
        if "pattern" in node:
            notes.append(f"Must match {node['pattern']}.")
        return " ".join(notes)

    def visit(node):
        if isinstance(node, list):
            return [visit(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "oneOf" in node:
            # The watch horizon: fixed names or EVENT(<admitted id>); the local validator checks both.
            names = [name for branch in node["oneOf"] for name in branch.get("enum", [])]
            event = any("pattern" in branch for branch in node["oneOf"])
            return {"type": "string", "description": "Exactly one of " + ", ".join(names)
                    + (", or EVENT(<admitted event id>)" if event else "") + "."}
        result = {}
        for key, value in node.items():
            if key in UNSUPPORTED_WIRE_KEYWORDS or (key == "minItems" and value > 1):
                continue
            if key == "type" and isinstance(value, list):
                continue
            result[key] = visit(value) if key not in ("enum", "const", "required") else value
        note = describe(node)
        if note:
            result["description"] = (result.get("description", "") + " " + note).strip()
        if isinstance(node.get("type"), list):
            description = result.pop("description", None)
            result = {"anyOf": [dict(result, type=name) if name != "null" else {"type": "null"}
                                for name in node["type"]]}
            if description:
                result["description"] = description
        return result

    wire = visit(schema)
    # A paragraph array states its items' text bound once, on the array, so the summary and section paragraphs are
    # one identical node that the provider's grammar compiles once. The take made the two separate nodes cross
    # Anthropic's grammar limit (G2.5, 2026-09-25: HTTP 400 at 5,497 bytes); the bound is still described.
    properties = wire.get("properties", {})
    if "summary" in properties and "sections" in properties:
        for array in [properties["summary"], *properties["sections"]["properties"].values()]:
            text = array["items"]["properties"]["text"]
            array["description"] = f"{array.get('description', '')} Each text: {text['description']}".strip()
            text["description"] = "Non-empty."
    return wire


def factored_transport_schema(schema):
    """`transport_schema` refactored so Anthropic's strict-grammar compiler accepts it.

    An isomorphic transport representation of `transport_schema`: no field, type, requirement, enum,
    or validation rule changes. Only genuine schema-valued positions (property values, `items`, and
    `anyOf`/`allOf`/`oneOf` members) may become local `$ref`s; a `properties` map is never itself
    replaced. Repeated schema nodes are hoisted into local `$defs` and referenced by
    `#/$defs/...`, which shrinks the fully inlined form the compiler reported as "too large" while
    preserving exact validation semantics (expanding every local ref reconstructs `transport_schema`).
    The OpenRouter route uses this form for both the user-message copy and `response_format` (owner
    ruling 2026-09-25: the inlined prompt copy put the 2026-09-24 light context 26 bytes over budget).
    """
    wire = transport_schema(schema)
    schema_list_keys = ("anyOf", "allOf", "oneOf")
    counts = {}

    def children(node):
        properties = node.get("properties")
        if isinstance(properties, dict):
            yield from (value for value in properties.values() if isinstance(value, dict))
        items = node.get("items")
        if isinstance(items, dict):
            yield items
        for key in schema_list_keys:
            values = node.get(key)
            if isinstance(values, list):
                yield from (value for value in values if isinstance(value, dict))

    def count(node):
        encoded = compact_json(node)
        if len(encoded) >= 100:  # tiny nodes cost more as a ref than inlined; leave them in place
            counts[encoded] = counts.get(encoded, 0) + 1
        for child in children(node):
            count(child)

    count(wire)
    # Larger repeated nodes first, so a definition can reference a smaller definition nested inside
    # it; a node can never contain an identical copy of itself, so the reference graph is acyclic.
    repeated = sorted((encoded for encoded, occurrences in counts.items() if occurrences >= 2),
                      key=lambda encoded: (-len(encoded), encoded))
    names = {encoded: f"d{index}" for index, encoded in enumerate(repeated, 1)}

    def rewrite(node, current=None, root=False):
        encoded = compact_json(node)
        # `current` guards a definition's own body from being replaced by a ref to itself.
        if not root and encoded in names and encoded != current:
            return {"$ref": f"#/$defs/{names[encoded]}"}
        result = dict(node)
        properties = node.get("properties")
        if isinstance(properties, dict):
            result["properties"] = {key: rewrite(value, current=current)
                                    for key, value in properties.items()}
        items = node.get("items")
        if isinstance(items, dict):
            result["items"] = rewrite(items, current=current)
        for key in schema_list_keys:
            values = node.get(key)
            if isinstance(values, list):
                result[key] = [rewrite(value, current=current) for value in values]
        return result

    factored = rewrite(wire, root=True)
    if names:
        factored["$defs"] = {names[encoded]: rewrite(json.loads(encoded), current=encoded)
                             for encoded in repeated}
    return factored


def analyst_model(config=None, environ=None):
    """Configured analyst identity: one model for every edition, overridable by environment.

    An environment override (`MARKET_BRIEF_MODEL`) is honored exactly and carries no fallback of its
    own; the configured `fallback_model` applies only to the configured primary."""
    environ = os.environ if environ is None else environ
    config = config or read_json(ROOT / "config/editions.json")
    configured = config["analyst"]["model"]
    override = environ.get("MARKET_BRIEF_MODEL")
    return dict(model=override or configured, source="environment" if override else "config/editions.json",
                fallback_model=None if override else config["analyst"].get("fallback_model"),
                cli_model=config["analyst"].get("cli_model", "sonnet"))


def validate_narrative(narrative, packet, context=None, schema=None):
    """Mechanical grounding: schema, mode, references that exist in admitted evidence and were
    actually supplied in the analyst context, numeric placeholders, and trade/current-language rules.

    `schema` is the contract actually advertised to the model; by default the edition's profile-bounded one.
    """
    profile = (context or {}).get("edition") or edition_profile(packet["run"]["checkpoint"])
    errors = list(Draft202012Validator(acceptance_schema(schema or narrative_schema(profile))).iter_errors(narrative))
    if errors:
        raise ValueError("malformed narrative at " + ".".join(map(str, errors[0].absolute_path)))
    if narrative["mode"] != packet["run"]["mode"]:
        raise ValueError("sample/live narrative mode mismatch")
    if ";" in narrative["banner"]["title"]:
        raise ValueError("headline must be one claim without a semicolon")
    take = narrative["take"]
    if bool(take["text"].strip()) != bool(take["evidence_ids"]):
        raise ValueError("take text and evidence must be both present or both empty")
    catalog = evidence_catalog(model_packet(packet))
    if context is not None:
        if context.get("evidence_hash") != digest(packet):
            raise ValueError("analyst context does not match the evidence record")
        shown = supplied_ids(context)
        catalog = {ident: row for ident, row in catalog.items() if ident in shown}
    # Current-condition records cite current evidence only; continuity records may add prior refs.
    records = [narrative["banner"], *narrative["summary"], take, *narrative["watches"], narrative["character"]]
    records += [p for section in narrative["sections"].values() for p in section]
    for record in records:
        refs = set(record["evidence_ids"])
        if not refs <= catalog.keys():
            # The analyst's own identifiers: naming them costs nothing and explains the rejection.
            raise ValueError("unknown, unavailable, or unsupplied evidence reference: "
                             + ", ".join(sorted(refs - catalog.keys())))
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
    attention = narrative["attention"]
    selected = [item["id"] for item in attention]
    if not set(selected) <= admitted:
        raise ValueError("unknown attention trigger")
    if len(set(selected)) != len(selected):
        raise ValueError("repeated attention trigger")
    for item in attention:
        if re.search(r"\d", ALLOWED_LABELS.sub("", item["why"])):
            raise ValueError("literal numeric claim in attention reason")
        if TRADE_LANGUAGE.search(item["why"]):
            raise ValueError("trade language in attention reason")
    if TRADE_LANGUAGE.search(SELL_OFF.sub("", take["text"])):
        raise ValueError("trade language in the take")
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
    OpenRouter retains a schema copy, in the same factored form `response_format` enforces: run
    34278983083 failed banner schema validation with strict response_format but no copy. The
    isolated CLI supplies its contract through --json-schema and opts out of the duplicate copy.
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
        projected["output_schema"] = factored_transport_schema(schema)
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


class _ModelUnavailableError(ValueError):
    """OpenRouter reports the requested model has no eligible endpoint: the explicit "no endpoints
    found" 404 (never a bare or unrelated 404), a routing failure that precedes any billable
    generation, and the one condition eligible for a bounded model failover. OpenRouter's own `models`
    array does not recover from it — a "no endpoints" 404 halts that chain — so failover is made
    explicitly. Carries the sanitized status and error for truthful provenance."""

    def __init__(self, status, error):
        self.status = status
        self.error = error
        super().__init__(f"OpenRouter HTTP {status}; "
                         f"diagnostic={canonical({'http_status': status, 'error': error})}")


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
        error = _safe_error(exc)  # reads the body once; reused for every diagnostic below
        diagnostic = canonical({"http_status": exc.code, "error": error})
        if exc.code in TRANSIENT_OPENROUTER_STATUS:
            raise _TransientOpenRouterError(f"OpenRouter transient HTTP {exc.code}; "
                                            f"diagnostic={diagnostic}") from None
        if exc.code == 404 and _is_model_unavailable(error):
            # "No endpoints found for <model>": model unavailable before any generation is billed. A
            # bare or unrelated 404 is not this condition and falls through to fail closed with no fallback.
            raise _ModelUnavailableError(exc.code, error) from None
        raise ValueError(f"OpenRouter HTTP {exc.code}; diagnostic={diagnostic}") from None
    except (URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError):
        raise _TransientOpenRouterError("OpenRouter network or response failure") from None


def _printable(text, limit):
    """Provider text made safe to log and save: whitespace becomes one space, other non-printables go, then bounded."""
    text = "".join(ch if ch.isprintable() else " " if ch.isspace() else "" for ch in text)
    return " ".join(text.split())[:limit]


def _provider_message(raw):
    """The provider's own error message inside OpenRouter's `metadata.raw` (for example Anthropic's "The compiled
    grammar is too large…"), bounded and printable. Nothing else from the provider body is kept, and a raw that is
    not the provider's JSON error, or cannot be read at all, yields nothing."""
    try:
        raw = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, RecursionError):
        return None
    error = raw.get("error") if isinstance(raw, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    return _printable(message, 300) if isinstance(message, str) else None


def _safe_error(exc, limit=20_000):
    """Run 34486249474 recorded only `OpenRouter HTTP 400`, and G2.5 (2026-09-25) only "Provider returned error".
    Keep the documented error code, a bounded message, provider labels and the provider's own bounded error
    message; never the rest of the raw provider body, headers or IDs."""
    try:
        body = json.loads(exc.read(limit).decode("utf-8"))
        error = body["error"]
        code, message, metadata = error.get("code"), error.get("message"), error.get("metadata")
    except (AttributeError, OSError, UnicodeError, ValueError, KeyError, TypeError, RecursionError):
        return "unknown"
    result = {}
    if type(code) in (int, float):
        result["code"] = code
    if isinstance(message, str):
        result["message"] = _printable(message, 300)
    if isinstance(metadata, dict):
        result["metadata"] = {key: _printable(metadata[key], 80) for key in ("provider_name", "error_type",
                                                                            "provider_code")
                              if isinstance(metadata.get(key), str)}
        detail = _provider_message(metadata.get("raw"))
        if detail:
            result["metadata"]["provider_message"] = detail
    return result or "unknown"


def _is_model_unavailable(error):
    """Classify a 404 as model-unavailable only from the explicit 'no endpoints found' message on the
    sanitized error; a bare, unreadable, or unrelated 404 is not this condition. Reads the already
    length-bounded `_safe_error` output (which keeps at most the provider's own bounded message) — never the raw
    response body."""
    message = error.get("message") if isinstance(error, dict) else None
    return isinstance(message, str) and bool(NO_ENDPOINT_404.search(message))


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
    # The provider-enforced schema is the same factored form the prompt copy carries: the inlined contract,
    # small enough for Anthropic's strict-grammar compiler. `schema_hash` below records exactly this wire form.
    schema = factored_transport_schema(
        NARRATIVE_SCHEMA if full else narrative_schema((context or {}).get("edition") or profile))
    requested_at = datetime.now(timezone.utc).isoformat()
    # No sampling parameters: Fable endpoints advertise none, and require_parameters would otherwise
    # leave no eligible provider. Bounds are enforced locally, not by the wire schema. The provider is a
    # hard allowlist of exactly one approved endpoint: live OpenRouter endpoint data shows Anthropic
    # direct is the only Fable 5.1 provider that advertises `structured_outputs` (Azure/Bedrock/Google
    # carry `response_format` but not strict json_schema), and `require_parameters` treats
    # `response_format` as a soft preference, so it cannot hold a strict request there on its own.
    # `order:["anthropic"]` with allow_fallbacks disabled cannot silently escape to an unverified
    # provider — the 2026-09-17 "Claude Platform on AWS" 400 was such an escape, on Fable 5, under PR
    # #27's since-removed `models` array. Every attempt shares this payload; only `model` changes
    # between the primary and any one fallback, so the bounded Fable 5 fallback is pinned identically.
    base_payload = dict(max_tokens=profile["max_output_tokens"],
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": user}],
                        plugins=[{"id": "response-healing"}],
                        provider={"order": ["anthropic"], "allow_fallbacks": False,
                                  "require_parameters": True},
                        response_format={"type": "json_schema", "json_schema": {
                            "name": "market_brief_narrative", "strict": True, "schema": schema}},
                        reasoning={"effort": profile["reasoning_effort"], "exclude": True})
    fallback_model = analyst["fallback_model"]
    # One paid generation per checkpoint. Attempt the configured primary; if and only if OpenRouter
    # reports it has no eligible endpoint (the explicit "no endpoints found" 404 — a routing failure
    # before any billing), make one bounded fallback attempt with the configured Fable 5 endpoint. A
    # bare or unrelated 404, transient 5xx/429, malformed 400, auth, and our own grounding/continuity
    # rejection downstream all fail closed with no second call —
    # a billable generation is never retried automatically, so `sleeper` stays unused. Model failover
    # is kept semantically separate from that transient handling and admits at most one narrative.
    primary_failure = None
    requested_model = analyst["model"]
    try:
        response = requester(dict(base_payload, model=requested_model), api_key)
    except _TransientOpenRouterError as exc:
        raise ValueError(f"OpenRouter transport failure; no automatic paid retry; cause={exc}") from None
    except _ModelUnavailableError as exc:
        if not fallback_model:
            raise ValueError(str(exc)) from None
        primary_failure = dict(model=requested_model, status=exc.status, error=exc.error)
        requested_model = fallback_model
        try:
            response = requester(dict(base_payload, model=requested_model), api_key)
        except _TransientOpenRouterError as exc2:
            raise ValueError(f"OpenRouter transport failure; no automatic paid retry; cause={exc2}") from None
        except _ModelUnavailableError as exc2:
            raise ValueError(str(exc2)) from None
    fallback_used = primary_failure is not None
    narrative = _openrouter_narrative(response)
    try:
        narrative = validate_narrative(narrative, packet, None if full else context, NARRATIVE_SCHEMA if full else None)
    except ValueError as exc:
        diagnostic = _openrouter_diagnostic(response)
        raise NarrativeRejected(f"{exc}; diagnostic={diagnostic}", narrative) from None
    safe_usage = _safe_usage(response)
    choice = (response.get("choices") or [{}])[0]
    finish_reason = choice.get("finish_reason", "unknown") if isinstance(choice, dict) else "unknown"
    provider_route = response.get("provider", "unknown")
    resolved_model = response.get("model", requested_model)
    fallback_suffix = f" fallback<-{primary_failure['model']}" if fallback_used else ""
    if safe_usage:
        print("Synthesis usage: " + " ".join(f"{key}={value}" for key, value in safe_usage.items())
              + f" finish={finish_reason} provider={provider_route} model={resolved_model}{fallback_suffix}",
              flush=True)
    else:
        print(f"Synthesis usage: unavailable finish={finish_reason} provider={provider_route}{fallback_suffix}",
              flush=True)
    return narrative, dict(route="openrouter", provider="OpenRouter", model=analyst["model"],
                           model_source=analyst["source"], fallback_model=fallback_model,
                           fallback_used=fallback_used, primary_failure=primary_failure,
                           requested_model=requested_model, profile=profile["profile"],
                           max_output_tokens=profile["max_output_tokens"],
                           reasoning_effort=profile["reasoning_effort"], attempts=2 if fallback_used else 1,
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
            "--json-schema", compact_json(transport_schema(schema))]
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
    except (json.JSONDecodeError, TypeError, AttributeError):
        raise ValueError("Claude did not return a structured narrative") from None
    try:
        validated = validate_narrative(narrative, packet, None if full else context, schema)
    except (TypeError, AttributeError):
        raise ValueError("Claude did not return a structured narrative") from None
    except ValueError as exc:
        raise NarrativeRejected(str(exc), narrative) from None
    models = list(envelope.get("modelUsage", {}).keys())
    return validated, dict(route="claude-cli", requested_model=analyst["cli_model"], profile=profile["profile"],
                           resolved_models=models or ["not exposed"],
                           schema_hash=digest(transport_schema(schema)),
                           prompt_hash=digest(dict(system=system, user=user)),
                           evidence_hash=digest(packet))

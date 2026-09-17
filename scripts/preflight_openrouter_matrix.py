import json
import os
import urllib.error
import urllib.request
from collections import Counter

from market_brief.context import edition_profile
from market_brief.synthesize import narrative_schema, transport_schema

URL = "https://openrouter.ai/api/v1/chat/completions"
KEY = os.environ["OPENROUTER_API_KEY"]
MODEL = "anthropic/claude-fable-5.1"
PROVIDER = {"order": ["anthropic"], "allow_fallbacks": False, "require_parameters": True}
ACTUAL = transport_schema(narrative_schema(edition_profile("PREMARKET")))
SCHEMA_LIST_KEYS = ("anyOf", "allOf", "oneOf")


def compact(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"))


def child_schemas(node):
    if not isinstance(node, dict):
        return
    props = node.get("properties")
    if isinstance(props, dict):
        for schema in props.values():
            if isinstance(schema, dict):
                yield schema
    items = node.get("items")
    if isinstance(items, dict):
        yield items
    for key in SCHEMA_LIST_KEYS:
        values = node.get(key)
        if isinstance(values, list):
            for schema in values:
                if isinstance(schema, dict):
                    yield schema


def count_nodes(node, counts):
    encoded = compact(node)
    if len(encoded) >= 100:
        counts[encoded] += 1
    for child in child_schemas(node):
        count_nodes(child, counts)


counts = Counter()
count_nodes(ACTUAL, counts)
selected = [encoded for encoded, count in counts.items() if count >= 2]
selected.sort(key=lambda encoded: (-len(encoded), encoded))
names = {encoded: f"d{index}" for index, encoded in enumerate(selected, 1)}


def rewrite_schema(node, *, current=None, root=False):
    encoded = compact(node)
    if not root and encoded in names and encoded != current:
        return {"$ref": f"#/$defs/{names[encoded]}"}
    out = dict(node)
    props = node.get("properties")
    if isinstance(props, dict):
        out["properties"] = {key: rewrite_schema(schema, current=current) for key, schema in props.items()}
    items = node.get("items")
    if isinstance(items, dict):
        out["items"] = rewrite_schema(items, current=current)
    for key in SCHEMA_LIST_KEYS:
        values = node.get(key)
        if isinstance(values, list):
            out[key] = [rewrite_schema(schema, current=current) for schema in values]
    return out


FACTORED = rewrite_schema(ACTUAL, root=True)
if names:
    FACTORED["$defs"] = {
        names[encoded]: rewrite_schema(json.loads(encoded), current=encoded)
        for encoded in selected
    }

print(json.dumps({
    "actual_bytes": len(compact(ACTUAL)),
    "factored_bytes": len(compact(FACTORED)),
    "defs": len(names),
    "repeated_nodes": {names[e]: counts[e] for e in selected},
}, sort_keys=True), flush=True)

payload = {
    "model": MODEL,
    "messages": [{"role": "user", "content": "Return the smallest valid JSON object matching the schema."}],
    "max_tokens": 2048,
    "provider": PROVIDER,
    "response_format": {"type": "json_schema", "json_schema": {
        "name": "probe", "strict": True, "schema": FACTORED}},
    "reasoning": {"effort": "low", "exclude": True},
    "plugins": [{"id": "response-healing"}],
}
req = urllib.request.Request(URL, data=json.dumps(payload).encode(),
    headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}, method="POST")
try:
    with urllib.request.urlopen(req, timeout=120) as response:
        body = json.loads(response.read().decode("utf-8", errors="replace"))
        choice = (body.get("choices") or [{}])[0]
        print(json.dumps({"probe": "valid_factored", "status": response.status,
                          "provider": body.get("provider"), "model": body.get("model"),
                          "finish_reason": choice.get("finish_reason")}, sort_keys=True), flush=True)
except urllib.error.HTTPError as exc:
    body = exc.read().decode("utf-8", errors="replace")[:3000]
    print(json.dumps({"probe": "valid_factored", "status": exc.code, "body": body}, sort_keys=True), flush=True)

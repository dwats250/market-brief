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
ACTUAL_SCHEMA = transport_schema(narrative_schema(edition_profile("PREMARKET")))


def compact(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def factor_repeated_subschemas(schema):
    counts = Counter()
    def collect(node, root=False):
        if isinstance(node, dict):
            if not root:
                encoded = compact(node)
                if len(encoded) >= 120:
                    counts[encoded] += 1
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)
    collect(schema, root=True)
    repeated = [encoded for encoded, count in counts.items() if count >= 2]
    repeated.sort(key=lambda encoded: (-len(encoded), encoded))
    names = {encoded: f"d{index}" for index, encoded in enumerate(repeated, 1)}
    def replace(node, current=None):
        if isinstance(node, dict):
            encoded = compact(node)
            if encoded in names and encoded != current:
                return {"$ref": f"#/$defs/{names[encoded]}"}
            return {key: replace(value, current) for key, value in node.items()}
        if isinstance(node, list):
            return [replace(value, current) for value in node]
        return node
    factored = replace(schema)
    if names:
        factored["$defs"] = {names[e]: replace(json.loads(e), current=e) for e in repeated}
    return factored


FACTORED_SCHEMA = factor_repeated_subschemas(ACTUAL_SCHEMA)
print(json.dumps({"inline_bytes": len(compact(ACTUAL_SCHEMA)), "factored_bytes": len(compact(FACTORED_SCHEMA)),
                  "defs": len(FACTORED_SCHEMA.get("$defs", {}))}, sort_keys=True), flush=True)


def probe(name, schema):
    tool_name = "emit_market_brief"
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Call emit_market_brief with a minimal valid object."}],
        "max_tokens": 1024,
        "provider": PROVIDER,
        "reasoning": {"effort": "low", "exclude": True},
        "tools": [{"type": "function", "function": {
            "name": tool_name,
            "description": "Emit the structured market brief narrative.",
            "strict": True,
            "parameters": schema,
        }}],
        "tool_choice": {"type": "function", "function": {"name": tool_name}},
    }
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8", errors="replace"))
            choice = (body.get("choices") or [{}])[0]
            calls = ((choice.get("message") or {}).get("tool_calls") or [])
            print(json.dumps({"probe": name, "status": response.status, "provider": body.get("provider"),
                              "model": body.get("model"), "finish_reason": choice.get("finish_reason"),
                              "tool_calls": len(calls)}, sort_keys=True), flush=True)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:3000]
        print(json.dumps({"probe": name, "status": exc.code, "body": body}, sort_keys=True), flush=True)


probe("strict_tool_inline", ACTUAL_SCHEMA)
probe("strict_tool_factored", FACTORED_SCHEMA)

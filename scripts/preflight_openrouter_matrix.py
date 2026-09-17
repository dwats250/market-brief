import json
import os
import string
import urllib.error
import urllib.request

from market_brief.context import edition_profile
from market_brief.synthesize import narrative_schema, transport_schema

URL = "https://openrouter.ai/api/v1/chat/completions"
KEY = os.environ["OPENROUTER_API_KEY"]
MODEL = "anthropic/claude-fable-5.1"
PROVIDER = {"order": ["anthropic"], "allow_fallbacks": False, "require_parameters": True}
ACTUAL = transport_schema(narrative_schema(edition_profile("PREMARKET")))


def compact(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"))


def strip_descriptions(node):
    if isinstance(node, dict):
        return {k: strip_descriptions(v) for k, v in node.items() if k != "description"}
    if isinstance(node, list):
        return [strip_descriptions(v) for v in node]
    return node


def property_names(node, found=None):
    found = set() if found is None else found
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            found.update(props)
        for v in node.values():
            property_names(v, found)
    elif isinstance(node, list):
        for v in node:
            property_names(v, found)
    return found


alphabet = string.ascii_lowercase + string.ascii_uppercase
names = sorted(property_names(ACTUAL))
if len(names) > len(alphabet):
    raise RuntimeError("probe mapper needs more symbols")
KEYMAP = dict(zip(names, alphabet, strict=True))


def minify_properties(node):
    if isinstance(node, list):
        return [minify_properties(v) for v in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for k, v in node.items():
        if k == "description":
            continue
        if k == "properties":
            out[k] = {KEYMAP[name]: minify_properties(schema) for name, schema in v.items()}
        elif k == "required":
            out[k] = [KEYMAP.get(name, name) for name in v]
        else:
            out[k] = minify_properties(v)
    return out


NO_DESC = strip_descriptions(ACTUAL)
MINIFIED = minify_properties(ACTUAL)
print(json.dumps({
    "actual_bytes": len(compact(ACTUAL)),
    "no_desc_bytes": len(compact(NO_DESC)),
    "minified_bytes": len(compact(MINIFIED)),
    "properties": len(KEYMAP),
}, sort_keys=True), flush=True)


def probe(name, schema):
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Return the smallest valid JSON object matching the schema."}],
        "max_tokens": 2048,
        "provider": PROVIDER,
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "probe", "strict": True, "schema": schema}},
        "reasoning": {"effort": "low", "exclude": True},
        "plugins": [{"id": "response-healing"}],
    }
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8", errors="replace"))
            choice = (body.get("choices") or [{}])[0]
            print(json.dumps({"probe": name, "status": response.status,
                              "provider": body.get("provider"), "model": body.get("model"),
                              "finish_reason": choice.get("finish_reason")}, sort_keys=True), flush=True)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:3000]
        print(json.dumps({"probe": name, "status": exc.code, "body": body}, sort_keys=True), flush=True)


probe("no_descriptions", NO_DESC)
probe("minified_isomorphic", MINIFIED)

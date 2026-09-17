import json
import os
import urllib.error
import urllib.request

from market_brief.context import edition_profile
from market_brief.synthesize import narrative_schema, transport_schema

URL = "https://openrouter.ai/api/v1/chat/completions"
KEY = os.environ["OPENROUTER_API_KEY"]
MODEL = "anthropic/claude-fable-5.1"
PROVIDER = {"order": ["anthropic"], "allow_fallbacks": False, "require_parameters": True}

TINY_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}
ACTUAL_SCHEMA = transport_schema(narrative_schema(edition_profile("PREMARKET")))


def call(name, *, schema=None, reasoning=False, plugin=False):
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Return JSON indicating success."}],
        "max_tokens": 1024,
        "provider": PROVIDER,
    }
    if schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "probe", "strict": True, "schema": schema},
        }
    if reasoning:
        payload["reasoning"] = {"effort": "low", "exclude": True}
    if plugin:
        payload["plugins"] = [{"id": "response-healing"}]

    req = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            body = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(body)
            print(json.dumps({
                "probe": name,
                "status": response.status,
                "provider": parsed.get("provider"),
                "model": parsed.get("model"),
                "finish_reason": ((parsed.get("choices") or [{}])[0]).get("finish_reason"),
            }, sort_keys=True), flush=True)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:2000]
        print(json.dumps({"probe": name, "status": exc.code, "body": body}, sort_keys=True), flush=True)
    except Exception as exc:
        print(json.dumps({"probe": name, "exception": f"{type(exc).__name__}: {exc}"}, sort_keys=True), flush=True)


call("plain")
call("tiny_schema", schema=TINY_SCHEMA)
call("tiny_schema_reasoning", schema=TINY_SCHEMA, reasoning=True)
call("tiny_schema_plugin", schema=TINY_SCHEMA, plugin=True)
call("tiny_schema_all", schema=TINY_SCHEMA, reasoning=True, plugin=True)
call("actual_schema", schema=ACTUAL_SCHEMA)
call("actual_schema_reasoning", schema=ACTUAL_SCHEMA, reasoning=True)
call("actual_schema_plugin", schema=ACTUAL_SCHEMA, plugin=True)
call("actual_schema_all", schema=ACTUAL_SCHEMA, reasoning=True, plugin=True)

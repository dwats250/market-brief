# Production synthesis cost containment — 2026-09-10

## Evidence and root cause

Baseline: `1a7752dbd7705876216bd72b74858c69674cb972`, repository
`dwats250/market-brief`. Read-only inspection of
[run 34480006410](https://github.com/dwats250/market-brief/actions/runs/34480006410)
and artifact `10153213045` reproduced the archived user message at exactly 32,526
bytes. The artifact contains `evidence.json`, `analyst_context.json`, and failed
`metadata.json`; it contains neither the response body nor a narrative.

The observed failure is exhaustion of a SHARED reasoning-plus-content ceiling:
18,331 prompt tokens, exactly 5,524 completion tokens, `finish_reason=length`,
8,395 content bytes, HTTP 200, Azure, and a 20,662-byte response envelope. The
response included `reasoning_details`, but the logger discarded nested usage,
response ID, resolved model and native finish reason. Byte counts cannot recover
native token counts. In particular, subtracting content bytes from envelope bytes
does not measure reasoning: JSON escaping, metadata and encrypted signatures are
also in the envelope. No exact visible/reasoning split can honestly be recovered.

The causal configuration defect is clear even though historical attribution is
incomplete. The request had a generic legacy `reasoning.max_tokens=1024`, no effort,
and a schema permitting 62,660 narrative characters. Its prompt described the word
range as editorial guidance and independently requested summary, sections, watches,
character and continuity prose. Strict shape did not make that work fit the budget.

[Anthropic's Fable-specific guidance](https://platform.claude.com/docs/en/build-with-claude/effort)
recommends effort for adaptive thinking and describes the total ceiling as shared.
[Thinking configuration](https://platform.claude.com/docs/en/build-with-claude/thinking)
distinguishes Fable's adaptive behavior from legacy manual thinking budgets.
The old request therefore did not establish a 1,024-token hard reasoning limit.
OpenRouter's exact translation on this historical Azure request is unavailable;
do not claim it definitely ignored the field or that exactly 1,024 tokens were used.

The precise defensible conclusion is: generation reached its combined cap while
producing a response to an oversized contract, with no proven reasoning reservation.
The evidence does NOT establish that excessive visible prose alone, or reasoning
alone, consumed the cap. The fix addresses both controls and the missing diagnostics.

## Why PR #15 was insufficient

[PR #15](https://github.com/dwats250/market-brief/pull/15) assumed the requested
1,024 reasoning tokens left 4,500 for JSON. It correctly added fail-closed length
handling, but retained every broad schema bound and tested a small fixture using
a byte/4 estimate. Neither a submitted parameter nor a small fixture proved that
the route honored that allocation or that maximum-shape output fit it.

## Accounting and response-healing

At the [published Fable rates](https://openrouter.ai/anthropic/claude-fable-5.1-20260831),
the reported cost reconciles exactly:

`18,331 × $10/M + 5,524 × $50/M = $0.18331 + $0.27620 = $0.45951`.

There is no visible separate healing surcharge. This does not exclude any work
already included in reported completion accounting. Historical healing metadata
was absent, so its actual participation and content changes are unknown.
[The healing documentation](https://openrouter.ai/docs/guides/features/plugins/response-healing)
describes JSON repair, not a guaranteed way to complete truncated responses.
There is no evidence to justify removing it. It remains enabled. No application
repair/model call is added, and length output is rejected even if parseable.

## Patch and budget

`src/market_brief/synthesize.py` owns the smaller schema, schema factoring, compact
wire encoding, low-effort request, single attempt, and sanitized accounting.
`config/editions.json` replaces `reasoning_max_tokens` with `reasoning_effort: low`;
ceilings and edition word targets stay unchanged. `prompts/synthesis.md` shares the
existing word target across ALL prose and requests compact JSON and minimal sufficient
citations. No changes to `context.py`, evidence selection, renderer, scheduling,
Cloudflare, source collection or the Cuttingboard repository are needed.

| Bound | Before rich | After rich |
|---|---:|---:|
| Headline characters | 160 | 160 |
| Banner limitation | 1,800 | 200 |
| Summary text, each | 1,800 | 360 |
| Section text, each | 1,800 | 240 |
| Paragraph uncertainty / alternative | 500 / 500 | 80 / 100 |
| Attention reason | 1,800 | 120 |
| Watch condition / confirmation / contradiction | 1,800 each | 140 / 100 / 100 |
| Character | 1,800 | 180 |
| Relationship statement / reason | 1,800 / 500 | 140 / 100 |
| Carried-watch reason / change text | 1,800 each | 120 / 140 |
| References per record | 12 | 4 |
| Identifier / instrument length | Unbounded | 96 / 40 |
| Cuttingboard narrative paragraphs | 1, rejected semantically | 0 |

Rich retains two summary paragraphs, three attention items, three new watches,
three relationships, three carried-watch assessments and three changes. All fields
and all grounding checks remain. Light retains its smaller summary/attention/watch
counts, uses 300-character summary, 130/80/80 watch criteria, 140-character character,
100-character watch updates, and at most two relationships/changes. It can still
assess all three carried watches. Citation limits require narrower fully supported
claims, never missing support; the prompt says so explicitly.

| Measurement | Before | After |
|---|---:|---:|
| Archived PREMARKET user message | 32,526 bytes | 28,250 bytes |
| Saved context, canonical encoding | 25,346 bytes | Unchanged |
| Schema embedded in user message | 7,161 bytes | 4,921 bytes |
| System prompt | 7,646 bytes | 8,849 bytes |
| System + user + transport schema, before envelope escaping | 47,333 bytes | 42,020 bytes |
| Schema maximum prose characters, rich | 62,660 | 6,180 |
| Maximum-shape rich JSON, representative IDs | 70,979 bytes | 10,414 bytes |
| Maximum-shape light JSON, representative IDs | 60,161 bytes | 7,921 bytes |
| New rich cold-start stress shape, no updates/changes | — | 8,595 bytes |
| Rich useful prose target | 350–500 words | 350–500 across all prose |
| Reasoning control | Requested 1,024; honoring unproved | Adaptive low effort; no fixed reservation |
| Total completion ceiling, rich / light | 5,524 / 3,524 | 5,524 / 3,524 |
| Application transport attempts | Up to 3 | Exactly 1 |

The old schema has NO finite total serialized bound: identifier strings are
unbounded, and the event-horizon pattern branch also omitted its string type.
Even excluding the semantically forbidden Cuttingboard paragraph, it allowed
59,860 prose characters. The stress shape above uses every schema array and
representative distinct references, not a maximum possible response or grounded
market claims. The new prose character bounds reduce that surface by about 90%.

The new rich/light stress estimates are 3,472 / 2,641 visible tokens at byte/3,
leaving 2,052 / 883 of their total ceilings for adaptive reasoning and variance.
This estimate is deliberately more conservative than PR #15's byte/4, but it is
still NOT the native Fable tokenizer. The unchanged complete sample is 4,146 compact
bytes and 334 prose words; the continuity fixture has 383 prose words and also
validates. A normal 350–500-word response should be roughly 4.5–7 KB depending on
citations and continuity, substantially below the all-fields-at-maximum shape.
This is an engineering expectation, not an observed new model result.

Schema bounds do not constrain JSON indentation or force a global 500-word count;
the explicit editorial allocation supplies that guidance. Extremely long IDs,
Unicode/control characters and adversarial strings can exceed the representative
byte/token estimate. Static proof cannot guarantee adaptive thinking duration or
model quality. The next single paid verification must demonstrate both headroom
and useful analysis; a merely parseable answer is insufficient.

For completeness, a conservative upper bound for compact serialization of the new
schema, allowing six encoded bytes per bounded string character plus JSON structure,
is 98,493 rich / 75,227 light bytes. This overestimates enums/patterns and semantic
restrictions but illustrates why a representative byte/token stress test cannot
honestly be described as proof that every schema-valid string fits the token cap.

## Local verification and reproduction

No Fable/OpenRouter generation, paid token-count request, workflow dispatch or push
was made during investigation or implementation. GitHub log/artifact reads and
public documentation were the only remote evidence. GitNexus had no indexed repo;
the call chain was traced directly from the source.

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest tests/test_synthesis_budget.py -q -s
.venv/bin/ruff check src tests
git diff --check
```

Tests exercise field overflows against the local AND factored transport schema,
maximum-shape bytes and edition ordering, exact preservation of saved context,
reasoning/healing diagnostics without private content, one HTTP attempt on timeout,
HTTP failure and malformed transport, no retry on length/semantic rejection, and
the existing grounding, continuity, render and presentation behavior.

Observed final local result: **207 tests passed in 4.85s**; Ruff reported **All
checks passed!**; `git diff --check` exited zero. The initial new regression suite
against the old implementation had 12 failures and 3 passes, including failure
of both maximum-shape budget checks and acceptance of excessive prose.

To reconstruct the context comparison offline after downloading the artifact:

```sh
PYTHONPATH=src .venv/bin/python - <<'PY'
import json
from pathlib import Path
from market_brief.synthesize import construct_prompt

root = Path('/tmp/market-brief-34480006410')
evidence_path = next(root.rglob('evidence.json'))
packet = json.loads(evidence_path.read_text())
context = json.loads(evidence_path.with_name('analyst_context.json').read_text())
_, user = construct_prompt(packet, context=context)
projected = json.loads(user)
projected.pop('output_schema')
assert projected == context
print(len(user.encode()))  # 28250 at this implementation
PY
```

Run `test_synthesis_budget.maximum_shape` with `PYTHONPATH=src:tests`
on the old/new schemas for the serialized shape comparison.
The old schema is available in `git show 1a7752d:src/market_brief/synthesize.py`;
the before measurement was saved before editing. These operations never invoke a model.

## One owner-authorized PREMARKET verification

After owner review and normal promotion of the final implementation commit, designate
ONE scheduled PREMARKET invocation as the paid verification. Alternatively designate
one explicit PREMARKET dispatch in the actual premarket window, coordinated with the
scheduled invocation so it is not a second call. Do not also run commissioning,
experiments, probes that invoke synthesis, or recovery. No scheduling redesign is
part of this patch. Do not label a later market phase PREMARKET to test it.

Verify the job checks out the reviewed implementation and sends low effort, unchanged
5,524 ceiling, strict schema, response-healing, parameter support required and no
provider fallback. There must be exactly one application synthesis request. Preserve
its evidence, context, narrative and metadata. No rerun on any failure.

PASS requires ALL of:

1. Correct PREMARKET/current evidence, expected configured/resolved model, one request,
   and `finish_reason=stop` with a normal native completion (not length).
2. Unmodified schema, reference, numeric grounding, continuity and publication checks
   pass. Rendered output is readable and preserves the evidence-first analysis.
3. Roughly 350–500 useful words across model prose; less only for a documented thin
   evidence packet. Relationships, contradictions and distinct observable watch
   confirmation/contradiction remain useful. No repetitive filler or unsupported claims.
4. Completion tokens at most 4,143 (75% of 5,524), compact JSON at most 7,000 bytes,
   and reported total cost at most $0.40. These are verification gates, not higher
   runtime ceilings. A successful response near the cap FAILS cost containment.
5. Response ID, actual route/model, content bytes, cost and nested reasoning-token
   accounting are recorded. Missing reasoning accounting means attribution remains
   unverified and is a verification FAIL, not zero reasoning. Inspect any available
   healing telemetry; absent router metadata remains explicitly unknown.

Any failed criterion means FAIL: retain the artifacts and stop. Do not retry, raise
the ceiling, silently repair/truncate output, or invoke another model. A failure
requiring another paid run returns to the owner for separate authorization.

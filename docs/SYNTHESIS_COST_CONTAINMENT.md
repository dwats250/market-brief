# Production synthesis cost containment — 2026-09-10

## Operational pause — remains in force

Before investigating the urgent addendum, the four live Cron Triggers on
`market-brief-scheduler` were removed using Cloudflare's trigger-only
`PUT /accounts/{account_id}/workers/scripts/market-brief-scheduler/schedules`
with JSON body `[]`. A subsequent GET returned `{"schedules": []}` at
**2026-09-10 14:23:42 UTC** (07:23:42 America/Vancouver).
The deployment list before/after was identical. The active deployment remains
`e98e28f2-8b7a-4068-8668-261e8a9df710`, with Worker version
`3d2cd283-4941-439a-ad7c-d2b94520454f` at 100%.
Evidence is saved at `/tmp/market-brief-cron-pause-2026-09-10.json`.

Because Cloudflare documents up to 15 minutes for trigger removals to propagate,
the GitHub executor was also temporarily disabled: workflow ID `352850846`,
`.github/workflows/schedule.yml`, state **`disabled_manually`**. No queued or
in-progress jobs were present. This blocks delayed wake-ups during propagation.
Neither operation changes repository source, Worker code or secret bindings.
Existing Wrangler login was refreshed normally; credentials were not rotated.
No Cuttingboard repository or Worker was modified.

Both operational safeguards remain paused. Green static tests and a completed
patch do NOT authorize resumption. Source still contains the original crons;
do not redeploy the scheduler or enable the executor while the hold is active.
One paid verification requires explicit owner authorization and does not imply
permission to restore recurring cron triggers.

Sources: [Cloudflare schedule update API](https://developers.cloudflare.com/api/resources/workers/subresources/scripts/subresources/schedules/methods/update/),
[Cron propagation behavior](https://developers.cloudflare.com/workers/configuration/cron-triggers/).

## Evidence and root cause

Baseline: `1a7752dbd7705876216bd72b74858c69674cb972`, repository
`dwats250/market-brief`. Read-only inspection of
[run 34480006410](https://github.com/dwats250/market-brief/actions/runs/34480006410)
and artifact `10153213045` reproduced the archived user message at exactly 32,526
bytes. The artifact contains `evidence.json`, `analyst_context.json`, and failed
`metadata.json`; it contains neither the response body nor a narrative.

The addendum's [OPEN_1M run 34483191122](https://github.com/dwats250/market-brief/actions/runs/34483191122)
was independently verified from its logs and downloaded artifact: 27,310-byte
packet, 14,227-byte catalog, 7,161-byte schema copy, 15,697 prompt tokens, exactly
3,524 completion tokens, `finish_reason=length`, 4,749 content bytes, Azure and
$0.33317. Both profiles exhausted their exact caps, establishing a systemic
output-budget defect rather than a PREMARKET-only failure. Their combined reported
cost was $0.79268; neither published.

Read-only job inspection also found [run 34486249474](https://github.com/dwats250/market-brief/actions/runs/34486249474),
OPEN_30M at 14:00 UTC, completed before the pause. It constructed 28,063 bytes and
failed with HTTP 400. Its logs expose neither usage nor an upstream error body;
do not count it as another length failure or invent its cost/cause.

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
already included in provider completion accounting. More decisively,
[OpenRouter's launch documentation](https://openrouter.ai/announcements/response-healing-reduce-json-defects-by-80percent)
identifies healing as free CPU-side processing. Historical healing metadata was
absent, so its actual participation and content changes are unknown, but there is
no basis to blame it for model token expenditure.
[The healing documentation](https://openrouter.ai/docs/guides/features/plugins/response-healing)
describes JSON repair, not a guaranteed way to complete truncated responses.
There is no evidence to justify removing it. It remains enabled. No application
repair/model call is added, and length output is rejected even if parseable.

## Duplicate schema: route-specific decision

The user-message schema is normally redundant with native structured-output
interfaces: [OpenRouter's documented interface](https://openrouter.ai/docs/guides/features/structured-outputs)
uses `response_format` with strict `json_schema`, and
[Claude CLI](https://code.claude.com/docs/en/headless) uses `--json-schema` alongside
`--output-format json`. The installed CLI's help confirms that option.

However, repository history supplies direct counterevidence for removing the
production OpenRouter copy without another experiment. Commit `429ea5f` removed
it while preserving strict `response_format`; paid Azure run
[34278983083](https://github.com/dwats250/market-brief/actions/runs/34278983083)
returned `finish_reason=stop`, 10,056 content bytes and `malformed narrative at banner`.
Commit `dfb232d` restored the copy, and run `34280109434` subsequently passed.
The first experiment also changed the evidence projection and did not require
provider parameter support, so it is not a controlled proof that duplication is
universally necessary. The raw banner was not retained, leaving its exact violation
unknown. Conversely, documentation alone does not establish that removing the copy
is safe on this previously failing route. `require_parameters: true` filters for
advertised parameter support; it is not proof of runtime schema compliance.

For the next call's success probability, retain the **factored 4,921/4,919-byte copy
on OpenRouter**, alongside strict transport enforcement and local validation.
Remove it from the **isolated CLI user message**: the CLI receives the same factored
contract once through `--json-schema`, with no change to its local validator or
tools-off isolation. Tests inspect the actual mocked argv/stdin for both normal
and full-packet modes. No CLI generation or paid schema experiment was performed.

This qualifies the earlier broad source comment: a specific route failed without
the copy; it is not a universal interface requirement. Complete removal from
OpenRouter is intentionally not claimed safe by this static-only patch.

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
| Archived OPEN_1M user message, OpenRouter | 27,310 bytes | 23,485 bytes |
| Same PREMARKET / OPEN_1M evidence, CLI user message | Schema duplicated | 23,312 / 18,549 bytes |
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

Diagnostics and successful metadata preserve safe nested reasoning and optional
text-token counts. When valid reasoning and completion totals both exist,
`non_reasoning_completion_tokens` records their difference. It is an accounting
residual, not a tokenizer measurement of `message.content`. Missing or inconsistent
counts never become an invented zero. Reasoning text, summaries, signatures and
`reasoning_details` contents are never persisted.

Low effort is a product-quality choice for both profiles, not a claim of a hard
thinking cap. This is a bounded editing task over admitted facts: deterministic
code has already computed numbers, temporal comparisons and classifications;
the model should select and explain relationships, alternatives and watches.
The especially short OPEN_1M edition does not warrant open-ended scenario research.
All substantive fields and grounding checks remain. Lower effort can still affect
interpretive quality, which local fixtures cannot measure; the one authorized
verification must pass the qualitative gates as well as size and cost gates.

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
was made during investigation or implementation. Remote writes were limited to the
owner-authorized Cloudflare trigger pause and temporary GitHub executor disablement.
Other remote operations were read-only evidence retrieval. GitNexus had no indexed repo;
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

After owner review, explicit authorization and normal promotion of the final
implementation commit, keep Cloudflare crons empty. Temporarily enable the GitHub
executor for ONE manual PREMARKET dispatch in the actual premarket window, then
disable it again after completion. Do not restore recurring triggers as part of
that single-call authorization. Do not also run commissioning, experiments,
probes that invoke synthesis, or recovery. No scheduling redesign is part of
this patch. Do not label a later market phase PREMARKET to test it.

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

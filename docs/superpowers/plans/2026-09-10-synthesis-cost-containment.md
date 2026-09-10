# Synthesis Cost Containment Implementation Plan

> Execute inline under the owner's authority. No model calls, workflow dispatch or push. The urgent addendum separately authorizes the operational Cloudflare cron pause; the GitHub executor is temporarily disabled to cover propagation. No deployed code or scheduling source edits.

**Goal:** Make the next authorized PREMARKET call a bounded editorial task with observable costs.

**Architecture:** Preserve the evidence projection and validators. Replace broad narrative lengths with field-specific limits, factor repeated schema definitions, and serialize model inputs compactly. Use Fable's supported adaptive effort control, retain total ceilings, and allow only one transport attempt. Keep response-healing and retain sanitized accounting.

**Tech Stack:** Python, JSON Schema Draft 2020-12, pytest, OpenRouter Chat Completions.

- [x] Add local contract tests for field bounds, maximum-shape serialization, edition sizes, schema validity, and unchanged grounding. Run `.venv/bin/python -m pytest tests/test_contract.py tests/test_openrouter.py -q` before implementation.
- [x] In `src/market_brief/synthesize.py`, bound paragraphs, watches, attention, continuity prose and reference strings; force the already-forbidden Cuttingboard narrative array empty. Factor repeated definitions without dropping the schema from context. Compact the request serialization without changing evidence hashes or stored records.
- [x] In `config/editions.json`, replace `reasoning_max_tokens` with `reasoning_effort: low`; keep 5524/3524 ceilings. In the synthesis request send `reasoning={effort: low, exclude: true}` and disable client retries and provider fallback. Do not retry on length, parse, validation, timeout, or HTTP failure.
- [x] In `prompts/synthesis.md`, allocate the existing edition word range across ALL narrative fields, request compact JSON and minimal sufficient citations, preserve relationships/contradictions/watches, and avoid repeated analysis in continuity records.
- [x] Record response ID, resolved model, native finish reason, numeric nested usage details, and numeric/boolean healing telemetry. Never retain reasoning content. Enable routing metadata through the documented request header.
- [x] Add deterministic budget reconstruction tests and an offline report using the archived failed context. Distinguish serialized-byte estimates from native tokens and unknown historical accounting.
- [x] Run `.venv/bin/python -m pytest -q`, `.venv/bin/ruff check src tests`, and `git diff --check`; inspect the complete diff and commit only this fix. Record evidence and the one-call PASS/FAIL procedure in `docs/ARCHITECTURE.md` and an investigation report.

Investigation: run 34480006410 contains only evidence, analyst context and failed metadata; raw output, nested usage, resolved response model and response ID were not retained. Exact historical reasoning/content/healing token attribution cannot be reconstructed. Fable-specific Anthropic thinking/effort documentation supersedes the generic legacy 1024-token guidance used in PR #15. GitNexus returned no indexed repositories; source tracing is the available authority.

Final local verification: 207 tests passed in 4.85s; Ruff and git diff --check passed. The implementation is packaged as a local commit only; no push or paid verification. Detailed limits and owner-run criteria are in docs/SYNTHESIS_COST_CONTAINMENT.md.

Urgent addendum:

- [x] Pause live Market Brief cron triggers before investigation; verify empty schedules and unchanged Worker deployment. Disable the executor workflow during propagation and keep it disabled. No code/secret/credential rotation.
- [x] Verify OPEN_1M run 34483191122 and identify systemic rich/light cap exhaustion; distinguish the separately discovered OPEN_30M HTTP 400 failure.
- [x] Inspect the actual earlier schema-omission failure, not just a source comment. Keep the compressed OpenRouter copy because its necessity cannot safely be ruled out for this route; remove the duplicate from CLI stdin while preserving --json-schema and a separate schema hash.
- [x] Preserve nested accounting and derive the non-reasoning residual only from valid reported counts. Confirm healing is free CPU-side processing and retain it.
- [x] Expand tests, update the report and one-call plan to retain the operational hold, and rerun the full local checks before the addendum commit.

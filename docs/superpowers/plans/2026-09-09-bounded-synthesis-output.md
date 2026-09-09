# Bounded Synthesis Output Implementation Plan

> **For agentic workers:** Execute this bounded plan inline, as authorized by the owner; no paid synthesis or production dispatch.

**Goal:** Bound hidden reasoning and reserve the existing JSON output allowances; fail explicitly on generation truncation.

**Architecture:** Keep edition budgets in `config/editions.json`. Send `reasoning.max_tokens=1024` and `exclude=true`, with total `max_tokens=5524` for rich and `3524` for light. Reject `finish_reason=length` before decoding or validation, outside transport retries.

**Tech Stack:** Python, pytest, OpenRouter Chat Completions.

- [x] Add outbound payload tests for all five checkpoints, and truncated-response tests for malformed and valid JSON, no validation/retry, and no leaked reasoning.
- [x] Set rich/light total generation caps to 5524/3524 and reasoning caps to 1024; retain 4500/2500 final-token capacity, current words, and schema.
- [x] Pass the reasoning cap in the request and record it in successful model metadata; reject length completion before parsing with sanitized diagnostics.
- [x] Adjust the complete-fixture budget check to subtract reasoning; document billing and provider assumptions in the architecture notes.
- [x] Run full pytest and Ruff; review the exact diff, commit and open a PR. Keep production main unchanged and stop for owner authorization before any paid recovery.

Verification commands (from this worktree):
`/home/dustin/Projects/market-brief-agent/.venv/bin/python -m pytest -q`
`/home/dustin/Projects/market-brief-agent/.venv/bin/ruff check src tests`

Source: https://openrouter.ai/docs/guides/best-practices/reasoning-tokens
Anthropic's documented minimum direct reasoning allocation is 1024; exclusion hides reasoning without removing billing. Total generation includes reasoning and final output. Existing fixture size checks are approximate bytes/token sanity checks, not a live tokenizer or provider guarantee. Retain the prior final-response capacity rather than sizing the cap solely from prose words, since JSON keys, references, and continuity assessments also consume output.

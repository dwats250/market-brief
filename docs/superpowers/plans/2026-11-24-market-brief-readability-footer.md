# Market Brief Readability & Footer Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Shorten the daily Market Brief reading path on phones while keeping its interpretation, rates facts, provenance, and evidence navigable.

**Architecture:** Keep calculations, source selection, narrative, validation, and Markdown output unchanged. In the daily HTML template, retain an opening rates takeaway, put the complete Rates in Context module behind a native disclosure, and group the existing coverage, source, evidence, and technical disclosures under one compact parent disclosure. Keep all row markup, IDs, and links intact so the existing hash-reveal behavior can open nested disclosures.

**Tech Stack:** Jinja2 HTML template, inline CSS and JavaScript, pytest, Ruff, headless Chrome.

---

### Task 1: Keep a rates takeaway visible and fold the detailed module

**Files:**
- Modify: `templates/brief.html.j2`
- Test: `tests/test_readability_pass.py`
- Check: `tests/test_rates_intelligence.py`

- [x] Add regression coverage that the existing rates lead remains visible when supplied; when it is absent, the first existing `macro.panel.leaders` line is shown with the module caption and a link to `#rates-context`.
- [x] Add regression coverage that Rates in Context is a native closed `<details>` disclosure whose summary retains its title, Treasury-entry date, and row count; its complete captioned rows, chart, spreads, horizon notes, funding values, caveats, and exact-value citations remain present after opening.
- [x] Run the focused tests and confirm the new expectations fail against the old markup.
- [x] Implement the visual change in `templates/brief.html.j2`; use the existing `rates_lead` where present, otherwise expose the latest existing front-versus-long-end note after the story read, and wrap the whole existing `rates-panel` contents in `<details class="rates-panel drawer" id="rates-context">` with a native `<summary>` and no removed data.
- [x] Run `tests/test_readability_pass.py` and `tests/test_rates_intelligence.py`; inspect the module at 320, 390, and 1280 CSS pixels in both themes.

### Task 2: Put the footer evidence drawers under one compact parent

**Files:**
- Modify: `templates/brief.html.j2`
- Test: `tests/test_readability_pass.py`
- Check: `tests/test_reader_truth.py`, `tests/test_presentation.py`, `tests/test_render.py`

- [x] Update regression coverage so the parent summary reports coverage-note, unavailable-source, and evidence-row counts; its nested coverage drawer opens by default after the parent is opened; source rows, ledger rows, stable IDs, citation anchors, and technical facts remain unchanged.
- [x] Run the focused tests and confirm the new expectations fail against the old markup.
- [x] Wrap the existing four footer drawers in one closed native `<details class="drawer coverage-disclosure">`; keep the accessible `h2` inside its first `<summary>`, show counts in that summary, retain the nested coverage drawer's existing open state, and preserve every source and evidence row verbatim.
- [x] Keep the existing `reveal()` script and prove a citation fragment opens the parent plus every nested evidence group, scrolls to the row, and remains keyboard-operable through native summaries and links.
- [x] Run focused footer, reader-truth, rendering, and phone-width tests at 320, 390, and 1280 CSS pixels in both themes.

### Task 3: Record the visual rule and validate the real close

**Files:**
- Modify: `docs/VISUAL_LANGUAGE.md`
- Create: `docs/superpowers/plans/2026-11-24-market-brief-readability-footer.md`
- Evidence: isolated screenshots and real-run comparison under `/tmp` until the PR evidence format is chosen.

- [x] Update the visual-language rules for the always-visible rate takeaway, progressive rates disclosure, and compact top-level coverage/evidence disclosure.
- [x] Re-render the archived Nov 24 `CLOSE_1M` evidence and frozen interpretation offline with the current template; do not invoke collection, synthesis, providers, publication, or continuity advancement.
- [x] Compare the rendered Markdown, evidence IDs, source rows and statuses, timestamps, citation fragments, and stale-date coverage notes with the archived page and evidence.
- [x] Capture before/after browser screenshots at 320, 390, and 1280 pixels in light and dark themes; verify deep-link expansion and keyboard activation; prepare one clean branch and PR without merging PR #82 or this branch.
- [x] Run all relevant regression tests, the full pytest suite, Ruff, and a final independent source-level review; request owner review and stop.

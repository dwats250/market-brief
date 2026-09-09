# Market Brief

A local evidence-first pre-market briefing desk that turns deterministic market facts into a readable brief.

## Current state

The pipeline supports live collection, fixture replay, a saved analyst context per run, structured session continuity (previous close → premarket → intraday → close), edition budget profiles, deterministic rendering, one configured analyst synthesis, and a stable `output/latest.html` inspection path. Read the [architecture](docs/ARCHITECTURE.md), [brief schema](docs/BRIEF_SCHEMA.md), [data sources](docs/DATA_SOURCES.md), and [Cuttingboard boundary](docs/CUTTINGBOARD_BOUNDARY.md) for detail.

## Commands

```bash
python -m market_brief premarket --replay                     # admits tests/fixtures/continuity.sample.json
python -m market_brief premarket --replay --continuity PATH   # replay against another bundle
python -m market_brief schedule --checkpoint PREMARKET
python -m market_brief continuity-restore --from-file bundle.json
python -m market_brief publish
python -m market_brief open
python -m pytest
```

The replay uses fictional evidence and is labeled in the generated brief.
Scheduled runs resolve exchange sessions in `America/Vancouver`; the five
checkpoints are `PREMARKET`, `OPEN_1M`, `OPEN_30M`, `AFTERNOON`, and `CLOSE_1M`.

## Output

The latest rendered brief is at `output/latest.html`. `python -m market_brief publish` copies only that human-facing HTML to `publish/index.html` for a static host. Each run under ignored `runs/<session>/<mode>-<checkpoint>-<time>-<id>/` holds `evidence.json`, `analyst_context.json`, `narrative.json`, `edition_state.json`, `session_handoff.json` (substantiated closes only), `metadata.json`, and the rendered brief. Accepted production state lives in `runs/continuity/bundle.json`, restored from and uploaded to Actions artifacts. Rendered fixture editions are in `examples/editions/`.

## Core principles

- Collect and validate deterministic evidence before AI synthesis.
- Keep `OBSERVED`, `INTERPRETATION`, and `WATCH` visibly separate.
- Treat Cuttingboard as optional, read-only context with no writable authority.
- Ship the pre-market cadence first.
- Prefer a local, simple architecture before services or frameworks.

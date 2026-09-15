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
Scheduled runs resolve exchange sessions in `America/Vancouver`. Two checkpoints
synthesize: `PREMARKET` (6:00 PT, the rich edition) and `OPEN_30M` (7:00 PT, the one
interpretive update after the open). Every other checkpoint is deterministic and never
calls the analyst: `OPEN_1M` (6:31 PT) and `HOURLY_0800` … `HOURLY_1200` refresh the
observed record under the last accepted interpretation, and `CLOSE_1M` is a close
snapshot that hands the session off to the next premarket. Every page carries two
clocks, "Interpretation as of" and "Data as of", plus the scheduler's next update.

## Output

The latest rendered brief is at `output/latest.html`. `python -m market_brief publish` copies only that human-facing HTML to `publish/index.html` for a static host. Each run under ignored `runs/<session>/<mode>-<checkpoint>-<time>-<id>/` holds `evidence.json`, `edition_state.json`, `session_handoff.json` (substantiated closes only), `metadata.json`, and the rendered brief; synthesis runs add `analyst_context.json` and `narrative.json`, and deterministic runs record which interpretation they carried in `metadata.json`. Accepted production state lives in `runs/continuity/bundle.json`, restored from and uploaded to Actions artifacts. Rendered fixture editions are in `examples/editions/`.

## Core principles

- Collect and validate deterministic evidence before AI synthesis.
- Keep `OBSERVED`, `INTERPRETATION`, and `WATCH` visibly separate.
- Treat Cuttingboard as optional, read-only context with no writable authority.
- Ship the pre-market cadence first.
- Prefer a local, simple architecture before services or frameworks.

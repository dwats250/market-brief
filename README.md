# Market Brief

A local evidence-first pre-market briefing desk that turns deterministic market facts into a readable brief.

## Current state

The pre-market pipeline supports live collection, fixture replay, deterministic rendering, optional Claude synthesis, and a stable `output/latest.html` inspection path. Read the [architecture](docs/ARCHITECTURE.md), [brief schema](docs/BRIEF_SCHEMA.md), [data sources](docs/DATA_SOURCES.md), and [Cuttingboard boundary](docs/CUTTINGBOARD_BOUNDARY.md) for detail.

## Commands

```bash
python -m market_brief premarket --replay
python -m market_brief open
python -m pytest
```

The replay uses fictional evidence and is labeled in the generated brief.

## Output

The latest rendered brief is at `output/latest.html`. Timestamped session artifacts remain under ignored `runs/`.

## Core principles

- Collect and validate deterministic evidence before AI synthesis.
- Keep `OBSERVED`, `INTERPRETATION`, and `WATCH` visibly separate.
- Treat Cuttingboard as optional, read-only context with no writable authority.
- Ship the pre-market cadence first.
- Prefer a local, simple architecture before services or frameworks.

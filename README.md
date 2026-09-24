# Market Brief

Market Brief is an evidence-first market briefing desk: deterministic market data first, model-assisted interpretation second.

It produces one rich premarket brief, one interpretive update after the open, then keeps the market state fresh through deterministic intraday updates without repeatedly spending model compute or rewriting the accepted analysis.

## Daily cadence

| Checkpoint | Market timing | Role | Analyst calls |
|---|---|---|---:|
| `PREMARKET` | NYSE open −30m | Flagship rich brief: prior close, overnight/premarket context, continuity, watches | 1 |
| `OPEN_1M` | Open +1m | Deterministic opening-print refresh | 0 |
| `OPEN_30M` | Open +30m | Opening-structure interpretation; final routine synthesis of the day | 1 |
| `HOURLY_1100` … `HOURLY_1500` | Exchange-clock hours inside the session | Deterministic numbers/state refresh under the frozen interpretation | 0 |
| `CLOSE_1M` | Close +1m | Deterministic close snapshot and continuity handoff | 0 |

Checkpoint timing is anchored to the NYSE session, including holidays and early closes. Pacific time is presentation only.

After a synthesis is accepted, its interpretation is frozen in continuity. Later deterministic updates can refresh observed values, tables, clocks, flags, and horizon state, but they do not call the analyst or rewrite the accepted prose. A carried page therefore names both clocks in its one clock line, **Analysis anchored** (the interpretation) and **Observed record refreshed** (this run), plus the scheduler's next update; a synthesis page reads **As of**. Each row keeps its own observation clock.

## Product principles

- **Evidence before interpretation.** Collect and validate deterministic evidence before any model call.
- **Scarce interpretation, frequent freshness.** Spend compute where judgment adds value; use deterministic refreshes for routine intraday state.
- **Frozen accepted analysis.** A later data refresh cannot silently rewrite the interpretation that was paid for and accepted.
- **Visible provenance.** Narrative claims, watches, tables, and evidence remain locally inspectable.
- **Fail closed.** Missing continuity, invalid evidence, or rejected synthesis does not overwrite the last accepted production brief.
- **Session continuity.** Previous close → premarket → opening structure → intraday refreshes → close handoff is one explicit chain.
- **No trading authority.** Market Brief explains and tracks market structure; it does not grant trade permission. Cuttingboard is optional, read-only context.

## Pipeline

The production path is intentionally small:

```text
sources
  ↓
deterministic evidence + derived comparisons
  ↓
validated analyst context (synthesis checkpoints only)
  ↓
accepted interpretation + continuity state
  ↓
deterministic renderer
  ↓
GitHub Pages
```

Production state is restored across ephemeral runners through a hash-validated continuity bundle. Synthesis runs save their analyst context and narrative; deterministic runs save the fresh observed state and the identity of the frozen interpretation they carried.

## Commands

```bash
python -m market_brief premarket --replay                     # fictional replay evidence
python -m market_brief premarket --replay --continuity PATH   # replay against another bundle
python -m market_brief schedule --checkpoint PREMARKET
python -m market_brief continuity-restore --from-file bundle.json
python -m market_brief publish
python -m market_brief open
python -m pytest
```

Replay output is explicitly labeled and cannot masquerade as live production.

## Output and evidence

The latest rendered brief is written to `output/latest.html`. `python -m market_brief publish` copies only the human-facing HTML to `publish/index.html` for static hosting.

Each run under `runs/<session>/<mode>-<checkpoint>-<time>-<id>/` records the evidence and metadata needed to explain what happened. Synthesis runs additionally contain `analyst_context.json` and `narrative.json`; deterministic updates record the carried interpretation instead of generating another one. Accepted production continuity lives in `runs/continuity/bundle.json` and is restored from/uploaded to GitHub Actions artifacts.

Rendered fictional examples live in `examples/editions/`.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Brief schema](docs/BRIEF_SCHEMA.md)
- [Data sources](docs/DATA_SOURCES.md)
- [Cuttingboard boundary](docs/CUTTINGBOARD_BOUNDARY.md)
- [Project state](PROJECT_STATE.md)
- [Decisions](DECISIONS.md)

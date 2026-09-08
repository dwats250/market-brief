# Product: a briefing worth reading

## The human problem

Dustin enjoys assembling the day's market puzzle: indices, sectors, important
names, rates, currencies, commodities, events, and the occasional contradiction
that makes a session interesting. Collection is repetitive; synthesis requires
attention. Market Brief should automate collection and organize the evidence
without flattening uncertainty into a confident market story.

The output is a personal analyst's note, readable in three to five minutes. It
should feel nutritious, appealing, and specific to this session. A friend should
understand it without knowing Cuttingboard. Enjoyment and informed curiosity are
legitimate outcomes; the product is not measured by trade signals or returns.

## Product promise

- Establish what is known, when it was observed, and what remains unavailable.
- Explain the few relationships that matter, including contradictory evidence.
- Identify what changed and what deserves another look.
- Make facts easy to scan and interpretation pleasant to read.
- Keep conditional watches testable without prescribing a position.

The defining split is OBSERVED / INTERPRETATION / WATCH. "Equities advanced after
the release" states sequence. "The release caused the advance" needs supporting
evidence and may remain a hypothesis. No model-generated state is trading
permission, and a friendly banner must never resemble Cuttingboard's authority.

## First product: PRE-MARKET v0

One command, one small evidence packet, one model synthesis, one Markdown brief
and its self-contained HTML edition. Target an on-demand run around 08:45 ET on
an exchange trading day, after common 08:30 releases where applicable. Do not
silently describe future releases as completed or assume every day has one.

Prior-close structure, today's checked events, recent primary-source context,
and available timestamped pre-market observations are enough for a first useful
brief. Full futures, live yields, or comprehensive news are not required. A
brief lacking pre-market prices must prominently say "Previous-close context;
current pre-market direction unavailable." It cannot claim the overnight thesis
has been established. A first live acceptance run should obtain valid SPY/QQQ
history and a freshly checked event calendar, plus real sourced context; a
fictional replay is not live acceptance.

The core equity scope starts with the independently mirrored 22-symbol
measurement universe in [the boundary](CUTTINGBOARD_BOUNDARY.md). The personal
list is separately editable. Broader macro coverage is independent of those
symbols. No automatic scan of thousands of stocks is required.

## Cadence after the first useful brief

| Checkpoint | Suggested window, ET | Primary editorial job |
|---|---|---|
| Pre-market | Around 08:45 | Initial map, event risk, previous-close structure, current observations where available |
| Open / early | 09:40–09:50 | Did the gap hold, and did overnight expectations survive the opening reaction? |
| Post-open | 10:00–11:00 | What session character emerged: concentration, broadening, rotation, cross-asset tensions? |
| Close | After the actual close plus source finalization delay | What persisted, faded, rotated, surprised, and carries forward? |

These are conceptual checkpoints, not schedules to implement now. Exchange
calendar, holidays, DST, and early closes determine session boundaries. A 13:00
early close must not wait for an assumed 16:00 close; preliminary closes must be
labeled until final bars arrive.

Later briefs lead with "Changed since [brief time]": at most three changes,
then persisted context and invalidated interpretations. If no previous comparable
brief exists, declare a baseline read. Never manufacture a delta. A missed
checkpoint does not require rebuilding all earlier briefs.

## Three distinct lists

| List | Meaning | Ownership |
|---|---|---|
| Personal watchlist | Names Dustin likes to follow | Local user preference |
| Measurement universe | Bounded instruments used to describe market structure | Versioned local mirror, independently maintained |
| Higher-timeframe attention | Names with a specific, evidenced daily/weekly development | Deterministic trigger plus editorial selection |

None is a qualified-trade list. A quoted Cuttingboard candidate is a fourth,
separately attributed category and never a Market Brief recommendation.

For v0, consider only two higher-timeframe triggers: a completed daily close
crossing its 50-session moving average, or a 20-session return spread of at least
3 percentage points in magnitude versus its declared benchmark. These are
initial attention filters, not calibrated signals. Show the date, values,
benchmark, trigger, and why it merits attention; publish at most three names.
Model selection can omit a triggered name, but cannot create an uncomputed
trigger. Scheduled earnings can explain attention without becoming a technical
trigger. No support/resistance engine, ATR, 200-day model, or ranking score yet.

## Editorial character and acceptance

Use short paragraphs, precise verbs, restrained color, and a compact source
strip. Three meaningful relationships beat a ticker inventory. Distinguish
previous-close structure from current session behavior and broad market breadth
from participation in a small observed basket.

For the first few real briefs, ask Dustin: "Did this save me assembly time? What
did I learn? Which sentence was unsupported or unhelpful? Would I read another?"
Record corrections locally. Do not invent a longitudinal scoring system.

## Non-goals and separation

No orders, options selection, position sizing, entry/exit instructions, price
targets, automated recommendations, or alterations to Cuttingboard. No hosting,
email delivery, social posting, account creation, paid subscriptions, database,
agent framework, multi-model jury, or background scheduler in v0.

The Cuttingboard Market Observer / Coverage Audit is independent. Do not supply
these briefs to its blind readers. A future comparison may reuse frozen reports
only after independently produced outputs exist, with timestamps and exposure to
Cuttingboard disclosed. This product design is not that experiment's implementation.

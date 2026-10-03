# Visual language

Read this before changing how the brief looks. It states the grammar; the stylesheet in
`templates/brief.html.j2` is its only implementation, and the tests gate its contrast and phone layout.

## Character

A financial newspaper crossed with a research note: calm, sparse, serious, warm. The page should read as
something a person cared about, never as a dashboard, a terminal, a card grid or a fintech app.

## Rules

1. **Editorial narrative stays on the page surface.** Headline, dek, paragraphs, the take, What changed,
   flags and events are ink on paper with nothing behind them.
2. **The inset surface is reserved for deterministic, checkable facts.** See the test below.
3. **Active questions use the teal gutter.** A watch carries a 2px rule in the left margin; a retired watch
   carries the same rule in grey. Nothing else uses a left rule in the gutter.
4. **Text holds the column edge.** Tints and marks sit in the gutter (`--hang`); the left edge of the text
   never moves, from the masthead to the footer.
5. **Whitespace and alignment replace rules before ornament is introduced.** Space first, a hairline
   second, the strong rule only between sections.
6. **Teal is structural.** It marks what is live: the masthead, links and `§`, the watch gutter, the list
   marker in What changed, the current curve. It is never decoration.
7. **Positive and negative colour is for numeric direction, never editorial judgment.** It appears on
   signed figures in tables. Rates and release values stay neutral. Prose is never coloured.
8. **Amber marks a limit**: a notice, an overdue clock.
9. **No general card language.** No borders, radii or shadows around blocks, and no tint behind prose, a
   table or a section.
10. **No asset-class icon vocabulary.** An instrument is its name and its ticker.
11. **Mobile reading width outranks decorative treatment.** No device may take width from the text column
    on a phone.

## Planes

| Plane | Holds | Its one signature |
|---|---|---|
| Narrative | Headline, dek, paragraphs, the take, What changed, flags, events | None: ink on paper |
| Evidence | Tables, the curve module, the release card, opened `§` evidence | Caps caption and tabular figures; compact objects on the inset surface |
| Watch | Open and carried watches | The teal gutter rule |
| Audit | The guide, Sources & coverage, the footer | Small muted type behind a disclosure |

A new element names its plane and reuses that plane's signature. If it needs a new one, change this
document in the same pull request.

## The inset surface

An object earns `--surface` only when all three hold:

1. it is observed fact, not interpretation;
2. it is self-contained, with its own label and time;
3. it is compact, no taller than about half a phone screen.

Today: the economic-release card, opened `§` evidence and proof lines, and a targeted ledger row.

The tint has square corners, no border and no shadow. It extends one `--hang` into the gutter on each side
while its text stays on the column edge. Never two surfaces side by side, never one inside another, never
a surface and a gutter rule on the same object.

The surface moves away from the ink in both themes, lighter than the page in light and darker in dark, so
text on it never loses contrast.

## Marks

This is the whole set. There are no icons.

| Mark | Means | Where |
|---|---|---|
| `§` | Evidence behind this claim | After a claim; never in tables |
| Teal square, 5px, in the gutter | A change since the last read | What changed |
| Teal rule, 2px, in the gutter | An open question (grey once retired) | Watches |
| `▸` / `▾` | Collapsed / open | Every drawer and ledger group |
| `·` `—` `→` `−` `+` | Separator, label dash, revision, sign | As typed |

## Instruments

Name in semibold, then the ticker in the one muted `.ticker` style, on one line: **Technology** XLK. The
ticker alone where it is the name. Tables and flags use the same form. Evidence lines and the ledger use
`Name · TICKER · measure`, which is a path, not a label.

## Type and space

- Serif for `h1` and `h2` only; the system sans for everything else; mono for evidence row ids only.
- Bold is a label, not emphasis: The take, verdict words, instrument names, the curve move.
- Figures are tabular, with a true minus and an explicit plus.
- Do not add a font size, weight or spacing value when an existing one is within 2px.
- Spacing tiers: 2–8px inside a component, 12px between paragraphs, 24px between components, 32px between
  subsections, and the section break. Only the section break shrinks on phones.
- Widths: prose 700px, compact data 560px (the rates table, spreads, chart and release card share it),
  five-column tables the full column.

## Themes and phones

- Every colour is a token defined in the three theme blocks; the two dark blocks are identical.
- Ink clears 7:1 and every other text token 4.5:1, on the page and on any surface it sits on.
- Direction is never carried by colour alone: the sign is in the text.
- The light page is warm paper, visibly off-white beside white. The dark page is slate; nothing glows.
- Check 320, 390 and 1280px in both themes. Nothing scrolls sideways, with every drawer open, and figures
  never wrap.

## Before adding a visual element

1. Which plane is it on, and does that plane's signature already cover it?
2. Does it improve grouping, scanning or identity? If it only decorates, stop.
3. Does it reuse existing tokens and the spacing tiers?
4. Does the text still hold the column edge, and does a phone keep its reading width?
5. Is it right in light and dark at 320, 390 and 1280px?

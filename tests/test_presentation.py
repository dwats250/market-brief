"""Signal over plumbing: the visible brief carries conclusions and useful tables, the ledger carries proof."""

from datetime import datetime, timezone

from test_history_admission import packet_at
from test_pipeline import fixture_packet, narrative

from market_brief.render import change_column, compact_equity_rows, measure_label, presentation, render

# Tuesday 2026-09-08's regular session, as `packet["run"]["session"]` carries it.
SESSION = dict(open="2026-09-08T13:30:00+00:00", close="2026-09-08T20:00:00+00:00")


def utc(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def intraday(symbol, value, observed_at):
    return dict(id=f"{symbol}-intraday", topic=symbol, metric="premarket return", value=value,
                unit="%", baseline="latest trade versus previous regular close", frequency="intraday",
                observed_at=observed_at, retrieved_at=observed_at, source_id="sample-prices",
                status="DELAYED", reason="", freshness="DELAYED", expected_freshness="LIVE",
                magnitude="NOTABLE")


def empty_attention_narrative():
    value = narrative()
    value["attention"] = []
    return value


# 3. Rows carry their instrument: name first, ticker muted.
def test_rows_name_their_instrument_with_the_ticker_muted():
    row = intraday("GLD", -1.74, "2026-09-08T19:59:00+00:00")
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"))
    assert measure_label(row, packet["run"]["session"]) == "GLD · Intraday vs prior close"
    packet["observations"].append(row)
    _, page = render(packet, narrative())
    assert '<b>Gold fund</b><span class="ticker">GLD</span>' in page
    assert '<b>Industrials</b><span class="ticker">XLI</span>' in page
    assert "<td>Intraday vs prior close</td>" not in page


# 7. Empty sections disappear; their absence is consolidated in coverage.
def test_empty_sections_are_omitted_and_consolidated():
    packet = fixture_packet()
    packet["cuttingboard"] = {"status": "UNAVAILABLE", "reason": "not requested"}
    md, page = render(packet, empty_attention_narrative())
    assert "<h2>Cuttingboard context</h2>" not in page
    assert "No admitted observations in this section" not in page
    assert "No admitted observations in this section" not in md
    assert "<h3>" not in page.split("What matters next", 1)[1].split("</section>", 1)[0]
    # Optional Cuttingboard context is not market coverage: its absence is not listed as an omission.
    limitations = page.split("Coverage limitations", 1)[1].split("</details>", 1)[0]
    assert "Cuttingboard" not in limitations and "No admitted material" not in limitations


def test_populated_sections_still_render_in_reading_order():
    """Top-down and fixed: the macro backdrop (with metals), then breadth, then the names."""
    md, page = render(fixture_packet(), narrative())
    for heading in ("What matters next", "Macro &amp; rates", "Sector view", "Equity structure",
                    "Cuttingboard context", "Sources &amp; coverage"):
        assert f"<h2>{heading}</h2>" in page
    order = [page.index(f"<h2>{h}</h2>") for h in ("What matters next", "Macro &amp; rates", "Sector view",
                                                   "Equity structure", "Sources &amp; coverage")]
    assert order == sorted(order)
    assert "<h2>Metals</h2>" not in page and "\n## Metals\n" not in md
    macro = page.split("<h2>Macro &amp; rates</h2>", 1)[1].split("</section>", 1)[0]
    assert '<div class="caption">Metals<span>20-session return spread, GDX vs GLD · ' in macro
    md_order = [md.index(f"## {h}") for h in ("What matters next", "Macro & rates", "Sector view", "Equity structure",
                                                "Sources & coverage")]
    assert md_order == sorted(md_order)
    assert md.index("**METALS** · 20-session return spread, GDX vs GLD") < md.index("## Sector view")
    # Attention items and today's event live inside What matters next, not in their own sections. NVDA is
    # already the summary's and a watch's subject, so only the industrials trigger earns a flag.
    matters = page.split("<h2>What matters next</h2>", 1)[1].split("</section>", 1)[0]
    assert '<li><b>Industrials · XLI</b>' in matters and '<li><b>NVDA</b>' not in matters
    assert "Fictional manufacturing survey" in matters
    assert "On the attention list" not in page and "Event risk" not in page


# 8. Table cell noise.
def test_mega_cap_rows_do_not_repeat_the_symbol_as_a_sublabel():
    _, page = render(fixture_packet(), narrative())
    assert "<small>NVDA</small>" not in page
    assert "<b>NVDA</b>" in page


def test_shared_observation_times_collapse_to_one_as_of():
    rows = compact_equity_rows([intraday("XLI", -0.47, "2026-09-08T19:59:00+00:00"),
                                intraday("XLE", 1.09, "2026-09-08T20:02:00+00:00")], ["XLI", "XLE"])
    # XLE's 1:02 PM PT print is after the 1:00 PM PT close, so the two prints span phases: the caption names
    # the latest trade, never "Intraday" for an after-hours print. The shared clock still collapses.
    assert change_column(rows, session=SESSION) == ("Latest trade vs prior close", "as of 1:02 PM PT")
    assert all(row["today"]["observed"] == "" for row in rows)
    assert change_column([]) == ("Change", None)


def test_sector_table_moves_shared_timestamp_to_heading():
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"))
    packet["observations"] += [intraday("XLI", -0.47, "2026-09-08T19:59:53+00:00"),
                               intraday("XLE", 1.09, "2026-09-08T19:59:59+00:00")]
    view = presentation(packet, narrative())
    assert view["sectors"]["asof"] == "as of 12:59 PM PT"
    assert all(row["today"]["observed"] == "" for row in view["sectors"]["rows"])
    _, page = render(packet, narrative())
    assert page.count("Tuesday, Sep 8 · 12:59 PM PT") <= 3  # not repeated per sector row
    assert "strongest to weakest<span>Intraday vs prior close · as of 12:59 PM PT" in page


def test_divergent_observation_times_stay_on_exception_rows_only():
    """One stale print does not push a full timestamp onto every row: the table keeps its shared clock
    and only the row that trails it carries its own short clock."""
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"))
    packet["observations"] += [intraday("XLI", -0.47, "2026-09-08T19:20:00+00:00"),
                               intraday("XLE", 1.09, "2026-09-08T19:59:59+00:00")]
    view = presentation(packet, narrative())
    assert view["sectors"]["asof"] == "as of 12:59 PM PT"
    observed = {row["symbol"]: row["today"]["observed"] for row in view["sectors"]["rows"]}
    assert observed["XLI"] == "12:20 PM PT"
    assert observed["XLE"] == ""
    _, page = render(packet, narrative())
    assert "Tuesday, Sep 8 · 12:20 PM PT" not in page.split("<h2>Sources", 1)[0]
    assert "2026-09-04" not in page.split("<h2>Sources", 1)[0]  # no raw ISO dates on the reading surface
    # An instrument without a print says so and keeps its history columns; the table stays in intraday mode.
    history = [row for row in packet["derived"] if row["topic"] == "XLI"]
    quiet_history = [dict(row, id=row["id"].replace("XLI", "XLK"), topic="XLK") for row in history]
    rows = compact_equity_rows([*packet["observations"], *history, *quiet_history], ["XLI", "XLE", "XLK"])
    label, asof = change_column(rows, session=packet["run"]["session"])
    quiet = next(row for row in rows if row["symbol"] == "XLK")
    assert label == "Intraday vs prior close" and asof == "as of 12:59 PM PT"
    assert quiet["today"]["display"] == "no print" and quiet["today"]["absent"]
    assert quiet["r20"]["display"].endswith("%") and quiet["dma"]["display"].endswith("%")


# 9. There is no headline figure strip: the tables and their captions are the only current-print surfaces above
# the ledger, so a stale fallback figure can never sit beside a live one at equal weight.
def test_no_figure_strip_and_current_prints_keep_their_label_in_the_tables():
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"))
    packet["observations"] += [intraday("QQQ", -0.15, "2026-09-08T20:02:00+00:00"),
                               intraday("XLI", -0.47, "2026-09-08T19:59:53+00:00")]
    view = presentation(packet, narrative())
    assert "chips" not in view and "figures" not in view
    md, page = render(packet, narrative())
    assert 'class="figures"' not in page and "OBSERVED SNAPSHOT" not in md
    assert view["sectors"]["change_label"] == "Intraday vs prior close"
    assert "premarket return" not in page.split("<h1>", 1)[1].split("<details>", 1)[0]


# 10. The analyst's coverage caveat is the reader-facing line inside Sources & coverage; the generated
# Basis line and other plumbing live in Technical details, where "breadth available" (sector rows exist)
# can no longer sit beside the analyst's "no breadth feed".
def sources_reading_line(page):
    return page.split("<h2>Sources &amp; coverage</h2>", 1)[1].split("<details", 1)[0]


def test_basis_excludes_technical_plumbing():
    packet = fixture_packet()
    md, page = render(packet, narrative())
    reading = sources_reading_line(page)
    for plumbing in ("Basis:", "Cuttingboard", "Bootstrap", "calendar", "BASELINE", "breadth available",
                     "breadth unavailable"):
        assert plumbing not in reading
    assert narrative()["banner"]["limitation"][:40] in reading
    technical = page.split("Technical details", 1)[1]
    assert "Bootstrap:" in technical and "Calendar" in technical
    assert "Basis:" in technical and "prior close" in technical
    header = md.split("## What matters next")[0]
    assert "Bootstrap" not in header and "Basis:" not in header


def test_basis_describes_current_prints_without_premarket_wording():
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"))
    _, page = render(packet, narrative())
    technical = page.split("Technical details", 1)[1]
    assert "current prints available" in technical
    assert "pre-market available" not in technical


def test_average_is_shown_as_distance_from_the_50dma_not_a_price():
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"))
    view = presentation(packet, narrative())
    nvda = next(row for row in view["equities"]["rows"] if row["symbol"] == "NVDA")
    history = next(h for h in packet["history"] if h["symbol"] == "NVDA")
    expected = 100 * (history["closes"][-1] / nvda["average"]["value"] - 1)
    sign = "+" if expected > 0 else "−"
    assert nvda["dma"]["display"] == f"{sign}{abs(expected):.2f}%"
    assert nvda["average"]["display"] == "114.03"  # the average itself stays in evidence
    assert nvda["r20"]["display"].endswith("%")
    _, page = render(packet, narrative())
    assert "114.03" not in page.split("<h2>Sources &amp; coverage</h2>", 1)[0]


def test_missing_current_prints_are_not_called_premarket_after_the_close():
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"), intraday=False)
    assert "pre-market" not in " ".join(packet["coverage"]["missing_domains"])
    assert "pre-market" not in packet["coverage"]["horizon"]
    assert "current prints unavailable" in packet["coverage"]["missing_domains"]


# 11. Plain presentation: no workflow enums, ranked sectors, paired yields, natural horizons.
def visible(page):
    return page.split("<body>", 1)[1].split("<script>", 1)[0]


def test_no_workflow_or_horizon_enums_are_visible():
    for now, checkpoint in (("2026-09-08T12:45:00+00:00", "PREMARKET"), ("2026-09-08T20:03:00+00:00", "CLOSE_1M")):
        packet = packet_at(utc(now), checkpoint=checkpoint)
        _, page = render(packet, narrative())
        body = visible(page)
        for token in ("PREMARKET", "OPEN_1M", "OPEN_30M", "HOURLY_", "CLOSE_1M", "NEXT_BRIEF", "NEXT_CLOSE",
                      "OPENING_HOUR", "SESSION", "INTERPRETATION", "OBSERVED"):
            assert token not in body.split('<details class="drawer"', 1)[0], token
        assert 'data-checkpoint="' in page  # machine marker stays in the head


def test_watch_horizons_render_as_natural_phrases_from_the_calendar():
    packet = packet_at(utc("2026-09-08T12:45:00+00:00"))
    view = presentation(packet, narrative())
    phrases = {w["horizon"]: w["phrase"] for w in view["next"]["watches"]}
    assert phrases == {"OPENING_HOUR": "Through the opening hour", "NEXT_CLOSE": "Into the close"}
    after = packet_at(utc("2026-09-08T20:03:00+00:00"), checkpoint="CLOSE_1M")
    view = presentation(after, narrative())
    phrases = {w["horizon"]: w["phrase"] for w in view["next"]["watches"]}
    assert phrases["NEXT_CLOSE"] == "Into the next session"
    assert phrases["OPENING_HOUR"] == "Through the next opening hour"
    value = narrative()
    value["watches"][0]["horizon"] = "NEXT_BRIEF"
    assert presentation(packet, value)["next"]["watches"][0]["phrase"] == "By the 7:00 AM PT update"
    value["watches"][0]["horizon"] = "EVENT(sample-event)"
    assert presentation(packet, value)["next"]["watches"][0]["phrase"] == "Around Fictional manufacturing survey"


def test_sectors_rank_by_labeled_spread_with_a_separate_dated_change_column():
    packet = packet_at(utc("2026-09-08T12:45:00+00:00"), intraday=False)
    view = presentation(packet, narrative())
    rows = view["sectors"]["rows"]
    assert rows[0]["symbol"] == "XLI" and rows[0]["relative"]["value"] is not None
    assert view["sectors"]["spread_label"].startswith("20-session return spread vs SPY")
    assert view["sectors"]["change_label"] == "Daily change · Fri, Sep 4"
    _, page = render(packet, narrative())
    assert '<th class="number">vs SPY</th>' in page and "Daily change · Fri, Sep 4" in page
    assert "20S" not in page and "vs SPY · 20s" not in page  # the window is explained once, in the caption
    # The ranking horizon does not silently switch when current quotes disappear.
    lagged = packet_at(utc("2026-09-08T20:03:00+00:00"), intraday=False)
    assert presentation(lagged, narrative())["sectors"]["spread_label"] == view["sectors"]["spread_label"]
    assert presentation(lagged, narrative())["sectors"]["change_label"] == "Change"
    assert all(row["today"]["display"] == "no print" for row in presentation(lagged, narrative())["sectors"]["rows"])


def test_treasury_table_pairs_only_compatible_yields_and_changes():
    packet = fixture_packet()
    view = presentation(packet, narrative())
    yields = {row["maturity"]: row for row in view["macro"]["yields"]}
    assert yields["2Y"]["level"]["display"] == "3.86%" and yields["2Y"]["change"]["display"] == "\u22124 bp"
    assert view["macro"]["yields_asof"] == "Fri, Sep 4"
    packet = fixture_packet()
    change = next(r for r in packet["observations"] if r["id"] == "treasury-10y-change")
    change["observed_at"] = "2026-09-03"
    view = presentation(packet, narrative())
    yields = {row["maturity"]: row for row in view["macro"]["yields"]}
    assert yields["10Y"]["change"]["display"] == "—" and "not paired" in yields["10Y"]["change_note"]
    assert "Thu, Sep 3" in yields["10Y"]["change_note"]
    assert view["macro"]["yields_asof"] == "Fri, Sep 4"  # the levels still share one date


def test_headline_is_not_clipped_and_long_headlines_wrap():
    value = narrative()
    value["banner"]["title"] = "Energy leadership persists while cyclicals and discretionary names keep lagging"
    _, page = render(fixture_packet(), value)
    assert "line-clamp" not in page
    assert value["banner"]["title"] in page


def test_carried_watches_and_changes_render_from_the_saved_context():
    from test_continuity import carried_setup

    from market_brief.context import edition_profile
    packet, context, _ = carried_setup()
    value = narrative()
    profile = edition_profile("OPEN_30M")
    value["summary"] = value["summary"][:1]
    value["watches"] = value["watches"][:1]
    value["attention"] = value["attention"][:2]
    value["watch_updates"] = [dict(carried_id="watch-sample-premarket-124500-tue-1", assessment="weakened",
                                   reason="The print turned positive against the premarket read.",
                                   evidence_ids=["SPY-intraday", "premarket:SPY-intraday"])]
    value["changes"] = [dict(comparison_id="cmp-premarket-SPY-intraday",
                             text="SPY moved from {{premarket:SPY-intraday}} to {{SPY-intraday}} since the premarket.",
                             evidence_ids=["SPY-intraday", "premarket:SPY-intraday"])]
    assert profile["profile"] == "light"
    md, page = render(packet, value, context)
    assert "<h2>What changed</h2><p class=\"sub\">vs premarket</p>" in page
    # The verdict on the carried watch is the change: it renders once, in What changed, and the generic change
    # that cites exactly the same rows is the same conclusion said twice, so it is not shown.
    since = page.split('<section class="since">', 1)[1].split("</section>", 1)[0]
    assert "<b>Weakened</b> — " in since and "The print turned positive against the premarket read." in since
    assert "SPY moved from −0.53% to +0.21% since the premarket." not in page
    # The watch stays live under What matters next as a question with its horizon, never with its verdict.
    matters = page.split("<h2>What matters next</h2>", 1)[1].split("</section>", 1)[0]
    assert '<span class="meta">From an earlier read · into the close</span>' in matters
    assert "weakened" not in matters and "Weakened" not in matters
    assert "CARRIED WATCH" not in page.split("<script>", 1)[0] and "Carried ·" not in page
    assert "## What changed · vs premarket" in md
    assert "- **Weakened** — " in md.split("## What changed", 1)[1].split("## What matters next", 1)[0]
    assert "FROM AN EARLIER READ · Into the close" in md and "FROM AN EARLIER READ · weakened" not in md
    assert "CARRIED WATCH" not in md


def test_markdown_tables_keep_shared_clocks_out_of_header_rows():
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"))
    packet["observations"] += [intraday("GLD", -1.74, "2026-09-08T19:59:57+00:00"),
                               intraday("GDX", -2.10, "2026-09-08T19:59:58+00:00")]
    md, _ = render(packet, narrative())
    for line in md.splitlines():
        if line.startswith("|"):
            assert line.rstrip().endswith("|"), line
    assert ("\n**METALS** · 20-session return spread, GDX vs GLD · Intraday vs prior close · as of 12:59 PM PT\n") in md
    assert "Cross-asset" not in md and "METALS STRUCTURE" not in md and "\n## Metals\n" not in md
    assert md.index("## Macro & rates") < md.index("**METALS** · ") < md.index("## Sector view")


def test_provisional_session_ending_prints_are_labeled_in_the_brief():
    from market_brief.evidence import finalize_coverage, normalize_observation
    late = utc("2026-09-08T20:38:00+00:00")
    packet = packet_at(late, checkpoint="CLOSE_1M", intraday=False)
    for symbol, value in (("SPY", 0.4), ("XLI", -0.3)):
        raw = dict(id=f"{symbol}-intraday", topic=symbol, metric="premarket return", value=value, unit="%",
                   baseline="latest trade versus previous regular close", frequency="intraday",
                   observed_at="2026-09-08T19:59:58+00:00", retrieved_at=late.isoformat(),
                   source_id="sample-prices", status="AVAILABLE", reason="")
        row = normalize_observation(raw, late, utc("2026-09-08T20:00:00+00:00"))
        row["expected_freshness"] = "LIVE"
        packet["observations"].append(row)
    finalize_coverage(packet)
    view = presentation(packet, narrative())
    assert view["sectors"]["change_label"] == "Near-close vs prior close · provisional"
    md, page = render(packet, narrative())
    assert "· provisional" in page.split("<h2>Sector view</h2>", 1)[1].split("</section>", 1)[0]
    assert "Near-close vs prior close · provisional" in md
    assert "not official closing bars" in page

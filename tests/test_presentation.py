"""Signal over plumbing: the visible brief carries conclusions and useful tables, the ledger carries proof."""

from datetime import datetime, timezone

from test_history_admission import packet_at
from test_pipeline import fixture_packet, narrative

from market_brief.render import measure_label, presentation, render, table_asof


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
    value["attention_ids"] = []
    value["attention"] = []
    return value


# 3. Observed rows carry their symbol.
def test_observed_rows_name_their_symbol():
    row = intraday("GLD", -1.74, "2026-09-08T19:59:00+00:00")
    assert measure_label(row) == "GLD · Intraday vs prior close"
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"))
    packet["observations"].append(row)
    _, page = render(packet, narrative())
    assert "<td>GLD · Intraday vs prior close</td>" in page
    assert "<td>SPY · Intraday vs prior close</td>" in page
    assert "<td>Intraday vs prior close</td>" not in page


# 7. Empty sections disappear; their absence is consolidated in coverage.
def test_empty_sections_are_omitted_and_consolidated():
    packet = fixture_packet()
    packet["cuttingboard"] = {"status": "UNAVAILABLE", "reason": "not requested"}
    md, page = render(packet, empty_attention_narrative())
    assert "<h2>On the attention list</h2>" not in page
    assert "<h2>Cuttingboard context</h2>" not in page
    assert "No admitted observations in this section" not in page
    assert "No admitted observations in this section" not in md
    assert "On the attention list" not in md.split("## Sources")[0]
    limitations = page.split("Coverage limitations", 1)[1].split("</details>", 1)[0]
    assert "attention" in limitations.lower()
    assert "Cuttingboard" in limitations


def test_populated_sections_still_render():
    _, page = render(fixture_packet(), narrative())
    assert "<h2>On the attention list</h2>" in page
    assert "<h2>Event risk</h2>" in page
    assert "<h2>Cuttingboard context</h2>" in page


# 8. Table cell noise.
def test_mega_cap_rows_do_not_repeat_the_symbol_as_a_sublabel():
    _, page = render(fixture_packet(), narrative())
    assert "<small>NVDA</small>" not in page
    assert "<b>NVDA</b>" in page


def test_shared_observation_times_collapse_to_one_as_of():
    assert table_asof(["2026-09-08T19:59:00+00:00", "2026-09-08T20:02:00+00:00"]) == "as of 1:02 PM PT"
    assert table_asof(["2026-09-08T19:59:00+00:00", "2026-09-08T20:30:00+00:00"]) is None
    assert table_asof(["2026-09-08T19:59:00+00:00", "2026-09-04"]) is None
    assert table_asof([]) is None


def test_sector_table_moves_shared_timestamp_to_heading():
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"))
    packet["observations"] += [intraday("XLI", -0.47, "2026-09-08T19:59:53+00:00"),
                               intraday("XLE", 1.09, "2026-09-08T19:59:59+00:00")]
    view = presentation(packet, narrative())
    equities = next(s for s in view["sections"] if s["key"] == "equities")
    assert equities["sector_asof"] == "as of 12:59 PM PT"
    assert all(row["today"]["observed"] == "" for row in equities["sector_rows"])
    _, page = render(packet, narrative())
    assert page.count("Tuesday, Sep 8 · 12:59 PM PT") <= 3  # observed table rows only, not per sector row
    assert "Sector snapshot · as of 12:59 PM PT" in page


def test_divergent_observation_times_stay_on_rows():
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"))
    packet["observations"] += [intraday("XLI", -0.47, "2026-09-08T19:20:00+00:00"),
                               intraday("XLE", 1.09, "2026-09-08T19:59:59+00:00")]
    view = presentation(packet, narrative())
    equities = next(s for s in view["sections"] if s["key"] == "equities")
    assert equities["sector_asof"] is None
    observed = {row["symbol"]: row["today"]["observed"] for row in equities["sector_rows"]}
    assert observed["XLI"] == "Tuesday, Sep 8 · 12:20 PM PT"
    assert observed["XLE"] == "Tuesday, Sep 8 · 12:59 PM PT"


# 9. Chip priority: current admitted market state outranks dated macro context.
def test_current_spy_qqq_chips_outrank_dated_treasury_rows():
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"))
    packet["observations"] += [intraday("QQQ", -0.15, "2026-09-08T20:02:00+00:00"),
                               intraday("XLI", -0.47, "2026-09-08T19:59:53+00:00"),
                               intraday("GLD", -1.74, "2026-09-08T19:59:57+00:00")]
    chips = [chip["id"] for chip in presentation(packet, narrative())["chips"]]
    assert chips[:3] == ["SPY-intraday", "QQQ-intraday", "XLI-intraday"]
    assert chips.index("treasury-2y-change") > chips.index("GLD-intraday")
    assert "SPY-daily" not in chips
    assert len(chips) <= 6


def test_chips_fall_back_to_daily_when_no_current_prints():
    chips = [chip["id"] for chip in presentation(fixture_packet(), narrative())["chips"]]
    assert chips[:2] == ["SPY-daily", "QQQ-daily"]


# 10. Basis is reader context; plumbing lives in Technical details.
def test_basis_excludes_technical_plumbing():
    packet = fixture_packet()
    md, page = render(packet, narrative())
    basis = page.split('class="basis">', 1)[1].split("</p>", 1)[0]
    for plumbing in ("Cuttingboard", "Bootstrap", "calendar", "BASELINE"):
        assert plumbing not in basis
    assert "prior close" in basis
    technical = page.split("Technical details", 1)[1]
    assert "Bootstrap:" in technical
    assert "Calendar" in technical
    header = md.split("## In a minute")[0]
    assert "Bootstrap" not in header


def test_basis_describes_current_prints_without_premarket_wording():
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"))
    _, page = render(packet, narrative())
    basis = page.split('class="basis">', 1)[1].split("</p>", 1)[0]
    assert "current prints available" in basis
    assert "pre-market available" not in basis


def test_chips_use_horizon_neutral_label_for_current_prints():
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"))
    chips = presentation(packet, narrative())["chips"]
    spy = next(chip for chip in chips if chip["id"] == "SPY-intraday")
    assert spy["metric_label"] == "intraday vs prior close"
    treasury = next(chip for chip in chips if chip["id"] == "treasury-2y-change")
    assert treasury["metric_label"] == "daily yield change"
    _, page = render(packet, narrative())
    assert "premarket return" not in page.split("<h1>", 1)[1].split("<details>", 1)[0]


def test_compact_average_cell_is_a_bare_price():
    packet = packet_at(utc("2026-09-08T20:03:00+00:00"))
    view = presentation(packet, narrative())
    equities = next(s for s in view["sections"] if s["key"] == "equities")
    nvda = next(row for row in equities["mega_rows"] if row["symbol"] == "NVDA")
    assert nvda["average"]["display"] == "114.03"
    assert nvda["r20"]["display"].endswith(" %")

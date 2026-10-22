"""Cboe Treasury yield indices as a separate intraday lane (2026-10-22): delayed index quotes (price = yield x10) for
13W/5Y/10Y/30Y, never the official par curve. Cboe serves no 2-year index, so 2Y is never filled. The rows run the
existing intraday freshness rules, stay out of the curve, the spreads, the named move, the chart, the analyst's
context and continuity, and render as their own small block under the par-curve module."""

import copy
import json
from datetime import datetime, timedelta
from unittest.mock import patch

from test_curve import feed, utc
from test_pipeline import NOW, narrative
from test_rates_module import rates_packet

from market_brief.collect import (
    CBOE_YIELDS,
    HOSTS,
    TREASURY,
    SourceError,
    allowed,
    cboe_yield_url,
    collect_live,
    source,
)
from market_brief.context import analyst_context, supplied_ids
from market_brief.curve import rates, spread_rows
from market_brief.evidence import finalize_coverage, normalize_packet
from market_brief.render import presentation, render

LAST = "2026-10-22T08:20:03.722000-05:00"   # Cboe stamps its index quotes in Central time
CURVE = feed(("2026-10-20", {"2Y": "4.70", "5Y": "4.84", "10Y": "5.02", "30Y": "5.35"}),
             ("2026-10-21", {"2Y": "4.72", "5Y": "4.85", "10Y": "5.01", "30Y": "5.33"}))
QUOTES = {"IRX": (39.50, 39.60), "FVX": (48.19, 49.72), "TNX": (50.36, 51.46), "TYX": (51.10, 52.00)}


def quote(symbol, price, prev, last=LAST):
    return json.dumps({"timestamp": "2026-10-22 13:20:13", "symbol": f"_{symbol}", "data": {
        "symbol": f"^{symbol}", "current_price": price, "prev_day_close": prev, "price_change": round(price - prev, 2),
        "last_trade_time": last, "security_type": "index"}})


def collect(now, quotes=QUOTES, last=LAST, treasury=CURVE):
    calls = []

    def fetcher(url, deadline):
        calls.append(url)
        if url.startswith(TREASURY) and "daily_treasury_yield_curve" in url and "real" not in url:
            return treasury
        for symbol, (price, prev) in quotes.items():
            if url == cboe_yield_url(symbol):
                return quote(symbol, price, prev, last)
        raise SourceError("HTTP 403" if "cboe" in url else "offline")
    class Clock(datetime):  # retrieval follows the run's own clock, as in production
        @classmethod
        def now(cls, tz=None):
            return now
    with patch("market_brief.collect.datetime", Clock):
        return collect_live(now, fetcher=fetcher), calls


def by_id(rows):
    return {row["id"]: row for row in rows}


# --- collection: host, URLs, units, ids --------------------------------------------------------------------------

def test_only_the_four_index_quotes_are_requested_from_cboe():
    assert "cdn-api.cboe.com" in HOSTS
    assert [label for label, _ in CBOE_YIELDS] == ["13W", "5Y", "10Y", "30Y"]
    for _, symbol in CBOE_YIELDS:
        assert allowed(cboe_yield_url(symbol))
    assert cboe_yield_url("TNX") == "https://cdn-api.cboe.com/api/global/delayed_quotes/quotes/_TNX.json"
    for url in ("https://cdn-api.cboe.com/api/global/delayed_quotes/options/SPY.json",
                "https://cdn-api.cboe.com/api/global/delayed_quotes/quotes/_2YY.json",
                "https://cdn-api.cboe.com/api/global/delayed_quotes/quotes/_TNX.json?x=1",
                "http://cdn-api.cboe.com/api/global/delayed_quotes/quotes/_TNX.json"):
        assert not allowed(url), url


def test_index_points_become_percent_and_whole_bp_with_a_utc_clock():
    now = utc("2026-10-22T13:30:00+00:00")
    (raw, calls) = collect(now)
    rows = by_id(raw["observations"])
    ten, change = rows["cboe-yield-10y"], rows["cboe-yield-10y-change"]
    assert ten["value"] == 5.036 and ten["unit"] == "% yield" and ten["metric"] == "intraday yield"
    assert ten["topic"] == "US 10Y" and ten["frequency"] == "intraday" and ten["source_id"] == "cboe-yields"
    assert ten["observed_at"] == "2026-10-22T13:20:03.722000+00:00"
    assert ten["baseline"] == "Cboe yield index, delayed quote, not the official par curve"
    assert change["value"] == -11 and isinstance(change["value"], int) and change["unit"] == "bp"
    assert change["metric"] == "intraday yield change" and change["baseline"] == "vs prior index close"
    assert {rows[f"cboe-yield-{t}"]["topic"] for t in ("13w", "5y", "30y")} == {"US 13W", "US 5Y", "US 30Y"}
    assert rows["cboe-yield-5y-change"]["value"] == -15 and rows["cboe-yield-13w"]["value"] == 3.95
    assert not any("2y" in ident for ident in rows if ident.startswith("cboe-"))  # 2Y is never filled
    assert not any("2YY" in url for url in calls)
    record = next(s for s in raw["sources"] if s["id"] == "cboe-yields")
    assert (record["name"], record["kind"], record["status"]) == ("Cboe Treasury yield indices", "quote", "AVAILABLE")
    assert record["llm_allowed"] is False   # display lane only: never analyst evidence or continuity state


def test_cboe_is_requested_after_the_treasury_feeds():
    _, calls = collect(utc("2026-10-22T13:30:00+00:00"))
    first_cboe = next(i for i, url in enumerate(calls) if "cboe" in url)
    assert all(i < first_cboe for i, url in enumerate(calls) if url.startswith(TREASURY))


def test_one_failed_index_costs_its_tenor_and_degrades_the_source():
    quotes = {k: v for k, v in QUOTES.items() if k != "TYX"}
    raw, _ = collect(utc("2026-10-22T13:30:00+00:00"), quotes=quotes)
    rows = by_id(raw["observations"])
    assert "cboe-yield-30y" not in rows and "cboe-yield-10y" in rows
    record = next(s for s in raw["sources"] if s["id"] == "cboe-yields")
    assert record["status"] == "DEGRADED" and "30Y" in record["reason"] and "HTTP 403" in record["reason"]


def test_no_index_at_all_is_unavailable_and_the_treasury_curve_is_untouched():
    raw, _ = collect(utc("2026-10-22T13:30:00+00:00"), quotes={})
    record = next(s for s in raw["sources"] if s["id"] == "cboe-yields")
    assert record["status"] == "UNAVAILABLE"
    assert "treasury-10y" in by_id(raw["observations"])


# --- freshness: the existing intraday rules, unchanged ------------------------------------------------------------

def test_a_quote_older_than_twenty_minutes_is_stale_and_withheld():
    raw, _ = collect(utc("2026-10-22T13:30:00+00:00"))
    fresh = by_id(normalize_packet(raw, utc("2026-10-22T13:30:00+00:00"), "LIVE", "HOURLY_1100")["observations"])
    assert fresh["cboe-yield-10y"]["status"] in ("AVAILABLE", "DELAYED") and fresh["cboe-yield-10y"]["value"] == 5.036
    late = utc("2026-10-22T13:41:00+00:00")
    stale = by_id(normalize_packet(raw, late, "LIVE", "HOURLY_1100")["observations"])
    for ident in ("cboe-yield-10y", "cboe-yield-10y-change"):
        assert stale[ident]["status"] == "STALE" and stale[ident]["value"] is None


# --- isolation: never the curve, the analyst or continuity --------------------------------------------------------

def live_packet(now=utc("2026-10-22T13:30:00+00:00")):
    raw, _ = collect(now)
    return finalize_coverage(normalize_packet(raw, now, "LIVE", "HOURLY_1100"))


def test_the_curve_never_reads_intraday_yields():
    packet = live_packet()
    tenors = rates(packet)
    ids = {row["id"] for legs in tenors.values() for row in legs.values() if row}
    assert ids and not any(ident.startswith("cboe-") for ident in ids)
    inputs = {i for row in spread_rows(tenors) for i in row["input_ids"]}
    assert inputs and not any(ident.startswith("cboe-") for ident in inputs)


def test_intraday_yields_never_reach_the_analyst():
    packet = live_packet()
    assert not any(ident.startswith("cboe-") for ident in supplied_ids(analyst_context(packet)))


def test_intraday_yields_are_not_current_equity_prints():
    # The only intraday rows here are the yield indices: coverage must not claim current equity prints.
    assert live_packet()["coverage"]["current_premarket"] is False


# --- render ---------------------------------------------------------------------------------------------------------

def page_packet(age=timedelta(minutes=10), quotes=QUOTES):
    packet = copy.deepcopy(rates_packet())
    observed = (NOW - age).isoformat()
    record = source("cboe-yields", "Cboe Treasury yield indices", "quote",
                    "https://cdn-api.cboe.com/api/global/delayed_quotes/quotes", NOW)
    record["llm_allowed"] = False
    from market_brief.collect import cboe_yield_rows
    rows = cboe_yield_rows({s: json.loads(quote(s, *quotes[s], last=observed)) for s in quotes}, NOW)
    raw = dict(mode="SAMPLE", sources=[record], observations=rows)
    extra = normalize_packet(raw, NOW, "SAMPLE")
    packet["sources"] += extra["sources"]
    packet["observations"] += extra["observations"]
    return packet


def test_the_block_shows_four_levels_with_changes_and_no_print_for_2y():
    view = presentation(page_packet(), narrative())
    block = view["macro"]["intraday_yields"]
    assert block["title"] == "Intraday yields"
    assert block["caption"] == "Cboe yield indices · delayed · not the official par curve"
    assert [row["maturity"] for row in block["rows"]] == ["13W", "2Y", "5Y", "10Y", "30Y"]
    rows = {row["maturity"]: row for row in block["rows"]}
    assert rows["2Y"]["level"]["display"] == "no print" and rows["2Y"]["change"]["display"] == ""
    assert (rows["10Y"]["level"]["display"], rows["10Y"]["change"]["display"]) == ("5.04%", "−11 bp")
    assert "PT" in block["clock"]
    assert not any(row["id"].startswith("cboe-") for row in view["macro"]["facts"])


def test_a_stale_block_blanks_every_change():
    view = presentation(page_packet(age=timedelta(minutes=25)), narrative())
    block = view["macro"]["intraday_yields"]
    for row in block["rows"]:
        assert row["change"]["display"] == "" and row["level"]["display"] == "no print"
    assert "older than 20 minutes" in block["clock"]


def test_the_block_sits_directly_under_the_par_curve_module():
    md, html = render(page_packet(), narrative())
    curve, block = html.index('id="treasury-curve"'), html.index("Intraday yields")
    assert curve < block
    between = html[curve:block]
    assert "Cboe" not in between
    assert "Cboe yield indices · delayed · not the official par curve" in html
    assert "<b>2Y</b>" in html[block:block + 3000] and "no print" in html[block:block + 3000]
    assert "**INTRADAY YIELDS**" in md and md.index("**U.S. TREASURY") < md.index("**INTRADAY YIELDS**")


def test_no_cboe_source_no_block():
    view = presentation(rates_packet(), narrative())
    assert view["macro"]["intraday_yields"] is None


def test_intraday_yields_never_stand_for_a_session_print_at_the_close():
    from market_brief.continuity import closing_data
    now = utc("2026-10-22T20:01:00+00:00")
    raw, _ = collect(now, last="2026-10-22T14:55:03-05:00")   # 3:55 PM New York, inside the near-close window
    packet = finalize_coverage(normalize_packet(raw, now, "LIVE", "CLOSE_1M"))
    assert any(r["id"] == "cboe-yield-10y" and r["value"] is not None for r in packet["observations"])
    assert closing_data(packet)["status"] != "PROVISIONAL_NEAR_CLOSE"


def test_a_refresh_with_only_yield_quotes_still_reads_as_having_no_current_prints():
    from market_brief.cli import missing_prints_reason
    assert "returned none" in missing_prints_reason(live_packet())


def test_withheld_quotes_are_one_coverage_line_not_eight():
    packet = live_packet(utc("2026-10-22T13:45:00+00:00"))   # 25 minutes after the 08:20 CT print
    lines = [line for line in packet["coverage"]["limitations"] if "intraday" in line or "Cboe" in line]
    assert lines == ["Cboe intraday yields 13W, 5Y, 10Y, 30Y: intraday observation older than twenty minutes"]

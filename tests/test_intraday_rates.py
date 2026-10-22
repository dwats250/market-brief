"""Stage 1: an intraday rates lane from Cboe's delayed Treasury yield indices, apart from the official par curve.

Quotes are hand-built documents shaped like Cboe's delayed-quote JSON (`data.current_price`, `prev_day_close`,
`last_trade_time` in New York time). The par curve is the minimized official October fixture (latest entry Friday,
October 16: 3M 4.26, 5Y 4.89, 10Y 5.09, 30Y 5.43); the run is Monday, October 19."""

import copy
import json
from datetime import timedelta

import pytest
from test_curve import utc
from test_pipeline import NOW, narrative
from test_rates_module import rates_packet

from market_brief import collect
from market_brief.collect import (
    CBOE_QUOTES,
    TREASURY,
    SourceError,
    allowed,
    cboe_yield_rows,
    collect_live,
    index_scale,
    intraday_yields_on,
)
from market_brief.evidence import ROOT, finalize_coverage, model_packet, normalize_observation, normalize_packet
from market_brief.render import presentation, render

NOMINAL = (ROOT / "tests/fixtures/treasury.2026-10.xml").read_text()
PAR = {"13W": 4.26, "5Y": 4.89, "10Y": 5.09, "30Y": 5.43}
RUN = utc("2026-10-19T14:45:00+00:00")  # 10:45 AM ET
LEVELS = {"IRX": (41.80, 41.85), "FVX": (48.91, 48.95), "TNX": (50.95, 51.12), "TYX": (54.20, 54.30)}


def quote(symbol, level, prior, when="2026-10-19T10:29:00"):
    return json.dumps({"timestamp": "2026-10-19 14:44:00", "data": {
        "symbol": f"_{symbol}", "current_price": level, "prev_day_close": prior, "close": level,
        "last_trade_time": when}})


def fetcher_for(quotes, calls=None):
    def fetcher(url, deadline):
        if calls is not None:
            calls.append(url)
        if url.startswith(TREASURY) and "real" not in url:
            return NOMINAL
        if url.startswith(CBOE_QUOTES):
            symbol = url.rsplit("/_", 1)[1].removesuffix(".json")
            if symbol not in quotes:
                raise SourceError("HTTP 404")
            return quotes[symbol]
        raise SourceError("offline")
    return fetcher


def documents(scale=10, when="2026-10-19T10:29:00", **times):
    return {symbol: quote(symbol, round(level * scale / 10, 4), round(prior * scale / 10, 4),
                          times.get(symbol, when)) for symbol, (level, prior) in LEVELS.items()}


@pytest.fixture(autouse=True)
def retrieval_clock(monkeypatch):
    """Retrieval follows the run's own clock, as in production, so a replayed run is not retrieved in the future."""
    from datetime import datetime

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return state["now"]
    state = {"now": RUN}
    monkeypatch.setattr(collect, "datetime", Clock)
    return state


@pytest.fixture
def switched_on(monkeypatch):
    monkeypatch.setattr(collect, "intraday_yields_on", lambda path=None: True)


def run(quotes, now=RUN, clock=None):
    if clock is not None:
        clock["now"] = now
    raw = collect_live(now, fetcher=fetcher_for(quotes))
    cboe = next(s for s in raw["sources"] if s["id"] == "cboe-rates")
    rows = {r["id"]: r for r in raw["observations"] if r["source_id"] == "cboe-rates"}
    return raw, cboe, rows


# --- The switch: off by default, and off means nothing is fetched or listed --------------------------------------

def test_the_switch_is_off_in_the_shipped_config_and_off_collects_nothing():
    assert intraday_yields_on() is False
    calls = []
    raw = collect_live(RUN, fetcher=fetcher_for(documents(), calls))
    assert not any(url.startswith(CBOE_QUOTES) for url in calls)
    assert all(s["id"] != "cboe-rates" for s in raw["sources"])


def test_only_the_four_index_quotes_are_allowed_on_the_cboe_host():
    for symbol in ("IRX", "FVX", "TNX", "TYX"):
        assert allowed(f"{CBOE_QUOTES}/_{symbol}.json")
    assert not allowed(f"{CBOE_QUOTES}/_SPX.json")
    assert not allowed("https://cdn.cboe.com/api/global/delayed_quotes/options/SPY.json")


# --- Units: the index scale is confirmed against the official curve, never assumed --------------------------------

@pytest.mark.parametrize("scale", [10, 1])
def test_the_index_scale_is_read_from_the_official_curve(switched_on, scale):
    _, cboe, rows = run(documents(scale=scale))
    assert cboe["status"] == "AVAILABLE"
    assert rows["cboe-10y"]["value"] == pytest.approx(5.095)
    assert rows["cboe-13w"]["value"] == pytest.approx(4.18)


def test_an_unconfirmed_scale_fails_closed():
    quotes = {"5Y": (48.91, 48.95, RUN), "10Y": (5.095, 5.09, RUN)}  # the two tenors disagree on the scale
    with pytest.raises(SourceError, match="scale not confirmed"):
        index_scale(quotes, PAR)
    with pytest.raises(SourceError, match="no official par yield"):
        index_scale({"10Y": (50.95, 50.90, RUN)}, {})


def test_without_the_official_curve_the_lane_is_unavailable(switched_on):
    def fetcher(url, deadline):
        if url.startswith(CBOE_QUOTES):
            return documents()[url.rsplit("/_", 1)[1].removesuffix(".json")]
        raise SourceError("offline")
    raw = collect_live(RUN, fetcher=fetcher)
    cboe = next(s for s in raw["sources"] if s["id"] == "cboe-rates")
    assert cboe["status"] == "UNAVAILABLE" and "par yield" in cboe["reason"]
    assert not [r for r in raw["observations"] if r["source_id"] == "cboe-rates"]


# --- Fresh quotes: levels, changes from Cboe's prior close, the paired spread, no 2Y -----------------------------

def test_fresh_quotes_carry_changes_and_the_5s30s_and_never_a_2y(switched_on):
    _, cboe, rows = run(documents())
    assert rows["cboe-10y-change"]["value"] == -2 and rows["cboe-10y-change"]["unit"] == "bp"  # −1.7 bp
    assert rows["cboe-5y-change"]["value"] == 0 and rows["cboe-30y-change"]["value"] == -1
    assert rows["cboe-5s30s"]["value"] == 53 and rows["cboe-5s30s-change"]["value"] == -1
    assert "not the official Treasury par curve" in rows["cboe-10y"]["baseline"]
    assert not any("2y" in ident for ident in rows)
    assert all(r["topic"].endswith(" intraday") for r in rows.values())
    assert cboe["llm_allowed"] is False


def test_quotes_too_far_apart_form_no_spread(switched_on):
    _, _, rows = run(documents(TYX="2026-10-19T10:20:00", FVX="2026-10-19T10:29:00"))
    assert "cboe-5s30s" not in rows and "cboe-30y-change" in rows


# --- Stale quotes: the latest level, labeled with its own time, and no implied overnight move --------------------

def test_a_premarket_run_shows_the_latest_levels_without_a_move(switched_on, retrieval_clock):
    premarket = utc("2026-10-19T13:00:00+00:00")  # 9:00 AM ET; the last quotes are Friday's
    raw, cboe, rows = run(documents(when="2026-10-16T15:59:00"), now=premarket, clock=retrieval_clock)
    assert set(rows) == {"cboe-13w", "cboe-5y", "cboe-10y", "cboe-30y"}
    assert cboe["status"] == "DEGRADED" and "no change implied" in cboe["reason"]
    packet = normalize_packet(raw, premarket, "LIVE")
    level = next(r for r in packet["observations"] if r["id"] == "cboe-10y")
    assert (level["status"], level["freshness"], level["value"]) == ("BACKGROUND", "DATED", pytest.approx(5.095))


def test_normalization_admits_a_move_only_from_a_fresh_quote():
    row = dict(id="cboe-10y-change", topic="US 10Y intraday", metric="intraday yield change", value=-3, unit="bp",
               baseline="b", frequency="delayed", source_id="cboe-rates", status="DELAYED", reason="",
               observed_at="2026-10-19T14:10:00+00:00", retrieved_at="2026-10-19T14:44:00+00:00")
    assert normalize_observation(row, RUN)["status"] == "DELAYED"
    late = normalize_observation(row, RUN + timedelta(minutes=10))
    assert late["status"] == "STALE" and late["value"] is None
    future = normalize_observation({**row, "observed_at": "2026-10-19T15:00:00+00:00"}, RUN)
    assert future["status"] == "INVALID"


def test_a_failed_quote_leaves_its_tenor_out_and_says_so(switched_on):
    quotes = documents()
    del quotes["IRX"]
    _, cboe, rows = run(quotes)
    assert cboe["status"] == "DEGRADED" and "no quote for 13W" in cboe["reason"]
    assert "cboe-13w" not in rows and "cboe-10y" in rows


# --- The page: a lane of its own, outside the analyst's context and the official curve ---------------------------

FRESH = "2026-09-08T12:25:00+00:00"  # twenty minutes before the sample run


def lane_packet(when):
    quotes = {"13W": (41.80, 41.85, utc(when)), "5Y": (48.91, 48.95, utc(when)), "10Y": (50.95, 50.85, utc(when)),
              "30Y": (54.20, 54.30, utc(when))}
    rows, _ = cboe_yield_rows(quotes, PAR, NOW, NOW)
    packet = rates_packet()
    extra = normalize_packet(dict(mode="SAMPLE", sources=[collect.source(
        "cboe-rates", "Cboe Treasury yield indices · delayed", "quote", CBOE_QUOTES, NOW,
        expected_freshness="DELAYED")], observations=rows), NOW, "SAMPLE")
    extra["sources"][0]["llm_allowed"] = False
    packet["sources"] += extra["sources"]
    packet["observations"] += extra["observations"]
    return finalize_coverage(packet)


def test_the_page_renders_the_lane_beside_an_unchanged_official_curve():
    md, page = render(lane_packet(FRESH), narrative())
    assert "Intraday yields" in page and "15-minute delayed" in page and "not the par curve" in page
    assert "5.09%" in page and "13-week bill" in page and "5s30s" in page
    assert "No 2Y: Cboe has no 2-year yield index here" in page
    assert "**INTRADAY YIELDS**" in md and "| 10Y | 5.09% | +1 bp |" in md
    base, with_lane = (presentation(p, narrative())["macro"] for p in (rates_packet(), lane_packet(FRESH)))
    assert base["curve"] and with_lane["curve"] == base["curve"] and with_lane["yields"] == base["yields"]
    assert base["intraday"] is None


def test_a_dated_lane_says_no_move_is_implied():
    md, page = render(lane_packet("2026-09-04T19:59:00+00:00"), narrative())
    assert "No fresh quote at this update" in page
    lane = md.split("**INTRADAY YIELDS**")[1].split("remains the record")[0]
    assert "| 10Y | 5.09% | — | latest Friday, Sep 4 · 12:59 PM PT |" in lane and "5s30s" not in lane


def test_the_analyst_never_sees_the_lane():
    packet = lane_packet("2026-09-08T12:25:00+00:00")
    assert any(r["source_id"] == "cboe-rates" for r in packet["observations"])
    shown = model_packet(copy.deepcopy(packet))
    assert not any(r.get("source_id") == "cboe-rates" for r in shown["observations"])
    assert all(s["id"] != "cboe-rates" for s in shown["sources"])


def test_a_replayed_run_renders_the_lane_and_names_only_the_2y_as_not_automated(tmp_path, monkeypatch):
    from market_brief import cli
    raw = json.loads((ROOT / "tests/fixtures/evidence.sample.json").read_text())
    quotes = {tenor: (level, prior, utc(FRESH)) for tenor, level, prior in
              (("13W", 41.80, 41.85), ("5Y", 48.91, 48.95), ("10Y", 50.95, 50.85), ("30Y", 54.20, 54.30))}
    rows, _ = cboe_yield_rows(quotes, PAR, NOW, NOW)
    raw["sources"].append(collect.source("cboe-rates", "Cboe Treasury yield indices · delayed", "quote", CBOE_QUOTES,
                                         NOW, expected_freshness="DELAYED"))
    raw["observations"] += rows
    supplied = tmp_path / "evidence.json"
    supplied.write_text(json.dumps(raw))
    original = cli.output_directory
    monkeypatch.setattr(cli, "output_directory", lambda root, *rest: original(tmp_path, *rest))
    monkeypatch.setattr(cli, "update_latest", lambda root, page: None)
    assert cli.main(["premarket", "--replay", "--input", str(supplied)]) == 0
    page = next((tmp_path / "runs").glob("*/*/brief.html")).read_text()
    assert 'id="intraday-rates"' in page and "intraday 2Y yield" in page and "live yields" not in page

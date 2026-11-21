"""Rates intelligence (WP-MB-RATES-INTELLIGENCE-01): horizons, spreads, real yields, 10Y volatility and funding.

The fixture `rates-history.2026-11-20.json` holds official records retrieved read-only on 2026-11-21: Treasury's
nominal and real par curves for 2025 and 2026 and the New York Fed's last 30 SOFR and EFFR publications, parsed by
the collector's own functions (hashes of the source files are in the fixture). Expected values below were read off
those official records, not computed by the code under test.
"""

import copy
import json
from datetime import datetime, timezone

import pytest

from market_brief.collect import (
    FUNDING,
    REAL_FIELDS,
    SourceError,
    allowed,
    funding_rows,
    funding_series,
    real_yield_rows,
    source,
    treasury_series,
    yield_rows,
)
from market_brief.context import analyst_context
from market_brief.evidence import ROOT, finalize_coverage, model_packet, normalize_packet, rates_history_record
from market_brief.metrics import derive
from market_brief.rates import daily_changes, horizon_change, volatility
from market_brief.render import presentation, render

FIXTURE = json.loads((ROOT / "tests/fixtures/rates-history.2026-11-20.json").read_text())
UNIVERSE = json.loads((ROOT / "config/universe.json").read_text())
# Monday, November 23, 2026, 9:00 AM ET: the first PREMARKET that can see Friday Nov 20's curve.
NOW = datetime(2026, 11, 23, 14, 0, tzinfo=timezone.utc)
TREASURY = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"


def upto(series, day):
    return [entry for entry in series if entry["date"] <= day]


def entries(series, fields):
    """Series back into the collector's `(date, fields)` form, so the real row builders run unchanged."""
    return [(e["date"], {fields[t]: f"{v:.2f}" for t, v in e["values"].items()}) for e in series]


def raw_input(nominal=None, real=None, sofr=None, effr=None, now=NOW, history=True):
    nominal = FIXTURE["treasury"] if nominal is None else nominal
    real = FIXTURE["treasury_real"] if real is None else real
    sofr = FIXTURE["sofr"] if sofr is None else sofr
    effr = FIXTURE["effr"] if effr is None else effr
    got = datetime(2026, 11, 23, 13, 58, tzinfo=timezone.utc)
    nominal_fields = {"2Y": "BC_2YEAR", "5Y": "BC_5YEAR", "10Y": "BC_10YEAR", "30Y": "BC_30YEAR", "3M": "BC_3MONTH"}
    sources = [source("treasury", "US Treasury", "economic_series", TREASURY, now),
               source("treasury-real", "US Treasury real yields", "economic_series", TREASURY, now),
               source("nyfed-sofr", "New York Fed SOFR", "economic_series", FUNDING["sofr"], now),
               source("nyfed-effr", "New York Fed EFFR", "economic_series", FUNDING["effr"], now)]
    observations = [*yield_rows(entries(nominal, nominal_fields), got),
                    *real_yield_rows(entries(real, REAL_FIELDS), got),
                    *(funding_rows(sofr, "sofr", got) if sofr else []),
                    *(funding_rows(effr, "effr", got) if effr else [])]
    raw = dict(mode="LIVE", sources=sources, observations=observations, history=[], events=[], context_items=[])
    if history:
        raw["rates_history"] = {
            "treasury": dict(source_id="treasury", retrieved_at=got.isoformat(), series=nominal, note=""),
            "treasury-real": dict(source_id="treasury-real", retrieved_at=got.isoformat(), series=real, note=""),
            **({"nyfed-sofr": dict(source_id="nyfed-sofr", retrieved_at=got.isoformat(), series=sofr)} if sofr else {}),
            **({"nyfed-effr": dict(source_id="nyfed-effr", retrieved_at=got.isoformat(), series=effr)} if effr else {}),
        }
    return raw


def packet_for(raw=None, now=NOW, checkpoint="PREMARKET"):
    packet = normalize_packet(raw or raw_input(now=now), now, "LIVE", checkpoint)
    return finalize_coverage(derive(packet, UNIVERSE))


def rows(packet):
    return {row["id"]: row for row in [*packet["observations"], *packet["derived"]]}


# --- H2 rates side: reproduces Market Review's own study -----------------------------------------------------

@pytest.mark.parametrize("day, sd, pct", [
    # studies/2026-10-27-h2-h3-measure-baselines.md, its table of weekly readings (10Y SD bp/day, 10Y percentile).
    ("2026-10-02", 5.34, 93.7), ("2026-10-09", 5.33, 92.1), ("2026-10-16", 5.63, 96.8),
    ("2026-10-23", 5.62, 96.0), ("2026-10-26", 5.62, 96.0)])
def test_ten_year_volatility_reproduces_the_market_review_h2_study(day, sd, pct):
    result = volatility(upto(FIXTURE["treasury"], day))
    assert (result["sd"], result["percentile"], result["date"]) == (sd, pct, day)
    assert result["window"] == 20 and result["baseline"] == 252


def test_the_h2_baseline_summary_matches_the_study():
    # "10Y: median 4.15 bp/day … range 2.44–5.88" (the 20-session SD, to Oct 26).
    result = volatility(upto(FIXTURE["treasury"], "2026-10-26"))
    assert (result["median"], result["low"], result["high"]) == (4.15, 2.44, 5.88)


def test_volatility_is_point_in_time():
    """A reading never uses a later observation: cutting the history at a date gives that date's value exactly."""
    full = FIXTURE["treasury"]
    for day in ("2026-10-23", "2026-11-12"):
        cut = volatility(upto(full, day))
        extended = [e for e in full if e["date"] <= day]
        assert volatility(extended) == cut
    # Appending later entries changes the latest reading, never the one at the cut.
    assert volatility(upto(full, "2026-11-20"))["sd"] != volatility(upto(full, "2026-10-23"))["sd"]


def test_insufficient_history_states_its_counts():
    short = FIXTURE["treasury"][-15:]
    result = volatility(short)
    assert result["sd"] is None and result["percentile"] is None and result["changes"] == 14
    no_baseline = volatility(FIXTURE["treasury"][-40:])
    assert no_baseline["sd"] is not None and no_baseline["percentile"] is None and no_baseline["values"] == 20


# --- Horizons count official entries, not calendar days --------------------------------------------------------

def test_horizons_count_entries_across_the_veterans_day_bond_holiday():
    """The bond market closed Wed Nov 11, 2026: five entries before Nov 16 is Nov 6, not Nov 9."""
    series = upto(FIXTURE["treasury"], "2026-11-16")
    assert "2026-11-11" not in [e["date"] for e in series]
    change, since = horizon_change(series, "10Y", 5)
    assert since == "2026-11-06"
    assert change == round(5.12 * 100) - round(next(e for e in series if e["date"] == since)["values"]["10Y"] * 100)


def test_a_tenor_the_latest_entry_lacks_has_no_horizon():
    series = copy.deepcopy(FIXTURE["treasury"][-30:])
    del series[-1]["values"]["3M"]
    assert horizon_change(series, "3M", 5) is None
    assert horizon_change(series, "10Y", 5) is not None


def test_daily_changes_skip_gaps_rather_than_fill_them():
    series = copy.deepcopy(FIXTURE["treasury"][-5:])
    del series[2]["values"]["2Y"]
    dates = [d for d, _ in daily_changes(series, "2Y")]
    assert dates == [series[1]["date"], series[3]["date"], series[4]["date"]]


# --- The Nov 16–20 selloff, from official records ---------------------------------------------------------------

def test_nov_20_rows_match_treasury_and_the_new_york_fed():
    found = rows(packet_for())
    # Friday Nov 20 par curve: 3M 4.32, 2Y 4.63, 5Y 4.71, 10Y 4.92, 30Y 5.31 (Nov 19: 4.34, 4.76, 4.86, 5.04, 5.40).
    assert found["treasury-3m"]["value"] == 4.32 and found["treasury-3m-change"]["value"] == -2
    assert found["treasury-2y-change"]["value"] == -13 and found["treasury-30y-change"]["value"] == -9
    # Five entries back is Fri Nov 13 (2Y 4.88, 10Y 5.15, 30Y 5.47); twenty back is Thu Oct 22 (Nov 11 is a bond
    # holiday, so the twenty entries reach one weekday further than twenty weekdays would).
    assert found["treasury-2y-change-5s"]["value"] == -25
    assert found["treasury-10y-change-5s"]["value"] == -23
    assert found["treasury-30y-change-5s"]["value"] == -16
    assert found["treasury-10y-change-5s"]["baseline"] == "official observation 2026-11-13, 5 observations earlier"
    assert found["treasury-10y-change-20s"]["baseline"].startswith("official observation 2026-10-22")
    # 2s30s 68 bp (+4 on the day), 3m10y 60 bp (−10).
    assert (found["treasury-2s30s"]["value"], found["treasury-2s30s-change"]["value"]) == (68, 4)
    assert (found["treasury-3m10y"]["value"], found["treasury-3m10y-change"]["value"]) == (60, -10)
    # 10Y real 2.77% (−11 on the day; 2.91 on Nov 13, so −14 over five entries); compensation −1 and −9.
    assert found["treasury-real-10y-change-5s"]["value"] == -14
    assert found["treasury-10y-inflation-comp-change-5s"]["value"] == -9
    # SOFR 3.91% and EFFR 3.88% for Thu Nov 19 (published Fri Nov 20): SOFR minus EFFR +3 bp.
    assert found["nyfed-sofr"]["value"] == 3.91 and found["nyfed-sofr"]["observed_at"] == "2026-11-19"
    assert found["nyfed-sofr-change"]["value"] == 1 and found["nyfed-effr-change"]["value"] == 0
    assert found["nyfed-sofr-effr"]["value"] == 3
    # 10Y volatility at Nov 20: 5.87 bp/day, 96.8th percentile.
    assert found["treasury-10y-vol20"]["value"] == 5.87 and found["treasury-10y-vol20-pct"]["value"] == 96.8


def test_the_front_end_led_on_every_qualifying_stress_session_of_nov_16_to_20():
    """Market Review H6's measure on SPY ≤ −1.0% sessions (Nov 16 −1.89%, Nov 17 −1.03%, Nov 19 −1.98%, Nov 20
    −3.51%, IEX, from the frozen daily files): Δ2Y versus Δ30Y on each, from the par curve."""
    nominal = FIXTURE["treasury"]
    found = {}
    for day in ("2026-11-16", "2026-11-17", "2026-11-19", "2026-11-20"):
        series = upto(nominal, day)
        found[day] = (horizon_change(series, "2Y", 1)[0], horizon_change(series, "30Y", 1)[0])
    assert found == {"2026-11-16": (-5, -1), "2026-11-17": (-3, 0), "2026-11-19": (-4, -1),
                     "2026-11-20": (-13, -9)}
    assert all(two < thirty for two, thirty in found.values())


def test_leadership_is_stated_per_horizon_without_a_cause():
    record = packet_for()["rates"]
    by = {item["horizon"]: item for item in record["leadership"]}
    assert (by["1d"]["front_bp"], by["1d"]["long_bp"], by["1d"]["lead"]) == (-13, -9, "front")
    assert (by["5s"]["front_bp"], by["5s"]["long_bp"], by["5s"]["lead"]) == (-25, -16, "front")
    assert all("because" not in item["text"] for item in record["leadership"])


# --- Fail-closed admission -------------------------------------------------------------------------------------

def test_a_history_that_disagrees_with_the_admitted_curve_is_withheld():
    raw = raw_input()
    raw["rates_history"]["treasury"]["series"] = copy.deepcopy(FIXTURE["treasury"])
    raw["rates_history"]["treasury"]["series"][-1]["values"]["10Y"] = 4.95  # a revision between fetches
    packet = packet_for(raw)
    assert not packet["rates"]["nominal_ok"]
    assert "treasury-10y-change-5s" not in rows(packet) and "treasury-10y-vol20" not in rows(packet)
    assert any("did not match" in note for note in packet["rates"]["notes"])


@pytest.mark.parametrize("series", [
    [{"date": "2026-11-20", "values": {"10Y": 4.92}}, {"date": "2026-11-19", "values": {"10Y": 5.04}}],  # out of order
    [{"date": "2026-11-20", "values": {"10Y": float("nan")}}],
    [{"date": "2026-11-23", "values": {"10Y": 4.9}}],                                                    # today
    [{"date": "20261120", "values": {"10Y": 4.9}}],
    "not a list",
])
def test_malformed_history_is_dropped_whole(series):
    now = NOW
    by_source = {"treasury": dict(id="treasury", status="AVAILABLE", retention_allowed=True)}
    record = rates_history_record({"treasury": dict(source_id="treasury", series=series)}, by_source, now)
    assert record == {}


def test_history_from_an_unavailable_or_unknown_source_is_not_admitted():
    good = {"treasury": dict(source_id="treasury", series=FIXTURE["treasury"][-3:])}
    assert rates_history_record(good, {"treasury": dict(status="UNAVAILABLE", retention_allowed=True)}, NOW) == {}
    assert rates_history_record(good, {}, NOW) == {}
    assert rates_history_record({"other": good["treasury"]}, {"treasury": dict(status="AVAILABLE",
                                                                             retention_allowed=True)}, NOW) == {}


def test_without_history_the_existing_curve_stands_alone():
    packet = packet_for(raw_input(history=False))
    assert not packet["rates"]["nominal_ok"] and "treasury-10y" in rows(packet)
    assert not any(row["id"].endswith(("-5s", "-20s")) for row in packet["derived"])


def test_a_stale_curve_withholds_history_moves_from_the_analyst():
    later = datetime(2026, 12, 2, 14, 0, tzinfo=timezone.utc)  # the Nov 20 curve is 12 days old
    packet = normalize_packet(raw_input(now=later), later, "LIVE", "PREMARKET")
    packet = finalize_coverage(derive(packet, UNIVERSE))
    model = model_packet(packet)
    assert not any(row["metric"] in ("five-session yield change", "twenty-session yield volatility")
                   for row in model["derived"])
    assert "rates_history" not in model and "rates" not in model


# --- New York Fed funding --------------------------------------------------------------------------------------

def test_funding_requests_are_exactly_the_two_documented_routes():
    assert allowed(FUNDING["sofr"]) and allowed(FUNDING["effr"])
    assert not allowed("https://markets.newyorkfed.org/api/rates/all/latest.json")
    assert not allowed("http://markets.newyorkfed.org/api/rates/secured/sofr/last/30.json")


def test_funding_series_keeps_only_its_own_type_and_past_dates():
    now = datetime(2026, 11, 20, 14, tzinfo=timezone.utc)
    text = json.dumps({"refRates": [
        {"effectiveDate": "2026-11-20", "type": "SOFR", "percentRate": 3.95},          # today: not yet a past day
        {"effectiveDate": "2026-11-19", "type": "SOFR", "percentRate": 3.91, "revisionIndicator": "Y"},
        {"effectiveDate": "2026-11-18", "type": "TGCR", "percentRate": 3.80},          # another series
        {"effectiveDate": "2026-11-18", "type": "SOFR", "percentRate": "3.90"},        # not a number
        {"effectiveDate": "2026-11-17", "type": "SOFR", "percentRate": 3.92},
    ]})
    series = funding_series(text, "sofr", now)
    assert [(e["date"], e["rate"], e["revised"]) for e in series] == [("2026-11-17", 3.92, False),
                                                                     ("2026-11-19", 3.91, True)]
    with pytest.raises(SourceError):
        funding_series(json.dumps({"refRates": []}), "effr", now)


def test_funding_rows_say_they_are_not_intraday_rates():
    for row in funding_rows(FIXTURE["sofr"], "sofr", NOW):
        assert row["frequency"] == "daily"
    level = funding_rows(FIXTURE["effr"], "effr", NOW)[0]
    assert "not an intraday market rate" in level["baseline"]


def test_sofr_minus_effr_needs_the_same_business_day():
    sofr = copy.deepcopy(FIXTURE["sofr"])
    sofr.append(dict(date="2026-11-20", rate=3.95, revised=False))
    packet = packet_for(raw_input(sofr=sofr))
    assert "nyfed-sofr-effr" not in rows(packet)
    assert packet["rates"]["funding"]["spread"] is None


# --- The analyst and the page ----------------------------------------------------------------------------------

def test_the_analyst_receives_the_rates_rows_but_not_the_history():
    context = analyst_context(packet_for())
    ids = {row["id"] for group in context["catalog"] for row in group["rows"]}
    assert {"treasury-10y-change-5s", "treasury-10y-vol20", "nyfed-sofr", "nyfed-sofr-effr",
            "treasury-real-10y-change-5s"} <= ids
    text = json.dumps(context)
    assert "rates_history" not in text and '"series"' not in text


def page(packet=None):
    from test_pipeline import narrative
    return render(packet or packet_for(), narrative())


def test_the_page_shows_horizons_leadership_volatility_and_funding():
    md, html = page()
    panel = html.split('id="rates-context"', 1)[1].split("</section>", 1)[0]
    assert "Treasury entries to Fri, Nov 20" in panel
    assert "<b>10Y</b></td><td class=\"number\">4.92%</td><td class=\"number\">−12</td>" in panel
    assert "2s30s 68 bp (4 bp steeper)" in panel and "3m10y 60 bp (10 bp flatter)" in panel
    assert "the front end (2Y) moved more (2Y −13, 30Y −9 bp)" in panel
    assert "nominal −12 = real −11 + inflation compensation −1 bp" in panel
    assert "5.87 bp a day" in panel and "96.8th percentile of its last 252 readings" in panel
    assert "<b>SOFR</b> 3.91%" in panel and "Thu, Nov 19" in panel and "SOFR minus EFFR 3 bp" in panel
    assert "neither is an intraday market rate" in panel
    assert "RATES IN CONTEXT" in md and "| 10Y | 4.92% | −12 | −23 |" in md


def test_the_page_chart_draws_twenty_entries():
    view = presentation(packet_for(), __import__("test_pipeline").narrative())
    chart = view["macro"]["panel"]["chart"]
    assert chart["count"] == 20 and chart["last"]["text"] == "Fri, Nov 20"
    assert {line["tenor"] for line in chart["lines"]} == {"2Y", "10Y"}
    assert all(len(line["segments"]) == 19 for line in chart["lines"])


def test_rates_rows_are_not_repeated_as_loose_macro_facts():
    view = presentation(packet_for(), __import__("test_pipeline").narrative())
    assert not [row for row in view["macro"]["facts"] if row["id"].startswith(("nyfed", "treasury"))]


def test_series_helper_matches_the_feed_fields():
    assert treasury_series([("2026-11-20", {"TC_10YEAR": "2.77", "TC_5YEAR": ""})], REAL_FIELDS, 5) == [
        {"date": "2026-11-20", "values": {"10Y": 2.77}}]


def test_rates_panel_fits_phone_and_desktop_widths(tmp_path):
    from test_cadence import phone_layout_width
    _, html = page()
    for width in (320, 390, 1280):
        assert phone_layout_width(html, tmp_path, width) <= width

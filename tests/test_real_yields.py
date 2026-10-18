"""W43 S3a: Treasury's official real (TIPS) par curve beside the nominal one, and the matched-date difference.

Fixtures are minimized from the two official XML feeds as retrieved 2026-10-17 (entries Oct 9, 13, 14, 15, 16; the
bond market was closed Monday Oct 12). Oct 15 is the W42 weekly's omitted decomposition: the 10-year fell 9 bp, real
6 bp, inflation compensation 3 bp. Oct 16's legs offset: -1 = -4 + 3, and the 30-year was unchanged while its parts
moved 2 bp each way."""

from pathlib import Path

import pytest
from test_curve import collected, utc

from market_brief.collect import TREASURY
from market_brief.curve import IC_CHANGE, IC_LEVEL, inflation_compensation_rows, rates, real_rates
from market_brief.evidence import normalize_packet

FIXTURES = Path(__file__).parent / "fixtures"
NOMINAL = (FIXTURES / "treasury.2026-10.xml").read_text()
REAL = (FIXTURES / "treasury-real.2026-10.xml").read_text()


def by_id(rows):
    return {row["id"]: row for row in rows}


def collected_on(day, real=True, nominal=True):
    texts = {**({2026: NOMINAL} if nominal else {}), **({("real", 2026): REAL} if real else {})}
    return collected(utc(f"{day}T12:45:00+00:00"), texts)[0]


@pytest.fixture(autouse=True)
def retrieval_clock(monkeypatch):
    """Retrieval times follow the run's own clock, as in production, so a replayed day is not retrieved later."""
    from datetime import datetime

    from market_brief import collect
    state = {}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return state["now"]
    monkeypatch.setattr(collect, "datetime", Clock)
    return state


def packet_on(day, clock, **kwargs):
    clock["now"] = utc(f"{day}T12:45:00+00:00")
    raw = collected_on(day, **kwargs)
    return normalize_packet(raw, utc(f"{day}T12:45:00+00:00"), "LIVE", "PREMARKET")


def derived_on(day, clock, **kwargs):
    packet = packet_on(day, clock, **kwargs)
    return packet, by_id(inflation_compensation_rows(rates(packet), real_rates(packet)))


def test_the_real_curve_is_its_own_source_with_its_own_rows(retrieval_clock):
    retrieval_clock["now"] = utc("2026-10-16T12:45:00+00:00")
    raw = collected_on("2026-10-16")  # Friday premarket: Thursday's curves are the latest before today
    rows = by_id(raw["observations"])
    assert {k: rows[f"treasury-real-{k}"]["value"] for k in ("5y", "10y", "30y")} == {"5y": 2.58, "10y": 2.78,
                                                                                      "30y": 3.18}
    assert rows["treasury-real-10y"]["observed_at"] == "2026-10-15" and rows["treasury-real-10y"]["topic"] == \
        "US 10Y real"
    assert rows["treasury-real-10y-change"]["value"] == -6
    assert rows["treasury-real-10y-change"]["baseline"] == "daily observation 2026-10-14"
    assert "treasury-real-2y" not in rows  # Treasury publishes no 2-year real yield
    source = next(s for s in raw["sources"] if s["id"] == "treasury-real")
    assert source["status"] == "AVAILABLE" and source["url"].startswith(TREASURY)


@pytest.mark.parametrize(("day", "expected"), [
    # (Δnominal, Δreal, Δinflation compensation) per tenor, and the 10-year level split.
    ("2026-10-16", {"5Y": (-9, -6, -3), "10Y": (-9, -6, -3), "30Y": (-9, -6, -3), "level": (5.10, 2.78, 2.32)}),
    ("2026-10-19", {"5Y": (-2, -6, 4), "10Y": (-1, -4, 3), "30Y": (0, -2, 2), "level": (5.09, 2.74, 2.35)}),
])
def test_nominal_change_is_real_change_plus_inflation_compensation_change_exactly(day, expected, retrieval_clock):
    packet, ic = derived_on(day, retrieval_clock)
    nominal, real = rates(packet), real_rates(packet)
    for tenor in ("5Y", "10Y", "30Y"):
        dn, dr = nominal[tenor]["change"]["value"], real[tenor]["change"]["value"]
        dic = ic[f"treasury-{tenor.lower()}-inflation-comp-change"]["value"]
        assert (dn, dr, dic) == expected[tenor], tenor
        assert dn == dr + dic
    level = ic["treasury-10y-inflation-comp"]
    assert (nominal["10Y"]["level"]["value"], real["10Y"]["level"]["value"], level["value"]) == expected["level"]
    assert level["metric"] == IC_LEVEL and level["input_ids"] == ["treasury-10y", "treasury-real-10y"]
    assert "not expected inflation" in level["baseline"]
    assert ic["treasury-10y-inflation-comp-change"]["metric"] == IC_CHANGE


def test_no_two_year_decomposition_and_no_split_without_a_real_curve(retrieval_clock):
    _, ic = derived_on("2026-10-16", retrieval_clock)
    assert not any("2y" in ident for ident in ic)
    packet, ic = derived_on("2026-10-16", retrieval_clock, real=False)
    assert ic == {} and rates(packet)["10Y"]["level"]["value"] == 5.10  # the nominal module is untouched
    source = next(s for s in packet["sources"] if s["id"] == "treasury-real")
    assert source["status"] == "UNAVAILABLE"


def test_curves_of_different_dates_are_never_paired(retrieval_clock):
    packet, _ = derived_on("2026-10-19", retrieval_clock)
    nominal, real = rates(packet), real_rates(packet)
    real["10Y"]["level"] = dict(real["10Y"]["level"], observed_at="2026-10-15")  # a real curve one day behind
    rows = by_id(inflation_compensation_rows(nominal, real))
    assert "treasury-10y-inflation-comp" not in rows and "treasury-10y-inflation-comp-change" not in rows
    assert "treasury-5y-inflation-comp" in rows  # other maturities that do match are unaffected


def test_a_change_is_paired_only_against_the_same_prior_date(retrieval_clock):
    packet, _ = derived_on("2026-10-19", retrieval_clock)
    nominal, real = rates(packet), real_rates(packet)
    real["10Y"]["change"] = dict(real["10Y"]["change"], baseline="daily observation 2026-10-14")
    rows = by_id(inflation_compensation_rows(nominal, real))
    assert "treasury-10y-inflation-comp" in rows and "treasury-10y-inflation-comp-change" not in rows


def test_the_derivation_reaches_the_packet_and_the_analyst_catalog(retrieval_clock):
    from test_curve import UNIVERSE

    from market_brief.context import analyst_context, edition_profile, supplied_ids
    from market_brief.evidence import finalize_coverage
    from market_brief.metrics import derive
    packet = packet_on("2026-10-16", retrieval_clock)
    derive(packet, UNIVERSE, None)
    finalize_coverage(packet)
    ids = {row["id"] for row in packet["derived"]}
    assert {"treasury-10y-inflation-comp", "treasury-10y-inflation-comp-change"} <= ids
    context = analyst_context(packet, edition_profile("PREMARKET"))
    assert {"treasury-real-10y", "treasury-real-10y-change", "treasury-10y-inflation-comp-change"} <= supplied_ids(
        context)


@pytest.mark.parametrize(("args", "sentence"), [
    (("10Y", -9, -6, -3), "The 10-year yield fell 9 bp, mostly through the real yield (−6 bp); inflation "
                          "compensation fell 3 bp."),
    (("10Y", -1, -4, 3), "The 10-year yield fell only 1 bp because its parts offset: the real yield fell 4 bp while "
                         "inflation compensation rose 3 bp."),
    (("30Y", 0, -2, 2), "The 30-year yield was unchanged, but not because nothing moved: the real yield fell 2 bp "
                        "and inflation compensation rose 2 bp."),
    (("5Y", 0, 0, 0), "The 5-year yield and both of its parts were unchanged."),
    (("10Y", 5, 3, 2), "The 10-year yield rose 5 bp, split between the real yield (+3 bp) and inflation "
                       "compensation (+2 bp)."),
    (("10Y", -4, 0, -4), "The 10-year yield fell 4 bp, all of it in the inflation compensation; the real yield was "
                         "unchanged."),
])
def test_the_change_split_is_said_plainly_and_offsetting_legs_never_get_shares(args, sentence):
    from market_brief.render import bridge_sentence
    assert bridge_sentence(*args) == sentence
    assert "%" not in sentence and "share" not in sentence and "expected inflation" not in sentence


def test_the_rates_module_shows_each_matched_split_with_its_caveat(retrieval_clock):
    from test_curve import UNIVERSE

    from market_brief.evidence import finalize_coverage
    from market_brief.metrics import derive
    from market_brief.render import PARTS_CAVEAT, rate_parts
    packet = packet_on("2026-10-19", retrieval_clock)
    derive(packet, UNIVERSE, None)
    finalize_coverage(packet)
    facts = [*packet["observations"], *packet["derived"]]
    parts = {p["maturity"]: p for p in rate_parts(facts, stale=False)}
    assert set(parts) == {"5Y", "10Y", "30Y"}
    ten = parts["10Y"]
    assert (ten["nominal"], ten["real"], ten["compensation"]) == ("5.09%", "2.74%", "2.35%")
    assert (ten["split"]["nominal"], ten["split"]["real"], ten["split"]["compensation"]) == (
        "−1 bp", "−4 bp", "+3 bp")
    assert ten["date"] == "Fri, Oct 16" and ten["split"]["since"] == "Thu, Oct 15"
    assert all(p["split"] is None for p in rate_parts(facts, stale=True))  # a stale curve shows levels only
    assert "not a pure inflation forecast" in PARTS_CAVEAT and "not the borrowing cost" in PARTS_CAVEAT


def test_the_page_carries_one_split_and_its_caveat(retrieval_clock):
    from test_curve import UNIVERSE

    from market_brief.evidence import finalize_coverage
    from market_brief.metrics import derive
    from market_brief.render import rates_module
    packet = packet_on("2026-10-16", retrieval_clock)
    derive(packet, UNIVERSE, None)
    finalize_coverage(packet)
    facts = [*packet["observations"], *packet["derived"]]
    module = rates_module(packet, [r for r in facts if r["topic"].startswith("US ")], {r["id"]: r for r in facts})
    lead = module["curve"]["lead_part"]
    assert lead["maturity"] == "10Y" and lead["split"]["sentence"].startswith("The 10-year yield fell 9 bp")
    assert "treasury-10y-inflation-comp-change" in module["proof_ids"]
    assert "treasury-real-30y" in module["proof_ids"]  # the other maturities stay one tap away

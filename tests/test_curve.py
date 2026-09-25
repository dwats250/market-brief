"""Treasury curve data and shape: collection (30Y, year boundary, integer bp), spreads, weekday freshness,
the curve-move rule table, and the release-after-curve note (R6, R7, R8, R10).

The September and January feeds are trimmed from Treasury's own daily par-yield XML (real observations). The
October entries are synthetic: they stand in for a week that has not happened yet, to exercise the Columbus Day
bond-market holiday on Monday, October 12, 2026.
"""

from datetime import date, datetime, timezone

import pytest

from market_brief.collect import BLS, FED, TREASURY, SourceError, collect_live, source, treasury_rows
from market_brief.curve import (
    LABELS,
    SENTENCES,
    classify,
    curve_freshness,
    expected_curve_date,
    release_notes,
)
from market_brief.evidence import ROOT, finalize_coverage, normalize_packet, read_json
from market_brief.metrics import derive

UNIVERSE = read_json(ROOT / "config/universe.json")
FIXTURES = ROOT / "tests/fixtures"


@pytest.fixture(autouse=True)
def no_equity_credentials(monkeypatch):
    """The collector's equity leg must never reach Alpaca from a test."""
    for name in ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)


def utc(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def feed(*entries):
    """A minimal Treasury-shaped feed: (date, {tenor: yield}) entries, namespaced tags as Treasury sends them."""
    body = "".join(
        f'<entry><content><m:properties><d:NEW_DATE>{day}T00:00:00</d:NEW_DATE>'
        + "".join(f"<d:BC_{tenor[:-1]}YEAR>{value}</d:BC_{tenor[:-1]}YEAR>" for tenor, value in yields.items())
        + "</m:properties></content></entry>" for day, yields in entries)
    return ('<feed xmlns="http://www.w3.org/2005/Atom" xmlns:m="urn:m" xmlns:d="urn:d">' + body + "</feed>")


# Synthetic October 2026 (future) entries; Monday October 12 is a bond-market holiday with no entry.
OCTOBER = feed(("2026-10-05", {"2Y": "4.70", "5Y": "4.84", "10Y": "5.02", "30Y": "5.35"}),
               ("2026-10-06", {"2Y": "4.72", "5Y": "4.85", "10Y": "5.01", "30Y": "5.33"}),
               ("2026-10-07", {"2Y": "4.69", "5Y": "4.83", "10Y": "5.01", "30Y": "5.34"}),
               ("2026-10-08", {"2Y": "4.66", "5Y": "4.81", "10Y": "5.00", "30Y": "5.34"}),
               ("2026-10-09", {"2Y": "4.64", "5Y": "4.80", "10Y": "5.01", "30Y": "5.36"}))


def by_id(rows):
    return {row["id"]: row for row in rows}


def collected(now, texts):
    """Run the live collector against canned Treasury feeds keyed by requested year; other sources fail."""
    calls = []

    def fetcher(url, deadline):
        calls.append(url)
        if url.startswith(TREASURY):
            year = int(url.rsplit("=", 1)[1])
            if year not in texts:
                raise SourceError("HTTP 404")
            return texts[year]
        raise SourceError("offline")
    raw = collect_live(now, fetcher=fetcher)
    return raw, [url for url in calls if url.startswith(TREASURY)]


def rates_packet(now, rows, events=(), context_items=(), checkpoint="PREMARKET", thresholds=None):
    """A LIVE packet of Treasury rows (plus optional calendar/news items) through normalization and derivation."""
    raw = dict(mode="LIVE", sources=[source("treasury", "US Treasury", "economic_series", TREASURY, now),
                                     source("bls", "BLS calendar", "calendar", BLS, now),
                                     source("fed", "Federal Reserve releases", "news", FED, now)],
               observations=list(rows), history=[], events=list(events), context_items=list(context_items))
    packet = normalize_packet(raw, now, "LIVE", checkpoint)
    derive(packet, UNIVERSE, thresholds)
    return finalize_coverage(packet)


def rows_for(day, levels, changes=None, prior="2026-09-23", now=None, source_id="treasury"):
    """Rows in the collector's own shape for a hand-built entry."""
    retrieved = (now or utc(f"{day}T23:00:00+00:00")).isoformat()
    result = []
    for tenor, level in levels.items():
        result.append(dict(id=f"treasury-{tenor.lower()}", topic=f"US {tenor}", metric="daily par yield",
                           value=level, unit="% yield", baseline="daily Treasury par curve, not an intraday quote",
                           frequency="daily", observed_at=day, retrieved_at=retrieved, source_id=source_id,
                           status="BACKGROUND", reason=""))
    for tenor, change in (changes or {}).items():
        result.append(dict(id=f"treasury-{tenor.lower()}-change", topic=f"US {tenor}", metric="daily yield change",
                           value=change, unit="bp", baseline=f"daily observation {prior}", frequency="daily",
                           observed_at=day, retrieved_at=retrieved, source_id=source_id, status="BACKGROUND",
                           reason=""))
    return result


SEP25 = utc("2026-09-25T13:00:00+00:00")  # Friday premarket; the latest entry is Thursday, September 24
SEP24_LEVELS = {"2Y": 4.87, "5Y": 5.03, "10Y": 5.18, "30Y": 5.47}
SEP24_CHANGES = {"2Y": 2, "5Y": 4, "10Y": 7, "30Y": 7}


# --- R6 collection: 30Y, integer basis points, the year boundary ----------------------------------------------

def test_collector_parses_30y_and_rounds_basis_points_at_derivation():
    rows = by_id(treasury_rows((FIXTURES / "treasury.2026-09.xml").read_text(), SEP25, SEP25))
    assert set(rows) == {f"treasury-{t}y{suffix}" for t in (2, 5, 10, 30) for suffix in ("", "-change")}
    assert rows["treasury-30y"]["value"] == 5.47 and rows["treasury-30y"]["topic"] == "US 30Y"
    assert rows["treasury-30y"]["metric"] == "daily par yield" and rows["treasury-30y"]["observed_at"] == "2026-09-24"
    # 5.18 - 5.11 is 6.99999999999994 in floating point; the reader and the classifier see the integer 7.
    for ident, expected in (("treasury-2y-change", 2), ("treasury-5y-change", 4), ("treasury-10y-change", 7),
                            ("treasury-30y-change", 7)):
        assert rows[ident]["value"] == expected and type(rows[ident]["value"]) is int, ident
        assert rows[ident]["baseline"] == "daily observation 2026-09-23"
    # The 30Y rows keep the existing row shape exactly.
    assert set(rows["treasury-30y-change"]) == set(rows["treasury-10y-change"])


def test_collector_ignores_the_display_duplicate_of_the_30y_field():
    text = (FIXTURES / "treasury.2026-09.xml").read_text()
    assert "BC_30YEARDISPLAY" in text  # Treasury's feed carries both fields
    rows = by_id(treasury_rows(text, SEP25, SEP25))
    assert rows["treasury-30y"]["value"] == 5.47


def test_first_session_of_the_year_takes_december_as_the_level():
    now = utc("2026-01-02T14:00:00+00:00")  # Friday, January 2, 2026: the year's first session, before its curve
    raw, calls = collected(now, {2026: (FIXTURES / "treasury.2026-01.xml").read_text(),
                                 2025: (FIXTURES / "treasury.2025-12.xml").read_text()})
    assert [url.rsplit("=", 1)[1] for url in calls] == ["2026", "2025"]
    rows = by_id(raw["observations"])
    assert rows["treasury-10y"]["observed_at"] == "2025-12-31" and rows["treasury-10y"]["value"] == 4.18
    assert rows["treasury-10y-change"]["value"] == 4 and rows["treasury-10y-change"]["baseline"] == \
        "daily observation 2025-12-30"
    assert rows["treasury-30y"]["value"] == 4.84
    assert raw["sources"][0]["status"] == "AVAILABLE"


def test_second_session_measures_the_change_against_december():
    now = utc("2026-01-05T14:00:00+00:00")  # Monday, January 5: one entry this year (January 2)
    raw, calls = collected(now, {2026: (FIXTURES / "treasury.2026-01.xml").read_text(),
                                 2025: (FIXTURES / "treasury.2025-12.xml").read_text()})
    assert [url.rsplit("=", 1)[1] for url in calls] == ["2026", "2025"]
    rows = by_id(raw["observations"])
    assert rows["treasury-2y"]["observed_at"] == "2026-01-02" and rows["treasury-2y"]["value"] == 3.47
    assert rows["treasury-2y-change"]["baseline"] == "daily observation 2025-12-31"
    assert {tenor: rows[f"treasury-{tenor}-change"]["value"] for tenor in ("2y", "5y", "10y", "30y")} == \
        {"2y": 0, "5y": 1, "10y": 1, "30y": 2}


def test_third_session_needs_no_previous_year():
    now = utc("2026-01-06T14:00:00+00:00")
    raw, calls = collected(now, {2026: (FIXTURES / "treasury.2026-01.xml").read_text()})
    assert [url.rsplit("=", 1)[1] for url in calls] == ["2026"]
    rows = by_id(raw["observations"])
    assert rows["treasury-10y"]["observed_at"] == "2026-01-05"
    assert rows["treasury-10y-change"]["baseline"] == "daily observation 2026-01-02"


def test_year_boundary_merge_deduplicates_by_date():
    now = utc("2026-01-05T14:00:00+00:00")
    overlap = feed(("2026-01-02", {"2Y": "3.47", "5Y": "3.74", "10Y": "4.19", "30Y": "4.86"}),
                   ("2025-12-31", {"2Y": "3.47", "5Y": "3.73", "10Y": "4.18", "30Y": "4.84"}))
    december = feed(("2025-12-31", {"2Y": "3.47", "5Y": "3.73", "10Y": "4.18", "30Y": "4.84"}),
                    ("2025-12-30", {"2Y": "3.45", "5Y": "3.68", "10Y": "4.14", "30Y": "4.81"}))
    # Two entries before today already: no second request.
    raw, calls = collected(now, {2026: overlap, 2025: december})
    assert len(calls) == 1
    rows = by_id(raw["observations"])
    assert rows["treasury-10y-change"]["baseline"] == "daily observation 2025-12-31"
    # One entry this year and an overlapping previous-year feed: the duplicate date counts once.
    first = feed(("2026-01-02", {"2Y": "3.47", "5Y": "3.74", "10Y": "4.19", "30Y": "4.86"}))
    both = feed(("2026-01-02", {"2Y": "3.47", "5Y": "3.74", "10Y": "4.19", "30Y": "4.86"}),
                ("2025-12-31", {"2Y": "3.47", "5Y": "3.73", "10Y": "4.18", "30Y": "4.84"}))
    raw, calls = collected(now, {2026: first, 2025: both})
    assert len(calls) == 2
    rows = by_id(raw["observations"])
    assert rows["treasury-10y"]["observed_at"] == "2026-01-02"
    assert rows["treasury-10y-change"]["baseline"] == "daily observation 2025-12-31"
    assert rows["treasury-10y-change"]["value"] == 1


def test_previous_year_failure_keeps_what_this_year_supplied():
    now = utc("2026-01-05T14:00:00+00:00")
    raw, calls = collected(now, {2026: (FIXTURES / "treasury.2026-01.xml").read_text()})  # 2025 request fails
    assert len(calls) == 2
    rows = by_id(raw["observations"])
    assert rows["treasury-10y"]["observed_at"] == "2026-01-02"
    assert not any(ident.endswith("-change") for ident in rows)  # no prior entry, no change
    assert raw["sources"][0]["status"] == "AVAILABLE"
    # With nothing before today in either feed, the source is unavailable, as before.
    raw, _ = collected(utc("2026-01-02T14:00:00+00:00"), {2026: (FIXTURES / "treasury.2026-01.xml").read_text()})
    assert raw["sources"][0]["status"] == "UNAVAILABLE" and not raw["observations"]


# --- R6 freshness: weekdays, not NYSE sessions --------------------------------------------------------------

@pytest.mark.parametrize("run,expected", [
    (date(2026, 9, 25), date(2026, 9, 24)),   # Friday -> Thursday
    (date(2026, 9, 28), date(2026, 9, 25)),   # Monday -> Friday
    (date(2026, 10, 12), date(2026, 10, 9)),  # Columbus Day Monday -> Friday
    (date(2026, 10, 13), date(2026, 10, 12)),  # the holiday itself is still a weekday
    (date(2026, 11, 12), date(2026, 11, 11)),  # the day after Veterans Day
    (date(2027, 1, 4), date(2027, 1, 1)),     # New Year's Day is a weekday with no curve
])
def test_expected_curve_date_is_the_previous_weekday(run, expected):
    assert expected_curve_date(run) == expected


@pytest.mark.parametrize("curve,run,status", [
    (date(2026, 9, 24), date(2026, 9, 25), "current"),
    (date(2026, 10, 9), date(2026, 10, 12), "current"),   # Monday reads Friday's curve
    (date(2026, 10, 9), date(2026, 10, 13), "older"),     # no curve for the Columbus Day bond holiday
    (date(2026, 11, 10), date(2026, 11, 12), "older"),    # Veterans Day
    (date(2026, 12, 31), date(2027, 1, 4), "older"),      # the New Year's Day bond holiday
    (date(2026, 9, 17), date(2026, 9, 22), "older"),      # five calendar days old: still displayed normally
    (date(2026, 9, 16), date(2026, 9, 22), "stale"),      # six
    (date(2026, 9, 10), date(2026, 9, 17), "stale"),
])
def test_curve_freshness(curve, run, status):
    assert curve_freshness(curve, run)["status"] == status


# --- R7 spreads ---------------------------------------------------------------------------------------------

def test_september_24_spreads_are_integer_bp_with_their_legs():
    packet = rates_packet(SEP25, treasury_rows((FIXTURES / "treasury.2026-09.xml").read_text(), SEP25, SEP25))
    derived = by_id(packet["derived"])
    level, change = derived["treasury-2s10s"], derived["treasury-2s10s-change"]
    assert (level["value"], change["value"]) == (31, 5)
    assert type(level["value"]) is int and type(change["value"]) is int
    assert level["input_ids"] == ["treasury-2y", "treasury-10y"]
    assert change["input_ids"] == ["treasury-2y-change", "treasury-10y-change"]
    assert level["formula_version"] and level["formula_version"] == change["formula_version"]
    assert level["unit"] == "bp spread" and change["unit"] == "bp"
    assert level["metric"] == "curve spread" and change["metric"] == "daily spread change"
    assert level["topic"] == change["topic"] == "US 2s10s"
    assert level["observed_at"] == change["observed_at"] == "2026-09-24"
    assert change["baseline"] == "daily observation 2026-09-23"
    assert (derived["treasury-5s30s"]["value"], derived["treasury-5s30s-change"]["value"]) == (44, 3)
    assert derived["treasury-5s30s"]["input_ids"] == ["treasury-5y", "treasury-30y"]
    # A level carries no move magnitude; a change does, from the configured bp thresholds.
    assert level["magnitude"] == "NEUTRAL" and change["magnitude"] == "NOTABLE"
    # Spread rows are ordinary admitted evidence: identity, status, citable.
    assert level["status"] == "BACKGROUND" and level["identity"]["window"] == "1d"


def test_inverted_curve_and_a_zero_crossing():
    inverted = rates_packet(SEP25, rows_for("2026-09-24", {"2Y": 4.50, "10Y": 4.15}, {"2Y": -3, "10Y": 2}))
    derived = by_id(inverted["derived"])
    assert derived["treasury-2s10s"]["value"] == -35 and derived["treasury-2s10s-change"]["value"] == 5
    crossing = rates_packet(SEP25, rows_for("2026-09-24", {"2Y": 4.47, "10Y": 4.50}, {"2Y": -3, "10Y": 2}))
    derived = by_id(crossing["derived"])
    assert derived["treasury-2s10s"]["value"] == 3 and derived["treasury-2s10s-change"]["value"] == 5  # from -2


def test_a_spread_exists_only_when_both_legs_come_from_one_entry():
    mixed = rows_for("2026-09-24", {"2Y": 4.87}, {"2Y": 2}) + rows_for("2026-09-23", {"10Y": 5.11}, {"10Y": 15},
                                                                        prior="2026-09-22")
    derived = by_id(rates_packet(SEP25, mixed)["derived"])
    assert "treasury-2s10s" not in derived and "treasury-2s10s-change" not in derived
    # Both levels from one entry, but only one leg has a prior entry: a level and no change.
    one_prior = rates_packet(SEP25, rows_for("2026-09-24", {"2Y": 4.87, "10Y": 5.18}, {"10Y": 7}))
    derived = by_id(one_prior["derived"])
    assert derived["treasury-2s10s"]["value"] == 31 and "treasury-2s10s-change" not in derived
    # Changes measured against different prior entries are not one spread change.
    split = rows_for("2026-09-24", {"2Y": 4.87}, {"2Y": 2}) + rows_for("2026-09-24", {"10Y": 5.18}, {"10Y": 9},
                                                                        prior="2026-09-22")
    derived = by_id(rates_packet(SEP25, split)["derived"])
    assert derived["treasury-2s10s"]["value"] == 31 and "treasury-2s10s-change" not in derived
    # No 30Y: no 5s30s rows at all.
    no_long = rates_packet(SEP25, rows_for("2026-09-24", {"2Y": 4.87, "5Y": 5.03, "10Y": 5.18},
                                           {"2Y": 2, "5Y": 4, "10Y": 7}))
    assert not any(row["topic"] == "US 5s30s" for row in no_long["derived"])


# --- R8 the rule table --------------------------------------------------------------------------------------

TABLE = [
    ((2, 7), "Bear steepener"),
    ((7, 2), "Bear flattener"),
    ((-7, -2), "Bull steepener"),
    ((-2, -7), "Bull flattener"),
    ((-4, 4), "Twist steepener"),
    ((4, -4), "Twist flattener"),
    ((5, 6), "Parallel shift higher"),
    ((-5, -6), "Parallel shift lower"),
    ((-1, 2), "Slight steepening"),
    ((1, -2), "Slight flattening"),
    ((1, 2), "Little changed"),
    ((0, 0), "Little changed"),
]


@pytest.mark.parametrize("changes,label", TABLE)
def test_every_label_in_the_rule_table(changes, label):
    move = classify(*changes, threshold=3)
    assert move["label"] == label
    assert move["sentence"] == SENTENCES[label]
    assert move["note"] == ""


def test_reader_copy_is_the_approved_sentence_per_label():
    assert SENTENCES == {
        "Bear steepener": "Long-end yields rose more than the front end.",
        "Bear flattener": "Front-end yields rose more than the long end.",
        "Bull steepener": "Front-end yields fell more than the long end.",
        "Bull flattener": "Long-end yields fell more than the front end.",
        "Twist steepener": "The front end fell while the long end rose.",
        "Twist flattener": "The front end rose while the long end fell.",
        "Parallel shift higher": "Yields rose by similar amounts across the curve.",
        "Parallel shift lower": "Yields fell by similar amounts across the curve.",
        "Slight steepening": "The curve steepened slightly; neither end moved much.",
        "Slight flattening": "The curve flattened slightly; neither end moved much.",
        "Little changed": "Yields were little changed.",
    }
    assert set(LABELS) == set(SENTENCES) | {"Mixed curve move", "Curve move unavailable"}
    assert not any("faster" in sentence for sentence in SENTENCES.values())


@pytest.mark.parametrize("changes,label", [
    # Twist needs both legs at the threshold, inclusive.
    ((-3, 3), "Twist steepener"),
    ((3, -3), "Twist flattener"),
    ((-2, 3), "Bear steepener"),     # the 2Y at t - 1: not a twist; the 10Y leads and rose
    ((-3, 2), "Bull steepener"),     # the 10Y at t - 1: the 2Y leads and fell
    # Steepening/flattening needs |Δs| >= t.
    ((0, 3), "Bear steepener"),
    ((0, 2), "Little changed"),
    ((3, 0), "Bear flattener"),
    ((2, 0), "Little changed"),
    ((0, -3), "Bull flattener"),
    ((-3, 0), "Bull steepener"),
    # Bull/bear needs the leading leg at the threshold; below it the move is only slight.
    ((-1, 3), "Bear steepener"),
    ((-1, 2), "Slight steepening"),
    ((1, -3), "Bull flattener"),
    ((1, -2), "Slight flattening"),
    # A parallel shift needs the leading leg at the threshold with |Δs| < t.
    ((2, 3), "Parallel shift higher"),
    ((1, 2), "Little changed"),
    ((-2, -3), "Parallel shift lower"),
    ((-1, -2), "Little changed"),
])
def test_boundaries_at_t_and_t_minus_one(changes, label):
    assert classify(*changes, threshold=3)["label"] == label


@pytest.mark.parametrize("changes,label", [
    ((3, 3), "Parallel shift higher"),   # same sign: the tie cannot disagree about direction
    ((-3, -3), "Parallel shift lower"),
    ((2, 2), "Little changed"),
    ((-2, 2), "Slight steepening"),      # opposite signs below t: no bull/bear is claimed
    ((2, -2), "Slight flattening"),
    ((-3, 3), "Twist steepener"),        # opposite signs at t: the twist rule decides
    ((-1, 1), "Little changed"),
])
def test_lead_leg_ties(changes, label):
    assert classify(*changes, threshold=3)["label"] == label


def test_the_threshold_comes_from_configuration():
    configured = read_json(ROOT / "config/magnitude.json")["bp"]["SMALL"]
    rows = rows_for("2026-09-24", {"2Y": 4.87, "10Y": 5.18}, {"2Y": 2, "10Y": 7})
    assert rates_packet(SEP25, rows)["curve"]["threshold_bp"] == configured
    wide = {"bp": {"SMALL": 10.0, "NOTABLE": 20.0}, "%": {"SMALL": 0.25, "NOTABLE": 1.0},
            "pp": {"SMALL": 0.25, "NOTABLE": 1.0}}
    curve = rates_packet(SEP25, rows, thresholds=wide)["curve"]
    assert curve["threshold_bp"] == 10.0 and curve["label"] == "Little changed"


# --- R8 the long end: mixed curve move and the long-end note -----------------------------------------------

@pytest.mark.parametrize("changes,long_change,label,sentence", [
    ((2, 7), -3, "Mixed curve move", "2s10s bear-steepened while 5s30s flattened."),
    ((-7, -2), -4, "Mixed curve move", "2s10s bull-steepened while 5s30s flattened."),
    ((7, 2), 3, "Mixed curve move", "2s10s bear-flattened while 5s30s steepened."),
    ((-2, -7), 5, "Mixed curve move", "2s10s bull-flattened while 5s30s steepened."),
    ((-4, 4), -3, "Mixed curve move", "2s10s twist-steepened while 5s30s flattened."),
    ((4, -4), 3, "Mixed curve move", "2s10s twist-flattened while 5s30s steepened."),
    ((-1, 2), -5, "Mixed curve move", "2s10s steepened slightly while 5s30s flattened."),
    ((1, -2), 5, "Mixed curve move", "2s10s flattened slightly while 5s30s steepened."),
    ((2, 7), -2, "Bear steepener", "Long-end yields rose more than the front end."),   # 5s30s below t
    ((2, 7), 4, "Bear steepener", "Long-end yields rose more than the front end."),    # same direction
])
def test_mixed_curve_move(changes, long_change, label, sentence):
    move = classify(*changes, long_change=long_change, threshold=3)
    assert (move["label"], move["sentence"], move["note"]) == (label, sentence, "")


@pytest.mark.parametrize("changes,long_change,label,note", [
    ((5, 6), 7, "Parallel shift higher", "5s30s steepened 7 bp."),
    ((1, 2), -3, "Little changed", "5s30s flattened 3 bp."),
    ((-5, -6), 3, "Parallel shift lower", "5s30s steepened 3 bp."),
    ((1, 2), -2, "Little changed", ""),     # below t: nothing to say about the long end
    ((1, 2), None, "Little changed", ""),   # 5s30s unavailable: claim nothing about the long end
    ((2, 7), None, "Bear steepener", ""),
])
def test_long_end_note(changes, long_change, label, note):
    move = classify(*changes, long_change=long_change, threshold=3)
    assert move["label"] == label and move["sentence"] == SENTENCES[label] and move["note"] == note


# --- R8 the record, end to end ----------------------------------------------------------------------------

def test_september_24_is_a_bear_steepener():
    packet = rates_packet(SEP25, treasury_rows((FIXTURES / "treasury.2026-09.xml").read_text(), SEP25, SEP25))
    curve = packet["curve"]
    assert curve["label"] == "Bear steepener"
    assert curve["sentence"] == "Long-end yields rose more than the front end."
    assert curve["note"] == ""  # 5s30s steepened 3 bp, the same direction as 2s10s
    assert curve["pair"] == "2s10s"
    assert curve["changes_bp"] == {"2Y": 2, "10Y": 7, "2s10s": 5, "5s30s": 3}
    assert curve["inputs"] == ["treasury-2y-change", "treasury-10y-change", "treasury-5s30s-change"]
    assert curve["observed_at"] == "2026-09-24" and curve["prior_observed_at"] == "2026-09-23"
    assert curve["freshness"] == "current" and curve["expected_observed_at"] == "2026-09-24"
    assert curve["rule_version"] and curve["threshold_bp"] == 3.0 and curve["reason"] is None
    assert curve["releases"] == [] and curve["release_note"] == ""


def test_the_curve_record_is_never_citable_evidence():
    from market_brief.evidence import evidence_catalog, model_packet
    packet = rates_packet(SEP25, treasury_rows((FIXTURES / "treasury.2026-09.xml").read_text(), SEP25, SEP25))
    catalog = evidence_catalog(model_packet(packet))
    assert "curve" not in catalog and not any(row.get("label") for row in catalog.values())
    assert {"treasury-2s10s", "treasury-2s10s-change", "treasury-5s30s", "treasury-5s30s-change"} <= set(catalog)


@pytest.mark.parametrize("rows,reason,sentence", [
    (rows_for("2026-09-24", {"10Y": 5.18}, {"10Y": 7}), "missing_tenor", "The latest curve has no 2Y yield."),
    (rows_for("2026-09-24", {"2Y": 4.87}, {"2Y": 2}), "missing_tenor", "The latest curve has no 10Y yield."),
    (rows_for("2026-09-24", {"2Y": 4.87, "10Y": 5.18}, {"10Y": 7}), "no_prior_entry",
     "There is no prior daily entry for the 2Y yield."),
    (rows_for("2026-09-24", {"2Y": 4.87, "10Y": 5.18}), "no_prior_entry",
     "There is no prior daily entry to compare with."),
    (rows_for("2026-09-24", {"2Y": 4.87}, {"2Y": 2}) + rows_for("2026-09-23", {"10Y": 5.11}, {"10Y": 15}),
     "mixed_entries", "The 2Y and 10Y yields come from different daily entries."),
    (rows_for("2026-09-24", {"2Y": 4.87}, {"2Y": 2}) + rows_for("2026-09-24", {"10Y": 5.18}, {"10Y": 9},
                                                                prior="2026-09-22"),
     "mixed_entries", "The 2Y and 10Y changes are measured against different daily entries."),
])
def test_curve_move_unavailable_states_its_reason(rows, reason, sentence):
    curve = rates_packet(SEP25, rows)["curve"]
    assert curve["label"] == "Curve move unavailable"
    assert curve["reason"] == reason and curve["sentence"] == sentence and curve["note"] == ""


def test_a_stale_curve_has_no_curve_move_but_keeps_its_levels():
    now = utc("2026-09-24T13:00:00+00:00")
    rows = rows_for("2026-09-17", SEP24_LEVELS, SEP24_CHANGES, prior="2026-09-16")
    packet = rates_packet(now, rows)
    curve = packet["curve"]
    assert curve["freshness"] == "stale"
    assert curve["label"] == "Curve move unavailable" and curve["reason"] == "stale_observation"
    assert curve["sentence"] == "The latest official curve is more than five days old."
    assert by_id(packet["observations"])["treasury-10y"]["value"] == 5.18  # levels still admitted


def test_missing_30y_classifies_from_2s10s_alone():
    rows = rows_for("2026-09-24", {"2Y": 4.87, "5Y": 5.03, "10Y": 5.18}, {"2Y": 2, "5Y": 4, "10Y": 7})
    curve = rates_packet(SEP25, rows)["curve"]
    assert curve["label"] == "Bear steepener" and curve["note"] == ""
    assert "5s30s" not in curve["changes_bp"] and curve["inputs"] == ["treasury-2y-change", "treasury-10y-change"]


def test_a_mixed_move_end_to_end():
    rows = rows_for("2026-09-24", {"2Y": 4.87, "5Y": 5.03, "10Y": 5.18, "30Y": 5.47},
                    {"2Y": 2, "5Y": 6, "10Y": 7, "30Y": 2})
    curve = rates_packet(SEP25, rows)["curve"]
    assert curve["label"] == "Mixed curve move"
    assert curve["sentence"] == "2s10s bear-steepened while 5s30s flattened."
    assert curve["changes_bp"]["5s30s"] == -4


def test_inverted_twist_end_to_end():
    rows = rows_for("2026-09-24", {"2Y": 4.50, "5Y": 4.30, "10Y": 4.15, "30Y": 4.25},
                    {"2Y": -4, "5Y": -1, "10Y": 3, "30Y": 4})
    curve = rates_packet(SEP25, rows)["curve"]
    assert curve["label"] == "Twist steepener" and curve["changes_bp"]["2s10s"] == 7


# --- R6 the Columbus Day bond holiday ---------------------------------------------------------------------

def test_columbus_day_monday_reads_fridays_curve_as_current():
    now = utc("2026-10-12T13:00:00+00:00")  # Monday premarket; NYSE trades, the bond market is closed
    packet = rates_packet(now, treasury_rows(OCTOBER, now, now))
    curve = packet["curve"]
    assert curve["observed_at"] == "2026-10-09" and curve["expected_observed_at"] == "2026-10-09"
    assert curve["freshness"] == "current" and curve["label"] != "Curve move unavailable"


def test_the_day_after_the_bond_holiday_shows_the_latest_official_observation():
    now = utc("2026-10-13T13:00:00+00:00")  # Tuesday: Treasury published no curve for Monday, October 12
    packet = rates_packet(now, treasury_rows(OCTOBER, now, now))
    curve = packet["curve"]
    assert curve["observed_at"] == "2026-10-09" and curve["expected_observed_at"] == "2026-10-12"
    assert curve["freshness"] == "older" and curve["age_days"] == 4
    # Older than expected but within five days: displayed normally, the move still classified.
    assert curve["label"] != "Curve move unavailable"


# --- R10 the release-after-curve note ---------------------------------------------------------------------

def event(ident, title, scheduled_at, now):
    return dict(id=ident, title=title, source_id="bls", published_at=None, checked_at=now.isoformat(),
                scheduled_at=scheduled_at, status="SCHEDULED")


def test_cpi_morning_note():
    now = utc("2026-10-14T13:00:00+00:00")  # Wednesday premarket; the curve is Tuesday's
    rows = rows_for("2026-10-13", {"2Y": 4.64, "10Y": 5.01}, {"2Y": 0, "10Y": 1}, prior="2026-10-09")
    events = [event("bls-event-0", "Consumer Price Index", "2026-10-14T12:30:00+00:00", now),
              event("bls-event-1", "Real Earnings", "2026-10-14T12:30:00+00:00", now),
              event("bls-event-2", "Later Release", "2026-10-14T14:00:00+00:00", now)]  # after this run
    curve = rates_packet(now, rows, events)["curve"]
    assert [item["id"] for item in curve["releases"]] == ["bls-event-0", "bls-event-1"]
    assert curve["release_note"] == ("Curve predates the 5:30 AM PT Consumer Price Index release and the "
                                     "5:30 AM PT Real Earnings release.")


def test_release_note_names_two_then_counts_the_rest():
    notes = release_notes(date(2026, 10, 13), utc("2026-10-14T15:00:00+00:00"), [
        dict(id=f"e{i}", title=title, scheduled_at=at) for i, (title, at) in enumerate((
            ("Consumer Price Index", "2026-10-14T12:30:00+00:00"),
            ("Real Earnings", "2026-10-14T12:30:00+00:00"),
            ("Business Employment Dynamics", "2026-10-14T14:00:00+00:00"),
            ("County Employment and Wages", "2026-10-14T14:00:00+00:00")))], [])
    assert len(notes["releases"]) == 4
    assert notes["text"] == ("Curve predates the 5:30 AM PT Consumer Price Index release, the 5:30 AM PT Real "
                             "Earnings release and 2 more.")
    single = release_notes(date(2026, 10, 13), utc("2026-10-14T15:00:00+00:00"),
                           [dict(id="e0", title="Consumer Price Index", scheduled_at="2026-10-14T12:30:00+00:00")], [])
    assert single["text"] == "Curve predates the 5:30 AM PT Consumer Price Index release."


def test_a_release_the_curve_already_reflects_is_not_named():
    # 8:30 AM ET on the curve's own date is before its 3:30 PM ET reference quotes.
    notes = release_notes(date(2026, 10, 13), utc("2026-10-14T13:00:00+00:00"),
                          [dict(id="e0", title="Producer Price Index", scheduled_at="2026-10-13T12:30:00+00:00")], [])
    assert notes == dict(releases=[], text="")
    # Exactly at the reference is not after it.
    at_reference = release_notes(date(2026, 10, 13), utc("2026-10-14T13:00:00+00:00"),
                                 [dict(id="e0", title="Edge", scheduled_at="2026-10-13T19:30:00+00:00")], [])
    assert at_reference["releases"] == []
    # At the run's own time is included.
    at_run = release_notes(date(2026, 10, 13), utc("2026-10-14T12:30:00+00:00"),
                           [dict(id="e0", title="Consumer Price Index", scheduled_at="2026-10-14T12:30:00+00:00")], [])
    assert [item["id"] for item in at_run["releases"]] == ["e0"]


def test_fomc_statement_from_the_fed_feed():
    # Titles as the Federal Reserve press feed published them on September 16, 2026 (production evidence).
    now = utc("2026-09-16T19:01:00+00:00")  # the 3:00 PM ET refresh; the curve is Tuesday's
    rows = rows_for("2026-09-15", {"2Y": 4.67, "10Y": 5.00}, {"2Y": 1, "10Y": 2}, prior="2026-09-14")
    items = [dict(id="fed-item-0", title="Federal Reserve issues FOMC statement", source_id="fed",
                  published_at="2026-09-16T18:00:00+00:00", status="AVAILABLE"),
             dict(id="fed-item-1", title="Federal Reserve Board and Federal Open Market Committee release economic "
                                         "projections from the September 15-16 FOMC meeting", source_id="fed",
                  published_at="2026-09-16T18:00:00+00:00", status="AVAILABLE")]
    curve = rates_packet(now, rows, context_items=items, checkpoint="HOURLY_1500")["curve"]
    assert [item["id"] for item in curve["releases"]] == ["fed-item-0"]
    assert curve["releases"][0]["kind"] == "fomc"
    assert curve["release_note"] == "Curve predates the 11:00 AM PT FOMC statement."


def test_release_note_dates_an_item_from_another_day():
    notes = release_notes(date(2026, 10, 9), utc("2026-10-13T13:00:00+00:00"),
                          [dict(id="e0", title="Monthly Treasury Statement", scheduled_at="2026-10-12T18:00:00+00:00")],
                          [])
    assert notes["text"] == "Curve predates the Mon, Oct 12 · 11:00 AM PT Monthly Treasury Statement release."

"""The Treasury curve's shape, from one run's admitted daily par-yield rows.

Deterministic and local: curve spreads, the curve's freshness against the weekday calendar, the reader's name
for the latest curve move, and the releases the curve predates. Nothing here fetches, models or recomputes a
yield, and every number is a whole basis point a reader can check against the table.

Treasury fits its par curve from indicative quotes taken near 3:30 PM ET and publishes it once a business day,
so every page shows the previous business day's curve. The bond market keeps holidays the NYSE trades through
(Columbus Day, Veterans Day), so freshness is judged against weekdays, never exchange sessions.

The curve record is a typed packet record saved with the evidence (`packet["curve"]`). It is not a numeric
evidence row and is never citable; the analyst cites the spread and tenor rows.
"""

import re
from datetime import date, datetime, time, timedelta

from .evidence import ET, ROOT, USABLE, read_json, timestamp
from .schedule import VANCOUVER

TENORS = ("2Y", "5Y", "10Y", "30Y")
# (name, short leg, long leg): each spread is the long leg minus the short leg, in basis points.
SPREADS = (("2s10s", "2Y", "10Y"), ("5s30s", "5Y", "30Y"))
LEVEL, CHANGE = "daily par yield", "daily yield change"
SPREAD_LEVEL, SPREAD_CHANGE = "curve spread", "daily spread change"
# A spread level is a level, not a move: its own unit keeps it unsigned on the page and outside move magnitudes.
SPREAD_UNIT = "bp spread"
SPREAD_FORMULA = "curve-spread.v1"
RULE_VERSION = "curve-move.v1"
STALE_AFTER_DAYS = 5
# Treasury's par curve is fitted from indicative quotes taken at or near 3:30 PM ET on its observation date.
QUOTE_TIME = time(15, 30)
PRIOR_ENTRY = re.compile(r"^daily observation (\d{4}-\d{2}-\d{2})$")
# The Federal Reserve press feed titles each policy statement "Federal Reserve issues FOMC statement" (verified
# against the feed items in the 2026-09-16 production evidence). Its projections and minutes items are not
# statements and do not match.
FOMC_STATEMENT = re.compile(r"\bFOMC statement\b", re.I)
SENTENCES = {
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
MIXED = "Mixed curve move"
UNAVAILABLE = "Curve move unavailable"
LABELS = (*SENTENCES, MIXED, UNAVAILABLE)
# How a mixed move names what 2s10s did.
VERBS = {"Bear steepener": "bear-steepened", "Bull steepener": "bull-steepened",
         "Bear flattener": "bear-flattened", "Bull flattener": "bull-flattened",
         "Twist steepener": "twist-steepened", "Twist flattener": "twist-flattened",
         "Slight steepening": "steepened slightly", "Slight flattening": "flattened slightly"}
SHAPE_RULES = {"twist", "steepness", "slight"}


def bp(value):
    """The whole basis points a reader sees. Display, spreads and the classifier all read this integer."""
    return int(round(value))


def expected_curve_date(run_date):
    """The previous weekday before the run date (ET): the curve a reader should expect to see."""
    day = run_date - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def curve_freshness(curve_date, run_date):
    """`current`, `older` than the expected weekday but at most five calendar days old, or `stale`."""
    expected = expected_curve_date(run_date)
    age = (run_date - curve_date).days
    status = "stale" if age > STALE_AFTER_DAYS else "older" if curve_date < expected else "current"
    return dict(status=status, expected=expected.isoformat(), age_days=age)


def classify(d2, d10, long_change=None, threshold=3.0):
    """Name the move between two daily entries from integer 2Y and 10Y changes (bp), rules applied in order.

    Δs = Δ10 − Δ2; the leading leg has the larger |Δ|; every threshold comparison is inclusive. A tie between
    the legs never decides a direction: equal magnitudes with opposite signs are a twist at the threshold and a
    slight or quiet move below it, and equal magnitudes with the same sign agree.
    """
    if not threshold > 0:
        raise ValueError("curve move threshold must be positive")
    spread = d10 - d2
    lead = d10 if abs(d10) >= abs(d2) else d2
    if d2 * d10 < 0 and min(abs(d2), abs(d10)) >= threshold:
        label, rule = ("Twist steepener" if spread > 0 else "Twist flattener"), "twist"
    elif abs(spread) >= threshold:
        shape = "steepener" if spread > 0 else "flattener"
        if abs(lead) >= threshold:
            label, rule = f"{'Bear' if lead > 0 else 'Bull'} {shape}", "steepness"
        else:
            label, rule = f"Slight {'steepening' if spread > 0 else 'flattening'}", "slight"
    elif abs(lead) >= threshold:
        label, rule = f"Parallel shift {'higher' if lead > 0 else 'lower'}", "parallel"
    else:
        label, rule = "Little changed", "quiet"
    sentence, note = SENTENCES[label], ""
    if long_change is not None and abs(long_change) >= threshold:
        long_shape = "steepened" if long_change > 0 else "flattened"
        if rule in SHAPE_RULES and (long_change > 0) != (spread > 0):
            label, rule, sentence = MIXED, "mixed", f"2s10s {VERBS[label]} while 5s30s {long_shape}."
        elif rule not in SHAPE_RULES:
            note = f"5s30s {long_shape} {abs(bp(long_change))} bp."
    return dict(label=label, sentence=sentence, note=note, rule=rule)


def _same_entry(a, b):
    return a["observed_at"] == b["observed_at"] and a["source_id"] == b["source_id"]


def rates(packet):
    """`{tenor: {"level": row, "change": row}}` from this run's usable par-yield rows. A change counts only when it
    pairs with its tenor's level: same entry date, same source (as the rendered table pairs them)."""
    result = {}
    for row in packet.get("observations", []):
        topic, metric = row.get("topic") or "", row.get("metric")
        tenor = topic.removeprefix("US ")
        if (not topic.startswith("US ") or tenor not in TENORS or metric not in (LEVEL, CHANGE)
                or row.get("status") not in USABLE or not isinstance(row.get("value"), (int, float))):
            continue
        result.setdefault(tenor, {}).setdefault("level" if metric == LEVEL else "change", row)
    for legs in result.values():
        level, change = legs.get("level"), legs.get("change")
        legs["change"] = change if level and change and _same_entry(level, change) else None
        legs.setdefault("level", None)
    return result


def _leg(tenors, tenor, kind):
    return (tenors.get(tenor) or {}).get(kind)


def prior_entry_date(row):
    """The prior entry a change row was measured against, when its baseline names it (the collector's form)."""
    match = PRIOR_ENTRY.match((row or {}).get("baseline") or "")
    return match[1] if match else None


def spread_rows(tenors):
    """Derived spread rows: a level when both legs come from one entry, a change when both legs' changes are
    measured against one prior entry. Integer bp; the change is the latest spread minus the prior spread."""
    rows = []
    for name, short, long in SPREADS:
        low, high = _leg(tenors, short, "level"), _leg(tenors, long, "level")
        if not (low and high and _same_entry(low, high)):
            continue
        common = dict(topic=f"US {name}", frequency="daily", observed_at=high["observed_at"],
                      retrieved_at=high.get("retrieved_at"), source_id=high["source_id"], status="BACKGROUND",
                      reason="", formula_version=SPREAD_FORMULA, freshness=high.get("freshness"),
                      expected_freshness=high.get("expected_freshness"))
        rows.append(dict(common, id=f"treasury-{name}", metric=SPREAD_LEVEL,
                         value=bp((high["value"] - low["value"]) * 100), unit=SPREAD_UNIT,
                         baseline=f"{long} minus {short} daily par yield, one daily entry",
                         input_ids=[low["id"], high["id"]]))
        before, after = _leg(tenors, short, "change"), _leg(tenors, long, "change")
        if before and after and before["baseline"] == after["baseline"]:
            rows.append(dict(common, id=f"treasury-{name}-change", metric=SPREAD_CHANGE,
                             value=bp(after["value"]) - bp(before["value"]), unit="bp", baseline=after["baseline"],
                             input_ids=[before["id"], after["id"]]))
    return rows


def _clock(value, run):
    when = timestamp(value).astimezone(VANCOUVER)
    clock = when.strftime("%-I:%M %p") + " PT"
    return clock if when.date() == run.astimezone(VANCOUVER).date() else when.strftime("%a, %b %-d · ") + clock


def release_notes(curve_date, run, events, context_items):
    """Releases the curve predates: scheduled calendar releases after the curve's 3:30 PM ET reference quotes and
    at or before this run, and FOMC policy statements published in the same window. Titles as given; PT clocks."""
    reference = datetime.combine(curve_date, QUOTE_TIME, tzinfo=ET)
    found = []
    for item in events:
        at = item.get("scheduled_at")
        if at and item.get("title") and reference < timestamp(at) <= run:
            found.append(dict(kind="release", id=item["id"], title=item["title"], at=at))
    for item in context_items:
        at = item.get("published_at")
        if at and FOMC_STATEMENT.search(item.get("title") or "") and reference < timestamp(at) <= run:
            found.append(dict(kind="fomc", id=item["id"], title=item["title"], at=at))
    found.sort(key=lambda item: timestamp(item["at"]))
    names = [f"the {_clock(item['at'], run)} FOMC statement" if item["kind"] == "fomc"
             else f"the {_clock(item['at'], run)} {item['title']} release" for item in found[:2]]
    if not found:
        text = ""
    elif len(found) == 1:
        text = f"Curve predates {names[0]}."
    elif len(found) == 2:
        text = f"Curve predates {names[0]} and {names[1]}."
    else:
        text = f"Curve predates {names[0]}, {names[1]} and {len(found) - 2} more."
    return dict(releases=found, text=text)


def _unavailable(tenors, fresh):
    """Why the move cannot be named, in the order a reader would want to hear it, or None."""
    two, ten = _leg(tenors, "2Y", "level"), _leg(tenors, "10Y", "level")
    missing = [tenor for tenor, row in (("2Y", two), ("10Y", ten)) if not row]
    if missing:
        return "missing_tenor", f"The latest curve has no {' or '.join(missing)} yield."
    if not _same_entry(two, ten):
        return "mixed_entries", "The 2Y and 10Y yields come from different daily entries."
    if fresh["status"] == "stale":
        return "stale_observation", "The latest official curve is more than five days old."
    before, after = _leg(tenors, "2Y", "change"), _leg(tenors, "10Y", "change")
    if not before and not after:
        return "no_prior_entry", "There is no prior daily entry to compare with."
    if not before or not after:
        return "no_prior_entry", f"There is no prior daily entry for the {'2Y' if not before else '10Y'} yield."
    if before["baseline"] != after["baseline"]:
        return "mixed_entries", "The 2Y and 10Y changes are measured against different daily entries."
    return None


def curve_record(packet, tenors, spreads, threshold):
    """The typed curve-move record: label and sentence, the pair, its inputs by evidence ID, the threshold and rule
    version, the reason when unavailable, and the releases the curve predates."""
    run = timestamp(packet["run"]["target_time"])
    run_date = run.astimezone(ET).date()
    dates = sorted({legs["level"]["observed_at"] for legs in tenors.values() if legs.get("level")})
    latest = dates[-1] if dates else None
    fresh = curve_freshness(date.fromisoformat(latest), run_date) if latest else dict(
        status=None, expected=expected_curve_date(run_date).isoformat(), age_days=None)
    record = dict(kind="treasury_curve_move", rule_version=RULE_VERSION, threshold_bp=threshold, pair="2s10s",
                  observed_at=latest, prior_observed_at=None, freshness=fresh["status"],
                  expected_observed_at=fresh["expected"], age_days=fresh["age_days"], label=UNAVAILABLE,
                  sentence="", note="", rule="unavailable", reason=None, inputs=[], changes_bp={})
    unavailable = _unavailable(tenors, fresh)
    if unavailable:
        record["reason"], record["sentence"] = unavailable
    else:
        before, after = _leg(tenors, "2Y", "change"), _leg(tenors, "10Y", "change")
        d2, d10 = bp(before["value"]), bp(after["value"])
        long_row = next((row for row in spreads if row["id"] == "treasury-5s30s-change"), None)
        if long_row and (long_row["observed_at"] != after["observed_at"] or long_row["baseline"] != after["baseline"]):
            long_row = None  # 5s30s from another entry says nothing about this move
        move = classify(d2, d10, long_row["value"] if long_row else None, threshold)
        record.update(move, prior_observed_at=prior_entry_date(after),
                      inputs=[before["id"], after["id"], *([long_row["id"]] if long_row else [])],
                      changes_bp={"2Y": d2, "10Y": d10, "2s10s": d10 - d2,
                                  **({"5s30s": long_row["value"]} if long_row else {})})
    notes = (release_notes(date.fromisoformat(latest), run, packet.get("events", []), packet.get("context_items", []))
             if latest else dict(releases=[], text=""))
    record.update(releases=notes["releases"], release_note=notes["text"])
    return record


def derive_curve(packet, thresholds=None):
    """Append the spread rows to `packet["derived"]` and save the curve record as `packet["curve"]`.

    The threshold is the configured bp SMALL magnitude (`config/magnitude.json`), never a second copy of it.
    """
    threshold = (thresholds or read_json(ROOT / "config/magnitude.json"))["bp"]["SMALL"]
    tenors = rates(packet)
    spreads = spread_rows(tenors)
    packet["derived"].extend(spreads)
    packet["curve"] = curve_record(packet, tenors, spreads, threshold)
    return packet

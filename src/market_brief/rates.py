"""Rates intelligence from the official dated history: horizons, extra spreads, real yields, volatility, funding.

Deterministic and local. Reads only `packet["rates_history"]` (Treasury's nominal and real par curves and the New York
Fed's SOFR and EFFR, as published) and this run's admitted level rows; fetches nothing, fills nothing.

Every horizon counts official observations, never calendar days: "five sessions" is five Treasury entries back, so a
bond-market holiday is skipped, not interpolated. Changes are whole basis points from the published two-decimal
yields (the same integers `curve.py` reads). A history that disagrees with this run's admitted rows is withheld whole.

Derived rows are evidence (citable, in the context catalog). `packet["rates"]` is a read-only typed record for the
page and the analyst: the 20-observation history, leadership, volatility with sample counts, and funding.
"""

import math
import statistics

from .curve import SPREAD_UNIT, bp, stale_movement

NOMINAL = ("3M", "2Y", "5Y", "10Y", "30Y")
HORIZONS = ((5, "5s", "five-session yield change"), (20, "20s", "twenty-session yield change"))
REAL_HORIZONS = ((5, "5s", "five-session real yield change"), (20, "20s", "twenty-session real yield change"))
IC_HORIZONS = ((5, "5s", "five-session inflation compensation change"),
               (20, "20s", "twenty-session inflation compensation change"))
EXTRA_SPREADS = (("2s30s", "2Y", "30Y"), ("3m10y", "3M", "10Y"))
# Front end, belly and long end as the reader names them; leadership compares the 2Y with the 30Y (Market Review H6).
FRONT, BELLY, LONG = "2Y", "5Y", "10Y"
LEADERS = ("2Y", "30Y")
VOL_WINDOW, VOL_BASELINE = 20, 252
VOL_METRIC, VOL_PCT_METRIC = "twenty-session yield volatility", "volatility percentile, 252 sessions"
CHART_POINTS = 20
# The history-derived rows the analyst reads (`evidence.model_packet` leaves the others out of synthesis).
MODEL_ROWS = {"treasury-2y-change-5s", "treasury-2y-change-20s", "treasury-10y-change-5s", "treasury-10y-change-20s",
              "treasury-30y-change-5s", "treasury-30y-change-20s", "treasury-real-10y-change-5s",
              "treasury-real-10y-change-20s", "treasury-10y-inflation-comp-change-5s",
              "treasury-10y-inflation-comp-change-20s", "treasury-10y-vol20", "treasury-10y-vol20-pct",
              "treasury-2s30s", "nyfed-sofr-effr"}
FUNDING_SPREAD = "funding spread"
FUNDING_EXPLAINER = ("SOFR is the overnight rate on loans backed by Treasury collateral (repo); EFFR is the overnight "
                     "rate banks pay to borrow reserves without collateral. Both are published by the New York Fed the "
                     "next business day; neither is an intraday market rate.")
HORIZON_METRICS = {metric for _, _, metric in (*HORIZONS, *REAL_HORIZONS, *IC_HORIZONS)}
RATE_HISTORY_METRICS = HORIZON_METRICS | {VOL_METRIC, VOL_PCT_METRIC, FUNDING_SPREAD, "overnight reference rate",
                                          "daily rate change"}


def _bp(value):
    """Whole basis points of a published two-decimal yield."""
    return bp(value * 100)


def _series(packet, key):
    return ((packet.get("rates_history") or {}).get(key) or {}).get("series") or []


def _level(packet, topic, metric):
    return next((row for row in packet.get("observations", []) if row.get("topic") == topic
                 and row.get("metric") == metric and isinstance(row.get("value"), (int, float))
                 and row.get("status") in ("BACKGROUND", "AVAILABLE", "DELAYED")), None)


def consistent(packet, series, topic_of, metric):
    """The history's latest entry is the curve this run admitted: same date and same yield at every tenor that has an
    admitted level row. Anything else (a revision between fetches, a mismatched feed) withholds the history."""
    if not series:
        return False
    latest = series[-1]
    checked = 0
    for tenor, value in latest["values"].items():
        row = _level(packet, topic_of(tenor), metric)
        if row is None:
            continue
        if row["observed_at"] != latest["date"] or _bp(row["value"]) != _bp(value):
            return False
        checked += 1
    return checked > 0


def _row(ident, topic, metric, value, unit, baseline, source, day, retrieved, inputs=(), **extra):
    return dict(id=ident, topic=topic, metric=metric, value=value, unit=unit, baseline=baseline, frequency="daily",
                observed_at=day, retrieved_at=retrieved, source_id=source, status="BACKGROUND", reason="",
                formula_version="rates-history.v1", freshness="PRIOR_CLOSE", expected_freshness="PRIOR_CLOSE",
                input_ids=list(inputs), **extra)


def horizon_change(series, tenor, steps):
    """`(change_bp, prior_date)` from the latest entry back `steps` official entries that report the tenor, or None."""
    reported = [entry for entry in series if tenor in entry["values"]]
    if not reported or reported[-1] is not series[-1] or len(reported) <= steps:
        return None
    prior = reported[-1 - steps]
    return _bp(series[-1]["values"][tenor]) - _bp(prior["values"][tenor]), prior["date"]


def daily_changes(series, tenor):
    """`[(date, change_bp)]` between consecutive entries that both report the tenor (point in time, oldest first)."""
    reported = [entry for entry in series if tenor in entry["values"]]
    return [(b["date"], _bp(b["values"][tenor]) - _bp(a["values"][tenor])) for a, b in zip(reported, reported[1:])]


def volatility(series, tenor="10Y", window=VOL_WINDOW, baseline=VOL_BASELINE):
    """Market Review H2's rates side, point in time: the sample SD (bp/day) of the last `window` daily changes, and
    its percentile among its own trailing `baseline` values, today included (the share at or below today's).

    Each historical value uses only changes up to its own date. Returns counts whatever the outcome; `sd` is None when
    fewer than `window` changes exist, `percentile` None when fewer than `baseline` values exist."""
    changes = daily_changes(series, tenor)
    values = [(changes[i][0], statistics.stdev([c for _, c in changes[i - window + 1:i + 1]]))
              for i in range(window - 1, len(changes))]
    result = dict(tenor=tenor, window=window, baseline=baseline, changes=len(changes), values=len(values),
                  sd=None, percentile=None, date=None, first=None, median=None, low=None, high=None)
    if not values:
        return result
    day, latest = values[-1]
    result.update(sd=round(latest, 2), date=day, first=changes[-window][0])
    if len(values) >= baseline:
        trailing = [value for _, value in values[-baseline:]]
        result.update(percentile=round(100 * sum(v <= latest + 1e-12 for v in trailing) / baseline, 1),
                      median=round(statistics.median(trailing), 2), low=round(min(trailing), 2),
                      high=round(max(trailing), 2))
    return result


def leadership(changes, threshold):
    """Which end moved more, per horizon, from integer 2Y and 30Y changes. Plain words; no cause is named."""
    result = []
    for label, pair in changes:
        if pair is None:
            continue
        front, long = pair
        if abs(front) < threshold and abs(long) < threshold:
            text = "little changed at both ends"
            lead = "neither"
        elif abs(abs(front) - abs(long)) < threshold:
            lead = "neither"
            text = "both ends moved by similar amounts"
        else:
            lead = "front" if abs(front) > abs(long) else "long"
            text = f"the {'front end (2Y)' if lead == 'front' else 'long end (30Y)'} moved more"
        result.append(dict(horizon=label, front_bp=front, long_bp=long, lead=lead, text=text))
    return result


def _funding(packet, retrieved_by):
    """SOFR, EFFR and their spread when both report the same business day, plus a short trend for the chart."""
    sofr, effr = _series(packet, "nyfed-sofr"), _series(packet, "nyfed-effr")
    record, rows = dict(sofr=None, effr=None, spread=None, explainer=FUNDING_EXPLAINER, trend=[]), []
    for key, series in (("sofr", sofr), ("effr", effr)):
        if series:
            latest, prior = series[-1], (series[-2] if len(series) >= 2 else None)
            record[key] = dict(date=latest["date"], rate=latest["rate"], revised=latest.get("revised", False),
                               prior_date=prior["date"] if prior else None,
                               change_bp=_bp(latest["rate"]) - _bp(prior["rate"]) if prior else None,
                               target=latest.get("target"), id=f"nyfed-{key}")
    common = sorted({e["date"] for e in sofr} & {e["date"] for e in effr})
    by_sofr, by_effr = {e["date"]: e for e in sofr}, {e["date"]: e for e in effr}
    record["trend"] = [dict(date=day, sofr=by_sofr[day]["rate"], effr=by_effr[day]["rate"],
                            spread_bp=_bp(by_sofr[day]["rate"]) - _bp(by_effr[day]["rate"]))
                       for day in common[-CHART_POINTS:]]
    if record["sofr"] and record["effr"] and record["sofr"]["date"] == record["effr"]["date"]:
        day = record["sofr"]["date"]
        spread = _bp(record["sofr"]["rate"]) - _bp(record["effr"]["rate"])
        prior = [d for d in common if d < day]
        change = (spread - (_bp(by_sofr[prior[-1]]["rate"]) - _bp(by_effr[prior[-1]]["rate"]))) if prior else None
        record["spread"] = dict(date=day, bp=spread, prior_date=prior[-1] if prior else None, change_bp=change,
                                id="nyfed-sofr-effr")
        rows.append(_row("nyfed-sofr-effr", "SOFR minus EFFR", FUNDING_SPREAD, spread, SPREAD_UNIT,
                         "SOFR minus EFFR, same business day; secured repo versus unsecured fed funds",
                         "nyfed-sofr", day, retrieved_by.get("nyfed-sofr"), ["nyfed-sofr", "nyfed-effr"]))
    return record, rows


def derive_rates(packet, threshold=3.0):
    """Append the history-derived rows to `packet["derived"]` and save the record as `packet["rates"]`."""
    history = packet.get("rates_history") or {}
    retrieved_by = {key: (record or {}).get("retrieved_at") for key, record in history.items()}
    nominal, real = _series(packet, "treasury"), _series(packet, "treasury-real")
    notes = [history[key]["note"] for key in history if history[key].get("note")]
    rows = []
    record = dict(kind="rates_history", rule_version="rates-history.v1", observed_at=None, nominal_ok=False,
                  real_ok=False, horizons={}, real={}, compensation={}, spreads={}, leadership=[], chart=[],
                  previous_curve=None, volatility=None, funding=None, notes=notes)
    nominal_ok = consistent(packet, nominal, lambda t: f"US {t}", "daily par yield")
    if nominal and not nominal_ok:
        notes.append("The Treasury history did not match this run's curve, so its horizons are withheld.")
    if nominal_ok:
        latest = nominal[-1]
        day, got = latest["date"], retrieved_by.get("treasury")
        record.update(observed_at=day, nominal_ok=True)
        previous = next((entry for entry in reversed(nominal[:-1])), None)
        record["previous_curve"] = previous
        record["chart"] = [dict(date=e["date"], **{t: e["values"].get(t) for t in NOMINAL})
                           for e in nominal[-CHART_POINTS:]]
        if "3M" in latest["values"]:
            rows.append(_row("treasury-3m", "US 3M", "daily par yield", latest["values"]["3M"], "% yield",
                             "daily Treasury par curve, not an intraday quote", "treasury", day, got))
            one = horizon_change(nominal, "3M", 1)
            if one:
                rows.append(_row("treasury-3m-change", "US 3M", "daily yield change", one[0], "bp",
                                 f"daily observation {one[1]}", "treasury", day, got, ["treasury-3m"]))
        for tenor in NOMINAL:
            record["horizons"][tenor] = {"level": latest["values"].get(tenor)}
            for steps, window, metric in HORIZONS:
                found = horizon_change(nominal, tenor, steps)
                record["horizons"][tenor][window] = found and dict(bp=found[0], since=found[1])
                if found:
                    rows.append(_row(f"treasury-{tenor.lower()}-change-{window}", f"US {tenor}", metric, found[0],
                                     "bp", f"official observation {found[1]}, {steps} observations earlier",
                                     "treasury", day, got))
            one = horizon_change(nominal, tenor, 1)
            record["horizons"][tenor]["1d"] = one and dict(bp=one[0], since=one[1])
        for name, short, long in EXTRA_SPREADS:
            if short in latest["values"] and long in latest["values"]:
                level = _bp(latest["values"][long]) - _bp(latest["values"][short])
                spread = dict(bp=level, change_bp=None, since=None)
                rows.append(_row(f"treasury-{name}", f"US {name}", "curve spread", level, SPREAD_UNIT,
                                 f"{long} minus {short} daily par yield, one daily entry", "treasury", day, got))
                if previous and short in previous["values"] and long in previous["values"]:
                    before = _bp(previous["values"][long]) - _bp(previous["values"][short])
                    spread.update(change_bp=level - before, since=previous["date"])
                    rows.append(_row(f"treasury-{name}-change", f"US {name}", "daily spread change", level - before,
                                     "bp", f"daily observation {previous['date']}", "treasury", day, got))
                record["spreads"][name] = spread
        pairs = []
        for steps, window in ((1, "1d"), (5, "5s"), (20, "20s")):
            legs = [horizon_change(nominal, tenor, steps) for tenor in LEADERS]
            pairs.append((window, (legs[0][0], legs[1][0]) if all(legs) else None))
        record["leadership"] = leadership(pairs, threshold)
        vol = volatility(nominal)
        record["volatility"] = vol
        if vol["sd"] is not None:
            rows.append(_row("treasury-10y-vol20", "US 10Y", VOL_METRIC, vol["sd"], "bp/day",
                             f"sample SD of the 20 daily 10Y changes from {vol['first']} to {vol['date']}",
                             "treasury", day, got, sample_count=VOL_WINDOW))
        if vol["percentile"] is not None:
            rows.append(_row("treasury-10y-vol20-pct", "US 10Y", VOL_PCT_METRIC, vol["percentile"], "percentile",
                             "share of the trailing 252 twenty-session values at or below today's, today included",
                             "treasury", day, got, ["treasury-10y-vol20"], sample_count=VOL_BASELINE))
    real_ok = consistent(packet, real, lambda t: f"US {t} real", "daily real par yield")
    if real and not real_ok:
        notes.append("The real-yield history did not match this run's real curve, so its horizons are withheld.")
    if real_ok:
        latest, got = real[-1], retrieved_by.get("treasury-real")
        record["real_ok"] = True
        for tenor in ("10Y",):
            record["real"][tenor] = {"level": latest["values"].get(tenor)}
            for steps, window, metric in REAL_HORIZONS:
                found = horizon_change(real, tenor, steps)
                record["real"][tenor][window] = found and dict(bp=found[0], since=found[1])
                if found:
                    rows.append(_row(f"treasury-real-{tenor.lower()}-change-{window}", f"US {tenor} real", metric,
                                     found[0], "bp", f"official observation {found[1]}, {steps} observations earlier",
                                     "treasury-real", latest["date"], got))
            one = horizon_change(real, tenor, 1)
            record["real"][tenor]["1d"] = one and dict(bp=one[0], since=one[1])
        record["real_chart"] = [dict(date=e["date"], real=e["values"].get("10Y")) for e in real[-CHART_POINTS:]]
    if nominal_ok and real_ok and nominal[-1]["date"] == real[-1]["date"]:
        # Inflation compensation only where both curves report the same dates (never paired across dates).
        by_real = {e["date"]: e for e in real}
        paired = [dict(date=e["date"],
                       values={"10Y": _bp(e["values"]["10Y"]) - _bp(by_real[e["date"]]["values"]["10Y"])})
                  for e in nominal if e["date"] in by_real and "10Y" in e["values"]
                  and "10Y" in by_real[e["date"]]["values"]]
        record["compensation"]["10Y"] = {"level_bp": paired[-1]["values"]["10Y"] if paired else None}
        for steps, window, metric in IC_HORIZONS:
            if len(paired) > steps and paired[-1]["date"] == nominal[-1]["date"]:
                change = paired[-1]["values"]["10Y"] - paired[-1 - steps]["values"]["10Y"]
                since = paired[-1 - steps]["date"]
                record["compensation"]["10Y"][window] = dict(bp=change, since=since)
                rows.append(_row(f"treasury-10y-inflation-comp-change-{window}", "US 10Y inflation compensation",
                                 metric, change, "bp", f"official observation {since}, {steps} observations earlier",
                                 "treasury-real", nominal[-1]["date"], retrieved_by.get("treasury-real")))
            else:
                record["compensation"]["10Y"][window] = None
        one = ((paired[-1]["values"]["10Y"] - paired[-2]["values"]["10Y"], paired[-2]["date"])
               if len(paired) > 1 else None)
        record["compensation"]["10Y"]["1d"] = one and dict(bp=one[0], since=one[1])
    funding, funding_rows = _funding(packet, retrieved_by)
    record["funding"] = funding
    rows.extend(funding_rows)
    for row in rows:
        if row["id"] not in MODEL_ROWS:
            # The synthesis input budget is shared (busiest Nov 16–20 request 55.2 KB of 64 KB): the analyst gets the
            # rows that answer the research questions; the rest are page and ledger evidence only.
            row["page_only"] = True
    existing = {row["id"] for key in ("observations", "derived") for row in packet.get(key, [])}
    packet["derived"].extend(row for row in rows if row["id"] not in existing)
    packet["rates"] = record
    return packet


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


__all__ = ["derive_rates", "volatility", "horizon_change", "daily_changes", "leadership", "stale_movement"]

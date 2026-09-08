"""Small deterministic price-return context; never a trading classifier."""

from datetime import timedelta
from statistics import fmean

import exchange_calendars as xcals

from .evidence import finite, timestamp

DEFAULT_MAGNITUDE_THRESHOLDS = {
    "%": {"SMALL": 0.25, "NOTABLE": 1.0},
    "bp": {"SMALL": 3.0, "NOTABLE": 10.0},
    "pp": {"SMALL": 0.25, "NOTABLE": 1.0},
}


def magnitude(value, unit, thresholds=None):
    limits = (thresholds or DEFAULT_MAGNITUDE_THRESHOLDS).get(unit)
    if limits is None or not finite(value):
        return "NEUTRAL"
    absolute = abs(value)
    if absolute < limits["SMALL"]:
        return "SMALL"
    if absolute < limits["NOTABLE"]:
        return "NOTABLE"
    return "LARGE"


def annotate_magnitude(packet, thresholds=None):
    for field in ("observations", "derived"):
        for row in packet[field]:
            row["magnitude"] = magnitude(row.get("value"), row.get("unit"), thresholds)
    return packet


def return_pct(current, baseline):
    if not finite(current) or not finite(baseline) or baseline <= 0:
        raise ValueError("invalid price baseline")
    return 100 * (current / baseline - 1)


def yield_bps(current, baseline):
    if not finite(current) or not finite(baseline):
        raise ValueError("invalid yield")
    return (current - baseline) * 100


def history_metrics(closes):
    if not closes or any(not finite(c) or c <= 0 for c in closes):
        raise ValueError("history contains invalid close")
    returns = [return_pct(c, p) for p, c in zip(closes, closes[1:])]
    cross = None
    if len(closes) >= 51:
        previous = closes[-2] - fmean(closes[-51:-1])
        current = closes[-1] - fmean(closes[-50:])
        if previous * current < 0:
            cross = "UP" if current > 0 else "DOWN"
    return dict(daily_returns=returns[-5:],
                return_20=return_pct(closes[-1], closes[-21]) if len(closes) >= 21 else None,
                sma_50=fmean(closes[-50:]) if len(closes) >= 50 else None, cross_50=cross)


def derive(packet, universe, thresholds=None):
    packet["derived"], packet["attention"], packet["history_errors"] = [], [], []
    packet["lookback"] = {}
    histories = {}
    seen = set()
    cal = xcals.get_calendar("XNYS")
    now = timestamp(packet["run"]["target_time"])
    for h in packet["history"]:
        sym = h["symbol"]
        try:
            if sym in seen or sym not in universe["benchmarks"]:
                raise ValueError("duplicate or out-of-universe symbol")
            seen.add(sym)
            if h["session"] != "regular_close" or h["adjustment"] != "split":
                raise ValueError("unsupported session or adjustment basis")
            dates, closes = h["dates"], h["closes"]
            if not 2 <= len(closes) <= 65 or len(dates) != len(closes):
                raise ValueError("history must contain two to sixty-five aligned closes")
            if dates[-1] != packet["run"]["session"]["previous_session"]:
                raise ValueError("history is not through the last completed exchange session")
            expected = [s.date().isoformat() for s in cal.sessions_in_range(dates[0], dates[-1])]
            if dates != expected:
                raise ValueError("history dates duplicate, unsorted, or missing sessions")
            retrieved = timestamp(h["retrieved_at"])
            if (retrieved > now + timedelta(minutes=5)
                    or retrieved < cal.session_close(dates[-1]).to_pydatetime()):
                raise ValueError("history retrieval clock inconsistent")
            m = history_metrics(closes)
            histories[sym] = (h, m)
            packet["lookback"][sym] = dict(sessions=len(closes),
                r20="available" if len(closes) >= 21 else "n/a",
                sma50="available" if len(closes) >= 50 else "n/a")
        except (ValueError, TypeError, KeyError) as exc:
            packet["history_errors"].append(f"{sym}: invalid/incomplete historical context ({exc})")
    def add(sym, suffix, metric, value, unit, baseline, h, inputs=None):
        row = dict(id=f"{sym}-{suffix}", topic=sym, metric=metric, value=round(value, 6),
                   unit=unit, baseline=baseline, observed_at=h["dates"][-1],
                   retrieved_at=h["retrieved_at"], source_id=h["source_id"],
                   frequency="daily", status="BACKGROUND", reason="", formula_version="v0",
                   input_ids=inputs or [h["id"]], adjustment=h["adjustment"],
                   freshness="PRIOR_CLOSE", expected_freshness="PRIOR_CLOSE",
                   magnitude=magnitude(value, unit, thresholds))
        packet["derived"].append(row)
        return row["id"]
    for sym, (h, m) in histories.items():
        add(sym, "daily", "daily return", m["daily_returns"][-1], "%",
            "prior regular close / price return", h)
        if m["return_20"] is not None:
            add(sym, "r20", "twenty-session return", m["return_20"], "%",
                "twenty completed sessions / price return", h)
        if m["sma_50"] is not None:
            avg_id = add(sym, "sma50", "fifty-session average", m["sma_50"], "USD",
                         "fifty completed regular closes", h)
            if m["cross_50"]:
                close_id = add(sym, "close", "regular close", h["closes"][-1], "USD",
                               "last completed session", h)
                packet["attention"].append(dict(id=f"attention-{sym}-cross", symbol=sym,
                    reason=f"Daily close crossed {m['cross_50'].lower()} through its moving average",
                    evidence_ids=[avg_id, close_id], horizon="daily", date=h["dates"][-1]))
        benchmark = universe["benchmarks"][sym]
        if benchmark in histories and m["return_20"] is not None:
            bh, bm = histories[benchmark]
            if (bm["return_20"] is not None and h["dates"][-21:] == bh["dates"][-21:]
                    and h["source_id"] == bh["source_id"]):
                spread = m["return_20"] - bm["return_20"]
                ident = add(sym, "spread20", f"relative to {benchmark}", spread, "pp",
                            f"twenty-session price return minus {benchmark}", h,
                            [h["id"], bh["id"]])
                if abs(spread) >= 3:
                    packet["attention"].append(dict(id=f"attention-{sym}-spread", symbol=sym,
                        reason=f"Material twenty-session return spread versus {benchmark}",
                    evidence_ids=[ident], horizon="daily", date=h["dates"][-1]))
    sectors = set(universe.get("groups", {}).get("SECTORS", []))
    sector_rows = [r for r in packet["derived"] if r["topic"] in sectors
                   and r["metric"] == "relative to SPY"]
    sector_rows.sort(key=lambda row: row["value"], reverse=True)
    packet["sector_leadership"] = dict(top=sector_rows[:3], bottom=list(reversed(sector_rows[-3:])))
    for row in packet["derived"]:
        if "magnitude" not in row:
            row["magnitude"] = magnitude(row.get("value"), row.get("unit"), thresholds)
    return packet

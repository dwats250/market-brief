"""Has the market moved materially since the analysis a page carries? (WP-MB-STATE-01)

A deterministic refresh renders the last accepted analysis unchanged under newer prints. When those prints have moved
far from the ones the analysis was written on, the page says so in one compact, evidence-linked notice, and the
analysis itself (its bytes, clock and continuity identity) stays exactly as frozen. No model call, no new data.

Inputs are this run's admitted current prints and a snapshot of the same measures taken from the interpretation's own
run when it was frozen (`market_state` in the interpretation record). Both are reduced to three measures:

- sector ETF participation: how many sector ETFs (of XLB … XLY) are above or below their prior close, and their median
  move, always on the cohort of sector ETFs held at both clocks, so a symbol missing at one clock can never create a
  shift. These are sector funds, never a count of S&P 500 stocks, and the page says so;
- SPY's move against its prior close;
- QQQ's, shown when held at both clocks, never a trigger.

The notice is shown only when every requirement holds (otherwise nothing is shown and the reason is recorded):

- comparable: both snapshots belong to the same session and measure against the same prior regular close, and every
  print is a live regular-session print: AVAILABLE under the existing freshness rules (at most a minute old when
  collected). Premarket, after-hours, delayed, near-close provisional and stale prints never enter the comparison.
  Each snapshot's prints sit within SNAPSHOT_SPREAD of one another;
- separated: the current prints start at least MIN_SEPARATION after the analysis's prints end;
- covered: SPY at both clocks, and at least SECTOR_FLOOR sector ETFs held at both clocks (the common cohort);
- material, all three in one direction: on the cohort, the median sector ETF moved at least MEDIAN_SHIFT percentage
  points and the share of sector ETFs on that side of their prior close grew by at least SHARE_SHIFT, and SPY moved
  at least BENCHMARK_SHIFT points the same way.

A move that also flips the cohort median's sign (each side at least REVERSAL_FLOOR from zero) reads as a reversal;
otherwise as broadening. Thresholds are deliberately wide: a quiet page is the normal outcome.
"""

import math
import re
from datetime import timedelta
from statistics import median

from .evidence import SECTOR_ETFS, finite, timestamp

RULE = "market-state.v1"
BENCHMARK = "SPY"
SHOWN_ONLY = ("QQQ",)
SECTOR_FLOOR = 9
SNAPSHOT_SPREAD = timedelta(minutes=15)
MIN_SEPARATION = timedelta(minutes=60)
MEDIAN_SHIFT = 0.50
BENCHMARK_SHIFT = 0.50
SHARE_SHIFT = 0.25
REVERSAL_FLOOR = 0.10
PRINT_METRICS = {"premarket return", "intraday return"}
PRINT_STATES = {"AVAILABLE"}
PRIOR_CLOSE = re.compile(r"prior close (\d{4}-\d{2}-\d{2}) regular session")
ROW_FIELDS = ("id", "topic", "metric", "value", "unit", "baseline", "observed_at", "status", "freshness", "source_id")


def _prior_close(row):
    match = PRIOR_CLOSE.search(row.get("reason") or "")
    return match[1] if match else None


def snapshot(packet):
    """The measures this run holds: each usable regular-session print of a sector ETF, SPY or QQQ, with the prior
    close it is measured against. Rows that do not qualify are left out (the comparison counts what is left)."""
    session = packet["run"]["session"]
    opening, closing = timestamp(session["open"]), timestamp(session["close"])
    wanted = SECTOR_ETFS | {BENCHMARK, *SHOWN_ONLY}
    rows = {}
    for row in packet.get("observations", []):
        if (row.get("topic") not in wanted or row.get("frequency") != "intraday"
                or row.get("metric") not in PRINT_METRICS or row.get("status") not in PRINT_STATES
                or not finite(row.get("value")) or not isinstance(row.get("observed_at"), str)):
            continue
        try:
            observed = timestamp(row["observed_at"])
        except ValueError:
            continue
        prior = _prior_close(row)
        if not opening <= observed <= closing or prior is None or row["topic"] in rows:
            continue
        rows[row["topic"]] = dict({key: row[key] for key in ROW_FIELDS if key in row}, prior_close=prior)
    return dict(rule=RULE, session_date=session["date"], target_time=packet["run"]["target_time"], rows=rows)


def _snapshot_reason(state):
    """Why one snapshot cannot be compared at all, or ""."""
    rows = state.get("rows") or {}
    if not rows:
        return "no live regular-session prints"
    if BENCHMARK not in rows:
        return f"no {BENCHMARK} print"
    if len({row["prior_close"] for row in rows.values()}) != 1:
        return "prints measured against different prior closes"
    clocks = sorted(timestamp(row["observed_at"]) for row in rows.values())
    if clocks[-1] - clocks[0] > SNAPSHOT_SPREAD:
        return "prints too far apart in time to compare as one snapshot"
    return ""


def _measures(state, cohort):
    rows = state["rows"]
    values = [rows[symbol]["value"] for symbol in cohort]
    clocks = sorted(timestamp(row["observed_at"]) for row in rows.values())
    return dict(n=len(values), up=sum(v > 0 for v in values), down=sum(v < 0 for v in values),
                median=median(values), spy=rows[BENCHMARK]["value"],
                prior_close=next(iter(rows.values()))["prior_close"], first=clocks[0], last=clocks[-1])


def live_snapshot(current, previous):
    """The snapshot a refresh's edition state keeps (WP-MB-TRUTH-01): this run's own when it can be compared,
    otherwise the last comparable one an earlier edition of the same session kept, so a close whose prints are all
    DELAYED still holds the last live evidence, at its own clock. Never another session's."""
    if not _snapshot_reason(current or {}) or not previous or previous.get("rule") != RULE:
        return current
    if previous.get("session_date") != (current or {}).get("session_date") or _snapshot_reason(previous):
        return current
    return previous


def divergence(opening, current):
    """`dict(status, reason, …)`. Status `shown` carries the direction, kind, the common cohort and both sides'
    measures; `quiet` and `insufficient` carry only why nothing is shown."""
    if not opening or opening.get("rule") != RULE:
        return dict(status="insufficient", reason="the analysis holds no market-state snapshot")
    if opening.get("session_date") != current.get("session_date"):
        return dict(status="insufficient", reason="the analysis belongs to another session")
    for label, state in (("at the analysis", opening), ("now", current)):
        why = _snapshot_reason(state)
        if why:
            return dict(status="insufficient", reason=f"{label}: {why}")
    cohort = sorted(SECTOR_ETFS & set(opening["rows"]) & set(current["rows"]))
    if len(cohort) < SECTOR_FLOOR:
        return dict(status="insufficient", reason=f"{len(cohort)} sector ETFs held at both clocks "
                                                  f"(needs {SECTOR_FLOOR})")
    before, after = _measures(opening, cohort), _measures(current, cohort)
    if before["prior_close"] != after["prior_close"]:
        return dict(status="insufficient", reason="the two snapshots measure against different prior closes")
    if after["first"] - before["last"] < MIN_SEPARATION:
        return dict(status="insufficient", reason="too little time since the analysis's prints")
    shift = after["median"] - before["median"]
    spy = after["spy"] - before["spy"]
    sign = -1 if shift < 0 else 1
    side = "down" if sign < 0 else "up"
    share = after[side] / after["n"] - before[side] / before["n"]
    material = (abs(shift) >= MEDIAN_SHIFT and share >= SHARE_SHIFT
                and abs(spy) >= BENCHMARK_SHIFT and math.copysign(1, spy) == sign)
    if not material:
        return dict(status="quiet", reason="no material divergence")
    reversal = (abs(before["median"]) >= REVERSAL_FLOOR and abs(after["median"]) >= REVERSAL_FLOOR
                and math.copysign(1, before["median"]) != math.copysign(1, after["median"]))
    return dict(status="shown", reason="", direction="lower" if sign < 0 else "higher",
                kind="reversal" if reversal else "broadening", side=side, cohort=cohort, before=before,
                after=after, opening_rows=opening["rows"], current_rows=current["rows"])

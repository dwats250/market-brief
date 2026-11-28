"""One presentation model, two local formats. Model prose cannot replace fact rows.

Order: header → headline / character / short executive read → what changed → what matters next →
macro interpretation, rates and metals → sector view → equity interpretation and support → collapsed
sources & coverage. Deterministic rows are the record; the analyst's interpretation sits above them and
is labeled once. One idea has one home: the lead claims it, a section proves it, one watch tests it, and
What changed carries every verdict on an earlier hypothesis exactly once.

Two clocks. The interpretation (headline, character, read, the take, what changed, watches, section
paragraphs, flagged reasons) is rendered from a frozen interpretation record at the values the analyst
saw; the observed record (tables, events, sources, ledger) is this run's. A synthesis edition freezes
its own record, so both clocks coincide; a deterministic refresh carries the record forward.
"""

import html
import math
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .actuals import CHANGE as PERCENT_CHANGE
from .actuals import (
    COMBINED,
    EVENT_TITLE,
    FAMILIES,
    JOBS,
    JOLTS_REVISION,
    PERCENT,
    PRODUCTIVITY_REVISION,
    REVISION,
    event_family,
    period_name,
)
from .continuity import CARRY_LIMIT, interpretation_record
from .curve import (
    CHANGE,
    IC_CHANGE,
    IC_LEVEL,
    IC_TENORS,
    LEVEL,
    REAL_CHANGE,
    REAL_LEVEL,
    SENTENCES,
    SPREAD_CHANGE,
    SPREAD_LEVEL,
    SPREAD_UNIT,
    SPREADS,
    TENORS,
    UNAVAILABLE,
    bp,
    curve_freshness,
    curve_record,
    prior_entry_date,
    rates,
    spread_rows,
)
from .evidence import (
    ET,
    NEAR_CLOSE_WINDOW,
    PLACEHOLDER,
    ROOT,
    SECTOR_ETFS,
    USABLE,
    evidence_catalog,
    ohlc_ranges,
    read_json,
    timestamp,
)
from .metrics import rank_by_spread
from .schedule import CHECKPOINT_KINDS, VANCOUVER_LABEL, checkpoint_kind, next_checkpoint, session_relation
from .state import divergence
from .state import snapshot as market_snapshot

PACIFIC = ZoneInfo("America/Vancouver")
DISPLAY_STATUSES = {"LIVE", "LIVE COMMISSIONING", "LAST GOOD BRIEF", "SAMPLE"}
EDITION_LABELS = {"PREMARKET": "Premarket edition", "OPEN_1M": "Opening refresh",
                  "OPEN_30M": "Opening structure edition", "HOURLY_1100": "Hourly refresh",
                  "HOURLY_1200": "Hourly refresh", "HOURLY_1300": "Hourly refresh", "HOURLY_1400": "Hourly refresh",
                  "HOURLY_1500": "Hourly refresh", "CLOSE_1M": "Close snapshot"}
PHASE_PHRASES = {"PREMARKET": "before the open", "OPEN_1M": "in the opening minutes",
                 "OPEN_30M": "during the morning session", "HOURLY_1100": "during the morning session",
                 "HOURLY_1200": "during the morning session", "HOURLY_1300": "during the afternoon session",
                 "HOURLY_1400": "during the afternoon session", "HOURLY_1500": "during the afternoon session",
                 "CLOSE_1M": "after the close"}
SECTOR_LABELS = {
    "XLK": "Technology", "XLF": "Financials", "XLE": "Energy",
    "XLI": "Industrials", "XLY": "Consumer Discretionary", "XLP": "Consumer Staples",
    "XLV": "Health Care", "XLU": "Utilities", "XLB": "Materials",
    "XLRE": "Real Estate", "XLC": "Communication Services",
}
SECTORS = set(SECTOR_LABELS)
MEGACAPS = ("AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOG")
METALS = ("GLD", "SLV", "GDX")
INSTRUMENT_LABELS = {"GLD": "Gold fund", "SLV": "Silver ETF proxy", "GDX": "Gold miners"}
HORIZON_LABELS = {"daily return": "1d", "twenty-session return": "20s", "fifty-session average": "50d avg",
                  "distance from 50DMA": "vs 50DMA"}
LEDGER_NOTE = "Exact values and baselines for every cell are in the evidence ledger."
# Reader-facing metric names for the provenance layers; the metric strings themselves are unchanged.
READER_METRIC_LABELS = {"daily return": "Daily return", "twenty-session return": "20-session return",
                        "fifty-session average": "50-session average", "distance from 50DMA": "vs 50DMA",
                        "daily par yield": "Daily par yield", "daily yield change": "Daily change",
                        "regular close": "Regular close"}
# A current print is a latest trade measured against the previous regular close. Its metric string is
# identity and never changes; `current_print_label` names the part of the session the trade came from.
CURRENT_PRINT_METRICS = {"premarket return", "intraday return"}
CURRENT_PRINT_PHASES = {"BEFORE OPEN": "Premarket vs prior close", "DURING SESSION": "Intraday vs prior close",
                        "AFTER CLOSE": "After-hours vs prior close"}
NEAR_CLOSE_PRINT = "Near-close vs prior close"
LATEST_TRADE = "Latest trade vs prior close"
SOURCE_KIND_LABELS = {"price": "prices", "quote": "current prints", "economic_series": "rates",
                      "calendar": "release calendar", "news": "releases"}
SOURCE_STATUS_LABELS = {"AVAILABLE": "Available", "UNAVAILABLE": "Unavailable", "DEGRADED": "Degraded",
                        "STALE": "Stale", "DELAYED": "Delayed"}
# Rates rows (yield and spread levels and their daily changes) are never coloured: the sign carries direction and
# the curve move label carries meaning, and rising yields are neither good nor bad.
SIGNED_METRICS = {"daily return", "premarket return", "intraday return", "distance from 50DMA"}
MINUS = "\u2212"
RATE_UNITS = {"% yield", "bp", SPREAD_UNIT}
CANADIAN_PERSONS = "persons"
RELEASE_UNITS = {JOBS, PERCENT, PERCENT_CHANGE, "million openings", "openings", CANADIAN_PERSONS}
ISSUER_UNITS = {"USD millions", "USD per share"}
# The economic release card (Macro & rates): one line per measure in the release's own order. A change pair reads
# `+0.1% m/m · +3.0% y/y`; payroll revisions read `Jul +21k → −10k` per month and then `Combined −60k`.
RELEASE_LINES = (("Payrolls", ("nonfarm payroll change",)), ("Unemployment", ("unemployment rate",)),
                 ("Avg hourly earnings", ("average hourly earnings, monthly change",
                                          "average hourly earnings, 12-month change")),
                 ("CPI", ("CPI-U all items, monthly change", "CPI-U all items, 12-month change")),
                 ("Core CPI", ("CPI-U all items less food and energy, monthly change",
                               "CPI-U all items less food and energy, 12-month change")),
                 ("Job openings", ("job openings level",)), ("Openings rate", ("job openings rate",)),
                 ("Labor productivity", ("nonfarm business labor productivity",)),
                 ("Unit labor costs", ("nonfarm business unit labor costs",)))
PAIR_WORDS = {"monthly change": "m/m", "12-month change": "y/y"}
SHORT_MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
RATE_METRICS = {LEVEL, CHANGE, SPREAD_LEVEL, SPREAD_CHANGE, REAL_LEVEL, REAL_CHANGE, IC_LEVEL, IC_CHANGE}
# The nominal = real + inflation compensation split (W43 S3a), with the caveat every appearance carries.
PARTS_CAVEAT = ("Inflation compensation is the nominal yield minus the TIPS real yield at the same maturity. It "
                "includes inflation risk and liquidity premiums, so it is not a pure inflation forecast, and a real "
                "Treasury yield is not the borrowing cost a company or household pays.")
# The rates module (R9): Treasury's official daily par curve, its spreads, the named move, and a small inline chart.
CURVE_TITLE = "U.S. Treasury par curve"
CURVE_CAPTIONS = {"current": "official daily observation", "older": "latest official daily observation",
                  "stale": "latest official daily observation"}
STALE_NOTE = "This curve is more than five days old, so only its levels are shown here."
# Evidence normalization admits no daily row more than seven days old, so such a curve has no yields to show.
OVER_AGE_NOTE = "This curve is more than a week old, so its yields are not shown."
# Chart geometry in CSS px. x is log-maturity as a share of the plot width (2Y 0, 5Y .34, 10Y .59, 30Y 1.0); the
# plot is inset by its container's padding so end dots and labels are never clipped. The y window spans at least
# 100 bp so a one-day move looks proportionate; no gridlines and no y-axis labels (the table carries the values).
CHART_HEIGHT, CHART_TOP, CHART_BOTTOM, CHART_LABEL_Y = 120, 10, 94, 114
CHART_MIN_SPAN = 1.0  # percentage points
CHART_PADDING = 1.25
TENOR_YEARS = {"2Y": 2, "5Y": 5, "10Y": 10, "30Y": 30}
# "How to read this brief" (R12): static reader copy, collapsed near the bottom, HTML only. The first entry is the
# latest curve move, when there is one to name; each entry is one or two sentences.
GUIDE = (
    ("2s10s", "One of the most widely watched Treasury curve slopes, comparing the policy-sensitive shorter end with "
              "the longer-duration 10-year yield. Watching it rise and fall shows that part of the curve steepening "
              "or flattening."),
    ("5s30s", "The 30-year yield minus the 5-year yield. It shows what the long end is doing on its own."),
    ("Bull and bear", "In bonds, bull means prices up and yields down, and bear means prices down and yields up. "
                      "Steepening means the signed spread rose; on an inverted curve, that means less inverted."),
    ("The par curve", "Treasury's official daily curve, fitted from indicative market quotes taken near 3:30 PM ET. "
                      "It updates once a business day, not with the hourly price refreshes."),
    ("Three clocks", "Prices refresh hourly, and the analysis is written before the open and once after it. The curve "
                     "carries its own date."),
    ("§ evidence", "Tap § to see the observations behind a line. The full evidence ledger is in Sources & coverage."),
)
GUIDE_MOVES = (*SENTENCES.items(),
               ("Mixed curve move", "2s10s and 5s30s moved in opposite directions; the sentence names each."),
               (UNAVAILABLE, "A tenor is missing, the curve is stale, or there is no prior entry to compare with."))
# Absence vocabulary. `no print`: the current observation is missing while useful history exists.
# `—`: structurally not applicable. `not collected`: the source or input is not automated.
NO_PRINT = "no print"
NOT_APPLICABLE = "—"
# A bare label left after the take's last sentence ("…would show it wrong.(QQQ lead)") is not reader prose. Only
# a short run of words with no figure and no sentence of its own qualifies; a cited value or an aside stays.
TRAILING_TAG = re.compile(r"(?<=[.!?])\s*\([A-Za-z][A-Za-z '’&/-]{0,39}\)\s*$")
NOT_COLLECTED = "not collected"
# The normalizer's reason for a trade stamped after the regular-session close (`evidence.normalize_observation`).
AFTER_HOURS_REASON = "after-hours trade, after the regular-session close"
# Rows whose print is this far behind the table's shared clock carry their own clock.
SHARED_CLOCK_TOLERANCE = timedelta(minutes=5)
# The header's three clocks. The interpretation is named by the synthesis edition that wrote it.
EDITION_WORDS = {"PREMARKET": "premarket", "OPEN_30M": "opening structure"}
# WP-MB-RECOVERY-01: the page a rejected PREMARKET / OPEN_30M publishes when no analysis of this session was accepted.
DATA_ONLY_HEADING = "Analysis unavailable — current market observations only"


def unavailable_narrative():
    """An empty narrative in the accepted shape: no headline read, summary, take, watch, relationship, change,
    attention or section prose. Only `presentation` uses it, in memory, so a data-only page renders the observed
    modules with nothing interpretive; it is never validated, written, frozen or carried."""
    empty = dict(text="", evidence_ids=[])
    return dict(banner=dict(title="", limitation="", evidence_ids=[]), character=dict(empty), summary=[],
                take=dict(empty), watches=[], watch_updates=[], relationships=[], changes=[], attention=[],
                sections=dict(macro=[], equities=[], attention=[], events=[], cuttingboard=[]))
NEXT_WORDS = {"synthesis": "analysis update", "close": "close snapshot", "refresh": "price refresh"}
# A page whose next scheduled update is this late says so in the reader's browser (no server change): publishes land a
# few minutes after their checkpoint, so only a real miss (a failed run or a legitimate no-publish) crosses it.
OVERDUE_GRACE = timedelta(minutes=15)
# A carried watch's verdict. "unresolved" is not one: nothing was adjudicated.
VERDICTS = ("strengthened", "weakened", "reversed")
# Deterministic attention triggers, compressed to a short tag; the analyst's sentence is the body.
TRIGGER_TAGS = (("Material twenty-session return spread versus ", "20-session spread vs "),
                ("Daily close crossed up through its moving average", "Crossed above its 50DMA"),
                ("Daily close crossed down through its moving average", "Crossed below its 50DMA"))
# What changed, before → after (readability pass): when a verdict cites one instrument's measurement at two
# clocks, the pair renders as one line (earlier value → later value, the window, a short verb) above the
# evidence marker. The arrow joins two measurements; it never claims the path between them, and a premarket
# print and an intraday print keep their own phase words in the window.
PHASE_WORDS = {"Premarket vs prior close": "Premarket", "Intraday vs prior close": "Intraday",
               "After-hours vs prior close": "After hours", NEAR_CLOSE_PRINT: "Near close",
               LATEST_TRADE: "Latest trade"}
PRINT_MEASURE = "vs prior close"
RETURN_METRICS = {"daily return", *CURRENT_PRINT_METRICS}
# The live set of watches, by state: `active` (open question), `unresolved` (assessed, nothing to judge yet),
# `judged` (a verdict in What changed, question still open), `expired` (horizon passed, no verdict) and
# `completed` (horizon passed and a verdict). The gutter rule stays teal while the question is open and turns
# grey once its horizon has passed; the verdict itself renders only in What changed.
VERDICT_POINTER = "verdict in What changed"
# The evidence chart (readability pass): a compact figure inside an opened `§` drawer, one per cited instrument
# with admitted close history, at most EVIDENCE_CHARTS per drawer (two keeps an opened drawer inside a phone
# screen). The left part is the last EVIDENCE_SESSIONS
# completed regular closes as a percentage of the previous close (the path ends at the previous close, at
# zero); the right part holds the cited prints of this session as isolated dots in clock order, never joined.
# The fifty-session average is a dotted reference at its own distance. Geometry only: the rows beside the
# figure carry every value, and a drawer whose rows cite no close history draws nothing.
EVIDENCE_HEIGHT, EVIDENCE_TOP, EVIDENCE_BOTTOM, EVIDENCE_LABEL_Y = 84, 8, 62, 79
EVIDENCE_SESSIONS = 20
EVIDENCE_SPLIT = 0.62      # share of the width given to the close path when prints follow it
EVIDENCE_MIN_SPAN = 2.0    # percentage points
EVIDENCE_PADDING = 1.15
EVIDENCE_TICK = 0.015      # share of the width the prior session's open tick reaches left of its wick
EVIDENCE_CHARTS = 2
EVIDENCE_DOT_LABELS = 3    # more prints than this label only the first and the last
# A chart earns its place only when it shows what the rows beside it cannot: a drawer cites the instrument's
# history (a twenty-session return or spread, its fifty-session average or distance from it, the close that
# crossed it) or at least EVIDENCE_SEQUENCE prints of one session. Two prints are two numbers; the before → after
# line and the rows carry them, and no path is drawn between them.
HISTORY_METRICS = {"twenty-session return", "fifty-session average", "distance from 50DMA", "regular close"}
AVERAGE_METRICS = {"fifty-session average", "distance from 50DMA", "regular close"}
EVIDENCE_SEQUENCE = 3


def clock_key(value):
    """Orders observations: a timestamp as given, a date at the start of its Eastern day."""
    if "T" in value:
        return timestamp(value)
    return datetime.fromisoformat(value).replace(tzinfo=ET)


def move_words(earlier, later, returns):
    """A short verb for a measurement seen at two clocks. A return against the previous close reads as gain or
    loss (`turned lower`, `pared its loss`, `gave back its gain`); any other measurement rose, fell or held."""
    first, last = round(earlier, 2), round(later, 2)
    if first == last:
        return "unchanged"
    if not returns:
        return "rose" if last > first else "fell"
    if first > 0 > last:
        return "turned lower"
    if first < 0 < last:
        return "turned higher"
    if last == 0:
        return "gave back its gain" if first > 0 else "recovered to flat"
    if first == 0:
        return "turned higher" if last > 0 else "turned lower"
    if first > 0:
        return "extended its gain" if last > first else "pared its gain"
    return "extended its loss" if last < first else "pared its loss"


def movement_rows(rows, session, anchored=(), stated=""):
    """Before → after lines from one block's cited rows: one per instrument and measurement cited at two clocks,
    earliest against latest. The window names each clock and, for prints, the phase it fell in, so a premarket
    print is never conflated with an intraday one. Rows cited once pair with nothing, and a pair whose two values
    the block's own sentence already states (`stated`) is not shown a second time under it."""
    groups = {}
    for row in rows:
        if (not isinstance(row.get("value"), (int, float)) or not isinstance(row.get("observed_at"), str)
                or not row.get("observed_at") or not row.get("topic") or not row.get("metric")):
            continue
        family = "print" if current_print(row) else row["metric"]
        groups.setdefault((row["topic"], family), []).append(row)
    result = []
    for (topic, family), members in groups.items():
        try:
            members.sort(key=lambda row: clock_key(row["observed_at"]))
        except (TypeError, ValueError):
            continue
        earlier, later = members[0], members[-1]
        if earlier["observed_at"] == later["observed_at"] or earlier.get("unit") != later.get("unit"):
            continue
        if family == "print":
            phases = [PHASE_WORDS.get(current_print_label(row, session), "") for row in (earlier, later)]
            clocks = [pacific_time(row["observed_at"]).removesuffix(f" {VANCOUVER_LABEL}") for row in (earlier, later)]
            if phases[0] == phases[1]:
                window = f"{phases[0]} · {clocks[0]} → {clocks[1]} {VANCOUVER_LABEL}".lstrip(" ·")
            else:
                window = f"{phases[0]} {clocks[0]} → {phases[1]} {clocks[1]} {VANCOUVER_LABEL}"
            measure = PRINT_MEASURE
        else:
            window = f"{compact_clock(earlier['observed_at'])} → {compact_clock(later['observed_at'])}"
            measure = reader_metric_label(later, session)
        if stated and formatted(earlier) in stated and formatted(later) in stated:
            continue
        delta = round(later["value"] - earlier["value"], 6)
        result.append(dict(
            topic=topic, label=instrument_label(topic),
            earlier=dict(cell(earlier), anchor=earlier.get("id") in anchored),
            later=dict(cell(later), anchor=later.get("id") in anchored),
            verb=move_words(earlier["value"], later["value"], family == "print" or family in RETURN_METRICS),
            direction="neutral" if round(delta, 2) == 0 else "positive" if delta > 0 else "negative",
            window=window, measure=measure))
    return result


def evidence_chart(history, prints=(), average=None, session_date=None, sessions=EVIDENCE_SESSIONS,
                   average_cited=False):
    """Server-side geometry for one instrument's evidence chart. None when the history is too short to draw. The
    fifty-session average is a reference, never the reason the chart exists: drawn at its distance when the drawer
    cites it (then the window makes room for it), otherwise only when it already falls inside the window."""
    closes, dates = history["closes"][-(sessions + 1):], history["dates"][-(sessions + 1):]
    if len(closes) < 2 or not closes[-1]:
        return None
    prior = closes[-1]
    path = [100 * (close / prior - 1) for close in closes]
    dots = sorted((row for row in prints if isinstance(row.get("value"), (int, float))
                   and isinstance(row.get("observed_at"), str) and "T" in row["observed_at"]),
                  key=lambda row: timestamp(row["observed_at"]))
    reference = 100 * (average / prior - 1) if isinstance(average, (int, float)) and average else None
    values = [*path, *(row["value"] for row in dots)]
    if reference is not None and (average_cited or min(values) <= reference <= max(values)):
        values.append(reference)
    else:
        reference = None
    low, high = min(values), max(values)
    span = max((high - low) * EVIDENCE_PADDING, EVIDENCE_MIN_SPAN)
    base = (low + high) / 2 - span / 2

    def y(value):
        return round(EVIDENCE_BOTTOM - (value - base) / span * (EVIDENCE_BOTTOM - EVIDENCE_TOP), 1)

    def x(share):
        return f"{100 * share:.1f}%"

    split = EVIDENCE_SPLIT if dots else 1.0
    points = [dict(x=x(split * index / (len(path) - 1)), y=y(value)) for index, value in enumerate(path)]
    segments = [dict(x1=a["x"], y1=a["y"], x2=b["x"], y2=b["y"]) for a, b in zip(points, points[1:])]
    zone = (split + 0.05, 0.98)
    marks = []
    for index, row in enumerate(dots):
        share = zone[0] + (zone[1] - zone[0]) * (index + 0.5) / len(dots)
        labelled = len(dots) <= EVIDENCE_DOT_LABELS or index in (0, len(dots) - 1)
        marks.append(dict(x=x(share), y=y(row["value"]), direction=direction(row),
                          label=(pacific_time(row["observed_at"]).removesuffix(f" {VANCOUVER_LABEL}")
                                 if labelled else "")))
    # The prior session's own range, a neutral wick and open tick on the prior point. The domain above is the closes'
    # and stays so, so a wick that runs past it ends at the box edge instead of moving every other point; the head
    # line always states the exact range.
    candle, ranges = None, ohlc_ranges(history)
    if ranges:
        opened, high, low = (ranges[key][-1] for key in ("opens", "highs", "lows"))
        top, bottom, tick = (min(max(y(100 * (price / prior - 1)), EVIDENCE_TOP), EVIDENCE_BOTTOM)
                             for price in (high, low, opened))
        candle = dict(x=points[-1]["x"], tick_x=x(split - EVIDENCE_TICK), y_high=top, y_low=bottom, y_open=tick,
                      label=f"Prior session · O {opened:.2f} H {high:.2f} L {low:.2f} C {prior:.2f}")
    return dict(height=EVIDENCE_HEIGHT, label_y=EVIDENCE_LABEL_Y, zero_y=y(0),
                reference_y=y(reference) if reference is not None else None,
                path=dict(points=points, segments=segments), prior=points[-1],
                divider_x=x(split + 0.025) if dots else None, dots=marks,
                left_label=f"{len(closes) - 1} sessions to {short_date(dates[-1])}",
                right_label=(f"{short_date(session_date)} prints" if dots and session_date else
                             "prints" if dots else ""),
                domain=[round(base, 4), round(base + span, 4)], candle=candle)


def pacific_time(value, include_date=False):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(PACIFIC)
    date_part = dt.strftime("%A, %b %-d · ") if include_date else ""
    return f"{date_part}{dt.strftime('%-I:%M %p')} {VANCOUVER_LABEL}"


def event_clock(event):
    """An event's displayed clock. A calendar event reads in the page's Vancouver clock. An issuer-announced event keeps
    the clock its source states (`2 p.m. PT (5 p.m. ET)`, where PT is US Pacific), never relabelled as the page's
    Vancouver time; the Vancouver time is shown beside it under its own name, with the source's note and link."""
    if not event.get("stated_time"):
        return dict(scheduled_label=pacific_time(event["scheduled_at"], True), source_line="")
    when = datetime.fromisoformat(event["scheduled_at"].replace("Z", "+00:00"))
    day = when.astimezone(ET).strftime("%A, %b %-d")
    local = when.astimezone(PACIFIC).strftime("%-I:%M %p")
    announced = datetime.fromisoformat(event["announced_on"]).strftime("%b %-d") if event.get("announced_on") else ""
    return dict(scheduled_label=f"{day} · {event['stated_time']}", local_label=f"{local} {VANCOUVER_LABEL}",
                source_line=", ".join(filter(None, ("NVIDIA release", announced))))


def short_date(value):
    """`2026-09-04` → `Fri, Sep 4`; an intraday clock keeps its Pacific time. The one daily-date formatter."""
    if not isinstance(value, str) or not value:
        return ""
    if "T" in value:
        return pacific_time(value, True)
    return datetime.fromisoformat(value).strftime("%a, %b %-d")


def shared_rates_baseline(yields):
    """Return a display label only when every available table change has one verified shared baseline."""
    changes = [row for row in yields if not row["change"]["absent"]]
    if not changes:
        return "Daily change"
    dates = []
    for row in changes:
        match = re.fullmatch(r"daily observation (\d{4}-\d{2}-\d{2})", row.get("change_baseline") or "")
        observed_at = row.get("change_observed_at")
        if not match or not observed_at:
            return "Daily change"
        baseline_date = match.group(1)
        try:
            baseline = datetime.fromisoformat(baseline_date).date()
            observed = datetime.fromisoformat(observed_at).date()
        except ValueError:
            return "Daily change"
        if baseline.isoformat() != baseline_date or baseline >= observed:
            return "Daily change"
        dates.append(baseline_date)
    if len(set(dates)) != 1:
        return "Daily change"
    return f"Daily change vs {short_date(dates[0])}"


def status_for(packet):
    run = packet["run"]
    explicit = run.get("display_status")
    if explicit in DISPLAY_STATUSES:
        return explicit
    if run["mode"] == "SAMPLE":
        return "SAMPLE"
    if run.get("commissioning"):
        return "LIVE COMMISSIONING"
    if (run["checkpoint"] == "PREMARKET"
            and not packet["run"]["session"].get("meaningful_premarket")):
        return "SAMPLE"
    return "LIVE"


def current_print(row):
    """A prior-snapshot row (`premarket:SPY-intraday`) carries no frequency; its metric alone names it."""
    return row.get("metric") in CURRENT_PRINT_METRICS and row.get("frequency") in (None, "intraday")


def current_print_label(row, session):
    """The one reader label for a current print, read from the row's own clock against the session bounds,
    so a frozen row keeps the phase it was observed in. A print that cannot be placed inside this session
    (no clock, another session's date, no bounds) is only the latest trade; nothing is ever the close."""
    if row.get("status") == "PROVISIONAL":
        return NEAR_CLOSE_PRINT
    try:
        observed = timestamp(row["observed_at"])
        opening, closing = timestamp(session["open"]), timestamp(session["close"])
    except (KeyError, TypeError, ValueError, AttributeError):
        return LATEST_TRADE
    if observed.astimezone(ET).date() != opening.astimezone(ET).date():
        return LATEST_TRADE
    return CURRENT_PRINT_PHASES[session_relation(observed, opening, closing)]


def measure_label(row, session=None):
    metric = row["metric"]
    if current_print(row):
        return f"{row['topic']} · {current_print_label(row, session)}"
    if metric.startswith("relative to "):
        return f"{row['topic']} vs {metric.removeprefix('relative to ')} · 20s"
    horizon = HORIZON_LABELS.get(metric, metric)
    return f"{row['topic']} · {horizon}"


def metric_label(row, session=None):
    if current_print(row):
        return current_print_label(row, session)
    return row["metric"]


def reader_metric_label(row, session=None):
    """`twenty-session return` → `20-session return`; `relative to SPY` → `vs SPY · 20 sessions`; a payroll revision
    names its month and estimates: `Payroll revision, July 2026 (+21k → −10k)`."""
    metric = row.get("metric", "")
    if metric == REVISION and row.get("revised_month") and "revised_from" in row and "revised_to" in row:
        return (f"Payroll revision, {period_name(row['revised_month'])} "
                f"({formatted(dict(row, value=row['revised_from']))} \u2192 "
                f"{formatted(dict(row, value=row['revised_to']))})")
    if metric.startswith("relative to "):
        return f"vs {metric.removeprefix('relative to ')} · 20 sessions"
    if current_print(row):
        return current_print_label(row, session)
    return READER_METRIC_LABELS.get(metric, metric[:1].upper() + metric[1:] if metric else "")


def reader_limitation(text):
    """A deterministic coverage limitation in reader words: an input the pipeline does not automate is
    `not collected`, as in the Sources drawer. The packet keeps its own wording."""
    if text.startswith("Not automated:"):
        return "Not collected:" + text.removeprefix("Not automated:")
    name, separator, reason = text.partition(": ")
    if separator and reason.startswith("not automated"):
        return f"{name}: {NOT_COLLECTED}"
    if separator and reason == AFTER_HOURS_REASON:
        # The packet keeps its reason and status; the reader is told plainly that no session value exists.
        return (f"{name}: no regular-session print, so no session value is shown; its latest trade was after the "
                "regular-session close (after-hours)")
    return text


def instrument_label(symbol):
    """`XLK` → `Technology · XLK`; unknown topics keep their own name."""
    name = SECTOR_LABELS.get(symbol) or INSTRUMENT_LABELS.get(symbol)
    return f"{name} · {symbol}" if name else symbol


def trigger_tag(reason):
    """`Material twenty-session return spread versus SPY` → `20-session spread vs SPY`."""
    for prefix, tag in TRIGGER_TAGS:
        if reason.startswith(prefix):
            return tag + reason[len(prefix):] if prefix.endswith(" ") else tag
    return reason


def source_rows(sources):
    """Reader-facing source ledger rows: what each source is, whether it worked, and why not."""
    rows = []
    for s in sources:
        kind = SOURCE_KIND_LABELS.get(s.get("kind", ""), s.get("kind", ""))
        name = s["name"]
        redundant = any(word in name.lower() for word in kind.lower().split())
        status = s.get("status", "AVAILABLE")
        reason = s.get("reason") or ""
        if reason.startswith("not automated"):
            reason = NOT_COLLECTED
        # Degraded is available with a stated gap (a release whose revisions did not read): its values are shown.
        rows.append(dict(s, kind_label="" if redundant else kind,
                         status_label=SOURCE_STATUS_LABELS.get(status, status.title()),
                         unavailable=status not in ("AVAILABLE", "DEGRADED"), degraded=status == "DEGRADED",
                         reason=reason,
                         retrieved=compact_clock(s.get("retrieved_at", "")),
                         detail=" · ".join(filter(None, (s.get("provider"), s.get("feed"), s.get("data_delay"))))))
    return rows


def observed_label(value):
    if isinstance(value, str) and "T" in value:
        return pacific_time(value, True)
    return short_date(value) if isinstance(value, str) and value else value


def item_detail(row):
    """A dated context item's ledger line: its bounded summary in the baseline's place, attributed when the record
    names whose statement it is (`NVIDIA statement: …`)."""
    if "value" in row or not row.get("summary"):
        return {}
    attribution = row.get("attribution")
    return dict(baseline=f"{attribution}: {row['summary']}" if attribution else row["summary"])


def release_clock(row, session):
    """A release value's official time: `5:30 AM Vancouver time` in its session, dated in any other.

    A carried Friday print on Monday's page never reads as this morning's.
    """
    value = row.get("observed_at") or ""
    if "T" in value and timestamp(value).astimezone(ET).date().isoformat() != (session or {}).get("date"):
        return pacific_time(value, True)
    return compact_clock(value)


def compact_clock(value):
    """Reader-facing clock above the audit layer: `Thu, Sep 10` for a dated row,
    `1:02 PM Vancouver time` for a print.
    """
    if not isinstance(value, str) or not value:
        return ""
    return pacific_time(value) if "T" in value else short_date(value)


def direction(row):
    if row.get("metric") not in SIGNED_METRICS and not row.get("metric", "").startswith("relative to "):
        return "neutral"
    value = row.get("value")
    if not isinstance(value, (int, float)) or round(value, 2) == 0:
        return "neutral"
    return "positive" if value > 0 else "negative"


def rate_display(value, unit):
    """Rates in desk form: a yield `5.18%`; a move in whole basis points `+7 bp`; a spread level `31 bp`, with a minus
    sign only when inverted. Basis points are the same integers the curve classifier reads."""
    if unit == "% yield":
        return f"{MINUS if value < 0 and round(value, 2) != 0 else ''}{abs(value):.2f}%"
    whole = bp(value)
    if whole == 0:
        return "0 bp"
    sign = MINUS if whole < 0 else "+" if unit == "bp" else ""
    return f"{sign}{abs(whole)} bp"


def release_display(value, unit):
    """An official release value as BLS prints it: payrolls in whole thousands `+29k`, a rate `4.2%`, a change
    `+0.1%`, each to its published precision. Presentation only; the row keeps its number."""
    if unit == JOBS:
        whole = round(value)
        return f"{MINUS if whole < 0 else '+' if whole > 0 else ''}{abs(whole):,}k"
    if unit == PERCENT:
        return f"{value:.1f}%"
    if unit == "million openings":
        return f"{value:.1f}m"
    if unit == "openings":
        whole = round(value)
        return f"{MINUS if whole < 0 else '+' if whole > 0 else ''}{abs(whole):,}"
    if unit == CANADIAN_PERSONS:
        sign = MINUS if value < 0 else "+" if value > 0 else ""
        return f"{sign}{abs(value):,.0f} persons"
    tenth = round(value, 1)
    return f"{MINUS if tenth < 0 else '+' if tenth > 0 else ''}{abs(tenth):.1f}%"


def formatted(row):
    """One number style everywhere: `+0.53%`, `−5.30 pp`, `−6 bp`, `90.02 USD`, and release values as BLS prints them
    (`+29k`, `4.2%`, `+0.1%`). A percentage keeps no space before its sign; a unit word keeps one. Negatives carry a
    true minus, as rates always have."""
    if row.get("value") is None:
        return NO_PRINT
    value, unit = row["value"], row["unit"]
    if unit in RATE_UNITS:
        return rate_display(value, unit)
    if unit in RELEASE_UNITS:
        return release_display(value, unit)
    if unit in ISSUER_UNITS:
        # An issuer's figure as its release states it: `$96,221M` revenue, `$2.46` diluted EPS.
        sign = MINUS if value < 0 else ""
        return f"{sign}${abs(value):,.0f}M" if unit == "USD millions" else f"{sign}${abs(value):.2f}"
    signed = unit in {"pp", "%"}
    # A value that rounds to zero is shown as zero: no sign, no colour.
    if signed and round(value, 2) == 0:
        number = "0.00"
    else:
        sign = MINUS if value < 0 else "+" if signed else ""
        number = f"{sign}{abs(value):.2f}"
    return f"{number}{unit}" if unit == "%" else f"{number} {unit}"


def absent_cell(text=NOT_APPLICABLE):
    return dict(display=text, value=None, direction="neutral", id=None, status=None, observed="",
                observed_at=None, absent=True)


def cell(row, bare=False, absent=NOT_APPLICABLE):
    if not row:
        return absent_cell(absent)
    return dict(display=formatted(row).removesuffix(" USD") if bare else formatted(row),
                value=row.get("value"), direction=direction(row), id=row.get("id"), status=row.get("status"),
                observed=observed_label(row.get("observed_at")), observed_at=row.get("observed_at"), absent=False)


def compact_equity_rows(rows, symbols, allow_daily_today=True):
    """One row per instrument: current-or-daily change, 20s return, labeled spread, 50DMA distance.

    `today` starts as the intraday print or the daily return; `change_column` settles the table's mode.
    """
    result = []
    for symbol in symbols:
        symbol_rows = [row for row in rows if row["topic"] == symbol]
        if not symbol_rows:
            continue
        current = next((row for row in symbol_rows
                        if row.get("frequency") == "intraday" and row["status"] in USABLE), None)
        daily = next((row for row in symbol_rows if row.get("metric") == "daily return"), None)
        today = current or (daily if allow_daily_today else None)
        r20 = next((row for row in symbol_rows if row.get("metric") == "twenty-session return"), None)
        relative = next((row for row in symbol_rows
                         if row.get("metric", "").startswith("relative to ")), None)
        average = next((row for row in symbol_rows if row.get("metric") == "fifty-session average"), None)
        distance = next((row for row in symbol_rows if row.get("metric") == "distance from 50DMA"), None)
        result.append(dict(symbol=symbol, label=SECTOR_LABELS.get(symbol) or INSTRUMENT_LABELS.get(symbol, symbol),
                           today=cell(today, absent=NO_PRINT), intraday=cell(current, absent=NO_PRINT),
                           daily=cell(daily, absent=NO_PRINT), r20=cell(r20), relative=cell(relative),
                           relative_label=(relative.get("metric", "").removeprefix("relative to ")
                                           if relative else "benchmark"),
                           average=cell(average, bare=True), dma=cell(distance),
                           current_is_intraday=bool(current)))
    return result


def table_notes(rows, change_label, packet, session):
    """Plain notes under one instrument table that separate what its cells are: last trades before the close (never
    official closing prices), symbols whose only trades were after the close, and history columns that lag a session.
    Empty when there is nothing to distinguish."""
    notes = []
    # The exchange's own clock: unambiguous, never the page's Vancouver time with a PT suffix.
    close = (timestamp(session["close"]).astimezone(ET).strftime("%-I:%M %p ET regular-session")
             if session.get("close") else "regular-session")
    printed = [row["intraday"] for row in rows if row["intraday"]["id"]]
    try:
        bell = timestamp(session["close"])
        final = [cell for cell in printed if bell - NEAR_CLOSE_WINDOW <= timestamp(cell["observed_at"]) <= bell]
    except (KeyError, TypeError, ValueError):
        final = []
    if final and len(final) == len(printed):
        # The last trades before the bell, at their own clocks: never official closing prices.
        provisional = (" Collected well after the close, so provisional." if
                       all(cell["status"] == "PROVISIONAL" for cell in final) else "")
        notes.append(f"Each Change is the last trade seen before the {close} close, not an official closing "
                     f"price.{provisional}")
    symbols = {row["symbol"] for row in rows if not row["intraday"]["id"]}
    after_hours = sorted({r["topic"] for r in packet["observations"]
                          if r.get("frequency") == "intraday" and r["topic"] in symbols
                          and r["status"] == "UNAVAILABLE" and (r.get("reason") or "").startswith("after-hours trade")})
    if after_hours:
        names = ", ".join(after_hours)
        notes.append(f"No print for {names}: the latest trade came after the {close} close, and after-hours trades "
                     "never stand for the session.")
    lag = packet.get("history_lag")
    if lag and rows:
        notes.append(f"The 20-session and 50DMA columns use daily history through "
                     f"{short_date(lag['history_through'])}; the {short_date(lag['completed_session'])} daily bar is "
                     "not published yet.")
    return notes


def change_column(rows, allow_daily_today=True, session=None):
    """Settle one table's change column: one shared intraday clock with per-row exceptions, or one daily date.

    Current-print mode applies as soon as any row has a usable print: the caption names the prints' phase
    (the latest trade when they span phases) and carries the latest print's clock, a row whose print is
    older than the tolerance carries its own clock, and a row without a print says so. Without any print
    the column is the dated daily return. Mixed columns never happen.
    """
    printed = [row for row in rows if row["intraday"]["id"]]
    if printed:
        phases = {current_print_label(row["intraday"], session) for row in printed}
        label = phases.pop() if len(phases) == 1 else LATEST_TRADE
        if all(row["intraday"]["status"] == "PROVISIONAL" for row in printed):
            label += " · provisional"
        latest = max(timestamp(row["intraday"]["observed_at"]) for row in printed)
        for row in rows:
            if row["intraday"]["id"]:
                row["today"] = dict(row["intraday"])
                behind = latest - timestamp(row["today"]["observed_at"])
                row["today"]["observed"] = (compact_clock(row["today"]["observed_at"])
                                            if behind > SHARED_CLOCK_TOLERANCE else "")
            else:
                row["today"] = absent_cell(NO_PRINT)
        return label, f"as of {pacific_time(latest.isoformat())}"
    dated = [row for row in rows if row["daily"]["id"]] if allow_daily_today else []
    if dated:
        dates = {row["daily"]["observed_at"] for row in dated}
        for row in rows:
            row["today"] = dict(row["daily"]) if row["daily"]["id"] else absent_cell(NO_PRINT)
            row["today"]["observed"] = (short_date(row["today"]["observed_at"])
                                        if len(dates) > 1 and row["today"]["id"] else "")
        if len(dates) == 1:
            return f"Daily change · {short_date(next(iter(dates)))}", None
        return "Daily change · dated per row", None
    for row in rows:
        row["today"] = absent_cell(NO_PRINT)
    return "Change", None


def treasury_rows(facts):
    """Maturity, yield, daily change in bp for the four tenors; a change pairs only with a same-dated, same-source
    yield, and a tenor the curve lacks reads `no print`."""
    tenor_rows = [r for r in facts if r["metric"] in (LEVEL, CHANGE)]
    if not tenor_rows:
        return [], None
    result = []
    for term in TENORS:
        topic = f"US {term}"
        level = next((r for r in tenor_rows if r["topic"] == topic and r["metric"] == LEVEL), None)
        change = next((r for r in tenor_rows if r["topic"] == topic and r["metric"] == CHANGE), None)
        paired = bool(level and change and level["observed_at"] == change["observed_at"]
                      and level["source_id"] == change["source_id"])
        anchor = level or change
        result.append(dict(maturity=term, level=cell(level, absent=NO_PRINT), change=cell(change if paired else None),
                           change_note="" if paired or not change else
                           f"change dated {short_date(change['observed_at'])} not paired",
                           change_baseline=change.get("baseline") if paired else None,
                           change_observed_at=change.get("observed_at") if paired else None,
                           date=short_date(anchor["observed_at"]) if anchor else "",
                           observed_at=anchor["observed_at"] if anchor else None,
                           ids=[r["id"] for r in (level, change if paired else None) if r]))
    dates = {row["observed_at"] for row in result if row["observed_at"]}
    asof = short_date(next(iter(dates))) if len(dates) == 1 else None
    if asof:
        for row in result:
            row["date"] = ""
    return result, asof


def spread_lines(spreads, tenors, stale):
    """`2s10s · 31 bp · 5 bp steeper`: a spread level is a level; its change is steeper or flatter by the signed spread
    (long minus short), whatever the level's sign, so on an inverted curve steeper means less inverted. A spread the
    curve cannot form is named as missing; a stale curve shows levels only."""
    lines, notes = [], []
    for name, short, long in SPREADS:
        level, change = spreads.get(f"treasury-{name}"), spreads.get(f"treasury-{name}-change")
        if not level:
            missing = [tenor for tenor in (short, long) if not (tenors.get(tenor) or {}).get("level")]
            notes.append(f"No {name}: the latest curve has no {' or '.join(missing)} yield." if missing else
                         f"No {name}: its {short} and {long} yields come from different daily entries.")
            continue
        value, detail, flip = bp(level["value"]), "", ""
        if change and not stale:
            move = bp(change["value"])
            prior = value - move
            if move == 0:
                detail = "unchanged"
            else:
                detail = f"{abs(move)} bp {'steeper' if move > 0 else 'flatter'}"
                if value < 0 and prior < 0:
                    detail += " (less inverted)" if move > 0 else " (more inverted)"
            if prior < 0 < value:
                flip = f"{name} turned positive"
            elif value < 0 <= prior:
                flip = f"{name} inverted"
        lines.append(dict(name=name, definition=f"{long} minus {short}", level=formatted(level), detail=detail,
                          flip=flip, ids=[level["id"], *([change["id"]] if change and not stale else [])]))
    return lines, notes


def curve_chart(tenors, curve_date, prior_date, stale):
    """Server-side geometry for the inline curve: the observed tenors of the latest entry as dots joined by straight
    segments between adjacent tenors (a missing tenor breaks the line), and the prior entry dashed when it has every
    tenor the current curve has. None when fewer than two adjacent tenors exist or the curve is stale."""
    if stale or not curve_date:
        return None
    points = {tenor: legs["level"]["value"] for tenor, legs in tenors.items()
              if legs.get("level") and legs["level"]["observed_at"] == curve_date}
    pairs = [(a, b) for a, b in zip(TENORS, TENORS[1:]) if a in points and b in points]
    if not pairs:
        return None
    changes = {tenor: tenors[tenor].get("change") for tenor in points}
    ghost = None
    if all(changes.values()) and len({row["baseline"] for row in changes.values()}) == 1:
        ghost = {tenor: points[tenor] - bp(changes[tenor]["value"]) / 100 for tenor in points}
    values = [*points.values(), *(ghost or {}).values()]
    low, high = min(values), max(values)
    span = max((high - low) * CHART_PADDING, CHART_MIN_SPAN)
    base = (low + high) / 2 - span / 2

    def x(tenor):
        share = (math.log(TENOR_YEARS[tenor]) - math.log(2)) / (math.log(30) - math.log(2))
        return f"{100 * share:.1f}%"

    def y(value):
        return round(CHART_BOTTOM - (value - base) / span * (CHART_BOTTOM - CHART_TOP), 1)

    def series(levels):
        return dict(points=[dict(x=x(tenor), y=y(levels[tenor]), tenor=tenor) for tenor in TENORS if tenor in levels],
                    segments=[dict(x1=x(a), y1=y(levels[a]), x2=x(b), y2=y(levels[b])) for a, b in pairs])

    return dict(height=CHART_HEIGHT, label_y=CHART_LABEL_Y, current=series(points),
                ghost=series(ghost) if ghost else None,
                labels=[dict(x=x(tenor), text=tenor) for tenor in TENORS],
                legend=dict(current=short_date(curve_date),
                            prior=short_date(prior_date) if prior_date else "prior entry") if ghost else None,
                domain=[round(base, 4), round(base + span, 4)])


def bridge_sentence(maturity, nominal, real, compensation):
    """The deterministic takeaway of one maturity's change split, from whole basis points (PRD §6.2): offsetting legs
    are said to offset and never given shares; an unchanged total whose parts moved says so."""
    name = {"5Y": "5-year", "10Y": "10-year", "20Y": "20-year", "30Y": "30-year"}.get(maturity, maturity)

    def signed(value):
        return f"+{value} bp" if value > 0 else f"{MINUS}{abs(value)} bp" if value < 0 else "0 bp"

    def moved(value, noun):
        return f"{noun} {'rose' if value > 0 else 'fell'} {abs(value)} bp"
    if nominal == real == compensation == 0:
        return f"The {name} yield and both of its parts were unchanged."
    if nominal == 0:
        return (f"The {name} yield was unchanged, but not because nothing moved: the "
                f"{moved(real, 'real yield')} and {moved(compensation, 'inflation compensation')}.")
    direction = "rose" if nominal > 0 else "fell"
    if real and compensation and (real > 0) != (compensation > 0):
        return (f"The {name} yield {direction} only {abs(nominal)} bp because its parts offset: the "
                f"{moved(real, 'real yield')} while {moved(compensation, 'inflation compensation')}.")
    parts = (("real yield", real), ("inflation compensation", compensation))
    lead, other = parts if abs(real) >= abs(compensation) else parts[::-1]
    if other[1] == 0:
        return (f"The {name} yield {direction} {abs(nominal)} bp, all of it in the {lead[0]}; the {other[0]} was "
                "unchanged.")
    if abs(lead[1]) >= 2 * abs(other[1]):
        return (f"The {name} yield {direction} {abs(nominal)} bp, mostly through the {lead[0]} ({signed(lead[1])}); "
                f"{moved(other[1], other[0])}.")
    return (f"The {name} yield {direction} {abs(nominal)} bp, split between the real yield ({signed(real)}) and "
            f"inflation compensation ({signed(compensation)}).")


def rate_parts(facts, stale):
    """Each maturity whose nominal and real yields share a date: its levels and, unless the curve is stale, its change
    split, with the takeaway sentence. From this run's rows only; nothing is paired across dates (`curve.py`)."""
    rows = {(r["topic"], r["metric"]): r for r in facts}
    parts = []
    for tenor in IC_TENORS:
        level = rows.get((f"US {tenor} inflation compensation", IC_LEVEL))
        nominal, real = rows.get((f"US {tenor}", LEVEL)), rows.get((f"US {tenor} real", REAL_LEVEL))
        if not (level and nominal and real):
            continue
        change = None if stale else rows.get((f"US {tenor} inflation compensation", IC_CHANGE))
        n_change, r_change = rows.get((f"US {tenor}", CHANGE)), rows.get((f"US {tenor} real", REAL_CHANGE))
        split = None
        if change and n_change and r_change:
            split = dict(nominal=formatted(n_change), real=formatted(r_change), compensation=formatted(change),
                         sentence=bridge_sentence(tenor, bp(n_change["value"]), bp(r_change["value"]),
                                                  bp(change["value"])),
                         since=short_date(prior_entry_date(change) or ""))
        parts.append(dict(maturity=tenor, date=short_date(level["observed_at"]), nominal=formatted(nominal),
                          real=formatted(real), compensation=formatted(level), split=split,
                          ids=[r["id"] for r in (nominal, real, level, n_change, r_change, change) if r]))
    return parts


def rates_module(packet, facts, catalog):
    """The Macro & rates module (R9): caption, the four-tenor table, spread lines, the named curve move, the chart,
    and notes, all from this run's deterministic rows and curve record; the analyst's paragraphs follow it."""
    yields, asof = treasury_rows(facts)
    if not yields:
        record = packet.get("curve") or {}
        if record.get("freshness") == "stale" and record.get("observed_at"):
            # Rows past the admission window arrive without values; the curve is still dated, and says so.
            notes = [OVER_AGE_NOTE, *([record["release_note"]] if record.get("release_note") else [])]
            return dict(yields=[], asof=None, daily_change_label="Daily change", proof_ids=[], curve=dict(
                title=CURVE_TITLE, caption=f"{short_date(record['observed_at'])} · {CURVE_CAPTIONS['stale']}",
                stale=True, spreads=[], move=None, chart=None, notes=notes))
        return dict(yields=[], asof=None, daily_change_label="Daily change", curve=None, proof_ids=[])
    tenors = rates(packet)
    spreads = {row["id"]: row for row in facts if row["metric"] in (SPREAD_LEVEL, SPREAD_CHANGE)}
    record = packet.get("curve")
    if record is None:
        # Evidence saved before the curve record existed: the same deterministic derivation, never saved from here.
        derived = spread_rows(tenors)
        spreads = spreads or {row["id"]: row for row in derived}
        record = curve_record(packet, tenors, derived, read_json(ROOT / "config/magnitude.json")["bp"]["SMALL"])
    freshness = record.get("freshness")
    stale = freshness == "stale"
    if stale:
        for row in yields:
            row["change"], row["change_note"] = absent_cell(), ""
            row["change_baseline"], row["change_observed_at"] = None, None
            row["ids"] = [row["level"]["id"]] if row["level"]["id"] else []
    daily_change_label = shared_rates_baseline(yields)
    caption = " · ".join(filter(None, (asof, CURVE_CAPTIONS.get(freshness, CURVE_CAPTIONS["current"])
                                       if asof else "official daily observations, dated per row")))
    lines, notes = spread_lines(spreads, tenors, stale)
    if stale:
        notes.append(STALE_NOTE)
    if record.get("release_note"):
        notes.append(record["release_note"])
    move = None if stale else dict(label=record["label"], sentence=record["sentence"], note=record.get("note", ""),
                                   unavailable=record["label"] == UNAVAILABLE)
    prior_date = record.get("prior_observed_at") or prior_entry_date(
        next((legs["change"] for legs in tenors.values() if legs.get("change")), None))
    chart = curve_chart(tenors, record.get("observed_at"), prior_date, stale)
    proof_ids = [ident for row in yields for ident in row["ids"]] + [ident for line in lines for ident in line["ids"]]
    parts = rate_parts(facts, stale)
    proof_ids += [ident for part in parts for ident in part["ids"]]
    return dict(yields=yields, asof=asof, daily_change_label=daily_change_label,
                proof_ids=[ident for ident in proof_ids if ident in catalog],
                curve=dict(title=CURVE_TITLE, caption=caption, stale=stale, spreads=lines, move=move, chart=chart,
                           notes=notes, parts=parts, parts_caveat=PARTS_CAVEAT if parts else "",
                           # One takeaway on the page: the 10-year's split; the other maturities are in the proof.
                           lead_part=next((p for p in parts if p["maturity"] == "10Y"), parts[0] if parts else None)))


PANEL_TITLE = "Rates in context"
PANEL_ROWS = (("3M", "US 3M", "treasury-3m"), ("2Y", "US 2Y", "treasury-2y"), ("5Y", "US 5Y", "treasury-5y"),
              ("10Y", "US 10Y", "treasury-10y"), ("30Y", "US 30Y", "treasury-30y"))
PANEL_CHART = dict(height=132, top=12, bottom=104, label_y=124)
PANEL_CAVEAT = ("Horizons count Treasury's own daily entries, so a bond-market holiday is skipped, never filled. "
                "Inflation compensation is nominal minus real (TIPS) at the same maturity and date; it is not "
                "expected inflation.")
VOL_EXPLAINER = ("How much the 10-year yield has typically moved per day over its last 20 entries (the sample standard "
                 "deviation), placed among its own last 252 readings.")


def _signed_bp(value):
    return "—" if value is None else "0" if value == 0 else f"{MINUS if value < 0 else '+'}{abs(value)}"


def _move(found):
    return dict(display=_signed_bp(found["bp"]) if found else "—", absent=not found,
                since=short_date(found["since"]) if found else "")


def is_rates_history_row(row):
    """A row the rates panel presents (`rates.py` or the New York Fed), so it is not repeated as a loose macro fact."""
    return (row.get("formula_version") == "rates-history.v1" or str(row.get("source_id", "")).startswith("nyfed-"))


def rates_chart(points):
    """Server-side geometry for the 2Y and 10Y over the last 20 official entries: straight segments between consecutive
    entries that both report the tenor (a gap breaks the line), first and last dates, and right-end values."""
    series = {tenor: [(i, p[tenor]) for i, p in enumerate(points) if p.get(tenor) is not None]
              for tenor in ("2Y", "10Y")}
    values = [v for pts in series.values() for _, v in pts]
    if len(points) < 2 or not values:
        return None
    low, high = min(values), max(values)
    span = max((high - low) * 1.15, 0.2)
    base = (low + high) / 2 - span / 2
    top, bottom = PANEL_CHART["top"], PANEL_CHART["bottom"]
    last = len(points) - 1

    def x(i):
        return f"{2 + 72 * i / last:.1f}%"

    def y(v):
        return round(bottom - (v - base) / span * (bottom - top), 1)

    lines = []
    for tenor, pts in series.items():
        segments = [dict(x1=x(a), y1=y(va), x2=x(b), y2=y(vb)) for (a, va), (b, vb) in zip(pts, pts[1:]) if b == a + 1]
        end = pts[-1] if pts else None
        lines.append(dict(tenor=tenor, segments=segments,
                          end=dict(x=x(end[0]), y=y(end[1]), label=f"{tenor} {end[1]:.2f}%") if end else None))
    # Keep the two end labels apart when the lines finish close together.
    ends = [line["end"] for line in lines if line["end"]]
    if len(ends) == 2 and abs(ends[0]["y"] - ends[1]["y"]) < 14:
        upper, lower = sorted(ends, key=lambda e: e["y"])
        mid = (upper["y"] + lower["y"]) / 2
        upper["label_y"], lower["label_y"] = round(mid - 8, 1), round(mid + 8, 1)
    for end in ends:
        end.setdefault("label_y", end["y"])
        end["label_y"] = round(end["label_y"] + 4, 1)
    return dict(height=PANEL_CHART["height"], label_y=PANEL_CHART["label_y"], lines=lines,
                first=dict(x=x(0), text=short_date(points[0]["date"])),
                last=dict(x=x(last), text=short_date(points[-1]["date"])), count=len(points),
                summary=(f"2Y and 10Y par yields over the last {len(points)} Treasury days, "
                         f"{short_date(points[0]['date'])} to {short_date(points[-1]['date'])}."))


def funding_view(funding, run_date):
    """SOFR, EFFR and their spread, each dated; a funding rate more than five days old shows its level only."""
    if not funding or not (funding.get("sofr") or funding.get("effr")):
        return None
    rows = []
    for key, name in (("sofr", "SOFR"), ("effr", "EFFR")):
        item = funding.get(key)
        if not item:
            rows.append(dict(name=name, level="not collected", change="", date="", id=None))
            continue
        fresh = curve_freshness(date.fromisoformat(item["date"]), run_date)
        if fresh["age_days"] > 7:
            # Evidence admission rejects a daily row more than seven days old; the page shows no number either.
            rows.append(dict(name=name, level="not shown", change=f"latest is {short_date(item['date'])}, over a "
                             "week old", date="", id=None, stale=True))
            continue
        change = ("" if fresh["status"] == "stale" or item["change_bp"] is None else
                  f"{rate_display(item['change_bp'], 'bp')} from {short_date(item['prior_date'])}")
        rows.append(dict(name=name, level=f"{item['rate']:.2f}%", change=change, date=short_date(item["date"]),
                         revised=item.get("revised", False), id=item["id"], stale=fresh["status"] == "stale"))
    spread = funding.get("spread")
    spread_line = None
    if spread and (run_date - date.fromisoformat(spread["date"])).days <= 7:
        detail = ("" if spread["change_bp"] is None else
                  f" ({rate_display(spread['change_bp'], 'bp')} from {short_date(spread['prior_date'])})")
        spread_line = dict(text=f"SOFR minus EFFR {rate_display(spread['bp'], SPREAD_UNIT)}{detail}", id=spread["id"],
                           date=short_date(spread["date"]))
    target = (funding.get("effr") or {}).get("target")
    trend = funding.get("trend") or []
    trend_text = ""
    if len(trend) >= 2:
        sofr = [t["sofr"] for t in trend]
        trend_text = (f"Over the last {len(trend)} shared business days ({short_date(trend[0]['date'])} to "
                      f"{short_date(trend[-1]['date'])}), SOFR ranged {min(sofr):.2f}–{max(sofr):.2f}% and "
                      f"SOFR minus EFFR {min(t['spread_bp'] for t in trend)} to "
                      f"{max(t['spread_bp'] for t in trend)} bp.")
    return dict(rows=rows, spread=spread_line, trend=trend_text, explainer=funding["explainer"],
                target=f"FOMC target range {target[0]:.2f}–{target[1]:.2f}%" if target else "")


def rates_panel(packet, catalog):
    """Rates in context: 1-, 5- and 20-entry moves across the curve, extra spreads, which end led, nominal versus real
    10Y, a 20-entry chart, 10Y volatility with its sample counts, and overnight funding. From `packet["rates"]` and
    the rows `rates.py` derived; nothing is computed here but display."""
    record = packet.get("rates") or {}
    if not record:
        return None
    run_date = timestamp(packet["run"]["target_time"]).astimezone(ET).date()
    stale = (packet.get("curve") or {}).get("freshness") == "stale"
    ids = []
    rows, chart, spreads, leaders, parts, vol = [], None, [], [], [], None
    if record.get("nominal_ok") and not stale:
        for maturity, topic, ident in PANEL_ROWS:
            horizons = record["horizons"].get(maturity) or {}
            if horizons.get("level") is None:
                continue
            rows.append(dict(maturity=maturity, level=f"{horizons['level']:.2f}%",
                             moves=[_move(horizons.get(w)) for w in ("1d", "5s", "20s")]))
            ids += [i for i in (ident, f"{ident}-change-5s", f"{ident}-change-20s") if i in catalog]
        real = (record.get("real") or {}).get("10Y")
        if real and real.get("level") is not None:
            rows.append(dict(maturity="10Y real", level=f"{real['level']:.2f}%",
                             moves=[_move(real.get(w)) for w in ("1d", "5s", "20s")]))
            ids += [i for i in ("treasury-real-10y-change-5s", "treasury-real-10y-change-20s") if i in catalog]
        comp = (record.get("compensation") or {}).get("10Y")
        if comp and comp.get("level_bp") is not None:
            rows.append(dict(maturity="10Y inflation comp.", level=f"{comp['level_bp'] / 100:.2f}%",
                             moves=[_move(comp.get(w)) for w in ("1d", "5s", "20s")]))
            ids += [i for i in ("treasury-10y-inflation-comp-change-5s", "treasury-10y-inflation-comp-change-20s")
                    if i in catalog]
        for name, spread in (record.get("spreads") or {}).items():
            move = spread["change_bp"]
            detail = ("" if move is None else " (unchanged)" if move == 0 else
                      f" ({abs(move)} bp {'steeper' if move > 0 else 'flatter'})")
            spreads.append(f"{name} {rate_display(spread['bp'], SPREAD_UNIT)}{detail}")
            ids += [i for i in (f"treasury-{name}", f"treasury-{name}-change") if i in catalog]
        words = {"1d": "1 day", "5s": "5 days", "20s": "20 days"}
        for item in record.get("leadership") or []:
            leaders.append(dict(horizon=words[item["horizon"]], text=item["text"],
                                detail=f"2Y {_signed_bp(item['front_bp'])}, 30Y {_signed_bp(item['long_bp'])} bp",
                                lead=item["lead"]))
        if real and comp:
            for window, word in (("1d", "1 day"), ("5s", "5 days"), ("20s", "20 days")):
                n = (record["horizons"].get("10Y") or {}).get(window)
                r, c = real.get(window), comp.get(window)
                if n and r and c and n["since"] == r["since"] == c["since"]:
                    parts.append(dict(horizon=word, text=f"nominal {_signed_bp(n['bp'])} = real {_signed_bp(r['bp'])}"
                                                         f" + inflation compensation {_signed_bp(c['bp'])} bp"))
        chart = rates_chart(record.get("chart") or [])
        v = record.get("volatility") or {}
        if v.get("sd") is not None:
            text = f"{v['sd']:.2f} bp a day over the 20 days {short_date(v['first'])} to {short_date(v['date'])}"
            if v.get("percentile") is not None:
                text += (f"; {v['percentile']:.1f}th percentile of its last {v['baseline']} readings "
                         f"(median {v['median']:.2f}, range {v['low']:.2f}–{v['high']:.2f})")
                ids += [i for i in ("treasury-10y-vol20", "treasury-10y-vol20-pct") if i in catalog]
            else:
                text += (f"; {v['values']} readings so far, fewer than the {v['baseline']} a percentile needs")
                ids += [i for i in ("treasury-10y-vol20",) if i in catalog]
            vol = dict(text=text, explainer=VOL_EXPLAINER)
        elif v:
            vol = dict(text=f"Not enough history: {v['changes']} daily changes, {v['window']} needed.",
                       explainer=VOL_EXPLAINER)
    funding = funding_view(record.get("funding"), run_date)
    if funding:
        ids += [i for i in ("nyfed-sofr", "nyfed-sofr-change", "nyfed-effr", "nyfed-effr-change", "nyfed-sofr-effr")
                if i in catalog]
    if not (rows or funding):
        return None
    observed = record.get("observed_at")
    caption = f"Treasury entries to {short_date(observed)}" if observed and rows else "official daily data"
    return dict(title=PANEL_TITLE, caption=caption, rows=rows, spreads=spreads, leaders=leaders, parts=parts,
                chart=chart, volatility=vol, funding=funding, notes=list(record.get("notes") or []),
                caveat=PANEL_CAVEAT if rows else "", proof_ids=ids)


def release_cards(rows, events, sources=(), unpublished=()):
    """One card per admitted release, from this run's rows (the observed clock): its official time, family, reference
    month and values, and the IDs of the calendar events it has now happened for, which leave What matters next.
    `unpublished` holds the rows of measures the release itself states are unavailable; each is named on the card of its
    own release as `unavailable` (never a number), and none starts a card of its own."""
    groups = {}
    for row in rows:
        groups.setdefault((row["observed_at"], row["topic"], row["reference_period"]), []).append(row)
    cards = []
    for (released, title, period), members in sorted(groups.items()):
        by_metric = {row["metric"]: row for row in members}
        absent = {row["metric"] for row in unpublished
                  if (row["observed_at"], row["topic"], row.get("reference_period")) == (released, title, period)}
        lines = []
        for label, metrics in RELEASE_LINES:
            parts = []
            for metric in metrics:
                word = PAIR_WORDS[metric.rsplit(", ", 1)[1]] if len(metrics) > 1 else ""
                if metric in by_metric:
                    parts.append(f"{formatted(by_metric[metric])} {word}" if word else formatted(by_metric[metric]))
                elif metric in absent:
                    parts.append(f"{word} unavailable" if word else "unavailable")
            if parts:
                lines.append(dict(label=label, values=[" · ".join(parts)]))
        for label, metric in (("Canadian employment change", "Canadian employment change"),
                              ("Canadian unemployment", "Canadian unemployment rate")):
            if metric in by_metric:
                lines.append(dict(label=label, values=[formatted(by_metric[metric])]))
        revisions = sorted((row for row in members if row["metric"] in
                            (REVISION, JOLTS_REVISION, PRODUCTIVITY_REVISION)),
                           key=lambda row: row.get("revised_period", row.get("revised_month", "")))
        combined = [row for row in members if row["metric"] == COMBINED]
        unread = any(source.get("status") == "DEGRADED" for source in sources
                     if source["id"] in {row["source_id"] for row in members})
        if unread and not (revisions or combined):
            # The release states revisions that did not read cleanly; the card says so instead of looking complete.
            lines.append(dict(label="Revisions", values=["not read from the release"]))
        if revisions or combined:
            lines.append(dict(label="Revisions", values=[
                *(f"{SHORT_MONTH_NAMES[int(row['revised_month'][5:]) - 1]} "
                  f"{formatted(dict(row, value=row['revised_from']))} \u2192 "
                  f"{formatted(dict(row, value=row['revised_to']))}" for row in revisions
                  if row["metric"] == REVISION),
                *(f"{SHORT_MONTH_NAMES[int(row['revised_month'][5:]) - 1]} openings "
                  f"{formatted(row)}" for row in revisions if row["metric"] == JOLTS_REVISION),
                *(f"{period_name(row['revised_period'])} "
                  f"{formatted(dict(row, value=row['revised_from']))} \u2192 "
                  f"{formatted(dict(row, value=row['revised_to']))}" for row in revisions
                  if row["metric"] == PRODUCTIVITY_REVISION),
                *(f"Combined {formatted(row)}" for row in combined)]))
        title_family = next((family for family, spec in FAMILIES.items() if spec["title"] == title), None)
        happened = [event["id"] for event in events if event.get("scheduled_at") == released
                    and (title_family is not None and event_family(
                        (EVENT_TITLE.fullmatch(event.get("title") or "") or [None, None])[1]) == title_family
                         or title == "Statistics Canada Labour Force Survey"
                         and event.get("title", "").startswith(title + " for "))]
        cards.append(dict(time=pacific_time(released), title=title, period=period_name(period), lines=lines,
                          ids=[row["id"] for row in members], events=happened))
    return cards


def next_update(info, session_date):
    """The scheduler's next checkpoint in reader words, its Vancouver display time, and its absolute scheduled time."""
    clock = pacific_time(info["scheduled_at"])
    if info["session_date"] != session_date:
        when, kind = f"{short_date(info['session_date'])} · {clock}", "premarket analysis"
    else:
        when, kind = clock, NEXT_WORDS.get(info["kind"], "price refresh")
    return dict(text=f"{when} · {kind}", when=when, at=info["scheduled_at"])


def next_update_label(info, session_date):
    """`8:00 AM Vancouver time · price refresh`, `7:00 AM Vancouver time · analysis update`,
    `1:01 PM Vancouver time · close snapshot`, or `Mon, Sep 28 · 6:00 AM Vancouver time · premarket analysis`."""
    return next_update(info, session_date)["text"]


def since_caption(anchors, unpublished=()):
    """`vs the previous close · Fri, Sep 11` or `vs premarket and the 7:00 AM update`. An anchor that was collected
    but never published reads `the unpublished 6:32 AM Vancouver time refresh`: readers never saw it, though it is
    still what the comparison was made against."""
    if "previous_close" in anchors:
        return f"vs the previous close · {short_date(anchors['previous_close']['session_date'])}"
    parts = []
    for name, anchor in anchors.items():
        hidden = "unpublished " if name in unpublished else ""
        if name == "premarket":
            parts.append(f"the {hidden}premarket" if hidden else "premarket")
        else:
            noun = "refresh" if CHECKPOINT_KINDS.get(anchor.get("checkpoint")) == "refresh" else "update"
            parts.append(f"the {hidden}{pacific_time(anchor['evidence_cutoff'])} {noun}")
    return "vs " + " and ".join(parts) if parts else None


def state_notice(interpretation, packet, carried, refs, catalog):
    """The compact "since the analysis" notice (`state.divergence`) for a page that carries an earlier analysis, or
    None. Deterministic fact on the inset surface: both clocks, the measures at each, and the rows behind them; the
    analysis above it is never edited."""
    if not carried:
        return None
    result = divergence(interpretation.get("market_state"), market_snapshot(packet))
    stopped = ""
    if result["status"] == "insufficient":
        # This page's own prints cannot be compared (at the close every print is the last regular-session trade,
        # DELAYED by collection). The session's last live snapshot, kept by an earlier refresh, still can: shown at
        # its own clock, with its own ledger rows (WP-MB-TRUTH-01). Live prints now always take precedence.
        kept = (packet.get("continuity") or {}).get("market_state")
        earlier = divergence(interpretation.get("market_state"), kept) if kept else result
        if earlier["status"] == "shown":
            result, stopped = earlier, result["reason"]
    if result["status"] != "shown":
        return None
    before, after, side = result["before"], result["after"], result["side"]
    word = result["direction"]

    def pct(value):
        return formatted(dict(value=value, unit="%"))

    measures = [dict(label=f"Sector ETFs {word}", earlier=f"{before[side]} of {before['n']}",
                     later=f"{after[side]} of {after['n']}"),
                dict(label="Median sector ETF", earlier=pct(before["median"]), later=pct(after["median"])),
                dict(label="SPY", earlier=pct(before["spy"]), later=pct(after["spy"]))]
    opening_rows, current_rows = result["opening_rows"], result["current_rows"]
    for symbol in ("QQQ",):
        if symbol in opening_rows and symbol in current_rows:
            measures.append(dict(label=symbol, earlier=pct(opening_rows[symbol]["value"]),
                                 later=pct(current_rows[symbol]["value"])))
    sentence = (f"Sector ETFs have reversed {word} since the analysis above was written." if
                result["kind"] == "reversal" else
                f"Sector ETFs have moved broadly {word} since the analysis above was written.")
    if stopped:
        moved = f"reversed {word}" if result["kind"] == "reversal" else f"moved broadly {word}"
        why = ("This page's own prints, the last trades before the close, are not compared."
               if packet["run"]["checkpoint"] == "CLOSE_1M"
               else "This page's own prints cannot be compared with the analysis.")
        sentence = (f"By {pacific_time(after['last'].isoformat())}, the last live prints, sector ETFs had {moved} "
                    f"since the analysis above was written. {why}")
    order = sorted(set(opening_rows) | set(current_rows), key=lambda t: (t != "SPY", t != "QQQ", t))
    # The analysis's prints have their own ledger rows (`ledger` below) under `analysis-state-<id>`, a namespace no
    # current (`evidence-<id>`) or frozen-citation (`interpretation-evidence-<id>`) anchor uses, so a link to SPY at
    # the analysis's clock never lands on this run's SPY row.
    opening = [opening_rows[t] for t in order if t in opening_rows]
    earlier = [dict(r, anchor=True, anchor_id=f"analysis-state-{r['id']}") for r in
               refs([row["id"] for row in opening], {row["id"]: row for row in opening}, False)]
    session = packet["run"]["session"]
    names = {source["id"]: source["name"] for source in packet["sources"]}

    def ledger_rows(rows, namespace):
        return [dict(id=row["id"], anchor_id=f"{namespace}-{row['id']}",
                     label=f"{instrument_label(row['topic'])} · {reader_metric_label(row, session)}",
                     display=formatted(row), when=compact_clock(row["observed_at"]), observed_at=row["observed_at"],
                     baseline=row.get("baseline") or "", source_id=row.get("source_id") or "source not preserved",
                     source_name=names.get(row.get("source_id"), row.get("source_id") or "source not preserved"))
                for row in rows]

    later_ledger = []
    if stopped:
        # The earlier refresh's prints are not this run's rows: they get their own anchors (`live-state-<id>`), so a
        # link to SPY at 1:00 PM never lands on this page's close print of SPY.
        current = [current_rows[t] for t in order if t in current_rows]
        later = [dict(r, anchor=True, anchor_id=f"live-state-{r['id']}") for r in
                 refs([row["id"] for row in current], {row["id"]: row for row in current}, False)]
        later_ledger = ledger_rows(current, "live-state")
    else:
        later = refs([current_rows[t]["id"] for t in order if t in current_rows], catalog, False)
    ledger = ledger_rows(opening, "analysis-state")
    return dict(caption="Since the analysis",
                window=(f"{pacific_time(before['last'].isoformat())} → {pacific_time(after['last'].isoformat())}"
                        f" · vs prior close {short_date(before['prior_close'])}"),
                sentence=sentence + " The analysis is unchanged; these later prints are not part of it.",
                measures=measures,
                note=(f"Counts are of the {before['n']} sector ETFs held at both clocks"
                      + (f" (of {len(SECTOR_ETFS)})" if before["n"] < len(SECTOR_ETFS) else "")
                      + ", not S&P 500 breadth. Each row keeps its own clock."),
                refs=[*earlier, *later], current_ids=[row["id"] for row in later],
                opening_ids=[row["id"] for row in opening], ledger=ledger,
                opening_clock=pacific_time(before["last"].isoformat()), kind=result["kind"], direction=word,
                later_ledger=later_ledger, later_clock=pacific_time(after["last"].isoformat()),
                later_namespace="live-state" if stopped else "evidence")


def presentation(packet, narrative=None, context=None, interpretation=None, glance=None, repairs=None,
                 degraded=None, data_only=None):
    if data_only is not None:
        # No analysis of this session was accepted: nothing interpretive is carried, read or shown. The blank record
        # is this run's own (one clock), with no context, so no prior watch, verdict or attention can enter it.
        narrative, context, interpretation = None, None, interpretation_record(packet, unavailable_narrative())
    if interpretation is None:
        interpretation = interpretation_record(packet, narrative, context)
    narrative = interpretation["narrative"]
    catalog = evidence_catalog(packet)
    frozen = interpretation["evidence"]
    actual_started_at = packet["run"].get("actual_started_at", packet["run"]["target_time"])
    status = status_for(packet)
    commissioning = (status == "SAMPLE" and packet["run"]["mode"] == "LIVE")
    live_commissioning = status == "LIVE COMMISSIONING"
    checkpoint = packet["run"]["checkpoint"]
    kind = checkpoint_kind(checkpoint)
    target = timestamp(packet["run"]["target_time"])
    interpreted = interpretation["origin"]
    # Carried: the interpretation was made by an earlier run, so the page has two clocks.
    carried = (interpreted.get("run_id") != packet["run"].get("run_id")
               or interpreted["target_time"] != packet["run"]["target_time"])
    session = packet["run"]["session"]
    session_date = session["date"]

    def expand(text, values=None):
        """Numeric placeholders resolve against the rows frozen with the text: a carried watch's own
        creation-time values first, then the interpretation record, then this run's row for a legacy
        reference the record does not hold, then a dash. Rendering can never abort an accepted run."""
        def value(match):
            row = (values or {}).get(match[1]) or frozen.get(match[1]) or catalog.get(match[1])
            return formatted(row) if row else NOT_APPLICABLE
        return PLACEHOLDER.sub(value, text)

    def refs(ids, rows=None, snapshot=None):
        """The rows behind one block, formatted for a quiet expandable marker; IDs are unchanged.

        Prose cites the frozen rows the analyst saw; table proofs cite this run's rows.
        """
        snapshot = rows is None if snapshot is None else snapshot
        rows = frozen if rows is None else rows
        result = []
        for ref in ids:
            row = rows.get(ref)
            if row is None:
                continue
            if row.get("metric") and row.get("topic"):
                label = f"{instrument_label(row['topic'])} · {reader_metric_label(row, session)}"
            else:
                label = row.get("title") or row.get("topic") or ref
            result.append(dict(id=ref, label=label, display=formatted(row) if "value" in row else "",
                               when=(release_clock(row, session) if row.get("frequency") == "release" else
                                     compact_clock(row.get("observed_at") or row.get("published_at")
                                                   or row.get("published_on") or "")),
                               anchor=(True if snapshot else ref in catalog),
                               anchor_id=(f"evidence-{ref}" if not snapshot else
                                          f"interpretation-evidence-{ref}"),
                               topic=row.get("topic") or ""))
        return result

    def cited_rows(ids, rows=None):
        """The rows behind one block, as rows: the frozen ones the prose cites, else this run's."""
        rows = frozen if rows is None else rows
        return [rows[ref] for ref in ids if rows.get(ref) is not None]

    # The chart's previous close is the history's last close, so the history must end at the session the prints
    # are measured against: the previous session, or the one before it when today's bar is not yet published.
    through = {session.get("previous_session"), (packet.get("history_lag") or {}).get("history_through")}
    histories = {h["symbol"]: h for h in packet["history"] if h.get("dates") and h["dates"][-1] in through}

    def charts_for(marker_refs):
        """Evidence charts for one opened `§` drawer: one per cited instrument whose rows make a chart useful (see
        HISTORY_METRICS), in the order the rows cite them, drawn from the rows this drawer cites (frozen values, so
        a carried page's chart shows the prints its analyst saw) and this run's history, the same prior sessions
        all day."""
        by_topic = {}
        for ref in marker_refs:
            row = frozen.get(ref["id"]) or catalog.get(ref["id"])
            if row is None or ref["topic"] not in histories:
                continue
            by_topic.setdefault(ref["topic"], []).append(row)
        charts = []
        for topic, rows in by_topic.items():
            prints = [row for row in rows if current_print(row)]
            metrics = {row.get("metric", "") for row in rows}
            history_cited = any(m in HISTORY_METRICS or m.startswith("relative to ") for m in metrics)
            if not history_cited and len(prints) < EVIDENCE_SEQUENCE:
                continue
            average = (catalog.get(f"{topic}-sma50") or {}).get("value")
            chart = evidence_chart(histories[topic], prints, average, session_date,
                                   average_cited=bool(metrics & AVERAGE_METRICS))
            if chart:
                charts.append(dict(topic=topic, label=instrument_label(topic), chart=chart))
            if len(charts) == EVIDENCE_CHARTS:
                break
        return charts

    def proof(rows, keys=("today", "relative", "r20", "dma")):
        """Every cell of one instrument table, as the same compact proof lines the markers use."""
        ids = [row[key]["id"] for row in rows for key in keys if row.get(key) and row[key].get("id")]
        return refs(list(dict.fromkeys(ids)), catalog)

    def paragraph(p):
        """The caveat and the alternative explanation stay separate, each rendered under its own label."""
        return {**p, "text": expand(p["text"]), "uncertainty": expand(p["uncertainty"]).strip(),
                "alternative": expand(p["alternative"]).strip(), "refs": refs(p["evidence_ids"])}

    facts = []
    for row in [*packet["observations"], *packet["derived"]]:
        if row["status"] not in USABLE or row["metric"] in {"regular close"}:
            continue
        facts.append({**row, "display": formatted(row), "direction": direction(row),
                      "measure": measure_label(row, session), "observed_label": observed_label(row["observed_at"])})
    history_symbols = {h["symbol"] for h in packet["history"]}
    equity = [r for r in facts if r["topic"] in history_symbols or r["frequency"] == "intraday"]
    treasuries = [r for r in facts if r["topic"].startswith("US ") and r["metric"] in RATE_METRICS]
    # An issuer's reported figures (fiscal quarter, accounting basis) are not an economic release card or a macro
    # fact: they stand in the evidence ledger and under any § that cites them.
    issuer = [r for r in facts if r.get("fiscal_period")]
    releases = [r for r in facts if r["frequency"] == "release" and r not in issuer]
    # Release measures the official page states are unavailable: named on their release's card, never a number. Their
    # reason (BLS's own words) is in Coverage limitations.
    unpublished = [r for r in packet["observations"]
                   if r["frequency"] == "release" and r["status"] == "UNAVAILABLE" and r.get("reference_period")]
    other_macro = [r for r in facts if r not in equity and r not in treasuries and r not in releases
                   and r not in issuer and not is_rates_history_row(r)]
    # After a close whose daily bar is not yet published, the retained daily return is two
    # sessions old relative to the completed session and must not pose as today.
    daily_today = not packet.get("history_lag")

    # Tables: mega-caps, sectors ranked by the labeled twenty-session spread, metals structure.
    mega_rows = compact_equity_rows(facts, MEGACAPS, daily_today)
    mega_change, mega_asof = change_column(mega_rows, daily_today, session)
    sector_rows = rank_by_spread(compact_equity_rows(facts, list(SECTOR_LABELS), daily_today), list(SECTOR_LABELS))
    sector_change, sector_asof = change_column(sector_rows, daily_today, session)
    metal_rows = compact_equity_rows(facts, METALS, daily_today)
    metal_change, metal_asof = change_column(metal_rows, daily_today, session)
    mega_notes = table_notes(mega_rows, mega_change, packet, session)
    sector_notes = table_notes(sector_rows, sector_change, packet, session)
    metal_notes = table_notes(metal_rows, metal_change, packet, session)
    module = rates_module(packet, treasuries, catalog)
    panel = rates_panel(packet, catalog)
    metal_pairs = [f"{row['symbol']} vs {row['relative_label']}" for row in metal_rows if row["relative"]["id"]]
    yields = module["yields"]
    # The rates takeaway (readability pass): one small line after the take, only when the lead itself cites the
    # curve (a Treasury tenor or spread row in the headline, character, summary or take), so rates appear near the
    # opening exactly when they bear on the thesis and nowhere else repeats the module. It is the module's own
    # rows and label under the curve's own date, with the note that places the curve before a release; the
    # sentence, chart and spread detail stay in Macro & rates.
    rates_lead = None
    if yields and module["curve"]:
        curve = module["curve"]
        rates_lead = dict(caption=curve["caption"], stale=curve["stale"],
                          tenors=[dict(maturity=row["maturity"], level=row["level"], change=row["change"])
                                  for row in yields if not row["level"]["absent"]],
                          spreads=[dict(name=line["name"], level=line["level"]) for line in curve["spreads"]],
                          move=curve["move"]["label"] if curve["move"] and not curve["move"]["unavailable"] else "",
                          note=next((note for note in curve["notes"] if note.startswith("Curve predates")), ""),
                          anchor="treasury-curve")

    # What matters next: new watches with their frozen horizons, then the carried watches continuity will keep.
    watches = []
    for w, horizon in zip(narrative["watches"], interpretation["horizons"]):
        expires = horizon.get("expires_at")
        expired = bool(expires) and timestamp(expires) <= target
        watches.append({**w, **{k: expand(w[k]) for k in ("condition", "confirmation", "contradiction")},
                        "phrase": horizon["phrase"], "expires_session": horizon.get("expires_session"),
                        "expired": expired, "state": "expired" if expired else "active",
                        "refs": refs(w["evidence_ids"])})
    prior = interpretation["prior_state"]
    available = prior.get("status") == "available"
    updates = {u["carried_id"]: u for u in narrative.get("watch_updates", [])}
    carried_watches = []
    for watch in prior.get("watches", []) if available else []:
        update = updates.get(watch["id"])
        expires = watch["horizon"].get("expires_at")
        # A carried criterion renders at the values its author saw; only a reassessment adds new text.
        # Each creation-time value keeps the row's identity so its marker is labeled like any other, but
        # never another observation's status: its own clock decides the phase its label names.
        creation = {ident: {**{key: value for key, value in (frozen.get(ident) or catalog.get(ident) or {}).items()
                               if key != "status"}, **row}
                    for ident, row in (watch.get("values") or {}).items()}
        carried_watches.append(dict(
            id=watch["id"], hypothesis=expand(watch["hypothesis"], creation),
            # Its criteria, like its question, at the values its author saw; an interpretation frozen before
            # the record kept them has none, and the watch renders its question alone.
            confirmation=expand(watch.get("confirmation") or "", creation),
            contradiction=expand(watch.get("contradiction") or "", creation),
            phrase=watch["horizon"].get("phrase", ""), lifecycle=watch["lifecycle"],
            expired=watch["lifecycle"] == "expired" or (bool(expires) and timestamp(expires) <= target),
            evaluability=watch["evaluability"],
            assessment=update["assessment"] if update else "",
            reason=expand(update["reason"]) if update else "",
            evidence_ids=update["evidence_ids"] if update else watch["evidence_refs"],
            refs=(refs(update["evidence_ids"]) if update
                  else [dict(ref, anchor=ref["id"] in (watch.get("values") or {}),
                            anchor_id=f"watch-evidence-{watch['id']}-{ref['id']}")
                        for ref in refs(watch["evidence_refs"], {**frozen, **creation}, snapshot=True)]),
            creation_evidence=[dict(
                id=ident, anchor_id=f"watch-evidence-{watch['id']}-{ident}",
                metric_label=(reader_metric_label(row, session) if row.get("metric") else "Published / scheduled item"),
                display=formatted(row) if "value" in row else row.get("title", NOT_APPLICABLE),
                when=compact_clock(row.get("observed_at") or row.get("published_at") or "") or "date not preserved",
                baseline=row.get("baseline") or "basis not preserved",
                source_name=row.get("source_id") or "source not preserved",
                source_id=row.get("source_id") or "source ID not preserved")
                for ident, row in (watch.get("values") or {}).items() if ident in watch.get("evidence_refs", [])]))
    # The live set is exactly what `continuity.edition_state` carries forward: new watches first, then the
    # carried ones still active and not reversed, at most CARRY_LIMIT. The frozen prior list is already
    # newest-first, so no ranking of our own. A verdict (strengthened, weakened, reversed) is adjudication and
    # renders once, in What changed; a live carried watch shows its question, horizon and criteria here. Everything
    # else carried, expired or set aside without a verdict, is one collapsed count: audit, not attention.
    live_carried = [c for c in carried_watches if c["lifecycle"] == "active" and c["assessment"] != "reversed"]
    live_carried = live_carried[:max(0, CARRY_LIMIT - len(watches))]
    verdicts = [c for c in carried_watches if c["assessment"] in VERDICTS]
    retired_watches = [dict(c, state="expired" if c["expired"] else "retired")
                       for c in carried_watches if c not in live_carried and c not in verdicts]
    # A live watch that was also adjudicated keeps its question here and its verdict in What changed; its state
    # points there without repeating the verdict. The gutter turns grey once the horizon has passed.
    live_carried = [dict(c, assessment="", reason="", state="completed" if c["expired"] else "judged")
                    if c["assessment"] in VERDICTS else
                    dict(c, state="expired" if c["expired"] else c["assessment"] or "active")
                    for c in live_carried]
    # A release whose official values are admitted has happened: its card in Macro & rates is its one home, so it is no
    # longer listed among the events that matter next.
    cards = [dict(card, proof=refs(card["ids"], catalog))
             for card in release_cards(releases, packet["events"], packet["sources"], unpublished)]
    happened = {ident for card in cards for ident in card["events"]}
    # An event whose scheduled time has passed without admitted values (Real Earnings; a release the collector
    # does not read) is no longer ahead: report only that the scheduled time passed and values were not collected.
    events = [{**event, **event_clock(event),
               "relation_label": event.get("session_relation", "").lower(), "refs": refs([event["id"]], catalog),
               # A time that has passed proves nothing about publication: only an admitted page (a card above) says
               # a release happened, so this says what is known. An announced call collects no values of its own.
               "passed": (("scheduled time passed" if event.get("stated_time")
                           else f"scheduled time passed · values {NOT_COLLECTED}")
                          if event.get("scheduled_at") and timestamp(event["scheduled_at"]) <= target else "")}
              for event in packet["events"] if event["id"] not in happened][:4]

    # What changed: adjudication of the prior accepted state, frozen with the interpretation. Watch verdicts
    # first, then carried relationships with a verdict, then deterministic changes the analyst interpreted. A
    # lower-priority record that cites no current evidence beyond what a higher-priority bullet already
    # cited is the same conclusion from the same measurements and is not shown again; a record that cites
    # anything of its own stays. "Unresolved" is not a change and never renders here.
    anchors = prior.get("anchors", {}) if available else {}
    continuity = interpretation["continuity"]

    def current(ids):
        return {i for i in ids if ":" not in i}

    ranked = [[dict(kind="watch", text=c["hypothesis"], assessment=c["assessment"], reason=c["reason"],
                    evidence_ids=c["evidence_ids"], refs=c["refs"]) for c in verdicts],
              [dict(kind="relationship", text=expand(r["statement"]), assessment=r["assessment"],
                    reason=expand(r["reason"]), evidence_ids=r["evidence_ids"], refs=refs(r["evidence_ids"]))
               for r in narrative.get("relationships", [])
               if r["carried_id"] is not None and r["assessment"] in VERDICTS],
              [dict(kind="change", text=expand(c["text"]), evidence_ids=c["evidence_ids"],
                    refs=refs(c["evidence_ids"])) for c in narrative.get("changes", [])]]
    since_entries, cited = [], set()
    for tier in ranked:
        kept = [entry for entry in tier if not (current(entry["evidence_ids"]) and
                                                current(entry["evidence_ids"]) <= cited)]
        since_entries += kept
        cited |= {i for entry in kept for i in current(entry["evidence_ids"])}
    for entry in since_entries:
        # Before → after lines from the entry's own citations, under the observation. One window is stated once
        # for the lines; windows that differ stay on their own lines, so a premarket print never shares a caption
        # with an intraday one. A pair the sentence already spells out is not repeated.
        stated = " ".join(str(entry.get(key) or "") for key in ("text", "reason"))
        entry["moves"] = movement_rows(cited_rows(entry["evidence_ids"]), session, catalog, stated)
        windows = {(move["window"], move["measure"]) for move in entry["moves"]}
        entry["window"] = " · ".join(next(iter(windows))) if len(windows) == 1 else ""
        # What changed leads with what was observed (the reason) and names the question or relationship it
        # judged as a labelled second line; a verdict with no stated reason keeps the question as its line.
        if entry["kind"] in ("watch", "relationship"):
            entry["observed"] = entry["reason"] or entry["text"]
            entry["judged"] = entry["text"] if entry["reason"] else ""
            entry["role"] = "Watch" if entry["kind"] == "watch" else "Relationship"

    # Flagged: a selected trigger earns its place only when its instrument is not already in the lead, in
    # What changed, or in a live watch, by cited row or by name. The frozen selection is unchanged; a
    # refresh reads the same frozen records, so the same flags show all day.
    lead_records = [narrative["banner"], narrative["character"], *narrative["summary"], *since_entries,
                    *watches, *live_carried]
    if narrative.get("take"):
        lead_records.append(narrative["take"])
    opening_records = [narrative["banner"], narrative["character"], *narrative["summary"],
                       *([narrative["take"]] if narrative.get("take") else [])]
    opening_ids = {i for record in opening_records for i in record.get("evidence_ids", [])}
    if rates_lead and not any((frozen.get(i) or catalog.get(i) or {}).get("metric") in RATE_METRICS
                              for i in opening_ids):
        rates_lead = None
    covered_ids = {i for record in lead_records for i in record.get("evidence_ids", [])}
    covered_topics = {row["topic"] for ident in covered_ids
                      for row in [frozen.get(ident) or catalog.get(ident)] if row and row.get("topic")}
    story = " ".join(str(record.get(key, "")) for record in lead_records
                     for key in ("title", "text", "hypothesis", "condition", "confirmation", "contradiction"))

    def covered(symbol):
        names = [symbol, SECTOR_LABELS.get(symbol, ""), INSTRUMENT_LABELS.get(symbol, "")]
        return symbol in covered_topics or any(
            re.search(rf"\b{re.escape(name)}\b", story, re.IGNORECASE) for name in names if name)

    attention_why = {item["id"]: item["why"] for item in narrative.get("attention", [])}
    attention = [{**a, "why": attention_why.get(a["id"], ""), "trigger": trigger_tag(a["reason"]),
                  # The table's naming: the name, then the ticker; Markdown keeps `Name · TICKER` in one string.
                  "label": SECTOR_LABELS.get(a["symbol"]) or INSTRUMENT_LABELS.get(a["symbol"], a["symbol"]),
                  "display_symbol": instrument_label(a["symbol"]),
                  "date_label": short_date(a.get("date")), "refs": refs(a["evidence_ids"])}
                 for a in interpretation["attention"] if not covered(a["symbol"])]
    since_note = ""
    if available and not continuity["changed"]:
        # The note describes the interpretation's comparison, so on a refresh it is dated to that clock.
        when = f" by the {pacific_time(interpreted['target_time'])} read" if carried else ""
        moved, arrived = ("had moved", "had arrived") if carried else ("has moved", "have arrived")
        since_note = (f"Nothing comparable {moved}{when}: {continuity['repeated']} prior-close observations "
                      f"repeated and no new session prints {arrived}." if continuity["repeated"] else
                      f"Nothing comparable {'was' if carried else 'is'} available to compare{when}.")
    elif not available:
        # Reader copy; the admission reason stays in Technical details.
        since_note = ("No accepted close to carry forward, so this is a baseline read."
                      if interpreted["checkpoint"] == "PREMARKET"
                      else "First edition of this session; nothing is carried forward yet.")

    cuttingboard = packet["cuttingboard"]
    cuttingboard_visible = (cuttingboard.get("status") == "AVAILABLE"
                            or bool(narrative["sections"]["cuttingboard"]))
    # Market sections this edition could not fill. Cuttingboard is optional context, not market coverage,
    # so its absence is stated in Technical details rather than listed here.
    omitted = []
    rates_present = bool(yields or module["curve"] or other_macro or narrative["sections"]["macro"] or cards)
    if not rates_present:
        # Metals share the section; when they still render, only the rates half is missing.
        omitted.append("Treasury rates" if metal_rows else "Macro & rates")
    if not metal_rows:
        omitted.append("Metals")
    if not sector_rows:
        omitted.append("Sector view")
    if not mega_rows and not narrative["sections"]["equities"] and not equity:
        omitted.append("Equity structure")
    limitations = [reader_limitation(item) for item in packet["coverage"]["limitations"]]
    if omitted:
        limitations.append("Not in this edition: " + ", ".join(omitted))
    if cuttingboard.get("status") == "AVAILABLE":
        cuttingboard_line = "Cuttingboard: " + (" · ".join(
            f"{label} {cuttingboard[key]}" for key, label in
            (("generated_at", "generated"), ("captured_at", "captured"), ("schema_version", "schema"))
            if cuttingboard.get(key)) or "AVAILABLE")
    else:
        reason = str(cuttingboard.get("reason") or "").strip()
        cuttingboard_line = (f"Cuttingboard: {cuttingboard.get('status') or 'UNAVAILABLE'}"
                             + (f" — {reason}" if reason else ""))
    run_continuity = packet.get("continuity") or dict(status=prior.get("status", "cold_start"),
                                                      reason=prior.get("reason", ""), anchors=anchors,
                                                      comparisons=[])
    run_comparisons = run_continuity.get("comparisons", [])
    technical = dict(
        generated_utc=actual_started_at,
        evidence_cutoff_utc=packet["run"]["target_time"],
        checkpoint=checkpoint, kind=kind,
        synthesis=(f"analyst call not accepted; deterministic refresh under interpretation run "
                   f"{interpreted.get('run_id') or 'sample'}" if degraded else
                   "this edition's one analyst call" if kind == "synthesis" else
                   f"none; deterministic {kind} under interpretation run {interpreted.get('run_id') or 'sample'}"),
        interpretation=dict(run_id=interpreted.get("run_id"), checkpoint=interpreted.get("checkpoint"),
                            evidence_cutoff=interpreted.get("target_time")),
        bootstrap=packet["coverage"]["bootstrap"],
        calendar=packet["coverage"].get("calendar", "unavailable"),
        basis=packet["coverage"].get("basis", ""), horizon=packet["coverage"].get("horizon", ""),
        continuity=dict(status=run_continuity.get("status", "cold_start"), reason=run_continuity.get("reason", ""),
                        anchors={k: v.get("run_id") for k, v in run_continuity.get("anchors", {}).items()},
                        comparisons=len(run_comparisons),
                        changed=sum(1 for c in run_comparisons if c.get("status") == "changed")),
        cuttingboard=cuttingboard_line,
        # Deterministic citation repairs this edition's analyst response received before validation (OD-1).
        repairs=[f"{r['path'].removesuffix('.evidence_ids')}: {alias} → {', '.join(pair)}"
                 for r in repairs or [] for alias, pair in r["aliases"].items()],
        providers=[dict(name=s["name"], provider=s.get("provider"), feed=s.get("feed"),
                        data_delay=s.get("data_delay")) for s in packet["sources"]
                   if s.get("provider") or s.get("feed") or s.get("data_delay")],
    )
    truth = None
    if status == "SAMPLE":
        truth = ("SAMPLE · COMMISSIONING RUN — a commissioning test, not the scheduled brief." if commissioning
                 else "FICTIONAL SAMPLE / REPLAY — not current market facts.")
    elif live_commissioning:
        phase = f", {PHASE_PHRASES[checkpoint]}" if checkpoint in PHASE_PHRASES else ""
        truth = (f"LIVE COMMISSIONING RUN — collected {pacific_time(actual_started_at)}{phase}, "
                 "not a scheduled checkpoint.")
    if degraded:
        # A synthesis checkpoint whose analysis was rejected publishes as a refresh under the earlier analysis
        # (owner ruling OD-1); the page says so where the clocks are, so it never reads as a new analysis.
        failed = EDITION_WORDS.get(degraded["checkpoint"], "analysis")
        kept = EDITION_WORDS.get(interpreted.get("checkpoint"), "earlier")
        due = timestamp(degraded["scheduled_at"])
        clock = (f"{due.astimezone(ET):%-I:%M %p} ET ({due.astimezone(PACIFIC):%-I:%M %p} {VANCOUVER_LABEL})")
        truth = (f"The {failed} analysis due {clock} was not accepted, so no new analysis was published; the "
                 f"{kept} analysis stands and the prices are this update's.")
    source_names = {s["id"]: s["name"] for s in packet["sources"]}
    # A row with no measurement shows its headline as the value: `item` lets the page stack and wrap it.
    evidence_rows = [dict(r, display=formatted(r) if "value" in r else r["title"], item="value" not in r,
                          metric_label=(reader_metric_label(r, session) if r.get("metric")
                                        else "Published / scheduled item"),
                          when=(release_clock(r, session) if r.get("frequency") == "release" else
                                compact_clock(r.get("observed_at") or r.get("published_at")
                                              or r.get("published_on") or "")),
                          **item_detail(r),
                          source_name=source_names.get(r.get("source_id"), r.get("source_id", "")),
                          status_label=SOURCE_STATUS_LABELS.get(r.get("status", ""), (r.get("status") or "").title()))
                     for r in catalog.values()]
    groups = {}
    for row in evidence_rows:
        groups.setdefault(row.get("topic") or row.get("title") or row["id"], []).append(row)
    evidence_groups = [dict(topic=topic, label=instrument_label(topic), rows=rows,
                            count=f"{len(rows)} observation{'s' if len(rows) != 1 else ''}")
                       for topic, rows in groups.items()]
    interpretation_rows = [dict(
        r, id=r.get("id") or ident,
        display=formatted(r) if "value" in r else r.get("title", NOT_APPLICABLE), item="value" not in r,
        metric_label=(reader_metric_label(r, session) if r.get("metric") else "Published / scheduled item"),
        when=(release_clock(r, session) if r.get("frequency") == "release" else
              compact_clock(r.get("observed_at") or r.get("published_at") or r.get("published_on") or "")),
        **item_detail(r),
        source_name=source_names.get(r.get("source_id"), r.get("source_id") or "not retained in snapshot"),
        status_label=SOURCE_STATUS_LABELS.get(r.get("status", ""), (r.get("status") or "").title()))
        for ident, r in frozen.items()]
    interpretation_groups_by_topic = {}
    for row in interpretation_rows:
        interpretation_groups_by_topic.setdefault(row.get("topic") or row.get("title") or row["id"], []).append(row)
    interpretation_groups = [dict(topic=topic, label=instrument_label(topic), rows=rows,
                                  count=f"{len(rows)} observation{'s' if len(rows) != 1 else ''}")
                             for topic, rows in interpretation_groups_by_topic.items()]
    sources = source_rows(packet["sources"])
    technical["sources_retrieved"] = [dict(id=s["id"], status=s.get("status", ""), reason=s.get("reason", ""),
                                           retrieved_at=s.get("retrieved_at", "")) for s in packet["sources"]]
    # Two clocks on a carried page: when the analysis was anchored and when this run refreshed the observed
    # record. Each row keeps its own observation clock; the page clock never claims every datum is that fresh.
    data_clock = pacific_time(actual_started_at)
    interpretation_clock = pacific_time(interpreted["target_time"])
    # The take is interpretation-clock prose, so a carried page shows it at the values its analyst saw. An empty
    # take, or a narrative frozen before the take existed, renders nothing at all.
    take = narrative.get("take") or {}
    take = (dict(text=TRAILING_TAG.sub("", expand(take["text"].strip())), evidence_ids=take["evidence_ids"],
                 refs=refs(take["evidence_ids"])) if take.get("text", "").strip() else None)
    # Three clocks, each saying what it measures. Prices: the latest current equity print across the page's tables
    # (the latest of their own "as of" clocks), else the prior close they are dated to. Analysis: when the carried
    # interpretation was made, and by which synthesis edition. Next: the scheduler's next checkpoint. The run's
    # collection time stays in Technical details.
    table_rows = [*mega_rows, *sector_rows, *metal_rows]
    prints = [timestamp(row["intraday"]["observed_at"]) for row in table_rows if row["intraday"]["id"]]
    closes = [row["daily"]["observed_at"] for row in table_rows if row["daily"]["id"]] if daily_today else []
    prices = (pacific_time(max(prints).isoformat()) if prints
              else f"prior close {short_date(max(closes))}" if closes else NO_PRINT)
    edition_word = EDITION_WORDS.get(interpreted.get("checkpoint")) or EDITION_LABELS.get(
        interpreted.get("checkpoint"), "analysis").removesuffix(" edition").lower()
    analysis = f"{interpretation_clock} · {edition_word}"
    # A commissioning run never claims its phase's scheduled slot, so that checkpoint can still be next.
    upcoming = next_update(next_checkpoint(target, None if packet["run"].get("commissioning") else checkpoint),
                           session_date)
    next_label = upcoming["text"]
    if not carried and prices == interpretation_clock:
        # A synthesis edition whose prices and analysis share one clock.
        clocks = [dict(label="Prices & analysis", text=analysis, anchor=True)]
    else:
        clocks = [dict(label="Prices", text=prices, anchor=True), dict(label="Analysis", text=analysis)]
    clocks.append(dict(label="Next", text=next_label, next_at=upcoming["at"], next_when=upcoming["when"],
                       grace_minutes=int(OVERDUE_GRACE.total_seconds() // 60)))
    since_label = since_caption(anchors, prior.get("unpublished", ()) if available else ())
    if since_label and carried:
        # A carried page's comparisons end where its analysis was written, not at this refresh.
        since_label += f" · through the {interpretation_clock} analysis"
    view = dict(data_only=False,
        mode=packet["run"]["mode"], status=status, commissioning=commissioning,
        live_commissioning=live_commissioning, checkpoint=checkpoint, kind=kind, carried=carried, glance=glance,
        # The scheduler's idempotency marker; a commissioning run never claims a scheduled slot.
        marker_checkpoint="COMMISSIONING" if packet["run"].get("commissioning") else checkpoint,
        edition_label=EDITION_LABELS.get(checkpoint, "Market edition"),
        session=packet["run"]["session"], target=packet["run"]["target_time"],
        actual_started_at=actual_started_at,
        masthead_date=pacific_time(actual_started_at, True).split(" · ")[0],
        # LIVE is the normal state and says nothing; any other status stays loud, with the edition named once.
        status_line="" if status == "LIVE" else f"{status} · {EDITION_LABELS.get(checkpoint, 'Market edition')}",
        clocks=clocks, prices_clock=prices, data_clock=data_clock, interpretation_clock=interpretation_clock,
        next_update=next_label,
        truth=truth,
        technical=technical, coverage=packet["coverage"], limitations=limitations,
        # The banner's limitation is the analyst's coverage caveat at its own clock. Only the edition that
        # made it shows it (with its marker); a carried page withholds it, unedited, because current
        # availability comes from this run's record alone.
        banner={**narrative["banner"], "title": expand(narrative["banner"]["title"]),
                "limitation": "" if carried else expand(narrative["banner"]["limitation"]),
                "refs": [] if carried else refs(narrative["banner"]["evidence_ids"])},
        character=expand(narrative["character"]["text"]),
        character_ids=narrative["character"]["evidence_ids"],
        character_refs=refs(narrative["character"]["evidence_ids"]),
        summary=[paragraph(p) for p in narrative["summary"]], take=take, rates_lead=rates_lead,
        shift=state_notice(interpretation, packet, carried, refs, catalog),
        evidence_charts=charts_for,
        since=dict(heading="What changed", label=since_label, entries=since_entries, note=since_note,
                   status=prior.get("status", "cold_start")),
        next=dict(watches=watches, carried=live_carried, retired=retired_watches, attention=attention,
                  events=events,
                  paragraphs=[paragraph(p) for key in ("attention", "events") for p in narrative["sections"][key]]),
        equities=dict(paragraphs=[paragraph(p) for p in narrative["sections"]["equities"]],
                      rows=mega_rows, change_label=mega_change, asof=mega_asof, proof=proof(mega_rows),
                      notes=mega_notes,
                      spread_label="20-session return spread vs QQQ",
                      lookback={s: v for s, v in packet.get("lookback", {}).items() if v["r20"] != "available"}),
        macro=dict(paragraphs=[paragraph(p) for p in narrative["sections"]["macro"]], releases=cards,
                   yields=yields, yields_asof=module["asof"], daily_change_label=module["daily_change_label"],
                   curve=module["curve"], facts=other_macro, panel=panel,
                   panel_proof=refs(panel["proof_ids"], catalog) if panel else [],
                   yields_proof=refs(module["proof_ids"], catalog),
                   facts_proof=refs([row["id"] for row in other_macro], catalog)),
        sectors=dict(rows=sector_rows, change_label=sector_change, asof=sector_asof, proof=proof(sector_rows),
                     notes=sector_notes,
                     spread_label="20-session return spread vs SPY, strongest to weakest"),
        cross_asset=dict(rows=metal_rows, change_label=metal_change, asof=metal_asof, proof=proof(metal_rows),
                         notes=metal_notes,
                         spread_label=("20-session return spread, " + ", ".join(metal_pairs)) if metal_pairs else ""),
        guide=dict(move=(dict(label=module["curve"]["move"]["label"], text=module["curve"]["move"]["sentence"])
                         if module["curve"] and module["curve"]["move"] and not module["curve"]["move"]["unavailable"]
                         else None),
                   entries=GUIDE, moves=GUIDE_MOVES),
        cuttingboard_section=dict(visible=cuttingboard_visible,
                                  paragraphs=[paragraph(p) for p in narrative["sections"]["cuttingboard"]]),
        sources=sources, unavailable_sources=sum(1 for s in sources if s["unavailable"]),
        interpretation_groups=interpretation_groups,
        cuttingboard=cuttingboard,
        evidence=evidence_rows, evidence_groups=evidence_groups, context_items=packet["context_items"])
    if data_only is not None:
        view.update(data_only_view(packet, data_only, view, prices, upcoming))
    return view


def data_only_view(packet, data_only, view, prices, upcoming):
    """What a data-only page says in place of the analysis (WP-MB-RECOVERY-01): the heading, the date and checkpoint,
    why there is no analysis, and nothing else. Tables, rates, events, sources and the evidence ledger are the
    deterministic view's own. Every interpretive surface is emptied here as well as by the blank record."""
    checkpoint = packet["run"]["checkpoint"]
    day = date.fromisoformat(packet["run"]["session"]["date"])
    edition = EDITION_LABELS.get(checkpoint, "Market edition")
    due = timestamp(data_only["scheduled_at"])
    clock = f"{due.astimezone(ET):%-I:%M %p} ET ({due.astimezone(PACIFIC):%-I:%M %p} {VANCOUVER_LABEL})"
    rejected = data_only.get("basis") == "synthesis_rejected"
    technical = dict(view["technical"],
                     synthesis=("analyst call not accepted (rejected by validation); data-only page, no analysis"
                                if rejected else "none at this checkpoint (no model call); no accepted analysis of "
                                "this session to carry; data-only page, no analysis"),
                     interpretation=dict(run_id=None, checkpoint="none (analysis unavailable)",
                                         evidence_cutoff="not applicable"),
                     cuttingboard="Cuttingboard: not shown on a data-only page (optional commentary)")
    if rejected:
        why = (f"The {EDITION_WORDS.get(checkpoint, 'scheduled')} analysis due {clock} was not accepted, so this "
               "page carries no analysis")
    else:
        why = (f"No analysis of this session has been accepted, so the {clock} update carries no analysis and no "
               "analysis was requested for it")
    return dict(
        data_only=True,
        banner=dict(title=DATA_ONLY_HEADING, limitation="", refs=[], evidence_ids=[]),
        character=f"{day:%A, %B} {day.day}, {day.year} · {edition} ({checkpoint})",
        character_ids=[], character_refs=[], summary=[], take=None, rates_lead=None, shift=None,
        truth=(f"{why}: no headline read, take, watches, relationships or flags, and nothing from an "
               "earlier analysis. The prices, rates, events and sources below are this checkpoint's own "
               "observations, each with its own clock."),
        clocks=[dict(label="Prices", text=prices, anchor=True),
                dict(label="Analysis", text="unavailable · not accepted"),
                dict(label="Next", text=upcoming["text"], next_at=upcoming["at"], next_when=upcoming["when"],
                     grace_minutes=int(OVERDUE_GRACE.total_seconds() // 60))],
        interpretation_clock="not applicable",
        since=dict(view["since"], label="", entries=[], note=""),
        next=dict(view["next"], watches=[], carried=[], retired=[], attention=[], paragraphs=[]),
        equities=dict(view["equities"], paragraphs=[]), macro=dict(view["macro"], paragraphs=[]),
        # Cuttingboard is optional upstream commentary, not this project's verified record: a data-only page shows
        # none of it (no section, no quotation, no status line). The project's own dated rows, rates, sources and
        # evidence ledger above are untouched.
        cuttingboard_section=dict(view["cuttingboard_section"], paragraphs=[], visible=False),
        interpretation_groups=[], technical=technical)


def markdown(view):
    def esc(value):
        text = " ".join(str(value).split())
        return re.sub(r"([\\`*_\[\]|])", r"\\\1", html.escape(text))

    frozen_ids = {row["id"] for group in view["interpretation_groups"] for row in group["rows"]}

    def refs(ids):
        return " ".join(f"[evidence](#{'interpretation-evidence' if i in frozen_ids else 'evidence'}-{i})" for i in ids)

    def para(p):
        blocks = [f"{esc(p['text'])} {refs(p['evidence_ids'])}"]
        if p["uncertainty"]:
            blocks.append(f"Caveat: {esc(p['uncertainty'])}")
        if p["alternative"]:
            blocks.append(f"Could also be: {esc(p['alternative'])}")
        return "\n\n".join(blocks)

    header = " · ".join(filter(None, (view["status_line"], view["masthead_date"])))
    lines = [f"# {esc(view['banner']['title'])}", "", esc(header), ""]
    lines += [f"- {clock['label']} · {esc(clock['text'])}" for clock in view["clocks"]]  # labels are constants
    if view["truth"]:
        lines += ["", f"> {esc(view['truth'])}"]
    lead = "OBSERVATIONS ONLY" if view.get("data_only") else "INTERPRETATION"
    lines += ["", f"**{lead}** · {esc(view['character'])} {refs(view['character_ids'])}", ""]
    if view["banner"]["limitation"]:
        lines += [esc(view["banner"]["limitation"]), ""]
    for p in view["summary"]:
        lines += [para(p), ""]
    if view["take"]:
        lines += [f"**The take:** {esc(view['take']['text'])} {refs(view['take']['evidence_ids'])}", ""]
    if view["coverage"]["missing_domains"]:
        lines += [f"Missing: {esc(' · '.join(view['coverage']['missing_domains']))}.", ""]
    lead_rates = view["rates_lead"]
    if lead_rates:
        tenors = " · ".join(f"{t['maturity']} {t['level']['display']}"
                            + ("" if lead_rates["stale"] or t["change"]["absent"] else f" {t['change']['display']}")
                            for t in lead_rates["tenors"])
        spreads = " · ".join(f"{s['name']} {s['level']}" for s in lead_rates["spreads"])
        move = f" · {lead_rates['move']}" if lead_rates["move"] else ""
        lines += [f"**RATES** · {esc(lead_rates['caption'])} · {tenors}" + (f" · {spreads}" if spreads else "") + move
                  + (f" · {esc(lead_rates['note'])}" if lead_rates["note"] else ""), ""]
    shift = view["shift"]
    if shift:
        measures = " · ".join(f"{m['label']} {m['earlier']} → {m['later']}" for m in shift["measures"])
        lines += [f"**{shift['caption'].upper()}** · {esc(shift['window'])} · {esc(shift['sentence'])} {measures}. "
                  f"{esc(shift['note'])}",
                  "At the analysis: " + " · ".join(f"[{esc(i)}](#analysis-state-{i})" for i in shift["opening_ids"])
                  + (f". At {esc(shift['later_clock'])}: " if shift["later_ledger"] else ". Now: ")
                  + " · ".join(f"[{esc(i)}](#{shift['later_namespace']}-{i})" for i in shift["current_ids"]) + ".",
                  ""]
    since = view["since"]
    if since["entries"] or since["note"]:
        lines += [f"## {since['heading']}" + (f" · {esc(since['label'])}" if since["label"] else ""), ""]
        for item in since["entries"]:
            if item["kind"] == "change":
                lines.append(f"- {esc(item['text'])} {refs(item['evidence_ids'])}")
            else:  # a verdict on a carried watch or relationship: the observation, then what it judged
                judged = f" {item['role']}: {esc(item['judged'])}" if item["judged"] else ""
                lines.append(f"- **{item['assessment'].capitalize()}** — {esc(item['observed'])}{judged} "
                             f"{refs(item['evidence_ids'])}")
            for move in item.get("moves", []):
                # Before → after, with its window: two measurements, never a path.
                lines.append(f"  - {esc(move['label'])} · {move['earlier']['display']} → "
                             f"{move['later']['display']} · {move['verb']} · {esc(move['window'])} · "
                             f"{esc(move['measure'])}")
        if since["note"]:
            lines.append(esc(since["note"]))
        lines.append("")
    nxt = view["next"]
    lines += ["## What matters next", ""]
    for p in nxt["paragraphs"]:
        lines += [para(p), ""]
    for w in nxt["watches"]:
        changes_it = f" Changes it: {esc(w['contradiction'])}" if w["contradiction"] else ""
        passed = " · horizon passed" if w["expired"] else ""
        lines.append(f"- **WATCH · {esc(w['phrase'])}{passed}** — {esc(w['condition'])} Confirm: "
                     f"{esc(w['confirmation'])}{changes_it} {refs(w['evidence_ids'])}")
    for c in nxt["carried"]:
        passed = " · horizon passed" if c["expired"] else ""
        status = f" · {c['assessment']}" if c["assessment"] else ""
        pointer = f" · {VERDICT_POINTER}" if c["state"] in ("judged", "completed") else ""
        # The reason stays beside the question; the labeled criteria follow it, so neither reads as the other.
        text = f"{esc(c['hypothesis'])} {esc(c['reason'])}"
        if c["confirmation"]:
            text = f"{text.rstrip()} Confirm: {esc(c['confirmation'])}"
        if c["contradiction"]:
            text = f"{text.rstrip()} Changes it: {esc(c['contradiction'])}"
        creation_refs = " ".join(f"[evidence](#{r['anchor_id']})" for r in c["refs"] if r["anchor"])
        lines.append(f"- **FROM AN EARLIER READ{status} · {esc(c['phrase'])}{passed}{pointer}** — "
                     f"{text} {creation_refs}")
    if nxt["retired"]:
        count = len(nxt["retired"])
        lines.append(f"- Earlier watches · {count} ended without a verdict: "
                     + " · ".join(f"{esc(c['hypothesis'])} {esc(c['reason'])}".strip() for c in nxt["retired"]))
    for a in nxt["attention"]:
        lines.append(f"- **{esc(a['display_symbol'])}** — {esc(a['why'] or a['reason'])} "
                     f"({esc(a['trigger'])} · {esc(a['date_label'])}) {refs(a['evidence_ids'])}")
    for e in nxt["events"]:
        passed = f" · {esc(e['passed'])}" if e["passed"] else ""
        lines.append(f"- **Event** — {esc(e['title'])} · {esc(e['scheduled_label'])} · {esc(e['relation_label'])}"
                     f"{passed} {refs([e['id']])}")
        if e["source_line"]:
            note = f" {esc(e['note'])}" if e.get("note") else ""
            lines.append(f"  {esc(e['local_label'])}.{note} Source: [{esc(e['source_line'])}]({e['url']})")
    lines.append("")
    mac = view["macro"]
    cross = view["cross_asset"]
    if mac["paragraphs"] or mac["releases"] or mac["curve"] or mac.get("panel") or mac["facts"] or cross["rows"]:
        lines += ["## Macro & rates", ""]
        for card in mac["releases"]:
            lines += [f"**ECONOMIC RELEASE** · {card['time']} · {esc(card['title'])} · {esc(card['period'])}", ""]
            # One `·` between a label and its values; months and their estimates are separated by `;`.
            lines += [f"- {line['label']} · " + "; ".join(line["values"]) for line in card["lines"]]
            lines.append("")
        if mac["curve"]:
            curve = mac["curve"]
            dated = not mac["yields_asof"]
            head = (
                "| Maturity | Yield |"
                + ("" if curve["stale"] else f" {mac['daily_change_label']} |")
                + (" Date |" if dated else "")
            )
            lines += [f"**{curve['title'].upper()}** · {esc(curve['caption'])}", ""]
            if mac["yields"]:
                lines += [head, "|---|---:|" + ("" if curve["stale"] else "---:|") + ("---|" if dated else "")]
            for row in mac["yields"]:
                note = f" ({esc(row['change_note'])})" if row["change_note"] else ""
                change = "" if curve["stale"] else f" {row['change']['display']}{note} |"
                lines.append(f"| {row['maturity']} | {row['level']['display']} |{change}"
                             + (f" {esc(row['date'])} |" if dated else ""))
            lines.append("")
            for spread in curve["spreads"]:
                lines.append(f"- {spread['name']} · " + " · ".join(filter(None, (spread["level"], spread["detail"],
                                                                                   spread["flip"]))))
            if curve["move"]:
                lines += ["", f"**{curve['move']['label']}** — {esc(curve['move']['sentence'])}"
                          + (f" {esc(curve['move']['note'])}" if curve["move"]["note"] else "")]
            for part in [curve["lead_part"]] if curve.get("lead_part") else []:
                line = (f"- {part['maturity']} {part['nominal']} = real {part['real']} + inflation compensation "
                        f"{part['compensation']}")
                if part["split"]:
                    line += f". {esc(part['split']['sentence'])}"
                lines.append(line)
            if curve.get("parts_caveat"):
                lines += ["", esc(curve["parts_caveat"])]
            for note in curve["notes"]:
                lines += ["", esc(note)]
            lines.append("")
        panel = mac.get("panel")
        if panel:
            lines += [f"**{panel['title'].upper()}** · {esc(panel['caption'])}", ""]
            if panel["rows"]:
                lines += ["| Maturity | Yield | 1 day | 5 days | 20 days |", "|---|---:|---:|---:|---:|"]
                lines += [f"| {row['maturity']} | {row['level']} | "
                          + " | ".join(m["display"] for m in row["moves"]) + " |"
                          for row in panel["rows"]]
                lines.append("")
            if panel["spreads"]:
                lines.append("- Spreads · " + " · ".join(panel["spreads"]))
            for item in panel["leaders"]:
                lines.append(f"- Front vs long end, {item['horizon']} · {item['text']} ({item['detail']})")
            for part in panel["parts"]:
                lines.append(f"- 10Y, {part['horizon']} · {part['text']}")
            if panel["volatility"]:
                lines.append(f"- 10Y volatility · {esc(panel['volatility']['text'])}")
            if panel["funding"]:
                funding = panel["funding"]
                for row in funding["rows"]:
                    parts = (row["level"], row["date"] and f"on {row['date']}", row["change"])
                    lines.append(f"- {row['name']} · " + " · ".join(filter(None, parts)))
                if funding["spread"]:
                    lines.append(f"- {funding['spread']['text']}")
                lines.append(f"- {esc(funding['explainer'])}")
            lines.append("")
        for p in mac["paragraphs"]:
            lines += [para(p), ""]
        if mac["yields"]:
            lines += [LEDGER_NOTE, ""]
        if mac["facts"]:
            lines += ["| Measure | Observation | Date / source |", "|---|---:|---|"]
            for row in mac["facts"]:
                lines.append(f"| {esc(row['measure'])} | {row['display']} | "
                             f"{row['observed_label']} · {row['status']} |")
            lines += ["", LEDGER_NOTE, ""]
        if cross["rows"]:
            asof = f" · {esc(cross['asof'])}" if cross["asof"] else ""
            caption = " · ".join(filter(None, (esc(cross["spread_label"]), esc(cross["change_label"]))))
            lines += [f"**METALS** · {caption}{asof}", "",
                      "| Instrument | Change | 20D | Spread | vs 50DMA |",
                      "|---|---:|---:|---:|---:|"]
            for row in cross["rows"]:
                spread = (f"{row['relative']['display']} vs {row['relative_label']}" if row["relative"]["id"]
                          else row["relative"]["display"])
                lines.append(f"| {esc(row['label'])} ({row['symbol']}) | {row['today']['display']} | "
                             f"{row['r20']['display']} | {spread} | {row['dma']['display']} |")
            lines += ["", *(text for note in cross["notes"] for text in (esc(note), "")), LEDGER_NOTE, ""]
    sec = view["sectors"]
    if sec["rows"]:
        asof = f" · {esc(sec['asof'])}" if sec["asof"] else ""
        lines += ["## Sector view", "", f"{esc(sec['spread_label'])} · {esc(sec['change_label'])}{asof}", "",
                  "| Sector | vs SPY | 20D | Change | vs 50DMA |",
                  "|---|---:|---:|---:|---:|"]
        for row in sec["rows"]:
            lines.append(f"| {esc(row['label'])} ({row['symbol']}) | {row['relative']['display']} | "
                         f"{row['r20']['display']} | {row['today']['display']} | {row['dma']['display']} |")
        lines += ["", *(text for note in sec["notes"] for text in (esc(note), "")), LEDGER_NOTE, ""]
    eq = view["equities"]
    if eq["paragraphs"] or eq["rows"]:
        lines += ["## Equity structure", ""]
        for p in eq["paragraphs"]:
            lines += [para(p), ""]
        if eq["rows"]:
            asof = f" · {esc(eq['asof'])}" if eq["asof"] else ""
            lines += [f"**MEGA-CAP SNAPSHOT** · {esc(eq['spread_label'])} · {esc(eq['change_label'])}{asof}", "",
                      "| Symbol | Change | 20D | vs QQQ | vs 50DMA |",
                      "|---|---:|---:|---:|---:|"]
            for row in eq["rows"]:
                lines.append(f"| {row['symbol']} | {row['today']['display']} | {row['r20']['display']} | "
                             f"{row['relative']['display']} | {row['dma']['display']} |")
            lines += ["", *(text for note in eq["notes"] for text in (esc(note), "")), LEDGER_NOTE, ""]
        for symbol, state in eq["lookback"].items():
            lines.append(f"{esc(symbol)} 20-session return unavailable: {state['sessions']} sessions of history")
        if eq["lookback"]:
            lines.append("")
    cb = view["cuttingboard"]
    if view["cuttingboard_section"]["visible"]:
        lines += ["## Cuttingboard context", ""]
        for p in view["cuttingboard_section"]["paragraphs"]:
            lines += [para(p), ""]
        if cb["status"] == "AVAILABLE":
            lines += [f"**SOURCE QUOTATION** · Outcome: {esc(cb.get('outcome') or 'not exposed')}; "
                      f"permission: {esc(cb.get('permission') or 'not exposed')}. "
                      "Read-only context captured; see Technical details.", ""]
        else:
            lines += [f"{esc(cb['status'])} — {esc(cb.get('reason', ''))}.", ""]
    lines += ["## Sources & coverage", ""]
    if view["banner"]["limitation"]:
        lines += [esc(view["banner"]["limitation"]), ""]
    for limitation in view["limitations"]:
        lines += [f"- {esc(limitation)}"]
    for s in view["sources"]:
        lines += [f"- [{esc(s['name'])}](<{s['url']}>) · {esc(s['kind'])} · {esc(s['status'])} "
                  f"· retrieved {esc(s['retrieved_at'])}"]
    lines += ["", "### Evidence ledger", ""]
    if view["interpretation_groups"]:
        lines += [f"**Evidence at analysis · {esc(view['interpretation_clock'])}**", ""]
        for group in view["interpretation_groups"]:
            lines += [f"**{esc(group['label'])}**", ""]
            for row in group["rows"]:
                when = esc(row.get("observed_at") or row.get("published_at") or row.get("published_on")
                           or "not exposed")
                source = esc(row.get("source_id") or "not retained in snapshot")
                lines += [f'<a id="interpretation-evidence-{row["id"]}"></a>',
                          f"**{esc(row['id'])}** · {esc(row.get('topic', row.get('title', '')))} · "
                          f"{esc(row['display'])} · {esc(row.get('baseline', 'published / scheduled item'))} "
                          f"· observed/published {when} · source {source}"
                          + (f" · [release](<{row['url']}>)" if row.get("url") else ""), ""]
    if view["shift"]:
        lines += [f"**Prints at analysis · {esc(view['shift']['opening_clock'])} · since-the-analysis check**", ""]
        for row in view["shift"]["ledger"]:
            lines += [f'<a id="{row["anchor_id"]}"></a>',
                      f"**{esc(row['id'])}** · {esc(row['label'])} · {esc(row['display'])} · {esc(row['baseline'])} "
                      f"· observed {esc(row['observed_at'])} · source {esc(row['source_id'])}", ""]
        if view["shift"]["later_ledger"]:
            lines += [f"**Last live prints · {esc(view['shift']['later_clock'])} · since-the-analysis check**", ""]
            for row in view["shift"]["later_ledger"]:
                lines += [f'<a id="{row["anchor_id"]}"></a>',
                          f"**{esc(row['id'])}** · {esc(row['label'])} · {esc(row['display'])} · "
                          f"{esc(row['baseline'])} · observed {esc(row['observed_at'])} · source "
                          f"{esc(row['source_id'])}", ""]
    for group in view["evidence_groups"]:
        lines += [f"**{esc(group['topic'])}**", ""]
        for row in group["rows"]:
            when = row.get("observed_at") or row.get("published_at") or row.get("published_on") or "not exposed"
            lines += [f'<a id="evidence-{row["id"]}"></a>',
                      f"**{esc(row['id'])}** · {esc(row.get('topic', row.get('title', '')))} · "
                      f"{esc(row['display'])} · {esc(row.get('baseline', 'published / scheduled item'))} "
                      f"· observed/published {esc(when)} "
                      f"· source {esc(row['source_id'])}"
                      + (f" · [release](<{row['url']}>)" if row.get("url") else ""), ""]
    carried_evidence = [(c, row) for c in view["next"]["carried"] for row in c["creation_evidence"]]
    if carried_evidence:
        lines += ["**Carried watch creation evidence**", ""]
        for watch, row in carried_evidence:
            lines += [f'<a id="{row["anchor_id"]}"></a>',
                      f"**{esc(row['id'])}** · {esc(row['metric_label'])} · {esc(row['display'])} · "
                      f"{esc(row['baseline'])} · observed {esc(row['when'])} · source {esc(row['source_name'])} "
                      f"({esc(row['source_id'])}) · watch {esc(watch['id'])}", ""]
    technical = view["technical"]
    lines += ["", "### Technical details", "",
              f"Generated UTC: {esc(technical['generated_utc'])}",
              f"Evidence cutoff UTC: {esc(technical['evidence_cutoff_utc'])}",
              f"Checkpoint: {esc(technical['checkpoint'])} ({esc(technical['kind'])})",
              f"Synthesis: {esc(technical['synthesis'])}",
              f"Interpretation: {esc(technical['interpretation']['checkpoint'])} · evidence cutoff "
              f"{esc(technical['interpretation']['evidence_cutoff'])}"
              + (f" · run {esc(technical['interpretation']['run_id'])}"
                 if technical["interpretation"]["run_id"] else ""),
              f"Bootstrap: {esc(technical['bootstrap'])}",
              f"Calendar: {esc(technical['calendar'])}",
              f"{esc(technical['basis'])}. {esc(technical['horizon'])}",
              f"Continuity: {esc(technical['continuity']['status'])}"
              + (f" — {esc(technical['continuity']['reason'])}" if technical["continuity"]["reason"] else ""), "",
              esc(technical["cuttingboard"]), "",
              *([f"Citations rewritten from a comparison to the two observations it compares: "
                 f"{esc('; '.join(technical['repairs']))}", ""] if technical["repairs"] else []),
              ("No interpretation on this page; factual rows are deterministic." if view.get("data_only")
               else "Model-assisted interpretation; factual rows are deterministic."), ""]
    return "\n".join(lines)


def render(packet, narrative=None, context=None, interpretation=None, glance=None, repairs=None, degraded=None,
           data_only=None):
    """Render one edition. A synthesis edition passes its narrative (and freezes it on the way); a
    deterministic refresh passes the frozen `interpretation` record it carries; a data-only page (WP-MB-RECOVERY-01)
    passes neither, only `data_only`. `glance` is the optional overseas line's view (`glance.view`), kept out of the
    packet so it never reaches evidence, continuity or the analyst."""
    if data_only is not None and (narrative is not None or context is not None or interpretation is not None):
        raise ValueError("a data-only page renders no narrative, context or interpretation")
    view = presentation(packet, narrative, context, interpretation, glance, repairs, degraded, data_only)
    env = Environment(loader=FileSystemLoader(ROOT / "templates"),
                      autoescape=select_autoescape(default=True))
    template = env.get_template("brief.html.j2")
    try:
        return markdown(view), template.render(**view)
    except Exception:
        if glance is None:
            raise
        # The optional overseas line never fails an edition: render without it.
        return markdown(view), template.render(**dict(view, glance=None))

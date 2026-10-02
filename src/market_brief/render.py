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
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .actuals import CHANGE as PERCENT_CHANGE
from .actuals import COMBINED, EVENT_TITLE, JOBS, PERCENT, REVISION, period_name
from .continuity import CARRY_LIMIT, interpretation_record
from .curve import (
    CHANGE,
    LEVEL,
    SENTENCES,
    SPREAD_CHANGE,
    SPREAD_LEVEL,
    SPREAD_UNIT,
    SPREADS,
    TENORS,
    UNAVAILABLE,
    bp,
    curve_record,
    prior_entry_date,
    rates,
    spread_rows,
)
from .evidence import ET, PLACEHOLDER, ROOT, USABLE, evidence_catalog, read_json, timestamp
from .metrics import rank_by_spread
from .schedule import CHECKPOINT_KINDS, checkpoint_kind, next_checkpoint, session_relation

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
INSTRUMENT_LABELS = {"GLD": "Gold fund", "SLV": "Silver fund", "GDX": "Gold miners"}
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
RELEASE_UNITS = {JOBS, PERCENT, PERCENT_CHANGE}
# The economic release card (Macro & rates): one line per measure in the release's own order. A change pair reads
# `+0.1% m/m · +3.0% y/y`; payroll revisions read `Jul +21k → −10k` per month and then `Combined −60k`.
RELEASE_LINES = (("Payrolls", ("nonfarm payroll change",)), ("Unemployment", ("unemployment rate",)),
                 ("Avg hourly earnings", ("average hourly earnings, monthly change",
                                          "average hourly earnings, 12-month change")),
                 ("CPI", ("CPI-U all items, monthly change", "CPI-U all items, 12-month change")),
                 ("Core CPI", ("CPI-U all items less food and energy, monthly change",
                               "CPI-U all items less food and energy, 12-month change")))
PAIR_WORDS = {"monthly change": "m/m", "12-month change": "y/y"}
SHORT_MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
RATE_METRICS = {LEVEL, CHANGE, SPREAD_LEVEL, SPREAD_CHANGE}
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
NOT_COLLECTED = "not collected"
# Rows whose print is this far behind the table's shared clock carry their own clock.
SHARED_CLOCK_TOLERANCE = timedelta(minutes=5)
# The header's three clocks. The interpretation is named by the synthesis edition that wrote it.
EDITION_WORDS = {"PREMARKET": "premarket", "OPEN_30M": "opening structure"}
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


def pacific_time(value, include_date=False):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(PACIFIC)
    date_part = dt.strftime("%A, %b %-d · ") if include_date else ""
    return f"{date_part}{dt.strftime('%-I:%M %p')} PT"


def short_date(value):
    """`2026-09-04` → `Fri, Sep 4`; an intraday clock keeps its Pacific time. The one daily-date formatter."""
    if not isinstance(value, str) or not value:
        return ""
    if "T" in value:
        return pacific_time(value, True)
    return datetime.fromisoformat(value).strftime("%a, %b %-d")


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
    """`twenty-session return` → `20-session return`; `relative to SPY` → `vs SPY · 20 sessions`."""
    metric = row.get("metric", "")
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
        rows.append(dict(s, kind_label="" if redundant else kind,
                         status_label=SOURCE_STATUS_LABELS.get(status, status.title()),
                         unavailable=status != "AVAILABLE", reason=reason,
                         retrieved=compact_clock(s.get("retrieved_at", "")),
                         detail=" · ".join(filter(None, (s.get("provider"), s.get("feed"), s.get("data_delay"))))))
    return rows


def observed_label(value):
    if isinstance(value, str) and "T" in value:
        return pacific_time(value, True)
    return short_date(value) if isinstance(value, str) and value else value


def compact_clock(value):
    """Reader-facing clock above the audit layer: `Thu, Sep 10` for a dated row, `1:02 PM PT` for a print."""
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
        return f"{MINUS if whole < 0 else '+' if whole > 0 else ''}{abs(whole)}k"
    if unit == PERCENT:
        return f"{value:.1f}%"
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


def rates_module(packet, facts, catalog):
    """The Macro & rates module (R9): caption, the four-tenor table, spread lines, the named curve move, the chart,
    and notes, all from this run's deterministic rows and curve record; the analyst's paragraphs follow it."""
    yields, asof = treasury_rows(facts)
    if not yields:
        record = packet.get("curve") or {}
        if record.get("freshness") == "stale" and record.get("observed_at"):
            # Rows past the admission window arrive without values; the curve is still dated, and says so.
            notes = [OVER_AGE_NOTE, *([record["release_note"]] if record.get("release_note") else [])]
            return dict(yields=[], asof=None, proof_ids=[], curve=dict(
                title=CURVE_TITLE, caption=f"{short_date(record['observed_at'])} · {CURVE_CAPTIONS['stale']}",
                stale=True, spreads=[], move=None, chart=None, notes=notes))
        return dict(yields=[], asof=None, curve=None, proof_ids=[])
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
            row["ids"] = [row["level"]["id"]] if row["level"]["id"] else []
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
    return dict(yields=yields, asof=asof, proof_ids=[ident for ident in proof_ids if ident in catalog],
                curve=dict(title=CURVE_TITLE, caption=caption, stale=stale, spreads=lines, move=move, chart=chart,
                           notes=notes))


def release_cards(rows, events):
    """One card per admitted release, from this run's rows (the observed clock): its official time, family, reference
    month and values, and the IDs of the calendar events it has now happened for, which leave What matters next."""
    groups = {}
    for row in rows:
        groups.setdefault((row["observed_at"], row["topic"], row["reference_period"]), []).append(row)
    cards = []
    for (released, title, period), members in sorted(groups.items()):
        by_metric = {row["metric"]: row for row in members}
        lines = []
        for label, metrics in RELEASE_LINES:
            present = [by_metric[metric] for metric in metrics if metric in by_metric]
            if present:
                lines.append(dict(label=label, values=[" · ".join(
                    f"{formatted(row)} {PAIR_WORDS[row['metric'].rsplit(', ', 1)[1]]}" if len(metrics) > 1
                    else formatted(row) for row in present)]))
        revisions = sorted((row for row in members if row["metric"] == REVISION), key=lambda row: row["revised_month"])
        combined = [row for row in members if row["metric"] == COMBINED]
        if revisions or combined:
            lines.append(dict(label="Revisions", values=[
                *(f"{SHORT_MONTH_NAMES[int(row['revised_month'][5:]) - 1]} "
                  f"{formatted(dict(row, value=row['revised_from']))} \u2192 "
                  f"{formatted(dict(row, value=row['revised_to']))}" for row in revisions),
                *(f"Combined {formatted(row)}" for row in combined)]))
        happened = [event["id"] for event in events if event.get("scheduled_at") == released
                    and (EVENT_TITLE.fullmatch(event.get("title") or "") or [None, None])[1] == title]
        cards.append(dict(time=pacific_time(released), title=title, period=period_name(period), lines=lines,
                          ids=[row["id"] for row in members], events=happened))
    return cards


def next_update(info, session_date):
    """The scheduler's next checkpoint in reader words, its PT display time, and its absolute scheduled time."""
    clock = pacific_time(info["scheduled_at"])
    if info["session_date"] != session_date:
        when, kind = f"{short_date(info['session_date'])} · {clock}", "premarket analysis"
    else:
        when, kind = clock, NEXT_WORDS.get(info["kind"], "price refresh")
    return dict(text=f"{when} · {kind}", when=when, at=info["scheduled_at"])


def next_update_label(info, session_date):
    """`11:00 AM PT · price refresh`, `7:00 AM PT · analysis update`, `1:01 PM PT · close snapshot`, or
    `Mon, Sep 28 · 6:00 AM PT · premarket analysis`."""
    return next_update(info, session_date)["text"]


def since_caption(anchors):
    """`vs the previous close · Fri, Sep 11` or `vs premarket and the 7:00 AM update`."""
    if "previous_close" in anchors:
        return f"vs the previous close · {short_date(anchors['previous_close']['session_date'])}"
    parts = []
    for name, anchor in anchors.items():
        if name == "premarket":
            parts.append("premarket")
        else:
            noun = "refresh" if CHECKPOINT_KINDS.get(anchor.get("checkpoint")) == "refresh" else "update"
            parts.append(f"the {pacific_time(anchor['evidence_cutoff'])} {noun}")
    return "vs " + " and ".join(parts) if parts else None


def presentation(packet, narrative=None, context=None, interpretation=None):
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

    def refs(ids, rows=None):
        """The rows behind one block, formatted for a quiet expandable marker; IDs are unchanged.

        Prose cites the frozen rows the analyst saw; table proofs cite this run's rows.
        """
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
                               when=compact_clock(row.get("observed_at") or row.get("published_at") or ""),
                               anchor=ref in catalog))
        return result

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
    releases = [r for r in facts if r["frequency"] == "release"]
    other_macro = [r for r in facts if r not in equity and r not in treasuries and r not in releases]
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
    module = rates_module(packet, treasuries, catalog)
    metal_pairs = [f"{row['symbol']} vs {row['relative_label']}" for row in metal_rows if row["relative"]["id"]]
    yields = module["yields"]

    # What matters next: new watches with their frozen horizons, then the carried watches continuity will keep.
    watches = []
    for w, horizon in zip(narrative["watches"], interpretation["horizons"]):
        expires = horizon.get("expires_at")
        watches.append({**w, **{k: expand(w[k]) for k in ("condition", "confirmation", "contradiction")},
                        "phrase": horizon["phrase"], "expires_session": horizon.get("expires_session"),
                        "expired": bool(expires) and timestamp(expires) <= target,
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
                  else refs(watch["evidence_refs"], {**frozen, **creation}))))
    # The live set is exactly what `continuity.edition_state` carries forward: new watches first, then the
    # carried ones still active and not reversed, at most CARRY_LIMIT. The frozen prior list is already
    # newest-first, so no ranking of our own. A verdict (strengthened, weakened, reversed) is adjudication and
    # renders once, in What changed; a live carried watch shows its question, horizon and criteria here. Everything
    # else carried, expired or set aside without a verdict, is one collapsed count: audit, not attention.
    live_carried = [c for c in carried_watches if c["lifecycle"] == "active" and c["assessment"] != "reversed"]
    live_carried = live_carried[:max(0, CARRY_LIMIT - len(watches))]
    verdicts = [c for c in carried_watches if c["assessment"] in VERDICTS]
    retired_watches = [c for c in carried_watches if c not in live_carried and c not in verdicts]
    # A live watch that was also adjudicated keeps its question here and its verdict in What changed.
    live_carried = [dict(c, assessment="", reason="") if c["assessment"] in VERDICTS else c for c in live_carried]
    # A release whose official values are admitted has happened: its card in Macro & rates is its one home, so it is no
    # longer listed among the events that matter next.
    cards = [dict(card, proof=refs(card["ids"], catalog)) for card in release_cards(releases, packet["events"])]
    happened = {ident for card in cards for ident in card["events"]}
    events = [{**event, "scheduled_label": pacific_time(event["scheduled_at"], True),
               "relation_label": event.get("session_relation", "").lower(), "refs": refs([event["id"]], catalog)}
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

    # Flagged: a selected trigger earns its place only when its instrument is not already in the lead, in
    # What changed, or in a live watch, by cited row or by name. The frozen selection is unchanged; a
    # refresh reads the same frozen records, so the same flags show all day.
    lead_records = [narrative["banner"], narrative["character"], *narrative["summary"], *since_entries,
                    *watches, *live_carried]
    if narrative.get("take"):
        lead_records.append(narrative["take"])
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
                  "display_symbol": (f"{SECTOR_LABELS[a['symbol']]} · {a['symbol']}"
                                     if a["symbol"] in SECTOR_LABELS else a["symbol"]),
                  "date_label": short_date(a.get("date")), "refs": refs(a["evidence_ids"])}
                 for a in interpretation["attention"] if not covered(a["symbol"])]
    since_note = ""
    if available and not continuity["changed"]:
        # The note describes the interpretation's comparison, so on a refresh it is dated to that clock.
        lead = (f"At the {pacific_time(interpreted['target_time'])} read no comparable measurement had changed"
                if carried else "No comparable measurement has changed")
        since_note = (f"{lead}: {continuity['repeated']} repeated prior-close observations and no new session "
                      "prints." if continuity["repeated"] else
                      f"{lead.replace('had changed', 'was available').replace('has changed', 'is available yet')}.")
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
    rates_present = bool(yields or module["curve"] or other_macro or narrative["sections"]["macro"])
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
        synthesis=("this edition's one analyst call" if kind == "synthesis" else
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
    source_names = {s["id"]: s["name"] for s in packet["sources"]}
    evidence_rows = [dict(r, display=formatted(r) if "value" in r else r["title"],
                          metric_label=(reader_metric_label(r, session) if r.get("metric")
                                        else "Published / scheduled item"),
                          when=compact_clock(r.get("observed_at") or r.get("published_at") or ""),
                          source_name=source_names.get(r.get("source_id"), r.get("source_id", "")),
                          status_label=SOURCE_STATUS_LABELS.get(r.get("status", ""), (r.get("status") or "").title()))
                     for r in catalog.values()]
    groups = {}
    for row in evidence_rows:
        groups.setdefault(row.get("topic") or row.get("title") or row["id"], []).append(row)
    evidence_groups = [dict(topic=topic, label=instrument_label(topic), rows=rows,
                            count=f"{len(rows)} observation{'s' if len(rows) != 1 else ''}")
                       for topic, rows in groups.items()]
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
    take = (dict(text=expand(take["text"].strip()), evidence_ids=take["evidence_ids"],
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
    since_label = since_caption(anchors)
    if since_label and carried:
        # A carried page's comparisons end where its analysis was written, not at this refresh.
        since_label += f" · through the {interpretation_clock} analysis"
    return dict(
        mode=packet["run"]["mode"], status=status, commissioning=commissioning,
        live_commissioning=live_commissioning, checkpoint=checkpoint, kind=kind, carried=carried,
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
        summary=[paragraph(p) for p in narrative["summary"]], take=take,
        since=dict(heading="What changed", label=since_label, entries=since_entries, note=since_note,
                   status=prior.get("status", "cold_start")),
        next=dict(watches=watches, carried=live_carried, retired=retired_watches, attention=attention,
                  events=events,
                  paragraphs=[paragraph(p) for key in ("attention", "events") for p in narrative["sections"][key]]),
        equities=dict(paragraphs=[paragraph(p) for p in narrative["sections"]["equities"]],
                      rows=mega_rows, change_label=mega_change, asof=mega_asof, proof=proof(mega_rows),
                      spread_label="20-session return spread vs QQQ",
                      lookback={s: v for s, v in packet.get("lookback", {}).items() if v["r20"] != "available"}),
        macro=dict(paragraphs=[paragraph(p) for p in narrative["sections"]["macro"]], releases=cards,
                   yields=yields, yields_asof=module["asof"], curve=module["curve"], facts=other_macro,
                   yields_proof=refs(module["proof_ids"], catalog),
                   facts_proof=refs([row["id"] for row in other_macro], catalog)),
        sectors=dict(rows=sector_rows, change_label=sector_change, asof=sector_asof, proof=proof(sector_rows),
                     spread_label="20-session return spread vs SPY, strongest to weakest"),
        cross_asset=dict(rows=metal_rows, change_label=metal_change, asof=metal_asof, proof=proof(metal_rows),
                         spread_label=("20-session return spread, " + ", ".join(metal_pairs)) if metal_pairs else ""),
        guide=dict(move=(dict(label=module["curve"]["move"]["label"], text=module["curve"]["move"]["sentence"])
                         if module["curve"] and module["curve"]["move"] and not module["curve"]["move"]["unavailable"]
                         else None),
                   entries=GUIDE, moves=GUIDE_MOVES),
        cuttingboard_section=dict(visible=cuttingboard_visible,
                                  paragraphs=[paragraph(p) for p in narrative["sections"]["cuttingboard"]]),
        sources=sources, unavailable_sources=sum(1 for s in sources if s["unavailable"]),
        cuttingboard=cuttingboard,
        evidence=evidence_rows, evidence_groups=evidence_groups, context_items=packet["context_items"])


def markdown(view):
    def esc(value):
        text = " ".join(str(value).split())
        return re.sub(r"([\\`*_\[\]|])", r"\\\1", html.escape(text))

    def refs(ids):
        return " ".join(f"[evidence](#evidence-{i})" for i in ids)

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
    lines += ["", f"**INTERPRETATION** · {esc(view['character'])} {refs(view['character_ids'])}", ""]
    if view["banner"]["limitation"]:
        lines += [esc(view["banner"]["limitation"]), ""]
    for p in view["summary"]:
        lines += [para(p), ""]
    if view["take"]:
        lines += [f"**The take:** {esc(view['take']['text'])} {refs(view['take']['evidence_ids'])}", ""]
    if view["coverage"]["missing_domains"]:
        lines += [f"Missing: {esc(' · '.join(view['coverage']['missing_domains']))}.", ""]
    since = view["since"]
    if since["entries"] or since["note"]:
        lines += [f"## {since['heading']}" + (f" · {esc(since['label'])}" if since["label"] else ""), ""]
        for item in since["entries"]:
            if item["kind"] == "change":
                lines.append(f"- {esc(item['text'])} {refs(item['evidence_ids'])}")
            else:  # a verdict on a carried watch or relationship
                lines.append(f"- **{item['assessment'].capitalize()}** — {esc(item['text'])} {esc(item['reason'])} "
                             f"{refs(item['evidence_ids'])}")
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
        # The reason stays beside the question; the labeled criteria follow it, so neither reads as the other.
        text = f"{esc(c['hypothesis'])} {esc(c['reason'])}"
        if c["confirmation"]:
            text = f"{text.rstrip()} Confirm: {esc(c['confirmation'])}"
        if c["contradiction"]:
            text = f"{text.rstrip()} Changes it: {esc(c['contradiction'])}"
        lines.append(f"- **FROM AN EARLIER READ{status} · {esc(c['phrase'])}{passed}** — {text}")
    if nxt["retired"]:
        count = len(nxt["retired"])
        lines.append(f"- Earlier watches · {count} ended without a verdict: "
                     + " · ".join(f"{esc(c['hypothesis'])} {esc(c['reason'])}".strip() for c in nxt["retired"]))
    for a in nxt["attention"]:
        lines.append(f"- **{esc(a['display_symbol'])}** — {esc(a['why'] or a['reason'])} "
                     f"({esc(a['trigger'])} · {esc(a['date_label'])}) {refs(a['evidence_ids'])}")
    for e in nxt["events"]:
        lines.append(f"- **Event** — {esc(e['title'])} · {esc(e['scheduled_label'])} · {esc(e['relation_label'])} "
                     f"{refs([e['id']])}")
    lines.append("")
    mac = view["macro"]
    cross = view["cross_asset"]
    if mac["paragraphs"] or mac["releases"] or mac["curve"] or mac["facts"] or cross["rows"]:
        lines += ["## Macro & rates", ""]
        for card in mac["releases"]:
            lines += [f"**ECONOMIC RELEASE** · {card['time']} · {esc(card['title'])} · {esc(card['period'])}", ""]
            lines += [f"- {line['label']} · " + " · ".join(line["values"]) for line in card["lines"]]
            lines.append("")
        if mac["curve"]:
            curve = mac["curve"]
            dated = not mac["yields_asof"]
            head = "| Maturity | Yield |" + ("" if curve["stale"] else " Daily change |") + (" Date |" if dated else "")
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
            for note in curve["notes"]:
                lines += ["", esc(note)]
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
            lines += ["", LEDGER_NOTE, ""]
    sec = view["sectors"]
    if sec["rows"]:
        asof = f" · {esc(sec['asof'])}" if sec["asof"] else ""
        lines += ["## Sector view", "", f"{esc(sec['spread_label'])} · {esc(sec['change_label'])}{asof}", "",
                  "| Sector | vs SPY | 20D | Change | vs 50DMA |",
                  "|---|---:|---:|---:|---:|"]
        for row in sec["rows"]:
            lines.append(f"| {esc(row['label'])} ({row['symbol']}) | {row['relative']['display']} | "
                         f"{row['r20']['display']} | {row['today']['display']} | {row['dma']['display']} |")
        lines += ["", LEDGER_NOTE, ""]
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
            lines += ["", LEDGER_NOTE, ""]
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
    for group in view["evidence_groups"]:
        lines += [f"**{esc(group['topic'])}**", ""]
        for row in group["rows"]:
            lines += [f'<a id="evidence-{row["id"]}"></a>',
                      f"**{esc(row['id'])}** · {esc(row.get('topic', row.get('title', '')))} · "
                      f"{esc(row['display'])} · {esc(row.get('baseline', 'published / scheduled item'))} "
                      f"· observed/published {esc(row.get('observed_at') or row.get('published_at') or 'not exposed')} "
                      f"· source {esc(row['source_id'])}", ""]
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
              "Model-assisted interpretation; factual rows are deterministic.", ""]
    return "\n".join(lines)


def render(packet, narrative=None, context=None, interpretation=None):
    """Render one edition. A synthesis edition passes its narrative (and freezes it on the way); a
    deterministic refresh passes the frozen `interpretation` record it carries."""
    view = presentation(packet, narrative, context, interpretation)
    env = Environment(loader=FileSystemLoader(ROOT / "templates"),
                      autoescape=select_autoescape(default=True))
    return markdown(view), env.get_template("brief.html.j2").render(**view)

"""Global Market Glance: five overseas indices' latest completed sessions, each against its own history.

Display only. Nothing here enters the evidence packet, continuity, the evidence freshness rules or the analyst's
context, and nothing here may fail a run. The day's record is collected once (normally by the premarket run) and
carried to later runs in the published page itself, as the `data-glance` attribute that `cli.run` reads back from
`publish/index.html`, the way it already reads `data-evidence-cutoff`.

OFF in production: `SOURCE` is None until a free source that permits public display of all five indices is
admitted (2026-10-13: none found; see docs/ARCHITECTURE.md). With no source, `resolve` returns None and the page
carries no Glance at all.
"""

import bisect
import csv
import html
import json
import math
import re
from datetime import date, datetime

import exchange_calendars as xcals

from .evidence import ET, ROOT

# (code, reader label, exchange calendar)
MARKETS = (("KOSPI", "KOSPI", "XKRX"), ("N225", "Nikkei", "XTKS"), ("TAIEX", "TAIEX", "XTAI"),
           ("HSI", "HSI", "XHKG"), ("DAX", "DAX", "XETR"))
CODES = tuple(code for code, _, _ in MARKETS)
BASELINE = ROOT / "config/glance_baseline.json"
THRESHOLD = 95.0  # percentile of the index's own absolute daily moves at which a move is shown as unusual
MAX_ATTEMPTS = 3  # collections per US session date, the first included
MIN_BASELINE = 250  # completed sessions a baseline needs before it may highlight anything
# `fetch(codes)` returns {code: [(session_date "YYYY-MM-DD", close), ...]} for recent sessions, oldest first.
SOURCE = None
ATTRIBUTE = re.compile(r'<details[^>]*\sdata-glance="([^"]*)"')
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MINUS = "−"


def expected_session(calendar, at):
    """The market's latest session whose regular close is at or before `at`."""
    cal = xcals.get_calendar(calendar)
    session = cal.date_to_session(at.astimezone(cal.tz).date().isoformat(), direction="previous")
    if cal.session_close(session) > at:
        session = cal.previous_session(session)
    return session.date().isoformat()


def completed(bars, session):
    """The completed session's change against the bar before it, or None if the source lacks that session."""
    rows = [(str(day), close) for day, close in bars or []
            if isinstance(close, (int, float)) and math.isfinite(close) and close > 0]
    rows.sort()
    for index, (day, close) in enumerate(rows):
        if day == session and index:
            previous, before = rows[index - 1]
            return dict(session=day, previous=previous, change_pct=round(100 * (close / before - 1), 2))
    return None


def percentile(change_pct, history):
    """Share of the index's historical absolute daily moves strictly smaller than this one, in percent."""
    return round(100 * bisect.bisect_left(history, abs(change_pct)) / len(history), 1)


def load_baseline(path=BASELINE):
    """{code: {"start", "end", "sessions", "abs_returns"}} for each valid market; anything malformed is dropped."""
    try:
        raw = json.loads(path.read_text())
        markets = raw["markets"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    valid = {}
    for code in CODES:
        entry = markets.get(code) if isinstance(markets, dict) else None
        values = entry.get("abs_returns") if isinstance(entry, dict) else None
        if (isinstance(values, list) and len(values) >= MIN_BASELINE
                and all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 for v in values)
                and values == sorted(values)):
            valid[code] = dict(start=entry.get("start"), end=entry.get("end"), sessions=len(values),
                               abs_returns=values)
    return valid


def build_baseline(rows):
    """From (market, session_date, close) rows, about three years per market, the static baseline file."""
    series = {}
    for market, day, close in rows:
        if market in CODES:
            series.setdefault(market, {})[str(day)] = float(close)
    markets = {}
    for code, closes in series.items():
        days = sorted(closes)
        moves = sorted(round(abs(100 * (closes[b] / closes[a] - 1)), 4) for a, b in zip(days, days[1:]))
        if moves:
            markets[code] = dict(start=days[0], end=days[-1], abs_returns=moves)
    return dict(version=1, measure="absolute close-to-close daily percentage change", markets=markets)


def write_baseline(source_csv, destination=BASELINE):
    """`python -m market_brief glance-baseline --from-file closes.csv`: columns market, date, close."""
    with open(source_csv, newline="") as handle:
        rows = [(r["market"], r["date"], r["close"]) for r in csv.DictReader(handle)]
    destination.write_text(json.dumps(build_baseline(rows), separators=(",", ":")) + "\n")
    return destination


def read_carried(page):
    """The record a previous run published, or None when there is none or it is malformed."""
    match = ATTRIBUTE.search(page or "")
    if not match:
        return None
    try:
        record = json.loads(html.unescape(match.group(1)))
        date.fromisoformat(record["day"])
        datetime.fromisoformat(record["as_of"])
        if not isinstance(record["attempts"], int) or not isinstance(record["markets"], dict):
            return None
        for code, row in record["markets"].items():
            if code not in CODES or not isinstance(row.get("change_pct"), (int, float)):
                return None
            date.fromisoformat(row["session"])
            date.fromisoformat(row["previous"])
        return record
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def resolve(now, page, fetch=None):
    """The record this run shows, or None when the Glance is off. Never raises.

    One collection a day: a record carried from today's earlier page is reused as is. Its `as_of` (the first
    collection's time) fixes which session each market is expected to show, so the DAX keeps its previous session
    all day instead of prompting a second collection after Frankfurt closes. Only a market whose expected session
    is still missing is fetched again, at most `MAX_ATTEMPTS` collections a day.
    """
    fetch = SOURCE if fetch is None else fetch
    if fetch is None:
        return None
    today = now.astimezone(ET).date().isoformat()
    try:
        carried = read_carried(page)
        carried = carried if carried and carried["day"] == today else None
        record = carried or dict(day=today, as_of=now.isoformat(), attempts=0, markets={}, status="ok", reason="")
        as_of = datetime.fromisoformat(record["as_of"])
        missing = [code for code, _, calendar in MARKETS
                   if (record["markets"].get(code) or {}).get("session") != expected_session(calendar, as_of)]
        if missing and record["attempts"] < MAX_ATTEMPTS:
            record = dict(record, attempts=record["attempts"] + 1, markets=dict(record["markets"]))
            try:
                bars = fetch(missing)
                for code, _, calendar in MARKETS:
                    if code in missing:
                        row = completed(bars.get(code), expected_session(calendar, as_of))
                        if row:
                            record["markets"][code] = row
                record.update(status="ok", reason="")
            except Exception as exc:  # noqa: BLE001 - optional data: any failure is a quiet unavailable state
                record.update(status="ok" if record["markets"] else "unavailable",
                              reason=f"source unavailable ({type(exc).__name__})")
        return record
    except Exception as exc:  # noqa: BLE001 - never fail the run over optional data
        return dict(day=today, as_of=now.isoformat(), attempts=MAX_ATTEMPTS, markets={}, status="unavailable",
                    reason=f"glance failed ({type(exc).__name__})")


def signed(value):
    return f"{'+' if value > 0 else MINUS if value < 0 else ''}{abs(value):.2f}%"


def short_date(day):
    when = date.fromisoformat(day)
    return f"{WEEKDAYS[when.weekday()]}, {when:%b} {when.day}"


def view(record, baseline=None):
    """What the template shows. `data` is the record itself, carried to the next run in the page."""
    if record is None:
        return None
    baseline = load_baseline() if baseline is None else baseline
    items = []
    for code, label, _ in MARKETS:
        row = record["markets"].get(code)
        if not row:
            items.append(dict(label=label, text="\u2014", day="", unusual=False, dates="", rank="not yet published"))
            continue
        history = baseline.get(code)
        share = percentile(row["change_pct"], history["abs_returns"]) if history else None
        # A session that is not today's (the DAX before Frankfurt closes, a market on holiday) carries its weekday.
        day = "" if row["session"] == record["day"] else WEEKDAYS[date.fromisoformat(row["session"]).weekday()]
        rank = ("no baseline" if share is None else "largest in its baseline" if share >= 100
                else f"larger than {share:g}% of its daily moves")
        items.append(dict(label=label, text=signed(row["change_pct"]), day=day, rank=rank,
                          unusual=share is not None and share >= THRESHOLD,
                          dates=f"{short_date(row['session'])} vs {short_date(row['previous'])}"))
    window = (f"{min(h['start'] for h in baseline.values())} to {max(h['end'] for h in baseline.values())}"
              if baseline else "")
    return dict(status=record["status"], markets=items, threshold=f"{THRESHOLD:g}", window=window,
                data=json.dumps(record, sort_keys=True, separators=(",", ":")))

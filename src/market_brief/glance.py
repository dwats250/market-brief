"""Global Equity Proxies: five US-listed country ETFs, each one's current print against its previous US close.

These are proxies, not the foreign indices: US-listed funds priced in US dollars during US hours. They do not report
the latest foreign-market sessions. Display only: nothing here enters the evidence packet, continuity or the analyst's
context, and nothing here may fail a run. Collection reuses the existing Alpaca integration (one snapshot request per
run, same keys and host) and the existing freshness rules (`evidence.normalize_observation`).
"""

import bisect
import csv
import json
import math
import os
import time
from datetime import datetime, timezone

from .collect import _alpaca_intraday, _alpaca_request
from .evidence import ET, ROOT, USABLE, normalize_observation, timestamp

MARKETS = (("EWY", "Korea"), ("EWJ", "Japan"), ("EWT", "Taiwan"), ("EWH", "Hong Kong"), ("EWG", "Germany"))
CODES = tuple(code for code, _ in MARKETS)
BASELINE = ROOT / "config/glance_baseline.json"
THRESHOLD = 95.0  # percentile of the fund's own absolute daily moves at which a move is shown as unusual
MIN_BASELINE = 250  # completed sessions a baseline needs before it may highlight anything
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MINUS = "−"


def alpaca_snapshots(symbols):
    """One IEX snapshot request on the existing Alpaca integration; None when no credentials are configured."""
    key_id, secret_key = os.environ.get("APCA_API_KEY_ID"), os.environ.get("APCA_API_SECRET_KEY")
    if not key_id or not secret_key:
        return None
    return _alpaca_request("/v2/stocks/snapshots", {"symbols": ",".join(symbols), "feed": "iex"},
                           time.monotonic() + 20, key_id, secret_key)


def utc_now():
    return datetime.now(timezone.utc)


def resolve(now, session, checkpoint, fetch=alpaca_snapshots, clock=utc_now):
    """Each fund's current print under the Brief's own freshness rules, or None when nothing is configured.

    The snapshot's own response time is its evidence cutoff (as for the main collection, PR #50), so a fund that
    traded seconds after the run's cutoff is current, not "future". Never raises: any failure is a quiet state.
    """
    try:
        payload = fetch(CODES)
        if payload is None:
            return None
        retrieved = max(now, clock())
        missing = {}
        rows = {row["topic"]: row for row in _alpaca_intraday(
            payload, [{"symbol": code} for code in CODES], now, retrieved, "alpaca-iex", missing)}
        close = timestamp(session["close"])
        markets = {}
        for code in CODES:
            if code not in rows:
                markets[code] = dict(change_pct=None, reason=missing.get(code, "not returned"))
                continue
            row = normalize_observation(rows[code], retrieved, close if checkpoint == "CLOSE_1M" else None, close)
            usable = row["status"] in USABLE and row["value"] is not None
            markets[code] = dict(change_pct=round(row["value"], 2) if usable else None,
                                 observed_at=row["observed_at"] if usable else None,
                                 reason="" if usable else row["reason"])
        return dict(status="ok", markets=markets)
    except Exception as exc:  # noqa: BLE001 - optional data: any failure is a quiet unavailable state
        return dict(status="unavailable", markets={}, reason=type(exc).__name__)


def percentile(change_pct, history):
    """Share of the fund's historical absolute daily moves strictly smaller than this one, in percent."""
    return round(100 * bisect.bisect_left(history, abs(change_pct)) / len(history), 1)


def load_baseline(path=BASELINE):
    """{code: {"start", "end", "sessions", "abs_returns"}} for each valid fund; anything malformed is dropped."""
    try:
        markets = json.loads(path.read_text())["markets"]
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
    """From (symbol, session_date, close) rows, about three years per fund, the static baseline file."""
    series = {}
    for symbol, day, close in rows:
        if symbol in CODES:
            series.setdefault(symbol, {})[str(day)] = float(close)
    markets = {}
    for code, closes in series.items():
        days = sorted(closes)
        moves = sorted(round(abs(100 * (closes[b] / closes[a] - 1)), 4) for a, b in zip(days, days[1:]))
        if moves:
            markets[code] = dict(start=days[0], end=days[-1], abs_returns=moves)
    return dict(version=1, measure="absolute close-to-close daily percentage change", markets=markets)


def write_baseline(source_csv, destination=BASELINE):
    """`python -m market_brief glance-baseline --from-file closes.csv`: columns symbol, date, close."""
    with open(source_csv, newline="") as handle:
        rows = [(r["symbol"], r["date"], r["close"]) for r in csv.DictReader(handle)]
    destination.write_text(json.dumps(build_baseline(rows), separators=(",", ":")) + "\n")
    return destination


def signed(value):
    return f"{'+' if value > 0 else MINUS if value < 0 else ''}{abs(value):.2f}%"


def view(record, baseline=None):
    """What the template shows."""
    if record is None:
        return None
    baseline = load_baseline() if baseline is None else baseline
    items, days = [], set()
    for code, label in MARKETS:
        row = record["markets"].get(code) or {}
        if row.get("change_pct") is None:
            items.append(dict(code=code, label=label, text="—", unusual=False, when="",
                              rank=row.get("reason") or "no current print"))
            continue
        observed = timestamp(row["observed_at"]).astimezone(ET)
        days.add(observed.date())
        history = baseline.get(code)
        share = percentile(row["change_pct"], history["abs_returns"]) if history else None
        rank = ("" if share is None else "largest in its baseline" if share >= 100
                else f"larger than {share:g}% of its daily moves")
        items.append(dict(code=code, label=label, text=signed(row["change_pct"]), rank=rank,
                          unusual=share is not None and share >= THRESHOLD,
                          when=f"{observed:%-I:%M %p} ET"))
    day = max(days) if days else None
    window = (f"{min(h['start'] for h in baseline.values())} to {max(h['end'] for h in baseline.values())}"
              if baseline else "")
    return dict(status=record["status"], markets=items, threshold=f"{THRESHOLD:g}", window=window,
                session=f"{WEEKDAYS[day.weekday()]}, {day:%b} {day.day}" if day else "")

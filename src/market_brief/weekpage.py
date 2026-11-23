"""The Week Ahead page (WEEK_AHEAD, B4): one Sunday edition in the Market Brief visual system.

The page reads the accepted weekly narrative (frozen as an interpretation record, like every synthesis), the archived
session rows the week admitted, and this collection's settled supplement. Everything numeric on it is a row: the week
returns, the archived session returns, the rates path and panel, the next-week calendar. The stylesheet and theme
control are the daily brief's own, read from `templates/brief.html.j2`, so the two editions cannot drift apart.
"""

import re
from datetime import datetime

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .continuity import interpretation_record
from .evidence import PLACEHOLDER, ROOT, evidence_catalog, timestamp
from .render import (
    NOT_APPLICABLE,
    SECTOR_LABELS,
    VERDICTS,
    compact_clock,
    direction,
    formatted,
    instrument_label,
    next_update_label,
    pacific_time,
    rates_panel,
    reader_limitation,
    reader_metric_label,
    short_date,
    source_rows,
)
from .schedule import ET, VANCOUVER, VANCOUVER_LABEL, next_checkpoint

WEEK_BARS = ("SPY", "QQQ", "XLK", "XLC", "XLY", "XLF", "XLI", "XLE", "XLB", "XLV", "XLP", "XLU", "XLRE")
STRIP = ("SPY", "QQQ", "GLD")
STRIP_SECTORS = 2  # the week's two strongest and two weakest sectors join the day-by-day strip
CROSS_ASSET = ("GLD", "SLV", "GDX", "AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOG")
PATH_TENORS = (("2Y", "US 2Y", "treasury-2y"), ("10Y", "US 10Y", "treasury-10y"), ("30Y", "US 30Y", "treasury-30y"),
               ("2s10s", "US 2s10s", "treasury-2s10s"))
ENDS = {"expired": "Horizon passed", "reversed": "Broke", "dropped": "Dropped", "open": "Still open"}
STATUS_WORDS = {"new": "new", "strengthened": "strengthened", "weakened": "weakened", "reversed": "broke",
                "unresolved": "unresolved"}
CARRY_CAPTION = "Conditions to watch next week; the daily briefs do not carry them forward."
STYLE = re.compile(r"<style>(.*?)</style>", re.S)
SCRIPT = re.compile(r"<script>(.*?)</script>", re.S)


def _shell():
    """The daily brief's stylesheet and theme script, verbatim, plus the week page's own small additions."""
    text = (ROOT / "templates/brief.html.j2").read_text()
    week = (ROOT / "templates/week.css").read_text()
    return STYLE.search(text).group(1) + week, SCRIPT.search(text).group(1)


def _day_label(day):
    return datetime.fromisoformat(day).strftime("%a %-d")


def _day_head(day):
    value = datetime.fromisoformat(day)
    return dict(weekday=value.strftime("%a"), day=value.strftime("%-d"))


def _span(week):
    if not week:
        return ""
    first, last = datetime.fromisoformat(week[0]), datetime.fromisoformat(week[-1])
    tail = last.strftime("%-d, %Y") if first.month == last.month else last.strftime("%b %-d, %Y")
    return f"{first.strftime('%b %-d')}–{tail}"


def _week_name(week):
    if not week:
        return ""
    number = datetime.fromisoformat(week[0]).isocalendar()[1]
    return f"W{number:02d}"


def _hours(info):
    opening = datetime.fromisoformat(info["opens_at"]).astimezone(VANCOUVER).strftime("%-I:%M %p")
    closing = datetime.fromisoformat(info["closes_at"]).astimezone(VANCOUVER).strftime("%-I:%M %p")
    et_close = datetime.fromisoformat(info["closes_at"]).astimezone(ET).strftime("%-I:%M %p")
    if info["early_close"]:
        return f"Early close {et_close} ET · {opening}–{closing} {VANCOUVER_LABEL}"
    return f"{opening}–{closing} {VANCOUVER_LABEL}"


def week_view(packet, narrative=None, context=None, interpretation=None, repairs=None, notice=None):
    if interpretation is None:
        interpretation = interpretation_record(packet, narrative, context)
    narrative = interpretation["narrative"]
    rows = evidence_catalog(packet)
    frozen = interpretation["evidence"]
    week = packet["week"]
    sessions = week["sessions"]
    session = packet["run"]["session"]
    target = timestamp(packet["run"]["target_time"])
    records = week["records"]
    sources = {s["id"]: s for s in packet["sources"]}
    reconstructed = sorted(day for day, item in records.items() if item.get("written_by") == "reconstruction")

    def source_name(row):
        return (sources.get(row.get("source_id")) or {}).get("name") or row.get("source_id") or "source not preserved"

    def expand(text):
        def value(match):
            row = frozen.get(match[1]) or rows.get(match[1])
            return formatted(row) if row else NOT_APPLICABLE
        return PLACEHOLDER.sub(value, text or "")

    def refs(ids):
        result = []
        for ref in ids:
            row = frozen.get(ref) or rows.get(ref)
            if row is None:
                continue
            raw = rows.get(ref, {})
            when = compact_clock(row.get("observed_at") or row.get("published_at") or "")
            if raw.get("archived"):
                when = f"{when} · session record"
            label = (f"{instrument_label(row['topic'])} · {reader_metric_label(row, session)}" if row.get("topic")
                     else row.get("title") or ref)
            result.append(dict(id=ref, label=label, display=formatted(row) if "value" in row else "", when=when,
                               anchor=ref in rows))
        return result

    def paragraph(p):
        return dict(text=expand(p["text"]), refs=refs(p["evidence_ids"]),
                    uncertainty=expand(p.get("uncertainty", "")).strip(),
                    alternative=expand(p.get("alternative", "")).strip())

    cited = set()
    for record in [narrative["banner"], narrative["character"], narrative["take"], *narrative["summary"],
                   *narrative["watches"], *narrative["relationships"],
                   *[p for section in narrative["sections"].values() for p in section]]:
        cited |= set(record.get("evidence_ids", []))
        for value in record.values():
            if isinstance(value, str):
                cited |= set(PLACEHOLDER.findall(value))

    # --- the week in markets: settled week returns, participation, the day-by-day strip -----------------
    week_rows = {r["topic"]: r for r in rows.values() if r.get("metric") == "week return"}
    span = max([abs(week_rows[s]["value"]) for s in WEEK_BARS if s in week_rows] or [1.0]) or 1.0
    bars = []
    for symbol in sorted((s for s in WEEK_BARS if s in week_rows), key=lambda s: (s not in ("SPY", "QQQ"),
                                                                                   -week_rows[s]["value"])):
        row = week_rows[symbol]
        share = min(abs(row["value"]) / span, 1.0) * 50
        left = 50 if row["value"] >= 0 else 50 - share
        bars.append(dict(symbol=symbol, name=SECTOR_LABELS.get(symbol, symbol), display=formatted(row),
                         direction=direction(row), left=f"{left:.2f}", width=f"{share:.2f}",
                         anchor=symbol in ("SPY", "QQQ"), id=row["id"]))
    sectors = [s for s in SECTOR_LABELS if s in week_rows]
    up = [s for s in sectors if week_rows[s]["value"] > 0]
    ranked = sorted(sectors, key=lambda s: week_rows[s]["value"], reverse=True)
    strip_symbols = list(STRIP) + ranked[:STRIP_SECTORS] + ranked[-STRIP_SECTORS:][::-1]
    strip_symbols = list(dict.fromkeys(strip_symbols))
    strip = []
    for symbol in strip_symbols:
        cells = []
        for day in sessions:
            row = rows.get(f"{symbol}-session-{day.replace('-', '')}")
            if day not in records:
                cells.append(dict(display="no record", direction="neutral", absent=True))
            elif row is None:
                cells.append(dict(display="no print", direction="neutral", absent=True))
            else:
                cells.append(dict(display=formatted(row), direction=direction(row), absent=False,
                                  basis=row.get("basis_note")))
        week_row = week_rows.get(symbol)
        strip.append(dict(symbol=symbol, cells=cells,
                          week=dict(display=formatted(week_row), direction=direction(week_row)) if week_row
                          else dict(display=NOT_APPLICABLE, direction="neutral")))
    bases = {cell.get("basis") for line in strip for cell in line["cells"] if not cell["absent"]}
    strip_note = ("Each day is that session's own record: "
                  + ("its last regular-session trade near the close against the prior close (IEX), not an official "
                     "closing bar" if bases == {"last_trade"} else
                     "its settled daily bar" if bases == {"daily_bar"} else
                     "a settled daily bar where the record held one, else its last regular-session trade")
                  + (". The week column is the settled return over the week." if week_rows else "."))
    cross = [dict(symbol=s, label=instrument_label(s), display=formatted(week_rows[s]),
                  direction=direction(week_rows[s])) for s in CROSS_ASSET if s in week_rows]
    first_close = next((r["window_start"] for r in week_rows.values() if r.get("window_start")), None)
    last_close = next((r["observed_at"] for r in week_rows.values()), None)

    # --- rates: the week's path from the records and this collection --------------------------------------
    path = []
    first_day = next((d for d in sessions if d in records), None)
    for label, topic, ident in PATH_TENORS:
        start = next((r for r in rows.values() if (r.get("lineage") or {}).get("original_id") == ident
                      and r["lineage"]["session_date"] == first_day and not r.get("frozen")), None)
        end = rows.get(ident)
        change = None
        if start and end and start.get("value") is not None and end.get("value") is not None:
            factor = 100 if end["unit"] == "% yield" else 1
            change = round((end["value"] - start["value"]) * factor)
        path.append(dict(label=label, start=formatted(start) if start else NOT_APPLICABLE,
                         start_date=short_date(start["observed_at"]) if start else "",
                         end=formatted(end) if end else NOT_APPLICABLE,
                         end_date=short_date(end["observed_at"]) if end else "",
                         change=(f"{'+' if change > 0 else '−' if change < 0 else ''}{abs(change)} bp"
                                 if change is not None else NOT_APPLICABLE),
                         direction="neutral" if not change else "positive" if change > 0 else "negative"))
    panel = rates_panel(packet, rows)

    # --- relationships and watches the week tested (archived, interpretation) ------------------------------
    note = week["note"]
    relationship_trail = []
    for relationship in note.get("relationships", []):
        steps = [dict(day=_day_label(st["day"]), status=st["status"], word=STATUS_WORDS.get(st["status"], st["status"]))
                 for st in relationship["statuses"]]
        relationship_trail.append(dict(statement=expand(relationship["statement"]), steps=steps,
                                       last=steps[-1]["word"] if steps else "no verdict this week"))
    tally = {}
    for watch in note.get("watches", []):
        verdicts = [v["status"] for v in watch.get("verdicts", [])]
        final = next((v for v in reversed(verdicts) if v in VERDICTS), None)
        key = final or ("unresolved" if verdicts else "no verdict")
        tally[key] = tally.get(key, 0) + 1
    by_day = {}
    for watch in note.get("watches", []):
        by_day.setdefault(watch["first"], []).append(dict(
            hypothesis=expand(watch["hypothesis"]), end=ENDS.get(watch["end"], watch["end"]),
            verdicts=", ".join(f"{v['status']} ({_day_label(v['day'])})" for v in watch.get("verdicts", []))))
    reads = [dict(day=short_date(s_["date"]), read=s_["read"], watches=by_day.get(s_["date"], []))
             for s_ in note.get("sessions", [])]
    watches_written = len(note.get("watches", []))

    # --- next week ---------------------------------------------------------------------------------------
    events = sorted(packet["events"], key=lambda e: e.get("scheduled_at") or "")
    calendar = []
    for info in week["calendar"]:
        day_events = [dict(title=e["title"], time=pacific_time(e["scheduled_at"]),
                           source=source_name(e))
                      for e in events if e.get("session_date") == info["date"]]
        calendar.append(dict(day=short_date(info["date"]), open=info["open"],
                             hours=_hours(info) if info["open"] else "NYSE closed",
                             early=info.get("early_close", False), events=day_events))
    calendar_sources = [s for s in packet["sources"] if s.get("kind") == "calendar"]
    calendar_note = "; ".join(f"{s['name']}: {s.get('reason') or s['status'].lower()}" for s in calendar_sources
                              if s["status"] not in ("AVAILABLE", "DEGRADED"))

    # --- next-week watches -------------------------------------------------------------------------------
    horizons = interpretation.get("horizons", [])
    next_watches = [dict(question=expand(w["condition"]), confirmation=expand(w["confirmation"]),
                         contradiction=expand(w["contradiction"]),
                         horizon=(horizons[i]["phrase"] if i < len(horizons) else w["horizon"]),
                         refs=refs(w["evidence_ids"])) for i, w in enumerate(narrative["watches"])]

    # --- provenance --------------------------------------------------------------------------------------
    ledger_ids = sorted(cited | {r["id"] for r in week_rows.values()}
                        | {r["id"] for r in rows.values() if r.get("archived") and r.get("metric") == "session return"
                           and r["topic"] in strip_symbols})
    ledger = []
    for ident in ledger_ids:
        row = rows.get(ident)
        if not row or "value" not in row:
            continue
        lineage = row.get("lineage") or {}
        ledger.append(dict(id=ident, label=f"{instrument_label(row.get('topic', ''))} · "
                                           f"{reader_metric_label(row, session)}",
                           display=formatted(row), when=compact_clock(row.get("observed_at") or ""),
                           source=source_name(row), baseline=row.get("baseline") or "",
                           record=(f"session record {short_date(lineage['session_date'])} · run "
                                   f"{lineage['run_id']}" if lineage else "")))
    archived_count = sum(1 for r in rows.values() if r.get("archived"))
    limitations = [reader_limitation(text) for text in packet["coverage"]["limitations"]]
    limitations += [g for g in week.get("gaps", []) if "week return" in g]
    next_info = next_checkpoint(target, "WEEK_AHEAD")
    restore_line = dict(name="Session records", status_label=f"{len(records)} of {len(sessions)} sessions",
                        detail="restored from this repository's close-run artifacts; each row above keeps its own "
                               "source" + (" · reconstructed offline from the archived close artifacts for "
                                           + ", ".join(short_date(d) for d in reconstructed) if reconstructed else ""))
    style, script = _shell()
    banner = narrative["banner"]
    experiment = bool(packet["run"].get("experiment"))
    return dict(
        style=style, script=script, mode=packet["run"]["mode"], experiment=experiment, notice=notice,
        reconstructed=[short_date(d) for d in reconstructed],
        sample=packet["run"]["mode"] == "SAMPLE",
        session_date=session["date"], evidence_cutoff=packet["run"]["target_time"],
        title=expand(banner["title"]), label=banner["label"], limitation=expand(banner.get("limitation", "")),
        banner_refs=refs(banner["evidence_ids"]),
        week_name=_week_name(sessions), week_span=_span(sessions), published_on=short_date(session["date"]),
        clocks=[("Week", f"{_span(sessions)} · {len(sessions)} sessions, {len(records)} with records"),
                ("Prices", f"settled close {short_date(last_close)}" if last_close else "settled closes unavailable"),
                ("Analysis", f"{pacific_time(packet['run']['target_time'], True)} · week ahead"),
                ("Next", next_update_label(next_info, session["date"]))],
        next_at=next_info["scheduled_at"],
        character=dict(text=expand(narrative["character"]["text"]), refs=refs(narrative["character"]["evidence_ids"])),
        summary=[paragraph(p) for p in narrative["summary"]],
        take=dict(text=expand(narrative["take"]["text"]), refs=refs(narrative["take"]["evidence_ids"]))
        if narrative["take"]["text"].strip() else None,
        sections={k: [paragraph(p) for p in v] for k, v in narrative["sections"].items()},
        relationships=[dict(statement=expand(r["statement"]), instruments=", ".join(r["instruments"]),
                            reason=expand(r.get("reason", "")), refs=refs(r["evidence_ids"]))
                       for r in narrative["relationships"]],
        bars=bars, sectors_up=len(up), sectors=len(sectors), strip=strip, strip_days=[_day_head(d) for d in sessions],
        week_missing=("" if week_rows else "Settled week returns were not collected this run ("
                      + ((sources.get("alpaca-daily") or {}).get("reason") or "daily bars unavailable") + ")."),
        strip_note=strip_note, cross=cross,
        week_window=(f"Settled closes, {short_date(first_close)} to {short_date(last_close)}"
                     if first_close and last_close else ""),
        path=path, path_note=("Start is the official curve the week's first session record held; end is this "
                              "collection's latest official curve. Treasury's daily par curve, not intraday quotes."),
        panel=panel, trail=relationship_trail, tally=sorted(tally.items(), key=lambda kv: -kv[1]),
        watches_written=watches_written, reads=reads,
        calendar=calendar, calendar_note=calendar_note, next_watches=next_watches, carry_caption=CARRY_CAPTION,
        limitations=limitations, sources=source_rows([s for s in packet["sources"] if not s.get("archived")]),
        archived_sources=[s for s in packet["sources"] if s.get("archived")], restore=restore_line,
        ledger=ledger, archived_count=archived_count,
        technical=dict(run_id=packet["run"].get("run_id"), cutoff=packet["run"]["target_time"],
                       records=records, missing=week["missing"], interpretation=interpretation["origin"].get("run_id"),
                       repairs=repairs or []),
    )


def render_week(packet, narrative=None, context=None, interpretation=None, repairs=None, notice=None):
    """`notice` is an explicit label a demonstration must carry (never set by the production pipeline)."""
    view = week_view(packet, narrative, context, interpretation, repairs, notice)
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(default=True))
    page = env.get_template("week.html.j2").render(**view)
    return markdown(view), page


def markdown(view):
    lines = [f"# {view['title']}", "", f"Week ahead · {view['week_span']} · {view['label']}", ""]
    for p in view["summary"]:
        lines += [p["text"], ""]
    if view["take"]:
        lines += [f"**The take.** {view['take']['text']}", ""]
    lines += ["## The week in markets", ""]
    lines += [f"- {b['name']} ({b['symbol']}): {b['display']}" for b in view["bars"]]
    lines += ["", "## Next week", ""]
    for day in view["calendar"]:
        lines.append(f"- {day['day']}: {day['hours']}" + "".join(f"; {e['title']} {e['time']}" for e in day["events"]))
    lines += ["", "## What to watch next week", "", view["carry_caption"], ""]
    for w in view["next_watches"]:
        lines += [f"- {w['question']} (confirms: {w['confirmation']}; changes it: {w['contradiction']}; "
                  f"{w['horizon']})"]
    return "\n".join(lines) + "\n"

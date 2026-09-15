"""One presentation model, two local formats. Model prose cannot replace fact rows.

Order: header → headline / character / short executive read → compact snapshot → what changed →
what matters next → equity interpretation and support → macro interpretation and rates → sector
view → cross-asset structure → collapsed sources & coverage. Deterministic rows are the record;
the analyst's interpretation sits above them and is labeled once.

Two clocks. The interpretation (headline, character, read, what changed, watches, section
paragraphs, flagged reasons) is rendered from a frozen interpretation record at the values the analyst
saw; the observed record (figures, tables, events, sources, ledger) is this run's. A synthesis edition
freezes its own record, so both clocks coincide; a deterministic refresh carries the record forward.
"""

import html
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .continuity import interpretation_record
from .evidence import PLACEHOLDER, ROOT, USABLE, evidence_catalog, timestamp
from .metrics import rank_by_spread
from .schedule import CHECKPOINT_KINDS, checkpoint_kind, next_checkpoint

PACIFIC = ZoneInfo("America/Vancouver")
DISPLAY_STATUSES = {"LIVE", "LIVE COMMISSIONING", "LAST GOOD BRIEF", "SAMPLE"}
EDITION_LABELS = {"PREMARKET": "Premarket edition", "OPEN_1M": "Opening refresh",
                  "OPEN_30M": "Opening structure edition", "HOURLY_0800": "Hourly refresh",
                  "HOURLY_0900": "Hourly refresh", "HOURLY_1000": "Hourly refresh", "HOURLY_1100": "Hourly refresh",
                  "HOURLY_1200": "Hourly refresh", "CLOSE_1M": "Close snapshot"}
PHASE_PHRASES = {"PREMARKET": "before the open", "OPEN_1M": "in the opening minutes",
                 "OPEN_30M": "during the morning session", "HOURLY_0800": "during the morning session",
                 "HOURLY_0900": "during the morning session", "HOURLY_1000": "during the morning session",
                 "HOURLY_1100": "during the morning session", "HOURLY_1200": "during the afternoon session",
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
                        "premarket return": "Premarket vs prior close", "intraday return": "Intraday vs prior close",
                        "regular close": "Regular close"}
SOURCE_KIND_LABELS = {"price": "prices", "quote": "current prints", "economic_series": "rates",
                      "calendar": "release calendar", "news": "releases"}
SOURCE_STATUS_LABELS = {"AVAILABLE": "Available", "UNAVAILABLE": "Unavailable", "DEGRADED": "Degraded",
                        "STALE": "Stale", "DELAYED": "Delayed"}
SIGNED_METRICS = {"daily return", "daily yield change", "premarket return", "intraday return", "distance from 50DMA"}
# Absence vocabulary. `no print`: the current observation is missing while useful history exists.
# `—`: structurally not applicable. `not collected`: the source or input is not automated.
NO_PRINT = "no print"
NOT_APPLICABLE = "—"
NOT_COLLECTED = "not collected"
# Rows whose print is this far behind the table's shared clock carry their own clock.
SHARED_CLOCK_TOLERANCE = timedelta(minutes=5)
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


def measure_label(row):
    metric = row["metric"]
    if row.get("frequency") == "intraday":
        return f"{row['topic']} · Intraday vs prior close"
    if metric.startswith("relative to "):
        return f"{row['topic']} vs {metric.removeprefix('relative to ')} · 20s"
    horizon = HORIZON_LABELS.get(metric, metric)
    return f"{row['topic']} · {horizon}"


def metric_label(row):
    if row.get("frequency") == "intraday":
        return "intraday vs prior close"
    return row["metric"]


def reader_metric_label(row):
    """`twenty-session return` → `20-session return`; `relative to SPY` → `vs SPY · 20 sessions`."""
    metric = row.get("metric", "")
    if metric.startswith("relative to "):
        return f"vs {metric.removeprefix('relative to ')} · 20 sessions"
    if row.get("frequency") == "intraday" and metric not in READER_METRIC_LABELS:
        return "Intraday vs prior close"
    return READER_METRIC_LABELS.get(metric, metric[:1].upper() + metric[1:] if metric else "")


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


def formatted(row):
    if row.get("value") is None:
        return NO_PRINT
    value = row["value"]
    signed = row["unit"] in {"bp", "pp", "%"}
    # A value that rounds to zero is shown as zero: no sign, no colour.
    number = "0.00" if signed and round(value, 2) == 0 else f"{value:+.2f}" if signed else f"{value:.2f}"
    return f"{number} {row['unit']}"


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


def change_column(rows, allow_daily_today=True):
    """Settle one table's change column: one shared intraday clock with per-row exceptions, or one daily date.

    Intraday mode applies as soon as any row has a usable print: the caption carries the latest print's
    clock, a row whose print is older than the tolerance carries its own clock, and a row without a print
    says so. Without any print the column is the dated daily return. Mixed columns never happen.
    """
    printed = [row for row in rows if row["intraday"]["id"]]
    if printed:
        provisional = all(row["intraday"]["status"] == "PROVISIONAL" for row in printed)
        label = "Session-ending print vs prior close · provisional" if provisional else "Intraday vs prior close"
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
    """Maturity, yield, daily change in bp; a change pairs only with a same-dated, same-source yield."""
    result = []
    for term in ("2Y", "5Y", "10Y", "30Y"):
        topic = f"US {term}"
        level = next((r for r in facts if r["topic"] == topic and r["metric"] == "daily par yield"), None)
        change = next((r for r in facts if r["topic"] == topic and r["metric"] == "daily yield change"), None)
        if not level and not change:
            continue
        paired = bool(level and change and level["observed_at"] == change["observed_at"]
                      and level["source_id"] == change["source_id"])
        anchor = level or change
        result.append(dict(maturity=term, level=cell(level, absent=NO_PRINT), change=cell(change if paired else None),
                           change_note="" if paired or not change else
                           f"change dated {short_date(change['observed_at'])} not paired",
                           date=short_date(anchor["observed_at"]), observed_at=anchor["observed_at"],
                           ids=[r["id"] for r in (level, change) if r]))
    dates = {row["observed_at"] for row in result}
    asof = short_date(next(iter(dates))) if len(dates) == 1 else None
    if asof:
        for row in result:
            row["date"] = ""
    return result, asof


def next_update_label(info, session_date):
    """`Next update · 10:00 AM PT`, naming an interpretation or the close snapshot, or the next session."""
    clock = pacific_time(info["scheduled_at"])
    if info["session_date"] != session_date:
        return f"Next update · {short_date(info['session_date'])} · {clock} premarket"
    if info["kind"] == "synthesis":
        return f"Next update · {clock} · interpretation"
    if info["kind"] == "close":
        return f"Next update · {clock} · close snapshot"
    return f"Next update · {clock}"


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
    session_date = packet["run"]["session"]["date"]

    def expand(text):
        """Numeric placeholders resolve against the frozen rows; a reference the record does not hold
        (a carried criterion older than the record) falls back to this run's row, then to a dash.
        Rendering can never abort an accepted run."""
        def value(match):
            row = frozen.get(match[1]) or catalog.get(match[1])
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
                label = f"{instrument_label(row['topic'])} · {reader_metric_label(row)}"
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
        return {**p, "text": expand(p["text"]), "uncertainty": expand(p["uncertainty"]),
                "alternative": expand(p["alternative"]),
                "context": " ".join(filter(None, (expand(p["uncertainty"]), expand(p["alternative"])))),
                "refs": refs(p["evidence_ids"])}

    facts = []
    for row in [*packet["observations"], *packet["derived"]]:
        if row["status"] not in USABLE or row["metric"] in {"regular close"}:
            continue
        facts.append({**row, "display": formatted(row), "direction": direction(row),
                      "measure": measure_label(row), "observed_label": observed_label(row["observed_at"])})
    history_symbols = {h["symbol"] for h in packet["history"]}
    equity = [r for r in facts if r["topic"] in history_symbols or r["frequency"] == "intraday"]
    treasuries = [r for r in facts if r["topic"].startswith("US ") and r["metric"] in
                  {"daily par yield", "daily yield change"}]
    other_macro = [r for r in facts if r not in equity and r not in treasuries]

    def current_or_daily(symbol):
        return next((i for i in (f"{symbol}-intraday", f"{symbol}-daily") if i in catalog), None)

    def figure_clock(row):
        """A figure carries its own clock only when it is dated or trails the page's data clock."""
        observed = row["observed_at"]
        if "T" not in observed:
            return compact_clock(observed)
        behind = timestamp(actual_started_at) - timestamp(observed)
        return compact_clock(observed) if behind > SHARED_CLOCK_TOLERANCE else ""

    current_sectors = sorted((r for r in facts if r["frequency"] == "intraday" and r["topic"] in SECTORS),
                             key=lambda r: abs(r["value"]), reverse=True)
    priority = [current_or_daily("SPY"), current_or_daily("QQQ"),
                current_sectors[0]["id"] if current_sectors else None, current_or_daily("GLD"),
                "treasury-2y-change", "treasury-10y-change"]
    chips = [dict(catalog[i], display=formatted(catalog[i]), direction=direction(catalog[i]),
                  metric_label=metric_label(catalog[i]),
                  observed_label=observed_label(catalog[i]["observed_at"]),
                  observed_short=figure_clock(catalog[i]))
             for i in dict.fromkeys(priority) if i and i in catalog][:6]
    if not chips:
        chips = [dict(row, metric_label=metric_label(row), observed_short=figure_clock(row))
                 for row in facts[:4]]
    # The page shows three figures: the first three of the same deterministic priority order
    # (SPY, QQQ, then the leading current sector print when one exists, else GLD). No new ranking.
    figures = chips[:3]
    # After a close whose daily bar is not yet published, the retained daily return is two
    # sessions old relative to the completed session and must not pose as today.
    daily_today = not packet.get("history_lag")

    # Tables: mega-caps, sectors ranked by the labeled twenty-session spread, metals structure.
    mega_rows = compact_equity_rows(facts, MEGACAPS, daily_today)
    mega_change, mega_asof = change_column(mega_rows, daily_today)
    sector_rows = rank_by_spread(compact_equity_rows(facts, list(SECTOR_LABELS), daily_today), list(SECTOR_LABELS))
    sector_change, sector_asof = change_column(sector_rows, daily_today)
    metal_rows = compact_equity_rows(facts, METALS, daily_today)
    metal_change, metal_asof = change_column(metal_rows, daily_today)
    yields, yields_asof = treasury_rows(treasuries)

    # What matters next: watches with their frozen horizons, carried watches, attention, events.
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
        if update is None and watch["lifecycle"] != "active":
            continue  # a horizon that passed without reassessment is history, not a live item
        expires = watch["horizon"].get("expires_at")
        carried_watches.append(dict(
            id=watch["id"], hypothesis=expand(watch["hypothesis"]),
            phrase=watch["horizon"].get("phrase", ""), lifecycle=watch["lifecycle"],
            expired=watch["lifecycle"] == "expired" or (bool(expires) and timestamp(expires) <= target),
            evaluability=watch["evaluability"],
            assessment=update["assessment"] if update else "not reassessed",
            reason=expand(update["reason"]) if update else "",
            evidence_ids=update["evidence_ids"] if update else watch["evidence_refs"],
            refs=refs(update["evidence_ids"] if update else watch["evidence_refs"])))
    attention_why = {item["id"]: item["why"] for item in narrative.get("attention", [])}
    attention = [{**a, "why": attention_why.get(a["id"], ""), "trigger": trigger_tag(a["reason"]),
                  "display_symbol": (f"{SECTOR_LABELS[a['symbol']]} · {a['symbol']}"
                                     if a["symbol"] in SECTOR_LABELS else a["symbol"]),
                  "date_label": short_date(a.get("date")), "refs": refs(a["evidence_ids"])}
                 for a in interpretation["attention"]]
    events = [{**event, "scheduled_label": pacific_time(event["scheduled_at"], True),
               "relation_label": event.get("session_relation", "").lower(), "refs": refs([event["id"]], catalog)}
              for event in packet["events"][:4]]

    # What changed: the analyst's interpretation of deterministic changed comparisons, frozen with it.
    anchors = prior.get("anchors", {}) if available else {}
    continuity = interpretation["continuity"]
    since_entries = [dict(kind="change", text=expand(c["text"]), evidence_ids=c["evidence_ids"],
                          refs=refs(c["evidence_ids"]))
                     for c in narrative.get("changes", [])]
    for r in narrative.get("relationships", []):
        if r["carried_id"] is not None:
            since_entries.append(dict(kind="relationship", text=expand(r["statement"]), assessment=r["assessment"],
                                    reason=expand(r["reason"]), evidence_ids=r["evidence_ids"],
                                    refs=refs(r["evidence_ids"])))
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

    cuttingboard_visible = (packet["cuttingboard"].get("status") == "AVAILABLE"
                            or bool(narrative["sections"]["cuttingboard"]))
    omitted = []
    if not mega_rows and not narrative["sections"]["equities"] and not equity:
        omitted.append("Equity structure")
    if not yields and not other_macro and not narrative["sections"]["macro"]:
        omitted.append("Macro & rates")
    if not sector_rows:
        omitted.append("Sector view")
    if not metal_rows:
        omitted.append("Cross-asset structure")
    if not cuttingboard_visible:
        omitted.append("Cuttingboard context")
    limitations = list(packet["coverage"]["limitations"])
    if omitted:
        limitations.append("No admitted material for: " + ", ".join(omitted))
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
        cuttingboard={key: packet["cuttingboard"].get(key) for key in
                      ("status", "generated_at", "captured_at", "schema_version")
                      if packet["cuttingboard"].get(key)},
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
                          metric_label=(reader_metric_label(r) if r.get("metric") else "Published / scheduled item"),
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
    # Two clocks: when the interpretation was made and when the observed record was collected.
    data_clock = pacific_time(actual_started_at)
    interpretation_clock = pacific_time(interpreted["target_time"])
    next_label = next_update_label(next_checkpoint(target, checkpoint), session_date)
    clocks = (f"Interpretation as of {interpretation_clock} · Data as of {data_clock}" if carried
              else f"As of {data_clock}") + f" · {next_label}"
    return dict(
        mode=packet["run"]["mode"], status=status, commissioning=commissioning,
        live_commissioning=live_commissioning, checkpoint=checkpoint, kind=kind, carried=carried,
        # The scheduler's idempotency marker; a commissioning run never claims a scheduled slot.
        marker_checkpoint="COMMISSIONING" if packet["run"].get("commissioning") else checkpoint,
        edition_label=EDITION_LABELS.get(checkpoint, "Market edition"),
        session=packet["run"]["session"], target=packet["run"]["target_time"],
        actual_started_at=actual_started_at,
        status_line=f"{status} · {EDITION_LABELS.get(checkpoint, 'Market edition')} · "
                    f"{pacific_time(actual_started_at, True).split(' · ')[0]}",
        clocks=clocks, data_clock=data_clock, interpretation_clock=interpretation_clock, next_update=next_label,
        truth=truth,
        technical=technical, coverage=packet["coverage"], limitations=limitations,
        banner={**narrative["banner"], "title": expand(narrative["banner"]["title"]),
                "limitation": expand(narrative["banner"]["limitation"]),
                "refs": refs(narrative["banner"]["evidence_ids"])},
        character=expand(narrative["character"]["text"]),
        character_ids=narrative["character"]["evidence_ids"],
        character_refs=refs(narrative["character"]["evidence_ids"]),
        chips=chips, figures=figures,
        summary=[paragraph(p) for p in narrative["summary"]],
        since=dict(heading="What changed", label=since_caption(anchors), entries=since_entries, note=since_note,
                   status=prior.get("status", "cold_start")),
        next=dict(watches=watches, carried=carried_watches, attention=attention, events=events,
                  paragraphs=[paragraph(p) for key in ("attention", "events") for p in narrative["sections"][key]]),
        equities=dict(paragraphs=[paragraph(p) for p in narrative["sections"]["equities"]],
                      rows=mega_rows, change_label=mega_change, asof=mega_asof, proof=proof(mega_rows),
                      spread_label="20-session return spread vs QQQ",
                      lookback={s: v for s, v in packet.get("lookback", {}).items() if v["r20"] != "available"}),
        macro=dict(paragraphs=[paragraph(p) for p in narrative["sections"]["macro"]],
                   yields=yields, yields_asof=yields_asof, facts=other_macro,
                   yields_proof=refs([i for row in yields for i in row["ids"]], catalog),
                   facts_proof=refs([row["id"] for row in other_macro], catalog)),
        sectors=dict(rows=sector_rows, change_label=sector_change, asof=sector_asof, proof=proof(sector_rows),
                     spread_label="20-session return spread vs SPY, strongest to weakest"),
        cross_asset=dict(rows=metal_rows, change_label=metal_change, asof=metal_asof, proof=proof(metal_rows),
                         spread_label="20-session return spread, "
                         + ", ".join(f"{row['symbol']} vs {row['relative_label']}" for row in metal_rows
                                     if row["relative"]["id"])),
        cuttingboard_section=dict(visible=cuttingboard_visible,
                                  paragraphs=[paragraph(p) for p in narrative["sections"]["cuttingboard"]]),
        sources=sources, unavailable_sources=sum(1 for s in sources if s["unavailable"]),
        cuttingboard=packet["cuttingboard"],
        evidence=evidence_rows, evidence_groups=evidence_groups, context_items=packet["context_items"])


def markdown(view):
    def esc(value):
        text = " ".join(str(value).split())
        return re.sub(r"([\\`*_\[\]|])", r"\\\1", html.escape(text))

    def refs(ids):
        return " ".join(f"[evidence](#evidence-{i})" for i in ids)

    def para(p):
        text = f"{esc(p['text'])} {refs(p['evidence_ids'])}"
        if p["context"]:
            text += f" {esc(p['context'])}"
        return text

    lines = [f"# {esc(view['banner']['title'])}", "", esc(view["status_line"]), esc(view["clocks"])]
    if view["truth"]:
        lines.append(f"> {esc(view['truth'])}")
    lines += ["", f"**INTERPRETATION — {view['banner']['label']}** · {esc(view['character'])} "
              f"{refs(view['character_ids'])}", "", esc(view["banner"]["limitation"]), ""]
    for p in view["summary"]:
        lines += [para(p), ""]
    if view["figures"]:
        lines += ["**OBSERVED SNAPSHOT**", "", "| Measure | Observation | As of |", "|---|---:|---|"]
        for chip in view["figures"]:
            lines.append(f"| {esc(chip['topic'])} · {esc(chip['metric_label'])} | {chip['display']} | "
                         f"{esc(chip['observed_label'])} · {chip['status']} {refs([chip['id']])} |")
        lines.append("")
    if view["coverage"]["missing_domains"]:
        lines += [f"Missing: {esc(' · '.join(view['coverage']['missing_domains']))}.", ""]
    since = view["since"]
    if since["entries"] or since["note"]:
        lines += [f"## {since['heading']}" + (f" · {esc(since['label'])}" if since["label"] else ""), ""]
        for item in since["entries"]:
            if item["kind"] == "change":
                lines.append(f"- {esc(item['text'])} {refs(item['evidence_ids'])}")
            else:
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
        lines.append(f"- **CARRIED WATCH · {c['assessment']} · {esc(c['phrase'])}{passed}** — {esc(c['hypothesis'])} "
                     f"{esc(c['reason'])}")
    for a in nxt["attention"]:
        lines.append(f"- **{esc(a['display_symbol'])}** — {esc(a['why'] or a['reason'])} "
                     f"({esc(a['trigger'])} · {esc(a['date_label'])}) {refs(a['evidence_ids'])}")
    for e in nxt["events"]:
        lines.append(f"- **Event** — {esc(e['title'])} · {esc(e['scheduled_label'])} · {esc(e['relation_label'])} "
                     f"{refs([e['id']])}")
    lines.append("")
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
    mac = view["macro"]
    if mac["paragraphs"] or mac["yields"] or mac["facts"]:
        lines += ["## Macro & rates", ""]
        for p in mac["paragraphs"]:
            lines += [para(p), ""]
        if mac["yields"]:
            asof = f" · {esc(mac['yields_asof'])}" if mac["yields_asof"] else ""
            lines += [f"**TREASURY PAR YIELDS**{asof}", "", "| Maturity | Yield | Daily change | Date |",
                      "|---|---:|---:|---|"]
            for row in mac["yields"]:
                note = f" ({esc(row['change_note'])})" if row["change_note"] else ""
                lines.append(f"| {row['maturity']} | {row['level']['display']} | {row['change']['display']}{note} | "
                             f"{esc(row['date'])} |")
            lines += ["", LEDGER_NOTE, ""]
        if mac["facts"]:
            lines += ["| Measure | Observation | Date / source |", "|---|---:|---|"]
            for row in mac["facts"]:
                lines.append(f"| {esc(row['measure'])} | {row['display']} | "
                             f"{row['observed_label']} · {row['status']} |")
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
    cross = view["cross_asset"]
    if cross["rows"]:
        asof = f" · {esc(cross['asof'])}" if cross["asof"] else ""
        lines += ["## Cross-asset structure", "",
                  f"**METALS STRUCTURE** · {esc(cross['spread_label'])} · {esc(cross['change_label'])}{asof}", "",
                  "| Instrument | Change | 20D | Spread | vs 50DMA |",
                  "|---|---:|---:|---:|---:|"]
        for row in cross["rows"]:
            spread = (f"{row['relative']['display']} vs {row['relative_label']}" if row["relative"]["id"]
                      else row["relative"]["display"])
            lines.append(f"| {esc(row['label'])} ({row['symbol']}) | {row['today']['display']} | "
                         f"{row['r20']['display']} | {spread} | {row['dma']['display']} |")
        lines += ["", LEDGER_NOTE, ""]
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
    lines += ["## Sources & coverage", "", esc(view["banner"]["limitation"]), ""]
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
              (f"Cuttingboard: generated {esc(technical['cuttingboard'].get('generated_at'))}; "
               f"captured {esc(technical['cuttingboard'].get('captured_at'))}; "
               f"schema {esc(technical['cuttingboard'].get('schema_version'))}"
               if technical["cuttingboard"] else ""), "",
              "Model-assisted interpretation; factual rows are deterministic.", ""]
    return "\n".join(lines)


def render(packet, narrative=None, context=None, interpretation=None):
    """Render one edition. A synthesis edition passes its narrative (and freezes it on the way); a
    deterministic refresh passes the frozen `interpretation` record it carries."""
    view = presentation(packet, narrative, context, interpretation)
    env = Environment(loader=FileSystemLoader(ROOT / "templates"),
                      autoescape=select_autoescape(default=True))
    return markdown(view), env.get_template("brief.html.j2").render(**view)

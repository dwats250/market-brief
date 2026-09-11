"""One presentation model, two local formats. Model prose cannot replace fact rows.

Order: header → headline / character / short executive read → compact snapshot → what changed →
what matters next → equity interpretation and support → macro interpretation and rates → sector
view → cross-asset structure → collapsed sources & coverage. Deterministic rows are the record;
the analyst's interpretation sits above them and is labeled once.
"""

import html
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .continuity import prior_values, resolve_horizon
from .evidence import ROOT, USABLE, evidence_catalog, timestamp
from .metrics import rank_by_spread
from .synthesize import TOKEN

PACIFIC = ZoneInfo("America/Vancouver")
DISPLAY_STATUSES = {"LIVE", "LIVE COMMISSIONING", "LAST GOOD BRIEF", "SAMPLE"}
CHECKPOINT_LABELS = {"PREMARKET": "PREMARKET", "OPEN_1M": "OPEN +1M", "OPEN_30M": "OPENING STRUCTURE",
                     "AFTERNOON": "AFTERNOON", "CLOSE_1M": "CLOSE +1M"}
EDITION_LABELS = {"PREMARKET": "Pre-market edition", "OPEN_1M": "Open +1M edition",
                  "OPEN_30M": "Opening structure edition", "AFTERNOON": "Afternoon edition",
                  "CLOSE_1M": "Close +1M edition"}
PHASE_PHRASES = {"PREMARKET": "before the open", "OPEN_1M": "in the opening minutes",
                 "OPEN_30M": "during the morning session", "AFTERNOON": "during the afternoon session",
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
LEDGER_NOTE = "Every cell is a ledger row; see the evidence ledger for exact values and baselines."
SIGNED_METRICS = {"daily return", "daily yield change", "premarket return", "intraday return", "distance from 50DMA"}


def pacific_time(value, include_date=False):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(PACIFIC)
    date_part = dt.strftime("%A, %b %-d · ") if include_date else ""
    return f"{date_part}{dt.strftime('%-I:%M %p')} PT"


def short_date(value):
    """`2026-09-04` → `Fri, Sep 4`; an intraday clock keeps its Pacific time."""
    if not isinstance(value, str):
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


def observed_label(value):
    if isinstance(value, str) and "T" in value:
        return pacific_time(value, True)
    return value


def compact_clock(value):
    """Reader-facing clock above the audit layer: `Thu, Sep 10` for a dated row, `1:02 PM PT` for a print."""
    if not isinstance(value, str) or not value:
        return ""
    return pacific_time(value) if "T" in value else short_date(value)


def change_head(label):
    """Short column header for the change column; the full label sits in the table caption."""
    for prefix, head in (("Daily", "Daily"), ("Session-ending", "Session end"), ("Intraday", "Intraday")):
        if label.startswith(prefix):
            return head
    return "Change"


def direction(row):
    if row.get("metric") not in SIGNED_METRICS and not row.get("metric", "").startswith("relative to "):
        return "neutral"
    value = row.get("value")
    if not isinstance(value, (int, float)) or value == 0:
        return "neutral"
    return "positive" if value > 0 else "negative"


def formatted(row):
    if row.get("value") is None:
        return "Unavailable"
    value = row["value"]
    signed = row["unit"] in {"bp", "pp", "%"}
    number = f"{value:+.2f}" if signed else f"{value:.2f}"
    return f"{number} {row['unit']}"


def cell(row, bare=False):
    return dict(display=(formatted(row).removesuffix(" USD") if bare else formatted(row)) if row else "n/a",
                value=row.get("value") if row else None,
                direction=direction(row) if row else "neutral",
                id=row.get("id") if row else None, status=row.get("status") if row else None,
                observed=observed_label(row.get("observed_at")) if row else "",
                observed_at=row.get("observed_at") if row else None)


def compact_equity_rows(rows, symbols, allow_daily_today=True):
    """One row per instrument: current-or-daily change, 20s return, labeled spread, 50DMA distance."""
    result = []
    for symbol in symbols:
        symbol_rows = [row for row in rows if row["topic"] == symbol]
        if not symbol_rows:
            continue
        current = next((row for row in symbol_rows
                        if row.get("frequency") == "intraday" and row["status"] in USABLE), None)
        daily = next((row for row in symbol_rows if row.get("metric") == "daily return"), None)
        if allow_daily_today:
            current = current or daily
        r20 = next((row for row in symbol_rows if row.get("metric") == "twenty-session return"), None)
        relative = next((row for row in symbol_rows
                         if row.get("metric", "").startswith("relative to ")), None)
        average = next((row for row in symbol_rows if row.get("metric") == "fifty-session average"), None)
        distance = next((row for row in symbol_rows if row.get("metric") == "distance from 50DMA"), None)
        result.append(dict(symbol=symbol, label=SECTOR_LABELS.get(symbol) or INSTRUMENT_LABELS.get(symbol, symbol),
                           today=cell(current), daily=cell(daily), r20=cell(r20), relative=cell(relative),
                           relative_label=(relative.get("metric", "").removeprefix("relative to ")
                                           if relative else "benchmark"),
                           average=cell(average, bare=True), dma=cell(distance),
                           current_is_intraday=bool(current and current.get("frequency") == "intraday")))
    return result


def table_asof(observed_at_values, tolerance_minutes=5):
    """One table-level clock when every row's intraday print is within a few minutes."""
    clocks = []
    for value in observed_at_values:
        if not (isinstance(value, str) and "T" in value):
            return None
        clocks.append(timestamp(value))
    if not clocks or (max(clocks) - min(clocks)).total_seconds() > tolerance_minutes * 60:
        return None
    return f"as of {pacific_time(max(clocks).isoformat())}"


def collapse_shared_clock(rows):
    asof = table_asof([row["today"]["observed_at"] for row in rows]) if rows else None
    if asof:
        for row in rows:
            row["today"]["observed"] = ""
    return asof


def change_column(rows):
    """Label the change column by what it is: a shared intraday clock or a dated daily return."""
    filled = [row for row in rows if row["today"]["id"]]
    if not filled:
        return "Change", None
    if all(row["current_is_intraday"] for row in filled):
        label = ("Session-ending print vs prior close · provisional"
                 if all(row["today"]["status"] == "PROVISIONAL" for row in filled) else "Intraday vs prior close")
        return label, collapse_shared_clock(rows)
    dates = {row["today"]["observed_at"] for row in filled}
    if len(dates) == 1 and all(not row["current_is_intraday"] for row in filled):
        for row in rows:
            row["today"]["observed"] = ""
        return f"Daily · {short_date(next(iter(dates)))}", None
    return "Change · dated per row", None


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
        result.append(dict(maturity=term, level=cell(level), change=cell(change if paired else None),
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


def presentation(packet, narrative, context=None):
    catalog = evidence_catalog(packet)
    values = dict(catalog, **prior_values(context))
    actual_started_at = packet["run"].get("actual_started_at", packet["run"]["target_time"])
    status = status_for(packet)
    commissioning = (status == "SAMPLE" and packet["run"]["mode"] == "LIVE")
    live_commissioning = status == "LIVE COMMISSIONING"
    checkpoint = packet["run"]["checkpoint"]
    target = timestamp(packet["run"]["target_time"])

    def expand(text):
        return TOKEN.sub(lambda m: formatted(values[m[1]]), text)

    def refs(ids):
        """The cited rows behind one block, formatted for a quiet expandable marker; IDs are unchanged."""
        rows = []
        for ref in ids:
            row = values.get(ref)
            if row is None:
                continue
            label = (measure_label(row) if row.get("metric") and row.get("topic")
                     else row.get("title") or row.get("topic") or ref)
            rows.append(dict(id=ref, label=label, display=formatted(row) if "value" in row else "",
                             anchor=ref in catalog))
        return rows

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

    current_sectors = sorted((r for r in facts if r["frequency"] == "intraday" and r["topic"] in SECTORS),
                             key=lambda r: abs(r["value"]), reverse=True)
    priority = [current_or_daily("SPY"), current_or_daily("QQQ"),
                current_sectors[0]["id"] if current_sectors else None, current_or_daily("GLD"),
                "treasury-2y-change", "treasury-10y-change"]
    chips = [dict(catalog[i], display=formatted(catalog[i]), direction=direction(catalog[i]),
                  metric_label=metric_label(catalog[i]),
                  observed_label=observed_label(catalog[i]["observed_at"]),
                  observed_short=compact_clock(catalog[i]["observed_at"]))
             for i in dict.fromkeys(priority) if i and i in catalog][:6]
    if not chips:
        chips = [dict(row, metric_label=metric_label(row), observed_short=compact_clock(row["observed_at"]))
                 for row in facts[:4]]
    # The page shows three figures: the first three of the same deterministic priority order
    # (SPY, QQQ, then the leading current sector print when one exists, else GLD). No new ranking.
    figures = chips[:3]
    # After a close whose daily bar is not yet published, the retained daily return is two
    # sessions old relative to the completed session and must not pose as today.
    daily_today = not packet.get("history_lag")

    # Tables: mega-caps, sectors ranked by the labeled twenty-session spread, metals structure.
    mega_rows = compact_equity_rows(facts, MEGACAPS, daily_today)
    mega_change, mega_asof = change_column(mega_rows)
    sector_rows = rank_by_spread(compact_equity_rows(facts, list(SECTOR_LABELS), daily_today), list(SECTOR_LABELS))
    sector_change, sector_asof = change_column(sector_rows)
    metal_rows = compact_equity_rows(facts, METALS, daily_today)
    metal_change, metal_asof = change_column(metal_rows)
    yields, yields_asof = treasury_rows(treasuries)

    # What matters next: watches with natural horizons, carried watches, attention, events.
    watches = []
    for w in narrative["watches"]:
        try:
            horizon = resolve_horizon(w["horizon"], target, packet["events"], checkpoint)
        except ValueError:
            horizon = dict(declared=w["horizon"], phrase=w["horizon"].replace("_", " ").title())
        watches.append({**w, **{k: expand(w[k]) for k in ("condition", "confirmation", "contradiction")},
                        "phrase": horizon["phrase"], "expires_session": horizon.get("expires_session"),
                        "refs": refs(w["evidence_ids"])})
    prior = (context or {}).get("prior_state") or {"status": "cold_start", "reason": ""}
    available = prior.get("status") == "available"
    updates = {u["carried_id"]: u for u in narrative.get("watch_updates", [])}
    carried = []
    for watch in prior.get("watches", []) if available else []:
        update = updates.get(watch["id"])
        if update is None and watch["lifecycle"] != "active":
            continue  # a horizon that passed without reassessment is history, not a live item
        carried.append(dict(id=watch["id"], hypothesis=expand(watch["hypothesis"]),
                            phrase=watch["horizon"].get("phrase", ""), lifecycle=watch["lifecycle"],
                            evaluability=watch["evaluability"],
                            assessment=update["assessment"] if update else "not reassessed",
                            reason=expand(update["reason"]) if update else "",
                            evidence_ids=update["evidence_ids"] if update else watch["evidence_refs"],
                            refs=refs(update["evidence_ids"] if update else watch["evidence_refs"])))
    selected = set(narrative["attention_ids"])
    attention_why = {item["id"]: item["why"] for item in narrative.get("attention", [])}
    attention = [{**a, "why": attention_why.get(a["id"], ""),
                  "display_symbol": (f"{SECTOR_LABELS[a['symbol']]} · {a['symbol']}"
                                     if a["symbol"] in SECTOR_LABELS else a["symbol"]),
                  "date_label": short_date(a.get("date")), "refs": refs(a["evidence_ids"])}
                 for a in packet["attention"] if a["id"] in selected]
    events = [{**event, "scheduled_label": pacific_time(event["scheduled_at"], True),
               "relation_label": event.get("session_relation", "").lower(), "refs": refs([event["id"]])}
              for event in packet["events"][:4]]

    # What changed: deterministic comparisons plus the analyst's interpretation of changed ones.
    comparisons = (context or {}).get("comparisons", [])
    anchors = prior.get("anchors", {}) if available else {}
    since_label = None
    if "previous_close" in anchors:
        since_label = f"Since the previous close · {short_date(anchors['previous_close']['session_date'])}"
    elif anchors:
        parts = ["the premarket edition" if name == "premarket" else
                 f"the {EDITION_LABELS.get(anchor['checkpoint'], 'latest edition').lower()} at "
                 f"{pacific_time(anchor['evidence_cutoff'])}" for name, anchor in anchors.items()]
        since_label = "Since " + " and ".join(parts)
    changed = [c for c in comparisons if c["status"] == "changed"]
    repeated = [c for c in comparisons if c["status"] == "no_new_observation"]
    since_entries = [dict(kind="change", text=expand(c["text"]), evidence_ids=c["evidence_ids"],
                          refs=refs(c["evidence_ids"]))
                     for c in narrative.get("changes", [])]
    for r in narrative.get("relationships", []):
        if r["carried_id"] is not None:
            since_entries.append(dict(kind="relationship", text=expand(r["statement"]), assessment=r["assessment"],
                                    reason=expand(r["reason"]), evidence_ids=r["evidence_ids"],
                                    refs=refs(r["evidence_ids"])))
    since_note = ""
    if available and not changed:
        since_note = (f"No comparable measurement has changed: {len(repeated)} repeated prior-close "
                      "observations and no new session prints." if repeated else
                      "No comparable measurement is available yet.")
    elif not available:
        # Reader copy; the admission reason stays in Technical details.
        since_note = ("No accepted close to carry forward, so this is a baseline read."
                      if checkpoint == "PREMARKET"
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
    technical = dict(
        generated_utc=actual_started_at,
        evidence_cutoff_utc=packet["run"]["target_time"],
        checkpoint=checkpoint,
        bootstrap=packet["coverage"]["bootstrap"],
        calendar=packet["coverage"].get("calendar", "unavailable"),
        continuity=dict(status=prior.get("status", "cold_start"), reason=prior.get("reason", ""),
                        anchors={k: v.get("run_id") for k, v in anchors.items()},
                        comparisons=len(comparisons), changed=len(changed)),
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
    evidence_rows = [dict(r, display=formatted(r) if "value" in r else r["title"]) for r in catalog.values()]
    groups = {}
    for row in evidence_rows:
        groups.setdefault(row.get("topic") or row.get("title") or row["id"], []).append(row)
    evidence_groups = [dict(topic=topic, rows=rows) for topic, rows in groups.items()]
    return dict(
        mode=packet["run"]["mode"], status=status, commissioning=commissioning,
        live_commissioning=live_commissioning, checkpoint=checkpoint,
        # The scheduler's idempotency marker; a commissioning run never claims a scheduled slot.
        marker_checkpoint="COMMISSIONING" if packet["run"].get("commissioning") else checkpoint,
        phase_label=CHECKPOINT_LABELS.get(checkpoint, checkpoint),
        edition_label=EDITION_LABELS.get(checkpoint, "Market edition"),
        session=packet["run"]["session"], target=packet["run"]["target_time"],
        actual_started_at=actual_started_at,
        status_line=f"{status} · {EDITION_LABELS.get(checkpoint, 'Market edition')} · "
                    f"{pacific_time(actual_started_at, True).replace(' · ', ' · as of ')}",
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
        since=dict(label=since_label, entries=since_entries, note=since_note, status=prior.get("status", "cold_start")),
        next=dict(watches=watches, carried=carried, attention=attention, events=events,
                  paragraphs=[paragraph(p) for key in ("attention", "events") for p in narrative["sections"][key]]),
        equities=dict(paragraphs=[paragraph(p) for p in narrative["sections"]["equities"]],
                      rows=mega_rows, change_label=mega_change, change_head=change_head(mega_change), asof=mega_asof,
                      lookback={s: v for s, v in packet.get("lookback", {}).items() if v["r20"] != "available"}),
        macro=dict(paragraphs=[paragraph(p) for p in narrative["sections"]["macro"]],
                   yields=yields, yields_asof=yields_asof, facts=other_macro),
        sectors=dict(rows=sector_rows, change_label=sector_change, change_head=change_head(sector_change),
                     asof=sector_asof,
                     spread_label="20-session spread vs SPY, strongest to weakest"),
        cross_asset=dict(rows=metal_rows, change_label=metal_change, change_head=change_head(metal_change),
                         asof=metal_asof),
        cuttingboard_section=dict(visible=cuttingboard_visible,
                                  paragraphs=[paragraph(p) for p in narrative["sections"]["cuttingboard"]]),
        sources=packet["sources"], cuttingboard=packet["cuttingboard"],
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

    lines = [f"# {esc(view['banner']['title'])}", "", esc(view["status_line"])]
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
    if since["label"] or since["note"]:
        lines += [f"## {esc(since['label'] or 'What changed')}", ""]
        for item in since["entries"]:
            if item["kind"] == "change":
                lines.append(f"- {esc(item['text'])} {refs(item['evidence_ids'])}")
            else:
                lines.append(f"- {esc(item['text'])} — **{item['assessment']}**. {esc(item['reason'])} "
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
        lines.append(f"- **WATCH · {esc(w['phrase'])}** — {esc(w['condition'])} Confirm: {esc(w['confirmation'])}"
                     f"{changes_it} {refs(w['evidence_ids'])}")
    for c in nxt["carried"]:
        lines.append(f"- **CARRIED WATCH · {c['assessment']}** — {esc(c['hypothesis'])} {esc(c['reason'])}")
    for a in nxt["attention"]:
        lines.append(f"- **{esc(a['display_symbol'])}** — {esc(a['reason'])}. {esc(a['why'])} "
                     f"({esc(a['date_label'])}) {refs(a['evidence_ids'])}")
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
            lines += [f"**MEGA-CAP SNAPSHOT**{asof}", "",
                      f"| Symbol | {esc(eq['change_label'])} | 20D | vs QQQ · 20s | vs 50DMA |",
                      "|---|---:|---:|---:|---:|"]
            for row in eq["rows"]:
                lines.append(f"| {row['symbol']} | {row['today']['display']} | {row['r20']['display']} | "
                             f"{row['relative']['display']} | {row['dma']['display']} |")
            lines += ["", LEDGER_NOTE, ""]
        for symbol, state in eq["lookback"].items():
            lines.append(f"{esc(symbol)} 20s: n/a ({state['sessions']} sessions)")
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
        lines += ["## Sector view", "", f"{esc(sec['spread_label'])}{asof}", "",
                  f"| Sector | vs SPY · 20s | 20D | {esc(sec['change_label'])} | vs 50DMA |",
                  "|---|---:|---:|---:|---:|"]
        for row in sec["rows"]:
            lines.append(f"| {esc(row['label'])} ({row['symbol']}) | {row['relative']['display']} | "
                         f"{row['r20']['display']} | {row['today']['display']} | {row['dma']['display']} |")
        lines += ["", LEDGER_NOTE, ""]
    cross = view["cross_asset"]
    if cross["rows"]:
        asof = f" · {esc(cross['asof'])}" if cross["asof"] else ""
        lines += ["## Cross-asset structure", "", f"**METALS STRUCTURE**{asof}", "",
                  f"| Instrument | {esc(cross['change_label'])} | 20D | vs benchmark · 20s | vs 50DMA |",
                  "|---|---:|---:|---:|---:|"]
        for row in cross["rows"]:
            lines.append(f"| {esc(row['label'])} ({row['symbol']}) | {row['today']['display']} | "
                         f"{row['r20']['display']} | {row['relative']['display']} vs {row['relative_label']} | "
                         f"{row['dma']['display']} |")
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
    lines += ["## Sources & coverage", "", esc(view["coverage"]["basis"]), esc(view["coverage"]["horizon"]), ""]
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
              f"Checkpoint: {esc(technical['checkpoint'])}",
              f"Bootstrap: {esc(technical['bootstrap'])}",
              f"Calendar: {esc(technical['calendar'])}",
              f"Continuity: {esc(technical['continuity']['status'])}"
              + (f" — {esc(technical['continuity']['reason'])}" if technical["continuity"]["reason"] else ""), "",
              (f"Cuttingboard: generated {esc(technical['cuttingboard'].get('generated_at'))}; "
               f"captured {esc(technical['cuttingboard'].get('captured_at'))}; "
               f"schema {esc(technical['cuttingboard'].get('schema_version'))}"
               if technical["cuttingboard"] else ""), "",
              "Model-assisted interpretation; factual rows are deterministic. Personal local edition.", ""]
    return "\n".join(lines)


def render(packet, narrative, context=None):
    view = presentation(packet, narrative, context)
    env = Environment(loader=FileSystemLoader(ROOT / "templates"),
                      autoescape=select_autoescape(default=True))
    return markdown(view), env.get_template("brief.html.j2").render(**view)

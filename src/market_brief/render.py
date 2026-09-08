"""One presentation model, two local formats. Model prose cannot replace fact rows."""

import html
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .evidence import ROOT, USABLE, evidence_catalog, timestamp
from .synthesize import TOKEN

TITLES = {"macro": "Macro & cross-asset", "equities": "Equity structure",
          "attention": "On the attention list", "cuttingboard": "Cuttingboard context",
          "events": "Event risk"}

HORIZON_LABELS = {"daily return": "1d", "twenty-session return": "20s",
                  "fifty-session average": "50d avg"}
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
    "XLI": "Industrials", "XLY": "CON Discretionary", "XLP": "CON Staples",
    "XLV": "Health Care", "XLU": "Utilities", "XLB": "Materials",
    "XLRE": "Real Estate", "XLC": "COMM Services",
}
SECTORS = set(SECTOR_LABELS)
MEGACAPS = {"AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOG"}


def pacific_time(value, include_date=False):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(PACIFIC)
    date_part = dt.strftime("%A, %b %-d · ") if include_date else ""
    return f"{date_part}{dt.strftime('%-I:%M %p')} PT"


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


def compact_equity_rows(rows, symbols, allow_daily_today=True):
    result = []
    for symbol in symbols:
        symbol_rows = [row for row in rows if row["topic"] == symbol]
        if not symbol_rows:
            continue
        current = next((row for row in symbol_rows
                        if row.get("frequency") == "intraday" and row["status"] in USABLE), None)
        if allow_daily_today:
            current = current or next((row for row in symbol_rows
                                       if row.get("metric") == "daily return"), None)
        r20 = next((row for row in symbol_rows if row.get("metric") == "twenty-session return"), None)
        relative = next((row for row in symbol_rows
                         if row.get("metric", "").startswith("relative to ")), None)
        average = next((row for row in symbol_rows if row.get("metric") == "fifty-session average"), None)

        def cell(row, bare=False):
            return dict(display=(formatted(row).removesuffix(" USD") if bare else formatted(row)) if row else "n/a",
                        direction=direction(row) if row else "neutral",
                        id=row.get("id") if row else None,
                        observed=observed_label(row.get("observed_at")) if row else "",
                        observed_at=row.get("observed_at") if row else None)

        result.append(dict(symbol=symbol, label=SECTOR_LABELS.get(symbol, symbol),
                           today=cell(current), r20=cell(r20), relative=cell(relative),
                           relative_label=(relative.get("metric", "").removeprefix("relative to ")
                                           if relative else "benchmark"), average=cell(average, bare=True)))
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


def direction(row):
    if row.get("metric") not in {"daily return", "daily yield change", "premarket return", "intraday return"} \
            and not row.get("metric", "").startswith("relative to "):
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


def presentation(packet, narrative):
    catalog = evidence_catalog(packet)
    actual_started_at = packet["run"].get("actual_started_at", packet["run"]["target_time"])
    status = status_for(packet)
    commissioning = (status == "SAMPLE" and packet["run"]["mode"] == "LIVE")
    live_commissioning = status == "LIVE COMMISSIONING"

    def expand(text):
        return TOKEN.sub(lambda m: formatted(catalog[m[1]]), text)

    def paragraph(p):
        return {**p, "text": expand(p["text"]), "uncertainty": expand(p["uncertainty"]),
                "alternative": expand(p["alternative"]),
                "context": " ".join(filter(None, (expand(p["uncertainty"]),
                                                     expand(p["alternative"]))))}

    facts = []
    for row in [*packet["observations"], *packet["derived"]]:
        if row["status"] not in USABLE:
            continue
        if row["metric"] in {"regular close"}:
            continue
        facts.append({**row, "display": formatted(row), "direction": direction(row),
                      "measure": measure_label(row), "observed_label": observed_label(row["observed_at"])})
    equity = [r for r in facts if r["topic"] in
              {h["symbol"] for h in packet["history"]} or r["frequency"] == "intraday"]
    equity_table_facts = [r for r in equity if r["topic"] not in SECTORS | MEGACAPS]
    macro = [r for r in facts if r not in equity]
    def current_or_daily(symbol):
        return next((i for i in (f"{symbol}-intraday", f"{symbol}-daily") if i in catalog), None)

    current_sectors = sorted((r for r in facts if r["frequency"] == "intraday" and r["topic"] in SECTORS),
                             key=lambda r: abs(r["value"]), reverse=True)
    priority = [current_or_daily("SPY"), current_or_daily("QQQ"),
                current_sectors[0]["id"] if current_sectors else None, current_or_daily("GLD"),
                "treasury-2y-change", "treasury-10y-change"]
    chips = [dict(catalog[i], display=formatted(catalog[i]), direction=direction(catalog[i]),
                  metric_label=metric_label(catalog[i]),
                  observed_label=observed_label(catalog[i]["observed_at"]))
             for i in dict.fromkeys(priority) if i and i in catalog][:6]
    if not chips:
        chips = [dict(row, metric_label=metric_label(row)) for row in facts[:4]]
    selected = set(narrative["attention_ids"])
    attention_why = {item["id"]: item["why"] for item in narrative.get("attention", [])}
    # After a close whose daily bar is not yet published, the retained daily return is two
    # sessions old relative to the completed session and must not pose as today.
    daily_today = not packet.get("history_lag")
    sections = []
    for key, title in TITLES.items():
        mega_rows = compact_equity_rows(facts, sorted(MEGACAPS), daily_today) if key == "equities" else []
        sector_rows = compact_equity_rows(facts, list(SECTOR_LABELS), daily_today) if key == "equities" else []
        sections.append(dict(key=key, title=title,
            paragraphs=[paragraph(p) for p in narrative["sections"][key]],
            facts=macro if key == "macro" else equity_table_facts if key == "equities" else [],
            mega_rows=mega_rows, mega_asof=collapse_shared_clock(mega_rows),
            sector_rows=sector_rows, sector_asof=collapse_shared_clock(sector_rows),
            attention=[{**a, "why": attention_why.get(a["id"], ""),
                        "display_symbol": (f"{a['symbol']} · {SECTOR_LABELS[a['symbol']]}"
                                           if a["symbol"] in SECTOR_LABELS else a["symbol"])}
                       for a in packet["attention"]
                       if a["id"] in selected]
                      if key == "attention" else [],
            events=[{**event, "scheduled_label": pacific_time(event["scheduled_at"], True)}
                    for event in packet["events"][:4]] if key == "events" else []))
    for section in sections:
        if section["key"] == "cuttingboard":
            section["visible"] = packet["cuttingboard"].get("status") == "AVAILABLE" or bool(section["paragraphs"])
        else:
            section["visible"] = any(section[k] for k in ("facts", "paragraphs", "attention", "events",
                                                         "mega_rows", "sector_rows"))
    omitted = [s["title"] for s in sections if not s["visible"]]
    limitations = list(packet["coverage"]["limitations"])
    if omitted:
        limitations.append("No admitted material for: " + ", ".join(omitted))
    sections = [s for s in sections if s["visible"]]
    sector_leadership = {key: [{**row, "display": formatted(row), "direction": direction(row),
                                "measure": measure_label(row)} for row in rows]
                         for key, rows in packet.get("sector_leadership", {}).items()}
    technical = dict(
        generated_utc=actual_started_at,
        evidence_cutoff_utc=packet["run"]["target_time"],
        checkpoint=packet["run"]["checkpoint"],
        bootstrap=packet["coverage"]["bootstrap"],
        calendar=packet["coverage"].get("calendar", "unavailable"),
        cuttingboard={key: packet["cuttingboard"].get(key) for key in
                      ("status", "generated_at", "captured_at", "schema_version")
                      if packet["cuttingboard"].get(key)},
        providers=[dict(name=s["name"], provider=s.get("provider"), feed=s.get("feed"),
                        data_delay=s.get("data_delay")) for s in packet["sources"]
                   if s.get("provider") or s.get("feed") or s.get("data_delay")],
    )
    checkpoint = packet["run"]["checkpoint"]
    return dict(mode=packet["run"]["mode"], status=status, commissioning=commissioning,
        live_commissioning=live_commissioning,
        checkpoint=checkpoint,
        # The scheduler's idempotency marker; a commissioning run never claims a scheduled slot.
        marker_checkpoint="COMMISSIONING" if packet["run"].get("commissioning") else checkpoint,
        phase_label=CHECKPOINT_LABELS.get(checkpoint, checkpoint),
        edition_label=EDITION_LABELS.get(checkpoint, "Market edition"),
        phase_phrase=PHASE_PHRASES.get(checkpoint, ""),
        session=packet["run"]["session"],
        target=packet["run"]["target_time"],
        actual_started_at=actual_started_at,
        header_label="LIVE COMMISSIONING" if live_commissioning else CHECKPOINT_LABELS.get(checkpoint, checkpoint),
        scheduled_label=pacific_time(actual_started_at if live_commissioning
                                     else packet["run"]["session"]["scheduled_checkpoint_at"], True),
        updated_label=pacific_time(actual_started_at),
        updated_prefix="Updated" if live_commissioning else "Last updated",
        technical=technical,
        coverage=packet["coverage"], limitations=limitations,
        banner={**narrative["banner"], "title": expand(narrative["banner"]["title"]),
                "limitation": expand(narrative["banner"]["limitation"])}, chips=chips,
        summary=[paragraph(p) for p in narrative["summary"]], sections=sections,
        watches=[{**w, **{k: expand(w[k]) for k in
                          ("condition", "confirmation", "contradiction", "horizon")}}
                 for w in narrative["watches"]],
        sources=packet["sources"], cuttingboard=packet["cuttingboard"],
        sector_leadership=sector_leadership or {"top": [], "bottom": []},
        lookback=packet.get("lookback", {}),
        evidence=[dict(r, display=formatted(r) if "value" in r else r["title"])
                  for r in catalog.values()], context_items=packet["context_items"])


def markdown(view):
    def esc(value):
        text = " ".join(str(value).split())
        return re.sub(r"([\\`*_\[\]|])", r"\\\1", html.escape(text))

    def refs(ids):
        return " ".join(f"[evidence](#evidence-{i})" for i in ids)

    def para(p):
        text = f"**{p['class']}** · {esc(p['text'])} {refs(p['evidence_ids'])}"
        if p["context"]:
            text += f" {esc(p['context'])}"
        return text

    lines = [f"# {esc(view['banner']['title'])}", ""]
    if view["status"] == "SAMPLE":
        label = " · COMMISSIONING RUN" if view["commissioning"] else " · FICTIONAL SAMPLE / REPLAY"
        lines += [f"> SAMPLE{label} — not the scheduled brief.", ""]
    lines += [f"{view['status']} · {view['phase_label']}",
              f"{esc(view['scheduled_label'])}",
              f"Last updated: {esc(view['updated_label'])}", "",
              f"**INTERPRETATION — {view['banner']['label']}**", "",
              esc(view["banner"]["limitation"]), "",
              esc(view["coverage"]["basis"]), "", esc(view["coverage"]["horizon"]), ""]
    if view["commissioning"]:
        lines += [f"> SAMPLE · COMMISSIONING RUN. Generated at {esc(view['updated_label'])}. ",
                  "This was a commissioning test, not the scheduled pre-market brief.", ""]
    lines += ["## In a minute", ""]
    for p in view["summary"]:
        lines += [para(p), ""]
    lines += ["## What to watch", ""]
    for w in view["watches"]:
        lines += [f"- **WATCH** · {esc(w['horizon'])} {esc(w['condition'])} "
                  f"Check: {esc(w['confirmation'])} If not: {esc(w['contradiction'])}. "
                  f"{refs(w['evidence_ids'])}"]
    lines += [""]
    for section in view["sections"]:
        lines += [f"## {section['title']}", ""]
        if section["key"] == "equities" and section["mega_rows"]:
            asof = f" · {esc(section['mega_asof'])}" if section["mega_asof"] else ""
            lines += [f"**MEGA-CAP SNAPSHOT**{asof}", "", "| Symbol | Today | 20D | VS QQQ | 50D AVG |",
                      "|---|---:|---:|---:|---:|"]
            for row in section["mega_rows"]:
                name = row["symbol"] if row["label"] == row["symbol"] else f"{row['symbol']} · {row['label']}"
                lines.append(f"| {name} | {row['today']['display']} | "
                             f"{row['r20']['display']} | {row['relative']['display']} | "
                             f"{row['average']['display']} |")
            lines.append("")
        if section["key"] == "equities" and section["sector_rows"]:
            asof = f" · {esc(section['sector_asof'])}" if section["sector_asof"] else ""
            lines += [f"**SECTOR SNAPSHOT**{asof}", "", "| Sector | Today | 20D | VS SPY | 50D AVG |",
                      "|---|---:|---:|---:|---:|"]
            for row in section["sector_rows"]:
                lines.append(f"| {row['symbol']} · {row['label']} | {row['today']['display']} | "
                             f"{row['r20']['display']} | {row['relative']['display']} | "
                             f"{row['average']['display']} |")
            lines.append("")
        if section["facts"]:
            lines += ["**OBSERVED**", "", "| Measure | Observation | Date / source |",
                      "|---|---:|---|"]
            for row in section["facts"]:
                lines.append(f"| {esc(row['measure'])} | {row['display']} | "
                             f"{row['observed_label']} · {row['status']} · {refs([row['id']])} |")
            lines.append("")
            if section["key"] == "equities":
                for symbol, state in view["lookback"].items():
                    if state["r20"] != "available":
                        lines.append(f"{esc(symbol)} 20s: n/a ({state['sessions']} sessions)")
                lines.append("")
        for p in section["paragraphs"]:
            lines += [para(p), ""]
        for a in section["attention"]:
            lines += [f"**OBSERVED · {a['symbol']}** — {esc(a['reason'])}. "
                      f"{esc(a['why'])} {a['date']} / {a['horizon']}. {refs(a['evidence_ids'])}", ""]
        for event in section["events"]:
            event_time = esc(event["scheduled_label"])
            lines += [f"**OBSERVED** · {esc(event['title'])} — {event_time} · "
                      f"{esc(event.get('session_relation', ''))}. "
                      f"{refs([event['id']])}", ""]
        if section["key"] == "cuttingboard":
            cb = view["cuttingboard"]
            if cb["status"] == "AVAILABLE":
                lines += [f"**SOURCE QUOTATION** · Outcome: {esc(cb.get('outcome') or 'not exposed')}; "
                          f"permission: {esc(cb.get('permission') or 'not exposed')}. "
                          "Read-only context captured; see Technical details.", ""]
            else:
                lines += [f"{esc(cb['status'])} — {esc(cb.get('reason', ''))}.", ""]
    lines += ["## Sources & coverage", ""]
    for limitation in view["limitations"]:
        lines += [f"- {esc(limitation)}"]
    for s in view["sources"]:
        lines += [f"- [{esc(s['name'])}](<{s['url']}>) · {esc(s['kind'])} · {esc(s['status'])} "
                  f"· retrieved {esc(s['retrieved_at'])}"]
    lines += ["", "### Evidence ledger", ""]
    for row in view["evidence"]:
        lines += [f'<a id="evidence-{row["id"]}"></a>',
                  f"**{esc(row['id'])}** · {esc(row.get('topic', row.get('title', '')))} · "
                  f"{esc(row['display'])} · {esc(row.get('baseline', 'published / scheduled item'))} "
                  f"· observed/published {esc(row.get('observed_at') or row.get('published_at') or 'not exposed')} "
                  f"· source {esc(row['source_id'])}", ""]
    lines += ["", "### Technical details", "",
              f"Generated UTC: {esc(view['technical']['generated_utc'])}",
              f"Evidence cutoff UTC: {esc(view['technical']['evidence_cutoff_utc'])}",
              f"Checkpoint: {esc(view['technical']['checkpoint'])}",
              f"Bootstrap: {esc(view['technical']['bootstrap'])}",
              f"Calendar: {esc(view['technical']['calendar'])}", "",
              (f"Cuttingboard: generated {esc(view['technical']['cuttingboard'].get('generated_at'))}; "
               f"captured {esc(view['technical']['cuttingboard'].get('captured_at'))}; "
               f"schema {esc(view['technical']['cuttingboard'].get('schema_version'))}"
               if view['technical']['cuttingboard'] else ""), "",
              "Model-assisted interpretation; factual rows are deterministic. Personal local edition.", ""]
    return "\n".join(lines)


def render(packet, narrative):
    view = presentation(packet, narrative)
    env = Environment(loader=FileSystemLoader(ROOT / "templates"),
                      autoescape=select_autoescape(default=True))
    return markdown(view), env.get_template("brief.html.j2").render(**view)

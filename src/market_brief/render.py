"""One presentation model, two local formats. Model prose cannot replace fact rows."""

import html
import re

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .evidence import ROOT, USABLE, evidence_catalog
from .synthesize import TOKEN

TITLES = {"macro": "Macro & cross-asset", "equities": "Equity structure",
          "attention": "On the attention list", "cuttingboard": "Cuttingboard context",
          "events": "Event risk"}


def direction(row):
    if row.get("metric") not in {"daily return", "daily yield change"} \
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

    def expand(text):
        return TOKEN.sub(lambda m: formatted(catalog[m[1]]), text)

    def paragraph(p):
        return {**p, "text": expand(p["text"]), "uncertainty": expand(p["uncertainty"]),
                "alternative": expand(p["alternative"])}

    facts = []
    for row in [*packet["observations"], *packet["derived"]]:
        if row["status"] not in USABLE:
            continue
        if row["metric"] in {"twenty-session return", "fifty-session average", "regular close"}:
            continue
        facts.append({**row, "display": formatted(row), "direction": direction(row)})
    equity = [r for r in facts if r["topic"] in
              {h["symbol"] for h in packet["history"]} or r["frequency"] == "intraday"]
    macro = [r for r in facts if r not in equity]
    priority = ["SPY-daily", "QQQ-daily", "treasury-2y-change", "treasury-10y-change",
                "GLD-daily", "GDX-daily"]
    chips = [dict(catalog[i], display=formatted(catalog[i]), direction=direction(catalog[i]))
             for i in priority if i in catalog]
    if not chips:
        chips = facts[:4]
    selected = set(narrative["attention_ids"])
    sections = []
    for key, title in TITLES.items():
        sections.append(dict(key=key, title=title,
            paragraphs=[paragraph(p) for p in narrative["sections"][key]],
            facts=macro if key == "macro" else equity if key == "equities" else [],
            attention=[a for a in packet["attention"] if a["id"] in selected]
                      if key == "attention" else [],
            events=packet["events"][:4] if key == "events" else []))
    return dict(mode=packet["run"]["mode"], session=packet["run"]["session"],
        target=packet["run"]["target_time"], coverage=packet["coverage"],
        banner={**narrative["banner"], "title": expand(narrative["banner"]["title"]),
                "limitation": expand(narrative["banner"]["limitation"])}, chips=chips,
        summary=[paragraph(p) for p in narrative["summary"]], sections=sections,
        watches=[{**w, **{k: expand(w[k]) for k in
                          ("condition", "confirmation", "contradiction", "horizon")}}
                 for w in narrative["watches"]],
        sources=packet["sources"], cuttingboard=packet["cuttingboard"],
        evidence=[dict(r, display=formatted(r) if "value" in r else r["title"])
                  for r in catalog.values()], context_items=packet["context_items"])


def markdown(view):
    def esc(value):
        text = " ".join(str(value).split())
        return re.sub(r"([\\`*_\[\]|])", r"\\\1", html.escape(text))

    def refs(ids):
        return " ".join(f"[{esc(i)}](#evidence-{i})" for i in ids)

    def para(p):
        text = f"**{p['class']}** · {esc(p['text'])} {refs(p['evidence_ids'])}"
        if p["uncertainty"]:
            text += f" Uncertainty: {esc(p['uncertainty'])}"
        if p["alternative"]:
            text += f" Alternative: {esc(p['alternative'])}"
        return text

    lines = [f"# {esc(view['banner']['title'])}", ""]
    if view["mode"] == "SAMPLE":
        lines += ["> FICTIONAL SAMPLE / REPLAY — not current market facts. No live model required.", ""]
    lines += [f"PRE-MARKET · {view['session']['date']} · Evidence cutoff {view['target']}", "",
              f"**INTERPRETATION — {view['banner']['label']}**", "",
              esc(view["banner"]["limitation"]), "",
              f"Coverage: **{view['coverage']['status']}**. {esc(view['coverage']['horizon'])}", ""]
    if view["mode"] == "LIVE" and not view["session"]["meaningful_premarket"]:
        lines += ["> OUTSIDE PRE-MARKET WINDOW — collection smoke test, not morning acceptance.", ""]
    lines += ["## The morning in a minute", ""]
    for p in view["summary"]:
        lines += [para(p), ""]
    for section in view["sections"]:
        lines += [f"## {section['title']}", ""]
        if section["facts"]:
            lines += ["**OBSERVED**", "", "| Measure | Observation | Horizon / evidence |",
                      "|---|---:|---|"]
            for row in section["facts"]:
                lines.append(f"| {esc(row['topic'])} · {esc(row['metric'])} | {row['display']} | "
                             f"{row['observed_at']} · {row['status']} · {refs([row['id']])} |")
            lines.append("")
        for p in section["paragraphs"]:
            lines += [para(p), ""]
        for a in section["attention"]:
            lines += [f"**OBSERVED · {a['symbol']}** — {esc(a['reason'])}. "
                      f"{a['date']} / {a['horizon']}. {refs(a['evidence_ids'])}", ""]
        for event in section["events"]:
            lines += [f"**OBSERVED** · {esc(event['title'])} — {esc(event['scheduled_at'])}. "
                      f"{refs([event['id']])}", ""]
        if section["key"] == "cuttingboard":
            cb = view["cuttingboard"]
            if cb["status"] == "AVAILABLE":
                lines += [f"**SOURCE QUOTATION** · Outcome: {esc(cb.get('outcome') or 'not exposed')}; "
                          f"permission: {esc(cb.get('permission') or 'not exposed')}. "
                          f"Generated {esc(cb['generated_at'])}. No override or recommendation.", ""]
            else:
                lines += [f"{esc(cb['status'])} — {esc(cb.get('reason', ''))}.", ""]
        elif not any(section[k] for k in ("facts", "paragraphs", "attention", "events")):
            lines += ["No admitted observations in this section; coverage is incomplete.", ""]
    lines += ["## What to watch", ""]
    for w in view["watches"]:
        lines += [f"- **WATCH** · {esc(w['condition'])} Confirmation: {esc(w['confirmation'])} "
                  f"Contradiction: {esc(w['contradiction'])} Horizon: {esc(w['horizon'])}. "
                  f"{refs(w['evidence_ids'])}"]
    lines += ["", "## Sources & coverage", ""]
    for limitation in view["coverage"]["limitations"]:
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
    lines += ["Model-assisted interpretation; factual rows are deterministic. Personal local edition.", ""]
    return "\n".join(lines)


def render(packet, narrative):
    view = presentation(packet, narrative)
    env = Environment(loader=FileSystemLoader(ROOT / "templates"),
                      autoescape=select_autoescape(default=True))
    return markdown(view), env.get_template("brief.html.j2").render(**view)

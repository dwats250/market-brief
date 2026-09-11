"""The analyst reads exactly one saved, bounded projection of the admitted evidence.

`model_packet` remains the authority filter (permitted sources, usable rows) for both this
projection and the validator; this module only changes the shape the model reads and records
which facts were supplied so that references can be checked against the exact context used.
"""

import re

from .evidence import ROOT, digest, evidence_catalog, model_packet, read_json

CONTEXT_SCHEMA = "market-brief.analyst-context.v1"
ANCHOR_TOPICS = ("SPY", "QQQ", "GLD", "US 2Y", "US 5Y", "US 10Y")
# Metrics that describe the current window; large dated background (20-session returns, 50DMA
# distances, spreads) is kept only when an anchor, a carried record, a trigger, or leadership cites it.
CURRENT_METRICS = {"daily return", "premarket return", "intraday return", "daily yield change"}


def editions_config():
    return read_json(ROOT / "config/editions.json")


def edition_profile(checkpoint, config=None):
    """The edition's budget profile plus its editorial guidance, from one small configuration."""
    config = config or editions_config()
    edition = config["editions"].get(checkpoint, config["editions"]["PREMARKET"])
    profile = config["profiles"][edition["profile"]]
    return dict(checkpoint=checkpoint, profile=edition["profile"], words=edition["words"],
                guidance=edition["guidance"], **profile)
HISTORY_ERROR = re.compile(r"^(?P<symbol>[A-Z][\w.-]*): invalid/incomplete historical context \((?P<reason>.*)\)$")
FACT_FIELDS = ("id", "metric", "value", "unit", "magnitude", "status", "observed_at")


def history_error_summary(packet, errors):
    """Twenty-two near-identical strings become one structured statement of the gap."""
    symbols, reasons = [], []
    for text in errors:
        match = HISTORY_ERROR.match(text)
        if match:
            symbols.append(match["symbol"])
            if match["reason"] not in reasons:
                reasons.append(match["reason"])
        elif text not in reasons:
            reasons.append(text)
    affected = set(symbols)
    latest = [h["dates"][-1] for h in packet.get("history", [])
              if h.get("symbol") in affected and h.get("dates")]
    return dict(affected_count=len(errors), affected_symbols=sorted(affected),
                expected_session=packet["run"]["session"]["previous_session"],
                latest_provider_session=max(latest) if latest else None, reasons=reasons)


def compact_fact(row):
    return {key: row[key] for key in FACT_FIELDS if key in row}


def _selected_ids(packet, catalog, profile, comparisons, prior):
    """Deterministic ranking for a light edition: anchors, cited dependencies, changed facts,
    leadership extremes, and material current-window evidence stay; the rest is an explicit gap."""
    keep = {ident for ident, row in catalog.items() if row.get("topic") in ANCHOR_TOPICS}
    for trigger in packet.get("attention", []):
        keep |= set(trigger.get("evidence_ids", []))
    for record in [*(prior or {}).get("watches", []), *(prior or {}).get("relationships", [])]:
        keep |= set(record.get("evidence_refs", []))
    for comparison in comparisons or []:
        if comparison["status"] == "changed" and comparison.get("current_ref"):
            keep.add(comparison["current_ref"])
    leaders = {row["id"] for rows in packet.get("sector_leadership", {}).values() for row in rows}
    leader_topics = {row["topic"] for rows in packet.get("sector_leadership", {}).values() for row in rows}
    keep |= leaders | {ident for ident, row in catalog.items() if row.get("topic") in leader_topics
                       and row.get("metric") in {"daily return", "premarket return", "intraday return"}}
    keep |= {ident for ident, row in catalog.items()
             if row.get("magnitude") == "LARGE" and row.get("metric") in CURRENT_METRICS}
    return keep


def analyst_context(packet, profile=None, comparisons=None, prior=None):
    """One canonical copy of each admitted fact, a small legend, and caller-owned identity.

    `profile` is the edition budget profile; a "changed" context keeps anchors, cited watch
    dependencies, changed facts, leadership extremes, and material opposing evidence, and names
    what it omitted. Nothing is truncated: a fact is either supplied whole or listed as omitted.
    """
    projected = model_packet(packet)
    catalog = evidence_catalog(projected)
    valued = {ident: row for ident, row in catalog.items() if "value" in row}
    selection = None
    if profile and profile.get("context") == "changed":
        keep = _selected_ids(packet, valued, profile, comparisons, prior)
        omitted = sorted(ident for ident in valued if ident not in keep)
        selection = dict(profile=profile["profile"], mode="changed", omitted_count=len(omitted),
                         omitted_topics=sorted({valued[i]["topic"] for i in omitted}),
                         note="Omitted rows are uncited non-anchor background; they remain in the full "
                              "evidence record and the rendered tables but cannot be cited here.")
        valued = {ident: row for ident, row in valued.items() if ident in keep}
    groups, baselines = {}, {}
    events, context_items = [], []
    for row in valued.values():
        baselines.setdefault(row["metric"], row.get("baseline"))
        groups.setdefault(row["topic"], []).append(compact_fact(row))
    for row in projected["events"]:
        events.append({key: row[key] for key in ("id", "title", "scheduled_at", "session_relation", "status")
                       if row.get(key) is not None})
    for row in projected["context_items"]:
        context_items.append({key: row[key] for key in ("id", "title", "published_at") if row.get(key) is not None})
    session = projected["run"].get("session", {})
    run = dict(mode=projected["run"]["mode"], checkpoint=projected["run"]["checkpoint"],
               target_time=projected["run"]["target_time"],
               session={key: session[key] for key in
                        ("date", "trading_day", "previous_session", "open", "close", "meaningful_premarket")
                        if key in session})
    if packet["run"].get("run_id"):
        run["run_id"] = packet["run"]["run_id"]
    result = dict(
        schema_version=CONTEXT_SCHEMA,
        evidence_hash=digest(packet),
        run=run,
        edition=({key: profile[key] for key in ("checkpoint", "profile", "words", "guidance",
                                                "summary_paragraphs", "attention_items", "watches")}
                 if profile else None),
        selection=selection,
        coverage=projected["coverage"],
        baselines=baselines,
        catalog=[dict(topic=topic, rows=rows) for topic, rows in groups.items()],
        sector_leadership={key: [row["id"] for row in rows if row["id"] in valued]
                           for key, rows in projected.get("sector_leadership", {}).items()},
        attention=projected["attention"],
        events=events, context_items=context_items,
        sources=[{key: value for key, value in source.items()
                  if key in ("id", "name", "kind", "status", "feed", "data_delay", "coverage_date")
                  and value is not None
                  or (key == "reason" and value and source.get("status") != "AVAILABLE")}
                 for source in projected["sources"]],
        cuttingboard=projected["cuttingboard"],
    )
    if packet.get("history_lag"):
        result["history_lag"] = packet["history_lag"]
    if packet.get("history_errors"):
        result["history_errors"] = history_error_summary(packet, packet["history_errors"])
    return result


def supplied_ids(context):
    """Every current evidence ID the analyst was actually shown."""
    ids = {row["id"] for group in context.get("catalog", []) for row in group["rows"]}
    ids |= {row["id"] for row in context.get("events", [])}
    ids |= {row["id"] for row in context.get("context_items", [])}
    return ids

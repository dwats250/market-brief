"""The analyst reads exactly one saved, bounded projection of the admitted evidence.

`model_packet` remains the authority filter (permitted sources, usable rows) for both this
projection and the validator; this module only changes the shape the model reads and records
which facts were supplied so that references can be checked against the exact context used.
"""

import re

from .evidence import digest, evidence_catalog, model_packet

CONTEXT_SCHEMA = "market-brief.analyst-context.v1"
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


def analyst_context(packet):
    """One canonical copy of each admitted fact, a small legend, and caller-owned identity."""
    projected = model_packet(packet)
    catalog = evidence_catalog(projected)
    groups, baselines = {}, {}
    events, context_items = [], []
    for row in catalog.values():
        if "value" not in row:
            continue
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
        coverage=projected["coverage"],
        baselines=baselines,
        catalog=[dict(topic=topic, rows=rows) for topic, rows in groups.items()],
        sector_leadership={key: [row["id"] for row in rows]
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

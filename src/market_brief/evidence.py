"""Explicit records, exchange clocks, and fail-closed factual normalization."""

import copy
import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

from .schedule import CHECKPOINT_TITLES, CHECKPOINTS, checkpoint_session, next_session_date, session_relation

ROOT = Path(__file__).resolve().parents[2]
ET = ZoneInfo("America/New_York")
USABLE = {"AVAILABLE", "DELAYED", "BACKGROUND"}
FRESHNESS = {"LIVE", "DELAYED", "PRIOR_CLOSE", "DATED", "STALE", "UNAVAILABLE", "INVALID"}
SCHEMA = "market-brief.evidence.v0"


def timestamp(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timezone required")
    return dt.astimezone(timezone.utc)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def read_json(path):
    if Path(path).stat().st_size > 2_000_000:
        raise ValueError("input exceeds two megabytes")
    return json.loads(Path(path).read_text(), parse_constant=lambda _: None)


def safe_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("source URL must be plain HTTPS without credentials/query/fragment")
    return value


def session_info(now, checkpoint="PREMARKET"):
    local = now.astimezone(ET)
    cal = xcals.get_calendar("XNYS")
    day = local.date().isoformat()
    trading = cal.is_session(day)
    session = cal.date_to_session(day, direction="next")
    previous = cal.previous_session(session)
    if trading and now >= cal.session_close(session).to_pydatetime():
        previous = session
    opening = cal.session_open(session).to_pydatetime()
    closing = cal.session_close(session).to_pydatetime()
    checkpoint_data = checkpoint_session(now, checkpoint)
    return dict(date=day, trading_day=bool(trading),
                previous_session=previous.date().isoformat(),
                next_session=next_session_date(now),
                open=opening.isoformat(), close=closing.isoformat(),
                meaningful_premarket=bool(trading and local.hour >= 7 and now < opening),
                session_gap=not trading,
                checkpoint=checkpoint,
                scheduled_checkpoint_at=checkpoint_data["scheduled_at"])


def normalize_observation(raw, now):
    keys = ("id", "topic", "metric", "value", "unit", "baseline", "frequency",
            "observed_at", "retrieved_at", "source_id", "status", "reason")
    row = {key: raw.get(key) for key in keys}
    row["reason"] = row["reason"] or ""
    row["freshness"] = "UNAVAILABLE"

    def reject(status, reason):
        row.update(status=status, reason=reason, value=None,
                   freshness=status if status in FRESHNESS else "UNAVAILABLE")
        return row

    if not all(isinstance(row[k], str) and row[k] for k in
               ("id", "topic", "metric", "unit", "baseline", "source_id")):
        return reject("INVALID", "missing identity, unit, or baseline")
    if row["value"] is None:
        return reject("UNAVAILABLE", row["reason"] or "no observation supplied")
    if not finite(row["value"]):
        return reject("INVALID", "nonfinite or nonnumeric value")
    if not row["observed_at"]:
        return reject("UNKNOWN", "observation time absent")
    try:
        retrieved = timestamp(row["retrieved_at"])
        if retrieved > now + timedelta(minutes=5):
            return reject("INVALID", "retrieval time after collection window")
        if row["frequency"] == "daily":
            observed = date.fromisoformat(row["observed_at"])
            if observed > min(now.astimezone(ET).date(), retrieved.astimezone(ET).date()):
                return reject("INVALID", "future daily observation")
            if (now.astimezone(ET).date() - observed).days > 7:
                return reject("STALE", "daily background older than seven calendar days")
            row["status"] = "BACKGROUND"
            row["freshness"] = "PRIOR_CLOSE"
        elif row["frequency"] == "intraday":
            observed = timestamp(row["observed_at"])
            if observed > now or observed > retrieved:
                return reject("INVALID", "future observation")
            age = (now - observed).total_seconds()
            if age > 1200:
                return reject("STALE", "intraday observation older than twenty minutes")
            row["status"] = "DELAYED" if age > 60 else "AVAILABLE"
            row["freshness"] = "DELAYED" if age > 60 else "LIVE"
        else:
            return reject("INVALID", "unsupported frequency")
    except (TypeError, ValueError):
        return reject("INVALID", "malformed observation/retrieval clock")
    return row


def source_record(raw):
    result = {key: raw.get(key) for key in (
        "id", "name", "kind", "url", "retrieved_at", "status", "reason",
        "llm_allowed", "retention_allowed", "coverage_date", "expected_freshness",
        "provider", "feed", "plan", "data_delay", "coverage_symbols")}
    if not all(isinstance(result[k], str) and result[k] for k in
               ("id", "name", "kind", "url", "retrieved_at", "status")):
        raise ValueError("malformed source record")
    safe_url(result["url"])
    timestamp(result["retrieved_at"])
    if result["expected_freshness"] is None:
        result["expected_freshness"] = {
            "economic_series": "PRIOR_CLOSE", "calendar": "DATED", "news": "DATED",
            "price": "PRIOR_CLOSE", "quote": "LIVE"
        }.get(result["kind"], "DATED")
    if result["expected_freshness"] not in FRESHNESS:
        raise ValueError("unsupported expected freshness")
    return result


def normalize_packet(raw, now, mode, checkpoint="PREMARKET"):
    if raw.get("mode") != mode or mode not in {"LIVE", "SAMPLE"}:
        raise ValueError("sample/live input mode mismatch")
    if checkpoint not in CHECKPOINTS:
        raise ValueError("unsupported checkpoint")
    sources = [source_record(s) for s in raw.get("sources", [])]
    by_source = {s["id"]: s for s in sources}
    if len(by_source) != len(sources):
        raise ValueError("duplicate source ID")
    packet = dict(schema_version=SCHEMA, run=dict(mode=mode, checkpoint=checkpoint,
                  target_time=now.isoformat(), session=session_info(now, checkpoint)), sources=sources,
                  observations=[], history=[], derived=[], events=[], context_items=[],
                  attention=[], previous=None, cuttingboard=raw.get("cuttingboard", {
                      "status": "UNAVAILABLE", "reason": "not requested"}))
    for raw_row in raw.get("observations", []):
        row = normalize_observation(raw_row, now)
        source = by_source.get(row["source_id"])
        if not source:
            raise ValueError("observation references unknown source")
        if source["retention_allowed"] is not True:
            row.update(value=None, status="UNAVAILABLE", reason="retention not admitted")
            row["freshness"] = "UNAVAILABLE"
        row["expected_freshness"] = source["expected_freshness"]
        packet["observations"].append(row)
    for h in raw.get("history", []):
        source = by_source.get(h.get("source_id"))
        if not source:
            raise ValueError("history references unknown source")
        if source["retention_allowed"] is not True:
            continue
        packet["history"].append({key: h.get(key) for key in (
            "id", "symbol", "dates", "closes", "adjustment", "session",
            "source_id", "retrieved_at")})
    for field in ("events", "context_items"):
        for item in raw.get(field, []):
            source = by_source.get(item.get("source_id"))
            if not source:
                raise ValueError("item references unknown source")
            if source["retention_allowed"] is not True:
                continue
            required = ("id", "title", "source_id")
            if not all(isinstance(item.get(k), str) and item[k] for k in required):
                raise ValueError("malformed event/context item")
            if item.get("published_at") and timestamp(item["published_at"]) > now:
                continue
            record = {k: item.get(k) for k in (*required, "published_at", "checked_at",
                                               "scheduled_at", "status")}
            if field == "events":
                when = timestamp(record["scheduled_at"])
                checked = timestamp(record["checked_at"] or source["retrieved_at"])
                if not now - timedelta(hours=24) <= checked <= now + timedelta(minutes=5):
                    continue
                session = packet["run"]["session"]
                event_date = when.astimezone(ET).date().isoformat()
                if event_date not in {now.astimezone(ET).date().isoformat(), session["next_session"]}:
                    continue
                record["freshness"] = "DATED"
                record["expected_freshness"] = source["expected_freshness"]
                record["scheduled_at_et"] = when.astimezone(ET).isoformat()
                record["session_date"] = event_date
                # Next-session items are carried for continuity; they are never "today".
                record["session_relation"] = ("NEXT SESSION" if event_date != now.astimezone(ET).date().isoformat()
                                              else session_relation(when, timestamp(session["open"]),
                                                                    timestamp(session["close"])))
            elif now - timestamp(item["published_at"]) > timedelta(days=2):
                continue
            else:
                record["freshness"] = "DATED"
                record["expected_freshness"] = source["expected_freshness"]
            packet[field].append(record)
    identities = [row.get("id") for key in ("observations", "history", "events", "context_items")
                  for row in packet[key]]
    if (len(set(identities)) != len(identities)
            or any(not isinstance(i, str) or not re.fullmatch(r"[a-zA-Z][\w-]{0,79}", i)
                   for i in identities)):
        raise ValueError("invalid or duplicate evidence ID")
    return packet


WINDOWS = {"daily return": "1s", "twenty-session return": "20s", "fifty-session average": "50s",
           "distance from 50DMA": "50s", "regular close": "1s", "daily par yield": "1d",
           "daily yield change": "1d", "premarket return": "intraday", "intraday return": "intraday"}


def metric_identity(row):
    """Stable identity for comparisons across runs: instrument, metric, window, benchmark, basis.

    Evidence IDs such as `SPY-daily` are unique only within a run; this identity is what
    makes a later observation of the same measurement mathematically comparable.
    """
    metric = row.get("metric") or ""
    benchmark = metric.removeprefix("relative to ") if metric.startswith("relative to ") else None
    window = "20s" if benchmark else WINDOWS.get(metric, row.get("frequency") or "unknown")
    basis = row.get("adjustment") or row.get("baseline") or "unspecified"
    if row.get("adjustment"):
        basis = f"{row['adjustment']}/regular_close"
    identity = dict(instrument=row.get("topic"), metric=metric, window=window,
                    benchmark=benchmark, basis=basis)
    identity["key"] = "|".join(str(identity[k] or "-")
                               for k in ("instrument", "metric", "window", "benchmark", "basis"))
    return identity


def annotate_identity(packet):
    for field in ("observations", "derived"):
        for row in packet[field]:
            row["identity"] = metric_identity(row)
    return packet


def finalize_coverage(packet):
    annotate_identity(packet)
    anchors = {r["topic"] for r in packet["derived"] if r["metric"] == "daily return"}
    calendars = [s for s in packet["sources"] if s["kind"] == "calendar"]
    missing = [f"{s} prior-close history unavailable" for s in ("SPY", "QQQ") if s not in anchors]
    missing += [f"{s['name']}: {s['reason'] or s['status']}" for s in packet["sources"]
                if s["status"] != "AVAILABLE"]
    missing += [f"{r['topic']}: {r['reason']}" for r in packet["observations"]
                if r["status"] not in USABLE]
    missing += packet.get("history_errors", [])
    if packet.get("history_lag"):
        lag = packet["history_lag"]
        missing.append(f"Daily history through {lag['history_through']}: the completed "
                       f"{lag['completed_session']} daily bar was not yet published, so 20D, "
                       "relative, and 50D context lag one session")
    has_current = any(r["frequency"] == "intraday" and r["status"] in USABLE
                      for r in packet["observations"])
    calendar_complete = {s["id"] for s in calendars if s["status"] == "AVAILABLE"
                         and s["coverage_date"] == packet["run"]["session"]["date"]}
    ready = {"SPY", "QQQ"} <= anchors and {"bls", "bea", "fed-calendar"} <= calendar_complete
    usable = bool(packet["derived"] or packet["events"] or packet["context_items"]
                  or any(r["status"] in USABLE for r in packet["observations"]))
    status = "READY" if ready else "PARTIAL" if usable else "INSUFFICIENT"
    core_present = {"SPY", "QQQ"} <= anchors
    bootstrap = ("BASELINE" if not packet.get("previous") else
                 "FULL" if core_present else "PARTIAL")
    missing_domains = []
    if not core_present:
        missing_domains.append("equity prior-close history")
    if not has_current:
        missing_domains.append("current prints unavailable")
    if not any(r["metric"] == "daily yield change" for r in packet["observations"]):
        missing_domains.append("current Treasury change")
    packet["coverage"] = dict(status=status, bootstrap=bootstrap, current_premarket=has_current,
        limitations=list(dict.fromkeys(missing)), missing_domains=missing_domains,
        horizon=("Timestamped intraday observations available; see individual clocks."
                 if has_current else "Previous-close / dated context only; no timestamped current prints."))
    checkpoint = packet["run"]["checkpoint"]
    basis = [CHECKPOINT_TITLES.get(checkpoint, checkpoint.replace("_", " ").title()),
             "prior close", "Treasury prior-close/current as available",
             "current prints available" if has_current else "current prints unavailable",
             "breadth available" if any(r.get("topic") in {"XLI", "XLK", "XLF"} for r in packet["derived"])
             else "breadth unavailable"]
    if packet["run"]["session"].get("session_gap"):
        basis.append("session gap / holiday")
    packet["coverage"]["basis"] = "Basis: " + " · ".join(basis)
    # Integration state is retained for the ledger, not the reading line.
    packet["coverage"]["calendar"] = ("checked" if any(s["status"] == "AVAILABLE" for s in calendars)
                                      else "unavailable")
    return packet


def evidence_catalog(packet):
    return {r["id"]: r for key in ("observations", "derived", "events", "context_items")
            for r in packet[key] if r.get("status", "AVAILABLE") in USABLE | {"SCHEDULED"}}


MODEL_RECORD_FIELDS = ("id", "topic", "metric", "value", "unit", "baseline", "observed_at",
                       "source_id", "status", "reason", "frequency", "freshness",
                       "expected_freshness", "magnitude", "identity")


def compact_model_record(row):
    """Keep editorial facts and freshness while dropping repeated collection plumbing."""
    return {key: row[key] for key in MODEL_RECORD_FIELDS if key in row}


def model_packet(packet):
    result = copy.deepcopy(packet)
    allowed = {s["id"] for s in packet["sources"] if s["llm_allowed"] is True}
    result.pop("history", None)
    for field in ("observations", "derived", "events", "context_items"):
        result[field] = [compact_model_record(r) for r in result[field]
                         if r.get("source_id") in allowed and r.get("status") in USABLE | {"SCHEDULED"}]
    result["sector_leadership"] = dict(
        top=[compact_model_record(r) for r in result.get("sector_leadership", {}).get("top", [])
             if r.get("source_id") in allowed and r.get("status") in USABLE | {"SCHEDULED"}],
        bottom=[compact_model_record(r) for r in result.get("sector_leadership", {}).get("bottom", [])
                if r.get("source_id") in allowed and r.get("status") in USABLE | {"SCHEDULED"}])
    permitted_ids = set(evidence_catalog(result))
    result["attention"] = [{key: a[key] for key in
                             ("id", "symbol", "reason", "evidence_ids", "horizon", "date")
                             if key in a} for a in result["attention"]
                           if set(a["evidence_ids"]) <= permitted_ids]
    result["sources"] = [{key: s[key] for key in
                           ("id", "name", "kind", "status", "reason", "expected_freshness",
                            "provider", "feed", "plan", "data_delay", "coverage_date",
                            "coverage_symbols") if key in s}
                          for s in result["sources"] if s["id"] in allowed]
    result["run"] = {key: result["run"][key] for key in
                      ("mode", "checkpoint", "target_time", "session") if key in result["run"]}
    result["coverage"] = {key: result["coverage"][key] for key in
                           ("status", "bootstrap", "current_premarket", "basis", "missing_domains",
                            "horizon") if key in result["coverage"]}
    result["cuttingboard"] = {"status": "QUOTED_SEPARATELY_BY_RENDERER"}
    return result

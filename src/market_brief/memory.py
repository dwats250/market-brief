"""Daily session memory: a deterministic, hashed record of one accepted production close (WP-MB-WEEK-AHEAD-01 A1).

`session_memory()` is a pure projection. It reads only what the accepted CLOSE_1M already holds: the validated evidence
packet, the session handoff written from it, and the same-session premarket and interpretation records the run admitted
from its continuity bundle. It makes no model call and fetches nothing.

The record has two parts:
- `observed`: facts, each with its original evidence id, source, observation and retrieval clocks (and, for derived
  rows, formula version and inputs). A missing value is `null` with a named gap. A defect here raises, so the close
  fails closed.
- `retained`: model-authored INTERPRETATION frozen as written (the session's read, watches, verdicts and relationships)
  plus the frozen facts those cite, taken from the values the analyst saw and never from the close's own catalog. Each
  part is guarded: a failure becomes a named gap and never stops the observed record or the close.

The record lives only in the close run's folder, which the workflow's always-run archive step uploads (30 days). See
docs/2026-09-27-market-memory-week-ahead.md §3 A1 and §4.
"""

import json

from .evidence import ET, USABLE, digest, evidence_catalog, model_packet, timestamp

MEMORY_SCHEMA = "market-brief.memory.v1"
MEMORY_KIND = "session_memory"
MEMORY_FILE = "session_memory.json"
# Every row the record keeps carries these fields when its original row had them: enough to cite it again with its
# original attribution and clocks, and to recompute its metric identity (`evidence.metric_identity`).
LINEAGE_FIELDS = ("id", "topic", "metric", "value", "unit", "baseline", "observed_at", "retrieved_at", "source_id",
                  "frequency", "status", "formula_version", "input_ids", "adjustment", "window_start", "sample_count")
SOURCE_FIELDS = ("id", "name", "kind", "url", "provider", "feed", "plan", "data_delay", "expected_freshness")
RETURN_GROUPS = ("MARKET", "SECTORS", "METALS", "MEGACAPS")
RATES_SOURCES = ("treasury", "treasury-real")
RATES_PREFIX = "nyfed-"
CURVE_FIELDS = ("rule_version", "observed_at", "prior_observed_at", "freshness", "label", "rule", "changes_bp", "pair")
WATCH_TEXT = ("hypothesis", "confirmation", "contradiction")
RETAINED_LIMIT = 9  # at most three carried watches per synthesis, two syntheses, plus the close's carried three


class ProjectionError(ValueError):
    """The observed projection failed: a code defect, so the close fails closed."""


def _lineage(row):
    return {key: row[key] for key in LINEAGE_FIELDS if key in row}


# --- observed: facts with lineage --------------------------------------------------------------

def _allowed_rows(packet):
    """The close's analyst-legal rows (allowed source, usable status, not stale, not page-only), as originally
    recorded: `model_packet` decides which, the packet keeps the retrieval clock and the plumbing lineage needs."""
    allowed = set(evidence_catalog(model_packet(packet)))
    rows = {}
    for field in ("observations", "derived"):
        for row in packet[field]:
            if row["id"] in allowed and row.get("status") in USABLE and row.get("value") is not None:
                rows[row["id"]] = row
    return rows


def _session_day(row):
    """The exchange-calendar date of a row's observation: a date as written, an instant in New York time."""
    try:
        observed = str(row["observed_at"])
        return timestamp(observed).astimezone(ET).date().isoformat() if "T" in observed else observed[:10]
    except (KeyError, TypeError, ValueError):
        return None


def _returns(rows, symbols, session_date, gaps):
    """One session return per symbol: the settled daily bar when the run holds this session's, else the run's own
    last trade of this session (labeled a trade, never a close)."""
    returns = {}
    for symbol in symbols:
        daily, trade = rows.get(f"{symbol}-daily"), rows.get(f"{symbol}-intraday")
        if daily and daily.get("observed_at") == session_date:
            returns[symbol] = dict(_lineage(daily), basis="daily_bar")
        elif trade and _session_day(trade) == session_date:
            returns[symbol] = dict(_lineage(trade), basis="last_trade")
        else:
            returns[symbol] = None
            gaps.append(f"observed.returns.{symbol}: no observation from session {session_date}")
    return returns


def _leadership(packet, rows, gaps):
    selection = packet.get("sector_leadership") or {}
    top = [_lineage(rows[r["id"]]) for r in selection.get("top", []) if r.get("id") in rows]
    bottom = [_lineage(rows[r["id"]]) for r in selection.get("bottom", []) if r.get("id") in rows]
    qqq = rows.get("QQQ-spread20")
    if not top and not bottom:
        gaps.append("observed.leadership: no admitted sector leadership selection")
    if qqq is None:
        gaps.append("observed.leadership.QQQ_vs_SPY: QQQ-spread20 not admitted")
    days = sorted({r["observed_at"] for r in top + bottom})
    return dict(observed=days[-1] if days else None, benchmark="SPY", window="20 sessions", top=top, bottom=bottom,
                QQQ_vs_SPY=_lineage(qqq) if qqq else None)


def _rates(packet, rows, gaps):
    kept = [_lineage(row) for ident, row in sorted(rows.items())
            if row.get("source_id") in RATES_SOURCES or str(row.get("source_id", "")).startswith(RATES_PREFIX)]
    curve = packet.get("curve") or {}
    move = {key: curve.get(key) for key in CURVE_FIELDS if key in curve} or None
    if not any(r["source_id"] == "treasury" for r in kept):
        gaps.append("observed.rates: no admitted Treasury rows")
    if move is None or not move.get("label"):
        gaps.append("observed.rates.move: no classified curve move")
    if not any(str(r["source_id"]).startswith(RATES_PREFIX) for r in kept):
        gaps.append("observed.rates.funding: no admitted New York Fed reference rates")
    days = sorted({r["observed_at"] for r in kept if r["source_id"] == "treasury"})
    return dict(observed=days[-1] if days else None, rows=kept, move=move)


def _observed(packet, universe, gaps):
    rows = _allowed_rows(packet)
    session_date = packet["run"]["session"]["date"]
    symbols = [s for group in RETURN_GROUPS for s in universe["groups"].get(group, [])]
    return dict(returns=_returns(rows, symbols, session_date, gaps), leadership=_leadership(packet, rows, gaps),
                rates=_rates(packet, rows, gaps))


# --- retained: frozen interpretation, guarded -------------------------------------------------

def _read(interpretation):
    narrative = interpretation["narrative"]
    origin = interpretation["origin"]
    return dict(checkpoint=origin["checkpoint"], run_id=origin["run_id"], hash=interpretation["content_hash"],
                evidence_cutoff=origin["target_time"], label=narrative["banner"]["label"],
                character=narrative["character"]["text"])


def _verdicts(watch, synthesis_runs):
    """Only the analyst's verdicts from this session's own syntheses count; `new` is creation, not a verdict, and a
    deterministic drop is an `end`, not a verdict."""
    return [dict(status=a["status"], by=synthesis_runs[a["run_id"]]) for a in watch.get("assessments", [])
            if a.get("origin") == "analyst" and a.get("run_id") in synthesis_runs and a.get("status") != "new"]


def _end(watch):
    statuses = [a.get("status") for a in watch.get("assessments", [])]
    if watch.get("lifecycle") == "retired":
        if "reversed" in statuses:
            return "reversed"
        return "dropped"
    return "expired" if watch.get("lifecycle") == "expired" else "open"


def _watch_entry(watch, synthesis_runs, facts, end=None, extra_verdicts=()):
    return dict(id=watch["id"], **{"from": watch.get("checkpoint")},
                **{key: watch.get(key) for key in WATCH_TEXT},
                horizon=(watch.get("horizon") or {}).get("declared"),
                facts=facts,
                verdicts=[*_verdicts(watch, synthesis_runs), *extra_verdicts], end=end or _end(watch),
                evaluability=watch.get("evaluability"))


def _premarket_watches(premarket):
    if not premarket:
        return {}
    return {w["id"]: w for w in [*premarket["assessment"].get("watches", []),
                                 *premarket["assessment"].get("retired", [])]}


def _with_lineage(row, fuller=None):
    """A frozen row, with any lineage field it lacks taken only from the same run's fuller copy of the same
    observation (same id, value and observation clock); never from another run's row."""
    fact = {key: row[key] for key in LINEAGE_FIELDS if key in row}
    if fuller and fuller.get("value") == row.get("value") and fuller.get("observed_at") == row.get("observed_at"):
        for key in LINEAGE_FIELDS:
            if key not in fact and key in fuller:
                fact[key] = fuller[key]
    return fact


def _frozen_facts(owner, refs, sources, synthesis_runs, gaps):
    """One watch's or relationship's cited rows, at the values its author saw, never the close's own catalog.

    `sources` is an ordered list of (rows, run_id, fuller rows from the same run): the first that holds a valued row
    for a ref supplies it, labeled with that run (`frozen_by`); a fact without its source or retrieval clock is a
    named gap."""
    facts = {}
    for ref in refs:
        found = next(((rows[ref], run, fuller) for rows, run, fuller in sources
                      if run and (rows.get(ref) or {}).get("value") is not None), None)
        if found is None:
            gaps.append(f"retained.facts.{ref}: value cited by {owner} unavailable")
            continue
        row, run, fuller = found
        fact = _with_lineage(row, (fuller or {}).get(ref))
        fact["frozen_by"] = dict(run_id=run, checkpoint=synthesis_runs.get(run))
        if not fact.get("source_id") or not fact.get("retrieved_at"):
            gaps.append(f"retained.facts.{ref}: frozen by {run} without its source or retrieval clock")
        facts[ref] = fact
    return facts


def _views(interpretation, premarket):
    premarket_run = (premarket or {}).get("origin", {}).get("run_id")
    interpretation_run = (interpretation or {}).get("origin", {}).get("run_id")
    snapshots = ((premarket or {}).get("observed") or {}).get("snapshots") or {}
    evidence = (interpretation or {}).get("evidence") or {}
    return premarket_run, interpretation_run, snapshots, evidence


def _watches(handoff, premarket, interpretation, synthesis_runs, gaps):
    """The session's watches with their same-session verdicts, how each ended and the facts each cited: the close's
    carried ones (active or expired), those the premarket retired, and those the later synthesis no longer carried,
    recovered from the premarket's own record (its assessments and values) and the interpretation's frozen prior
    state (its lifecycle then): reversed by that synthesis's verdict, expired at its horizon, or dropped by the
    three-watch carry limit."""
    premarket_run, interpretation_run, snapshots, evidence = _views(interpretation, premarket)
    by_premarket = _premarket_watches(premarket)
    seen, watches = set(), []

    def facts_for(watch):
        """A watch's cited rows, at the values its author saw, each credited to the run that froze it.

        The close's own copy of a watch's values is never read (a close fills a legacy watch's values from its own
        catalog). A watch the later synthesis wrote: that synthesis's frozen evidence. A watch the premarket wrote:
        its frozen criterion values, then the premarket's snapshots. A watch carried from an earlier session: only the
        rows today's own assessments of it cited, from the run that assessed; anything else is in its own session's
        record, a named gap here."""
        origin = watch.get("origin_run_id")
        refs = list(watch.get("evidence_refs", []))
        if bool(interpretation_run) and origin == interpretation_run:
            return _frozen_facts(watch["id"], refs, [(evidence, interpretation_run, evidence)], synthesis_runs, gaps)
        if origin and origin == premarket_run:
            values = (by_premarket.get(watch["id"]) or {}).get("values") or {}
            return _frozen_facts(watch["id"], refs, [(values, premarket_run, snapshots),
                                                     (snapshots, premarket_run, snapshots)], synthesis_runs, gaps)
        # Carried from an earlier session: its criterion values cannot be told apart from values a later carry filled
        # in, so only the rows today's own assessments cited are kept, from the run that assessed (either copy of
        # the watch may hold that assessment).
        facts = {}
        frozen_by = {premarket_run: snapshots, interpretation_run: evidence}
        seen_assessments, assessed = set(), []
        for copy in (by_premarket.get(watch["id"]) or {}, watch):
            for a in copy.get("assessments", []):
                key = (a.get("run_id"), a.get("status"), a.get("assessed_at"))
                if (a.get("origin") == "analyst" and a.get("run_id") in synthesis_runs
                        and key not in seen_assessments):
                    seen_assessments.add(key)
                    assessed.append(a)
        for assessment in assessed:
            run = assessment["run_id"]
            cited = [ref for ref in assessment.get("current_refs", []) if ref in refs and ref not in facts]
            facts.update(_frozen_facts(watch["id"], cited, [(frozen_by.get(run) or {}, run, frozen_by.get(run))],
                                       synthesis_runs, gaps))
        rest = [ref for ref in refs if ref not in facts]
        if rest:
            gaps.append(f"retained.watches.{watch['id']}: carried from an earlier session; "
                        f"{', '.join(rest)} are in that session's record")
        return facts

    for watch in [*handoff["assessment"]["watches"], *(premarket["assessment"].get("retired", []) if premarket
                                                       else [])]:
        if watch["id"] not in seen:
            seen.add(watch["id"])
            watches.append(_watch_entry(watch, synthesis_runs, facts_for(watch)))
    if interpretation and interpretation["origin"]["checkpoint"] != "PREMARKET":
        by = synthesis_runs.get(interpretation_run)
        updates = {u["carried_id"]: u for u in interpretation["narrative"].get("watch_updates", [])}
        for frozen in (interpretation.get("prior_state") or {}).get("watches", []):
            if frozen.get("id") in seen:
                continue
            seen.add(frozen["id"])
            update = updates.get(frozen["id"])
            verdicts = [dict(status=update["assessment"], by=by)] if update and by else []
            if update and update["assessment"] == "reversed":
                ended = "reversed"
            elif frozen.get("lifecycle") == "expired":
                ended = "expired"
            else:
                ended = "dropped"
            # The premarket's own record of the watch carries its checkpoint, assessments and values.
            watch = dict(by_premarket.get(frozen["id"]) or dict(frozen, checkpoint=None))
            watches.append(_watch_entry(watch, synthesis_runs, facts_for(watch), ended, verdicts))
    return watches[:RETAINED_LIMIT]


def _judged(interpretation, synthesis_runs):
    """Verdicts the session's later synthesis gave carried watches (`watch_updates`), including watches it retired."""
    if not interpretation or interpretation["origin"]["checkpoint"] == "PREMARKET":
        return []
    by = synthesis_runs.get(interpretation["origin"]["run_id"])
    return [dict(id=u["carried_id"], status=u["assessment"], by=by)
            for u in interpretation["narrative"].get("watch_updates", []) if by]


def _relationships(handoff, synthesis_runs, interpretation=None, premarket=None, gaps=None):
    """Each carried relationship with this session's own analyst statuses and the facts behind them: for one written
    today, the rows its creation cited, frozen by that run; for one carried from an earlier session, only the rows
    today's own assessments cited, frozen by the run that assessed (its original facts are in its own session's
    record)."""
    gaps = gaps if gaps is not None else []
    premarket_run, interpretation_run, snapshots, evidence = _views(interpretation, premarket)
    frozen_by = {premarket_run: snapshots, interpretation_run: evidence}
    result = []
    for r in handoff["assessment"].get("relationships", []):
        assessed = [a for a in r.get("assessments", []) if a.get("origin") == "analyst"
                    and a.get("run_id") in synthesis_runs]
        facts = {}
        for assessment in assessed:
            run = assessment["run_id"]
            refs = [ref for ref in assessment.get("current_refs", []) if ref not in facts]
            facts.update(_frozen_facts(r["id"], refs, [(frozen_by.get(run) or {}, run, frozen_by.get(run))],
                                       synthesis_runs, gaps))
        rest = [ref for ref in r.get("evidence_refs", []) if ref not in facts]
        if r.get("origin_run_id") not in synthesis_runs and rest:
            gaps.append(f"retained.relationships.{r['id']}: carried from an earlier session; "
                        f"{', '.join(rest)} are in the session record that cited them")
        result.append(dict(id=r["id"], statement=r.get("statement"), facts=facts,
                           carried=r.get("origin_run_id") not in synthesis_runs,
                           statuses=[dict(status=a.get("status"), by=synthesis_runs[a["run_id"]]) for a in assessed]))
    return result


def _guarded(name, gaps, build, default):
    try:
        return build()
    except Exception as exc:  # noqa: BLE001 - an optional part never stops the record or the close
        gaps.append(f"{name}: not recorded ({type(exc).__name__})")
        return default


def _admitted(bundle, packet):
    """The same-session premarket edition and interpretation, by the same rules the close's own continuity admission
    applies (schema, kind, hash, production origin, an evidence cutoff before this close, this session)."""
    from .continuity import _origin_reason, admit_interpretation
    interpretation, _ = admit_interpretation(bundle, packet)
    premarket = bundle.get("premarket")
    if (_origin_reason(premarket, packet, "edition_state") is not None
            or premarket["session"]["date"] != packet["run"]["session"]["date"]
            or premarket["origin"].get("checkpoint") != "PREMARKET"):
        premarket = None
    return interpretation, premarket


EMPTY_RETAINED = {"class": "INTERPRETATION", "read": None, "watches": [], "judged": [], "relationships": []}


def _retained(packet, handoff, bundle, gaps):
    interpretation, premarket = _admitted(bundle, packet)
    synthesis_runs = {}
    if premarket:
        synthesis_runs[premarket["origin"]["run_id"]] = "PREMARKET"
    else:
        gaps.append("retained.premarket: no accepted premarket edition for this session")
    if interpretation:
        synthesis_runs[interpretation["origin"]["run_id"]] = interpretation["origin"]["checkpoint"]
    else:
        gaps.append("retained.read: no accepted interpretation for this session")
    read = _guarded("retained.read", gaps, lambda: _read(interpretation) if interpretation else None, None)
    watches = _guarded("retained.watches", gaps,
                       lambda: _watches(handoff, premarket, interpretation, synthesis_runs, gaps), [])
    judged = _guarded("retained.judged", gaps, lambda: _judged(interpretation, synthesis_runs), [])
    relationships = _guarded("retained.relationships", gaps,
                             lambda: _relationships(handoff, synthesis_runs, interpretation, premarket, gaps), [])
    return {"class": "INTERPRETATION", "read": read, "watches": watches, "judged": judged,
            "relationships": relationships}


# --- the record --------------------------------------------------------------------------------

def retained_facts(retained):
    """Every frozen fact the retained watches and relationships cite, each with its owner."""
    return [(owner["id"], ref, fact) for owner in [*retained.get("watches", []), *retained.get("relationships", [])]
            for ref, fact in (owner.get("facts") or {}).items()]


def _sources(packet, observed, retained):
    used = set()
    returns = [r for r in observed["returns"].values() if r]
    lead = observed["leadership"]
    for row in [*returns, *lead["top"], *lead["bottom"], lead["QQQ_vs_SPY"] or {}, *observed["rates"]["rows"],
                *(fact for _, _, fact in retained_facts(retained))]:
        if row.get("source_id"):
            used.add(row["source_id"])
    return {s["id"]: {key: s[key] for key in SOURCE_FIELDS if s.get(key) is not None}
            for s in packet["sources"] if s["id"] in used}


def session_memory(packet, handoff, bundle, universe, run):
    """Project one accepted production close into its session record.

    `run` names the close run: run_id, target_time, code_revision, evidence_hash. Raises `ProjectionError` when the
    observed projection fails; optional retained parts become named gaps instead.
    """
    gaps = []
    try:
        observed = _observed(packet, universe, gaps)
        session = packet["run"]["session"]
        source = dict(mode=packet["run"]["mode"], commissioning=bool(packet["run"].get("commissioning")),
                      experiment=bool(packet["run"].get("experiment")), checkpoint=packet["run"]["checkpoint"],
                      run_id=run["run_id"], target_time=run["target_time"], code_revision=run["code_revision"],
                      evidence_hash=run["evidence_hash"], handoff_hash=handoff["content_hash"],
                      closing_data=handoff["observed"]["closing_data"]["status"],
                      written_by=run.get("written_by", "close"))
    except Exception as exc:  # noqa: BLE001 - a projection defect fails the close closed, with a truthful reason
        raise ProjectionError(f"session memory projection failed ({type(exc).__name__}: {exc})") from None
    try:
        retained = _retained(packet, handoff, bundle or {}, gaps)
    except Exception as exc:  # noqa: BLE001 - optional: the observed record and the close stand without it
        gaps.append(f"retained: not recorded ({type(exc).__name__})")
        retained = json.loads(json.dumps(EMPTY_RETAINED))
    try:
        sources = _sources(packet, observed, retained)
        record = dict(schema_version=MEMORY_SCHEMA, kind=MEMORY_KIND,
                      session=dict(date=session["date"], open=session["open"], close=session["close"]),
                      source=source, sources=sources, observed=observed, retained=retained, gaps=gaps)
        record = json.loads(json.dumps(record, allow_nan=False))
    except Exception as exc:  # noqa: BLE001
        raise ProjectionError(f"session memory projection failed ({type(exc).__name__}: {exc})") from None
    return dict(record, content_hash=digest(record))


def verify(record):
    """Whether a record is a session memory whose content hash verifies (schema, kind and hash only)."""
    if not isinstance(record, dict) or record.get("schema_version") != MEMORY_SCHEMA \
            or record.get("kind") != MEMORY_KIND:
        return False
    body = {key: value for key, value in record.items() if key != "content_hash"}
    return record.get("content_hash") == digest(body)


def eligible(kind, production, handoff):
    """A record is written only by an accepted production close that handed the session off."""
    return kind == "close" and production and handoff is not None


# --- B2: restore, acceptance and admission for the Sunday edition ---------------------------------

RUN_ARTIFACT_PREFIX = "market-brief-run-CLOSE_1M-"
CONTINUITY_ARTIFACT = "market-brief-continuity"
RESTORE_PAGES = 5
CANDIDATES_PER_SESSION = 2
WEEK_DIR = ("runs", "week")
RESTORED_INDEX = "restored.json"
ARCHIVED_STATUS = "BACKGROUND"
SESSION_METRIC = "session return"
# Rows a week context keeps from the archived rates: the levels and daily changes a reader can follow through the
# week. The rest (horizons, volatility, real yields, funding) stay in the evidence record and the ledger.
WEEK_RATE_TOPICS = ("US 2Y", "US 10Y", "US 30Y", "US 2s10s")
WEEK_RATE_METRICS = ("daily par yield", "daily yield change", "curve spread")


def compact_day(day):
    return str(day).replace("-", "")


def accept(record, metadata, week, allow_reconstructed=False):
    """Why a restored record is not accepted for this week, or None.

    The record must verify (schema, kind, hash), come from a production close (LIVE, neither experiment nor
    commissioning, CLOSE_1M), belong to a session of the completed week, and agree with its sibling metadata: the same
    run and evidence, validation PASS and continuity advanced. A reconstructed record is accepted only when asked
    (an isolated experiment), never by the production edition."""
    if not verify(record):
        return "record does not verify (schema, kind or content hash)"
    source = record["source"]
    if source.get("mode") != "LIVE" or source.get("experiment") or source.get("commissioning"):
        return "not a production record"
    if source.get("checkpoint") != "CLOSE_1M":
        return "not a close record"
    if source.get("written_by") != "close" and not (allow_reconstructed and source.get("written_by")
                                                     == "reconstruction"):
        return f"written by {source.get('written_by')}, not by the close"
    if record["session"]["date"] not in week:
        return f"session {record['session']['date']} is outside the completed week"
    if not isinstance(metadata, dict):
        return "sibling metadata missing"
    if (metadata.get("validation") != "PASS" or not (metadata.get("continuity") or {}).get("advanced")
            or metadata.get("checkpoint") != "CLOSE_1M" or metadata.get("mode") != "LIVE"
            or metadata.get("experiment") or metadata.get("commissioning")):
        return "sibling metadata is not an accepted production close (PASS and advanced)"
    if metadata.get("run_id") != source.get("run_id") or metadata.get("evidence_hash") != source.get("evidence_hash"):
        return "sibling metadata names another run or evidence"
    return None


def load_week(root, week, allow_reconstructed=False, base=None):
    """Every accepted record for the week found under `runs/week/` (or `base`), one per session, the earliest.

    Returns (records, report). `report` names the sessions, the records used with their hashes and where they were
    restored from, the missing sessions and every ignored record with its reason."""
    from pathlib import Path
    base = Path(base) if base else Path(root).joinpath(*WEEK_DIR)
    index = {}
    if (base / RESTORED_INDEX).is_file():
        try:
            index = json.loads((base / RESTORED_INDEX).read_text()).get("restored_from", {})
        except (OSError, ValueError, AttributeError):
            index = {}
    candidates, ignored = {}, []
    for path in sorted(base.rglob(MEMORY_FILE)) if base.is_dir() else []:
        if path.is_symlink():
            ignored.append(dict(path=str(path.relative_to(base)), reason="symlink"))
            continue
        try:
            record = json.loads(path.read_text())
            metadata = json.loads((path.parent / "metadata.json").read_text()) \
                if (path.parent / "metadata.json").is_file() else None
        except (OSError, ValueError):
            ignored.append(dict(path=str(path.relative_to(base)), reason="unreadable"))
            continue
        reason = accept(record, metadata, week, allow_reconstructed)
        if reason:
            ignored.append(dict(path=str(path.relative_to(base)), reason=reason))
            continue
        record = dict(record)
        candidates.setdefault(record["session"]["date"], []).append((record["source"]["target_time"], record, path))
    records = []
    for day in week:
        found = sorted(candidates.get(day, []), key=lambda item: item[0])
        if not found:
            continue
        _, record, path = found[0]
        origin = index.get(record["source"]["run_id"]) or {}
        records.append(dict(record=record, restored_from=dict(artifact_id=origin.get("artifact_id"),
                                                              workflow_run_id=origin.get("workflow_run_id"))))
        for _, other, other_path in found[1:]:
            ignored.append(dict(path=str(other_path.relative_to(base)), reason=f"a later record for {day}"))
    used = {item["record"]["session"]["date"] for item in records}
    report = dict(sessions=list(week), missing=[day for day in week if day not in used], ignored=ignored,
                  records={item["record"]["session"]["date"]: dict(
                      run_id=item["record"]["source"]["run_id"], content_hash=item["record"]["content_hash"],
                      written_by=item["record"]["source"]["written_by"], **item["restored_from"])
                      for item in records})
    return records, report


def _archived_row(row, ident, metric, record, restored_from, **extra):
    kept = {key: row[key] for key in LINEAGE_FIELDS if key in row and key != "id"}
    kept.update(id=ident, metric=metric, status=ARCHIVED_STATUS, archived=True, **extra)
    kept["lineage"] = dict(original_id=row["id"], original_status=row.get("status"),
                           session_date=record["session"]["date"],
                           run_id=record["source"]["run_id"], record_hash=record["content_hash"],
                           restored_from=restored_from)
    return kept


def _fact_suffix(row):
    observed = str(row.get("observed_at") or "")
    if "T" in observed:
        when = timestamp(observed).astimezone(ET)
        return when.strftime("%Y%m%dT%H%M")
    return compact_day(observed)


def archived_rows(records):
    """Each record's observations as dated rows that keep their original source, clocks and formula lineage.

    Returns (rows, sources, gaps): `sources` are the records' archived descriptors, by id; identical frozen facts
    from two sessions are one row, and a conflicting one is a named gap."""
    rows, sources, gaps, seen = [], {}, [], {}

    def add(row):
        prior = seen.get(row["id"])
        if prior is None:
            seen[row["id"]] = row
            rows.append(row)
        elif (prior.get("value"), prior.get("observed_at")) != (row.get("value"), row.get("observed_at")):
            gaps.append(f"archived {row['id']}: two records disagree; the first is kept")
    for item in records:
        record, origin = item["record"], item["restored_from"]
        day = compact_day(record["session"]["date"])
        for source_id, descriptor in record.get("sources", {}).items():
            sources.setdefault(source_id, descriptor)
        observed = record["observed"]
        for symbol, row in observed["returns"].items():
            if row:
                add(_archived_row(row, f"{symbol}-session-{day}", SESSION_METRIC, record, origin,
                                  basis_note=row.get("basis"), original_metric=row.get("metric")))
        lead = observed["leadership"]
        for row in [*lead.get("top", []), *lead.get("bottom", []), *([lead["QQQ_vs_SPY"]] if lead.get(
                "QQQ_vs_SPY") else [])]:
            add(_archived_row(row, f"{row['id']}-{_fact_suffix(row)}", row["metric"], record, origin))
        for row in observed["rates"]["rows"]:
            add(_archived_row(row, f"{row['id']}-{_fact_suffix(row)}", row["metric"], record, origin))
        for _, ident, row in retained_facts(record["retained"]):
            if row.get("value") is None or not row.get("metric"):
                continue
            add(_archived_row(dict(row, id=ident), f"{ident}-{_fact_suffix(row)}", row["metric"], record, origin,
                              frozen=True, frozen_by=row.get("frozen_by")))
    return rows, sources, gaps


def admit_archived(packet, records):
    """Append the archived rows to the packet as citable, dated BACKGROUND rows, and admit each referenced source the
    current collection lacks from its archived descriptor. No row is attributed to the restore channel."""
    from .evidence import metric_identity
    rows, descriptors, gaps = archived_rows(records)
    unattributed = [row for row in rows if not row.get("source_id")]
    if unattributed:
        # A row without its original source cannot be cited with its attribution: withheld, and named.
        gaps.append("archived rows without an original source, withheld: "
                    + ", ".join(sorted(row["id"] for row in unattributed)))
        rows = [row for row in rows if row.get("source_id")]
    present = {s["id"] for s in packet["sources"]}
    current = {r["id"] for key in ("observations", "derived") for r in packet[key]}
    for source_id in sorted({row["source_id"] for row in rows} - present):
        descriptor = descriptors.get(source_id)
        if not descriptor or not descriptor.get("url"):
            gaps.append(f"source {source_id}: no archived descriptor; its rows are withheld")
            rows = [row for row in rows if row["source_id"] != source_id]
            continue
        packet["sources"].append(dict(
            {key: descriptor.get(key) for key in SOURCE_FIELDS}, retrieved_at=min(
                row["retrieved_at"] for row in rows if row["source_id"] == source_id and row.get("retrieved_at")),
            status="AVAILABLE", reason="archived descriptor from the week's session records", llm_allowed=True,
            retention_allowed=True, coverage_date=None, coverage_symbols=None, archived=True))
    for row in rows:
        if row["id"] in current:
            gaps.append(f"archived {row['id']}: collides with a current row; withheld")
            continue
        row["identity"] = metric_identity(row)
        packet["derived"].append(row)
    return packet, gaps


def rewrite_placeholders(text, facts, suffixes):
    from .evidence import PLACEHOLDER

    def replace(match):
        ident = match.group(1)
        return "{{" + suffixes[ident] + "}}" if ident in suffixes else "(value unavailable)"
    return PLACEHOLDER.sub(replace, text or "")


def _suffixes(facts):
    return {ident: f"{ident}-{_fact_suffix(row)}" for ident, row in (facts or {}).items()
            if row.get("value") is not None and row.get("metric")}


def week_note(records):
    """The week's earlier hypotheses and their verdicts: INTERPRETATION, not evidence.

    Each watch and relationship appears once, from the session that first recorded it, with its same-session verdict
    trail across the week and how it ended. Placeholders in old text are rewritten to the archived ids of the facts
    that watch froze (or "(value unavailable)"). Per session only the accepted read's label and character; no
    headline or take."""
    sessions, watches, relationships = [], {}, {}
    for item in records:
        record = item["record"]
        retained = record["retained"]
        day = record["session"]["date"]
        read = retained.get("read")
        sessions.append(dict(date=day, closing_data=record["source"]["closing_data"],
                             read=dict(checkpoint=read["checkpoint"], label=read["label"],
                                       character=read["character"]) if read else None,
                             gaps=len(record.get("gaps", []))))
        for w in retained.get("watches", []):
            suffixes = _suffixes(w.get("facts"))
            entry = watches.setdefault(w["id"], dict(
                id=w["id"], first=day, **{"from": w.get("from")},
                hypothesis=rewrite_placeholders(w.get("hypothesis"), None, suffixes), facts=set(), verdicts=[],
                end=None))
            entry["facts"] |= set(suffixes.values())
            entry["verdicts"] += [dict(v, day=day) for v in w.get("verdicts", [])]
            entry["end"] = w.get("end")
        for r in retained.get("relationships", []):
            suffixes = _suffixes(r.get("facts"))
            entry = relationships.setdefault(r["id"], dict(
                id=r["id"], first=day, statement=rewrite_placeholders(r.get("statement"), None, suffixes),
                facts=set(), statuses=[]))
            entry["facts"] |= set(suffixes.values())
            entry["statuses"] += [dict(s, day=day) if isinstance(s, dict) else dict(status=s, day=day)
                                  for s in r.get("statuses", [])]
    for entry in [*watches.values(), *relationships.values()]:
        entry["facts"] = sorted(entry["facts"])
    return {"class": "INTERPRETATION",
            "note": ("Earlier analyst hypotheses and their same-session verdicts, as written at the time. They are "
                     "not evidence: describe the week from the dated rows and cite those."),
            "sessions": sessions, "watches": list(watches.values()), "relationships": list(relationships.values())}


def reconstruct(folder, bundle, universe, restored_from):
    """Rebuild a close's session record offline from its archived run folder and the continuity bundle that same run
    uploaded, for an isolated experiment only. Every input is verified first: the evidence against its metadata hash,
    the handoff's own hash and evidence, the bundle's record hashes (by the loader) and its close slot naming this run.
    The result says `written_by: reconstruction`; a production week ahead never accepts it."""
    from pathlib import Path

    from .continuity import _hash_ok
    folder = Path(folder)
    metadata = json.loads((folder / "metadata.json").read_text())
    packet = json.loads((folder / "evidence.json").read_text())
    handoff = json.loads((folder / "session_handoff.json").read_text())
    if digest(packet) != metadata.get("evidence_hash"):
        raise ValueError("archived evidence does not match its metadata hash")
    if not _hash_ok(handoff) or handoff["origin"].get("evidence_hash") != metadata["evidence_hash"]:
        raise ValueError("archived handoff does not verify against its evidence")
    close = bundle.get("close") or {}
    if (close.get("origin") or {}).get("run_id") != metadata.get("run_id"):
        raise ValueError("the continuity bundle was not uploaded by this close")
    run = dict(run_id=metadata["run_id"], target_time=metadata["target_time"],
               code_revision=metadata.get("code_revision", "unknown"), evidence_hash=metadata["evidence_hash"],
               written_by="reconstruction")
    record = session_memory(packet, handoff, bundle, universe, run)
    body = {key: value for key, value in record.items() if key != "content_hash"}
    body["source"] = dict(body["source"], reconstructed_from=restored_from)
    return dict(body, content_hash=digest(body))

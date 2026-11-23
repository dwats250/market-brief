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

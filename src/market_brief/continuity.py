"""Structured session state: admission, deterministic comparisons, packaging, validation.

Deterministic code owns every persistent identity here (watch, relationship, comparison and
snapshot references). The analyst proposes new items and assessments of carried items; it never
names or renames a carried record. Observed/comparable change (`observed`) and model-authored
assessment (`assessment`) stay in separate blocks of every saved artifact so the point-in-time
factual record remains usable on its own.
"""

import json
import uuid
from datetime import timedelta
from pathlib import Path

import exchange_calendars as xcals

from .curve import SPREAD_CHANGE, SPREAD_LEVEL
from .evidence import ET, PLACEHOLDER, USABLE, digest, evidence_catalog, model_packet, read_json, timestamp
from .schedule import CHECKPOINT_TITLES, VANCOUVER, next_session_date, next_synthesis

CONTINUITY_SCHEMA = "market-brief.continuity.v1"
BUNDLE_SCHEMA = "market-brief.continuity-bundle.v1"
ANCHORS = ("previous_close", "premarket", "latest")
# Bundle slots: the previous session's close handoff, this session's premarket and latest accepted
# edition states, and the last accepted synthesis frozen for deterministic refreshes to render.
SLOTS = ("close", "premarket", "latest", "interpretation")
INTERPRETATION_FIELDS = ("id", "topic", "metric", "value", "unit", "baseline", "observed_at", "frequency",
                         "status", "magnitude", "title", "published_at", "scheduled_at", "session_relation")
ASSESSMENTS = ("new", "strengthened", "weakened", "reversed", "unresolved")
CARRIED_ASSESSMENTS = ("strengthened", "weakened", "reversed", "unresolved")
LIFECYCLES = ("active", "expired", "retired")
EVALUABILITY = ("assessable", "missing_evidence", "not_comparable")
COMPARISON_STATUSES = ("changed", "unavailable", "no_new_observation", "not_comparable")
CLOSING_DATA = ("COMPLETED_SESSION", "PROVISIONAL_NEAR_CLOSE", "EARLIER_HISTORY_ONLY", "NONE")
ANCHOR_INSTRUMENTS = ("SPY", "QQQ", "GLD", "US 2Y", "US 10Y")
CARRY_LIMIT = 3
CURVE_METRICS = {SPREAD_LEVEL, SPREAD_CHANGE}
SNAPSHOT_FIELDS = ("id", "topic", "metric", "value", "unit", "baseline", "observed_at", "frequency",
                   "status", "magnitude", "identity")
HORIZON_PHRASES = {"OPENING_HOUR": "Through the opening hour", "SESSION": "Into the close",
                   "NEXT_CLOSE": "Into the next close", "NEXT_BRIEF": "By the next interpretation"}


def _hashed(record):
    body = {key: value for key, value in record.items() if key != "content_hash"}
    return dict(body, content_hash=digest(body))


def _hash_ok(record):
    return isinstance(record, dict) and record.get("content_hash") == digest(
        {key: value for key, value in record.items() if key != "content_hash"})


# --- horizons ---------------------------------------------------------------------------------

def resolve_horizon(horizon, now, events=(), current_checkpoint=None):
    """Turn a declared horizon into a resolved expiry session/time and a natural phrase.

    NEXT_BRIEF is the next synthesis checkpoint, the next time an analyst can judge the watch; a
    deterministic refresh in between only carries it. After the last synthesis of the day that is the
    next session's premarket.
    """
    cal = xcals.get_calendar("XNYS")
    day = now.astimezone(ET).date().isoformat()
    session = cal.date_to_session(day, direction="next")
    trading_today = cal.is_session(day)
    opening = cal.session_open(session).to_pydatetime()
    closing = cal.session_close(session).to_pydatetime()
    following = next_session_date(now)
    if horizon.startswith("EVENT("):
        ident = horizon[6:-1]
        event = next((row for row in events if row.get("id") == ident), None)
        if event is None:
            raise ValueError("watch references unknown event horizon")
        when = timestamp(event["scheduled_at"])
        return dict(declared=horizon, expires_at=when.isoformat(),
                    expires_session=when.astimezone(ET).date().isoformat(),
                    phrase=f"Around {event['title']}")
    if horizon == "OPENING_HOUR":
        end = opening + timedelta(minutes=60)
        if trading_today and now < end:
            return dict(declared=horizon, expires_at=end.isoformat(), expires_session=day,
                        phrase=HORIZON_PHRASES[horizon])
        # Today's opening hour has passed (or today is closed): the next session's opening hour.
        next_open = cal.next_session(session).date().isoformat() if trading_today else following
        later = cal.session_open(next_open).to_pydatetime() + timedelta(minutes=60)
        return dict(declared=horizon, expires_at=later.isoformat(), expires_session=next_open,
                    phrase="Through the next opening hour")
    if horizon in {"SESSION", "NEXT_CLOSE"}:
        if trading_today and now < closing:
            return dict(declared=horizon, expires_at=closing.isoformat(), expires_session=day,
                        phrase="Into the close")
        later = cal.session_close(following).to_pydatetime()
        return dict(declared=horizon, expires_at=later.isoformat(), expires_session=following,
                    phrase="Into the next session")
    if horizon == "NEXT_BRIEF":
        info = next_synthesis(now, current_checkpoint)
        when = timestamp(info["scheduled_at"]).astimezone(VANCOUVER)
        phrase = (f"By the {when.strftime('%-I:%M %p')} PT update" if info["session_date"] == day
                  else "By the next session's premarket")
        return dict(declared=horizon, expires_at=info["scheduled_at"], expires_session=info["session_date"],
                    phrase=phrase, next_checkpoint=info["checkpoint"],
                    next_checkpoint_label=CHECKPOINT_TITLES.get(info["checkpoint"], info["checkpoint"]))
    raise ValueError("unsupported watch horizon")


# --- bundle persistence -----------------------------------------------------------------------

def empty_bundle():
    return dict(schema_version=BUNDLE_SCHEMA, close=None, premarket=None, latest=None, interpretation=None,
                updated_at=None, updated_by_run=None)


def bundle_path(root):
    return Path(root) / "runs" / "continuity" / "bundle.json"


def load_bundle(path):
    """Read a bundle fail-closed: wrong schema or a record whose hash does not verify is absent."""
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        return empty_bundle(), "no continuity bundle"
    try:
        value = read_json(path)
    except (OSError, ValueError, TypeError):
        return empty_bundle(), "continuity bundle unreadable"
    if not isinstance(value, dict) or value.get("schema_version") != BUNDLE_SCHEMA:
        return empty_bundle(), "continuity bundle schema mismatch"
    bundle = empty_bundle()
    dropped = []
    for slot in SLOTS:
        record = value.get(slot)
        if record is None:
            continue
        if _hash_ok(record) and record.get("schema_version") == CONTINUITY_SCHEMA:
            bundle[slot] = record
        else:
            dropped.append(slot)
    bundle["updated_at"], bundle["updated_by_run"] = value.get("updated_at"), value.get("updated_by_run")
    return bundle, ("corrupt continuity records dropped: " + ", ".join(dropped)) if dropped else ""


def write_bundle(path, bundle):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("continuity bundle cannot be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f"bundle-{uuid.uuid4().hex}.tmp"
    temporary.write_text(json.dumps(bundle, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(path)
    return path


def advance_bundle(bundle, state, handoff, now, interpretation=None):
    """Edition and close pointers stay separate: a premarket never overwrites the previous close.

    Only a synthesis edition supplies an `interpretation`; a deterministic refresh advances the observed
    state and leaves the frozen interpretation exactly as the analyst produced it.
    """
    updated = dict(bundle, latest=state, updated_at=now.isoformat(), updated_by_run=state["origin"]["run_id"])
    if state["origin"]["checkpoint"] == "PREMARKET":
        updated["premarket"] = state
    if handoff is not None:
        updated["close"] = handoff
    if interpretation is not None:
        updated["interpretation"] = interpretation
    return updated


# --- restore across runners -------------------------------------------------------------------

ARTIFACT_NAME = "market-brief-continuity"
WORKFLOW_PATH = ".github/workflows/schedule.yml"


def select_artifact(artifacts, run_lookup, branch="main", workflow_path=WORKFLOW_PATH):
    """Pick the newest unexpired bundle artifact from a successful run of the expected workflow.

    The artifact name alone proves nothing; branch, workflow, and conclusion are checked.
    """
    candidates = [a for a in artifacts if isinstance(a, dict) and not a.get("expired")
                  and isinstance(a.get("workflow_run"), dict)
                  and a["workflow_run"].get("head_branch") == branch and a.get("created_at")]
    for artifact in sorted(candidates, key=lambda a: a["created_at"], reverse=True):
        run = run_lookup(artifact["workflow_run"]["id"]) or {}
        if run.get("conclusion") == "success" and run.get("path") == workflow_path:
            return artifact
    return None


def restore_bundle(source, destination, mode="LIVE"):
    """Validate a downloaded bundle and install it as this workspace's continuity state.

    Returns (bundle, note). Records from the wrong mode or from commissioning/experiment
    origins are dropped before anything is written; nothing valid means an explicit cold start.
    """
    bundle, note = load_bundle(source)
    dropped = []
    for slot in SLOTS:
        record = bundle.get(slot)
        if record is None:
            continue
        origin = record.get("origin", {})
        if origin.get("mode") != mode or origin.get("commissioning") or origin.get("experiment"):
            bundle[slot] = None
            dropped.append(slot)
    if dropped:
        note = "; ".join(filter(None, [note, "non-production records dropped: " + ", ".join(dropped)]))
    if any(bundle[slot] for slot in SLOTS):
        write_bundle(destination, bundle)
    return bundle, note


# --- admission ----------------------------------------------------------------------------------

def _origin_reason(record, packet, kind):
    if record is None:
        return "absent"
    if record.get("schema_version") != CONTINUITY_SCHEMA or record.get("kind") != kind:
        return "schema or kind mismatch"
    if not _hash_ok(record):
        return "content hash mismatch"
    origin = record.get("origin", {})
    if origin.get("mode") != packet["run"]["mode"]:
        return "mode mismatch"
    if origin.get("commissioning") or origin.get("experiment"):
        return "commissioning or experiment origin"
    try:
        if timestamp(origin["target_time"]) >= timestamp(packet["run"]["target_time"]):
            return "prior state is not earlier than this run"
    except (KeyError, TypeError, ValueError):
        return "malformed origin clock"
    return None


def _anchor_summary(record):
    origin = record["origin"]
    return dict(run_id=origin["run_id"], checkpoint=origin["checkpoint"], session_date=origin["session_date"],
                evidence_cutoff=origin["target_time"], evidence_hash=origin["evidence_hash"],
                data_status=record["observed"]["data_status"])


def admit_prior_state(bundle, packet):
    """Choose which persisted records this run may see, by exchange session and checkpoint.

    Premarket admits only the previous exchange session's accepted close handoff. Later
    checkpoints admit this session's premarket anchor and its latest accepted edition.
    """
    now = timestamp(packet["run"]["target_time"])
    session = packet["run"]["session"]
    checkpoint = packet["run"]["checkpoint"]
    result = dict(status="cold_start", reason="", anchors={}, snapshots={}, watches=[], relationships=[],
                  closing_character=None, next_events=[])
    carrier = None
    if checkpoint == "PREMARKET":
        record = bundle.get("close")
        reason = _origin_reason(record, packet, "session_handoff")
        if reason is None and record["session"]["date"] != session["previous_session"]:
            reason = (f"last accepted close handoff is for {record['session']['date']}, "
                      f"not the previous exchange session {session['previous_session']}")
        if reason:
            result["reason"] = f"close continuity unavailable: {reason}"
            return result
        result["anchors"]["previous_close"] = _anchor_summary(record)
        result["snapshots"]["previous_close"] = record["observed"]["snapshots"]
        result["closing_character"] = record["assessment"]["closing_character"]
        result["next_events"] = record.get("next_events", [])
        carrier = record
    else:
        reasons = []
        for slot in ("premarket", "latest"):
            record = bundle.get(slot)
            reason = _origin_reason(record, packet, "edition_state")
            if reason is None and record["session"]["date"] != session["date"]:
                reason = f"belongs to session {record['session']['date']}"
            if reason is None and slot == "latest" and "premarket" in result["anchors"] \
                    and record["origin"]["run_id"] == result["anchors"]["premarket"]["run_id"]:
                continue
            if reason:
                reasons.append(f"{slot}: {reason}")
                continue
            result["anchors"][slot] = _anchor_summary(record)
            result["snapshots"][slot] = record["observed"]["snapshots"]
            carrier = record
        if not result["anchors"]:
            result["reason"] = "same-session continuity unavailable: " + "; ".join(reasons)
            return result
        if reasons:
            result["reason"] = "; ".join(reasons)
    result["status"] = "available"
    for watch in carrier["assessment"]["watches"]:
        carried = json.loads(json.dumps(watch))
        if carried["lifecycle"] == "active" and timestamp(carried["horizon"]["expires_at"]) <= now:
            carried["lifecycle"] = "expired"
        if carried["lifecycle"] != "retired":
            result["watches"].append(carried)
    result["relationships"] = json.loads(json.dumps(carrier["assessment"]["relationships"]))
    return result


def admit_interpretation(bundle, packet):
    """The frozen synthesis a deterministic refresh may render: this session's, production, hash-verified.

    Returns (record, reason); a refresh without an admitted interpretation has nothing to publish.
    """
    record = bundle.get("interpretation")
    reason = _origin_reason(record, packet, "interpretation")
    if reason is None and record["session"]["date"] != packet["run"]["session"]["date"]:
        reason = f"belongs to session {record['session']['date']}, not {packet['run']['session']['date']}"
    if reason:
        return None, f"no accepted interpretation for this session: {reason}"
    return record, ""


# --- deterministic comparisons ----------------------------------------------------------------

def _current_by_key(packet):
    rows = {}
    for row in [*packet["observations"], *packet["derived"]]:
        if row.get("status") in USABLE and row.get("value") is not None and row.get("identity"):
            rows.setdefault(row["identity"]["key"], row)
    return rows


def _observation_date(row):
    value = row.get("observed_at") or ""
    if "T" in value:
        return timestamp(value).astimezone(ET).date().isoformat()
    return value


def compare_anchor(anchor, snapshots, packet):
    """Mathematically valid change only: same identity, compatible basis and session."""
    current = _current_by_key(packet)
    comparisons = []
    for ident, prior in snapshots.items():
        key = prior["identity"]["key"]
        row = current.get(key)
        record = dict(id=f"cmp-{anchor}-{ident}", anchor=anchor, metric_key=key, topic=prior["topic"],
                      metric=prior["metric"], unit=prior["unit"], prior_ref=f"{anchor}:{ident}",
                      prior_run_id=prior["run_id"], prior_value=prior["value"],
                      prior_observed_at=prior["observed_at"], current_ref=None, current_value=None,
                      current_observed_at=None, delta=None, status="unavailable",
                      reason="no usable current observation of this measurement")
        if row is None:
            comparisons.append(record)
            continue
        record.update(current_ref=row["id"], current_value=row["value"], current_observed_at=row["observed_at"])
        if row["unit"] != prior["unit"]:
            record.update(status="not_comparable", reason="unit differs")
        elif prior.get("frequency") == "intraday" and _observation_date(row) != _observation_date(prior):
            record.update(status="not_comparable", reason="intraday prints from different sessions")
        elif row["observed_at"] == prior["observed_at"]:
            record.update(status="no_new_observation", reason="same observation as the prior state")
        else:
            record.update(status="changed", reason="", delta=round(row["value"] - prior["value"], 6))
        comparisons.append(record)
    return comparisons


def compare_all(prior, packet):
    comparisons = []
    for anchor in ANCHORS:
        if anchor in prior.get("snapshots", {}):
            comparisons.extend(compare_anchor(anchor, prior["snapshots"][anchor], packet))
    return comparisons


def evaluability(watch, prior, comparisons):
    """Can the carried watch be judged on current evidence? Missing data is not survival."""
    refs = set(watch.get("evidence_refs", []))
    statuses = [c["status"] for c in comparisons if c["prior_ref"].split(":", 1)[1] in refs]
    if not statuses or all(status == "unavailable" for status in statuses):
        return "missing_evidence"
    if any(status == "changed" for status in statuses):
        return "assessable"
    return "not_comparable"


# --- the analyst's view --------------------------------------------------------------------------

COMPARISON_FIELDS = ("id", "anchor", "topic", "metric", "unit", "status", "prior_ref", "current_ref",
                     "prior_value", "current_value", "delta", "prior_observed_at", "current_observed_at")


def _citable_idents(prior, comparisons):
    """Prior identities a light edition may cite: carried-record dependencies and changed measurements."""
    idents = set()
    for record in [*prior["watches"], *prior["relationships"]]:
        idents |= set(record.get("evidence_refs", []))
    idents |= {c["prior_ref"].split(":", 1)[1] for c in comparisons if c["status"] == "changed"}
    return idents


def continuity_context(prior, comparisons, profile=None):
    """Prior state as bounded hypotheses plus deterministic comparisons, separately namespaced.

    A "changed" (light) profile keeps every comparison's identity and status but supplies the full
    record only for `changed` comparisons, the only ones a narrative may interpret, and supplies prior
    snapshots only for identities a carried record or a changed comparison depends on. Evaluability
    is computed from the complete comparison list before that projection. Rich profiles are unchanged.
    """
    if prior["status"] != "available":
        return dict(prior_state=dict(status="cold_start", reason=prior["reason"]), comparisons=[])
    bounded = bool(profile and profile.get("context") == "changed")
    citable = _citable_idents(prior, comparisons) if bounded else None
    changed_refs = {c["prior_ref"] for c in comparisons if c["status"] == "changed"}
    observed = set()
    watches = []
    for watch in prior["watches"]:
        record = dict(id=watch["id"], lifecycle=watch["lifecycle"],
                      evaluability=evaluability(watch, prior, comparisons),
                      hypothesis=watch["hypothesis"], confirmation=watch["confirmation"],
                      contradiction=watch["contradiction"], horizon=watch["horizon"],
                      evidence_refs=watch["evidence_refs"], origin_run_id=watch["origin_run_id"],
                      latest_assessment=watch["assessments"][-1]["status"])
        if watch.get("values"):
            # The numbers the criteria quote, as the author saw them, with the identity that labels them;
            # they are not current evidence.
            record["values"] = {ident: {key: row[key] for key in ("topic", "metric", "frequency", "value", "unit",
                                                                 "observed_at") if key in row}
                                for ident, row in watch["values"].items()}
        watches.append(record)
    relationships = [dict(id=r["id"], instruments=r["instruments"], statement=r["statement"],
                          latest_assessment=r["assessments"][-1]["status"]) for r in prior["relationships"]]
    snapshots = []
    for anchor, rows in prior["snapshots"].items():
        for ident, row in rows.items():
            if citable is not None:
                # Anchors are visited in order; a later anchor's identical observation adds nothing
                # unless it is the prior side of a changed comparison, which stays citable.
                observation = (ident, row["value"], row["observed_at"])
                if ident not in citable or (observation in observed and f"{anchor}:{ident}" not in changed_refs):
                    continue
                observed.add(observation)
            snapshots.append(dict(ref=f"{anchor}:{ident}", topic=row["topic"], metric=row["metric"],
                                  value=row["value"], unit=row["unit"], observed_at=row["observed_at"]))
    prior_state = {"status": "available", "class": "INTERPRETATION", "note": (
        "Prior relationships, watches, and character are earlier analyst hypotheses, not evidence. "
        "Describe current conditions from current evidence first; cite prior refs only in changes, "
        "relationships, and watch_updates."), "anchors": prior["anchors"], "watches": watches,
        "relationships": relationships, "closing_character": prior["closing_character"], "snapshots": snapshots}
    if prior.get("reason"):
        prior_state["limitation"] = prior["reason"]
    if not bounded:
        return dict(prior_state=prior_state,
                    comparisons=[{key: c[key] for key in COMPARISON_FIELDS} for c in comparisons])
    compact = [{key: c[key] for key in COMPARISON_FIELDS} if c["status"] == "changed"
               else dict(id=c["id"], status=c["status"]) for c in comparisons]
    counts = {}
    for c in comparisons:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    summary = dict(mode="changed", counts=counts, note=(
        "Only changed comparisons carry values; the others are listed by id and status because "
        "they cannot be interpreted as changes. Prior snapshots cover carried-record dependencies "
        "and changed measurements only, once per distinct observation."))
    return dict(prior_state=prior_state, comparisons=compact, comparison_summary=summary)


def prior_values(context):
    """Values for numeric placeholders that cite prior snapshots, keyed by namespaced ref."""
    prior = (context or {}).get("prior_state", {})
    return {row["ref"]: row for row in prior.get("snapshots", [])}


# --- validation of the analyst's continuity records ------------------------------------------

def validate_state(narrative, context, shown, topics):
    """Carried IDs, allowed transitions, evaluability, and prior-ref namespacing."""
    prior = (context or {}).get("prior_state", {"status": "cold_start"})
    available = prior.get("status") == "available"
    carried_watches = {w["id"]: w for w in prior.get("watches", [])} if available else {}
    carried_relationships = {r["id"] for r in prior.get("relationships", [])} if available else set()
    prior_refs = set(prior_values(context)) if available else set()
    comparisons = {c["id"]: c for c in (context or {}).get("comparisons", [])} if available else {}

    def check_refs(record, allow_prior):
        for ref in record["evidence_ids"]:
            if ":" in ref:
                if not allow_prior or ref not in prior_refs:
                    raise ValueError("prior evidence reference not admitted for this record")
            elif ref not in shown:
                raise ValueError("unknown, unavailable, or unsupplied evidence reference")

    check_refs(narrative["character"], False)
    seen = set()
    for update in narrative["watch_updates"]:
        watch = carried_watches.get(update["carried_id"])
        if watch is None or update["carried_id"] in seen:
            raise ValueError("watch update names an unknown or repeated carried watch")
        seen.add(update["carried_id"])
        if watch["evaluability"] != "assessable" and update["assessment"] != "unresolved":
            raise ValueError("a watch without comparable current evidence can only be unresolved")
        check_refs(update, True)
        if not any(":" not in ref for ref in update["evidence_ids"]) and watch["evaluability"] == "assessable":
            raise ValueError("an assessed watch must cite current evidence")
    for relationship in narrative["relationships"]:
        carried = relationship["carried_id"]
        if carried is None and relationship["assessment"] != "new":
            raise ValueError("a new relationship must be assessed as new")
        if carried is not None and (carried not in carried_relationships or relationship["assessment"] == "new"):
            raise ValueError("relationship names an unknown carried record or restarts it as new")
        if not set(relationship["instruments"]) <= topics:
            raise ValueError("relationship names an instrument outside the supplied evidence")
        check_refs(relationship, carried is not None)
    seen = set()
    for change in narrative["changes"]:
        comparison = comparisons.get(change["comparison_id"])
        if comparison is None or change["comparison_id"] in seen:
            raise ValueError("change names an unknown or repeated comparison")
        seen.add(change["comparison_id"])
        if comparison["status"] != "changed":
            raise ValueError("only a deterministic changed comparison can be interpreted as a change")
        check_refs(change, True)
    return narrative


# --- packaging ----------------------------------------------------------------------------------

def closing_data(packet):
    """What a post-close run actually has: completed prices, near-close prints, or older history."""
    session_date = packet["run"]["session"]["date"]
    daily = next((r for r in packet["derived"] if r["id"] == "SPY-daily" and r["status"] in USABLE), None)
    if daily and daily["observed_at"] == session_date and not packet.get("history_lag"):
        return dict(status="COMPLETED_SESSION", reason="completed-session daily bars admitted")
    prints = [r for r in packet["observations"] if r.get("frequency") == "intraday" and r["status"] in USABLE
              and _observation_date(r) == session_date]
    if prints:
        latest = max(r["observed_at"] for r in prints)
        return dict(status="PROVISIONAL_NEAR_CLOSE",
                    reason=f"timestamped session prints through {latest}; not an official closing bar")
    # Price history only: the Treasury curve's derived spreads say nothing about how the equity session ended.
    if any(row.get("metric") not in CURVE_METRICS for row in packet["derived"]):
        return dict(status="EARLIER_HISTORY_ONLY",
                    reason="only history through an earlier completed session; no observation from this session")
    return dict(status="NONE", reason="no usable price observations")


def data_status(packet):
    if packet["run"]["checkpoint"] == "CLOSE_1M":
        return closing_data(packet)
    if packet["coverage"].get("current_premarket"):
        return dict(status="INTRADAY_OBSERVATIONS", reason="timestamped current prints admitted")
    return dict(status="PRIOR_CLOSE_ONLY", reason="dated prior-close context only")


def _snapshot(row, run_id):
    return dict({key: row[key] for key in SNAPSHOT_FIELDS if key in row}, run_id=run_id)


def criterion_values(watch, catalog):
    """The rows a watch's criteria quote as `{{evidence-id}}`, at the values its author saw. They travel
    with the watch so a carried criterion never drifts when newer market data exists."""
    ids = set()
    for key in ("condition", "confirmation", "contradiction", "hypothesis"):
        ids |= set(PLACEHOLDER.findall(watch.get(key) or ""))
    return {ident: {key: catalog[ident][key] for key in INTERPRETATION_FIELDS if key in catalog[ident]}
            for ident in sorted(ids) if ident in catalog}


def _watch_record(watch, run_id, now, packet, catalog, ordinal):
    refs = [ref for ref in watch["evidence_ids"] if ":" not in ref]
    rows = [catalog[ref] for ref in refs if ref in catalog]
    return dict(id=f"watch-{run_id}-{ordinal}", version=1, supersedes=None, origin_run_id=run_id,
                first_observed_at=now.isoformat(), checkpoint=packet["run"]["checkpoint"],
                instruments=sorted({row["topic"] for row in rows}),
                metric_keys=sorted({row["identity"]["key"] for row in rows if row.get("identity")}),
                hypothesis=watch["condition"], confirmation=watch["confirmation"],
                contradiction=watch["contradiction"], values=criterion_values(watch, catalog),
                horizon=resolve_horizon(watch["horizon"], now, packet["events"], packet["run"]["checkpoint"]),
                evidence_refs=refs, lifecycle="active", evaluability="assessable", criteria_version=1,
                assessments=[dict(status="new", run_id=run_id, assessed_at=now.isoformat(),
                                  current_refs=refs, previous_refs=[], reason="", origin="analyst")])


def edition_state(packet, narrative, prior, comparisons, context_hash, narrative_hash, app_version):
    """Package one accepted edition. Model-authored records are labeled interpretation."""
    run = packet["run"]
    run_id = run["run_id"]
    now = timestamp(run["target_time"])
    catalog = evidence_catalog(model_packet(packet))
    catalog = {ident: row for ident, row in catalog.items() if "value" in row}
    # Carried watches: append this edition's assessment, refresh lifecycle/evaluability.
    updates = {u["carried_id"]: u for u in narrative["watch_updates"]}
    carried, retired = [], []
    for watch in prior.get("watches", []):
        record = json.loads(json.dumps(watch))
        record["evaluability"] = evaluability(record, prior, comparisons)
        if not record.get("values"):
            # A record written before criteria carried their values is frozen at this carry, the best
            # record available, so it stops drifting from here on.
            record["values"] = criterion_values(record, catalog)
        update = updates.get(record["id"])
        if update:
            record["assessments"].append(dict(
                status=update["assessment"], run_id=run_id, assessed_at=now.isoformat(),
                current_refs=[r for r in update["evidence_ids"] if ":" not in r],
                previous_refs=[r for r in update["evidence_ids"] if ":" in r],
                reason=update["reason"], origin="analyst"))
            if update["assessment"] == "reversed":
                record["lifecycle"] = "retired"
        if record["lifecycle"] == "active":
            carried.append(record)
        else:
            retired.append(record)
    new_watches = [_watch_record(w, run_id, now, packet, catalog, i + 1) for i, w in enumerate(narrative["watches"])]
    watches = (new_watches + sorted(carried, key=lambda w: w["first_observed_at"], reverse=True))
    for overflow in watches[CARRY_LIMIT:]:
        overflow = dict(overflow, lifecycle="retired")
        overflow["assessments"].append(dict(status="unresolved", run_id=run_id, assessed_at=now.isoformat(),
                                            current_refs=[], previous_refs=[], origin="deterministic",
                                            reason="dropped: at most three watches are carried"))
        retired.append(overflow)
    watches = watches[:CARRY_LIMIT]
    # Relationships: carried assessments appended, new ones assigned a code-owned ID.
    prior_relationships = {r["id"]: json.loads(json.dumps(r)) for r in prior.get("relationships", [])}
    relationships, new_ordinal = [], 0
    for proposal in narrative["relationships"]:
        assessment = dict(status=proposal["assessment"], run_id=run_id, assessed_at=now.isoformat(),
                          current_refs=[r for r in proposal["evidence_ids"] if ":" not in r],
                          previous_refs=[r for r in proposal["evidence_ids"] if ":" in r],
                          reason=proposal["reason"], origin="analyst")
        if proposal["carried_id"] is None:
            new_ordinal += 1
            relationships.append(dict(id=f"rel-{run_id}-{new_ordinal}", origin_run_id=run_id,
                                      first_observed_at=now.isoformat(), instruments=proposal["instruments"],
                                      statement=proposal["statement"],
                                      evidence_refs=assessment["current_refs"], assessments=[assessment]))
        else:
            record = prior_relationships.pop(proposal["carried_id"])
            record["assessments"].append(assessment)
            if proposal["assessment"] != "reversed":
                relationships.append(record)
    relationships = relationships[:CARRY_LIMIT]
    # Snapshots: anchors, every dependency of carried/new records, and the character's evidence.
    wanted = {ident for ident, row in catalog.items() if row["topic"] in ANCHOR_INSTRUMENTS}
    for watch in watches:
        wanted |= set(watch["evidence_refs"])
    for relationship in relationships:
        wanted |= set(relationship["evidence_refs"])
    wanted |= set(narrative["character"]["evidence_ids"])
    snapshots = {ident: _snapshot(catalog[ident], run_id) for ident in sorted(wanted) if ident in catalog}
    for watch in watches:
        for ref in watch["evidence_refs"]:
            if ref not in snapshots:
                for anchor_rows in prior.get("snapshots", {}).values():
                    if ref in anchor_rows:
                        snapshots[ref] = anchor_rows[ref]
                        break
    state = dict(
        schema_version=CONTINUITY_SCHEMA, kind="edition_state",
        origin=dict(run_id=run_id, mode=run["mode"], checkpoint=run["checkpoint"],
                    session_date=run["session"]["date"], target_time=run["target_time"],
                    evidence_hash=digest(packet), context_hash=context_hash, narrative_hash=narrative_hash,
                    app_version=app_version, commissioning=bool(run.get("commissioning")),
                    experiment=bool(run.get("experiment"))),
        session=dict(date=run["session"]["date"], previous_session=run["session"]["previous_session"],
                     next_session=run["session"].get("next_session"), close=run["session"]["close"],
                     evidence_cutoff=run["target_time"]),
        observed=dict(data_status=data_status(packet), anchors=prior.get("anchors", {}),
                      continuity=prior["status"], continuity_reason=prior.get("reason", ""),
                      comparisons=comparisons, snapshots=snapshots),
        assessment={"class": "INTERPRETATION", "origin": "analyst",
                    "character": dict(text=narrative["character"]["text"],
                                      label=narrative["banner"]["label"],
                                      evidence_refs=list(narrative["character"]["evidence_ids"])),
                    "relationships": relationships, "watches": watches, "retired": retired,
                    "changes": [dict(comparison_id=c["comparison_id"], text=c["text"],
                                     evidence_refs=list(c["evidence_ids"])) for c in narrative["changes"]]},
        next_events=[dict(id=e["id"], title=e["title"], scheduled_at=e["scheduled_at"],
                          session_date=e.get("session_date"))
                     for e in packet["events"] if e.get("session_relation") == "NEXT SESSION"],
    )
    return _hashed(state)


def session_handoff(state):
    """The close-designated state, derived from the same accepted state without another model call.

    A deterministic close carries the last accepted character forward (labeled with the checkpoint that
    interpreted it); without any same-session interpretation the handoff still carries the observed close.
    """
    if state["origin"]["checkpoint"] != "CLOSE_1M":
        return None, "not a post-close edition"
    closing = state["observed"]["data_status"]
    if closing["status"] not in {"COMPLETED_SESSION", "PROVISIONAL_NEAR_CLOSE"}:
        return None, f"closing character unavailable: {closing['reason']}"
    handoff = {key: value for key, value in state.items() if key != "content_hash"}
    handoff = json.loads(json.dumps(handoff))
    handoff["kind"] = "session_handoff"
    handoff["observed"]["closing_data"] = closing
    character = state["assessment"].get("character")
    handoff["assessment"]["closing_character"] = (
        dict(character, provisional=closing["status"] == "PROVISIONAL_NEAR_CLOSE") if character else None)
    return _hashed(handoff), ""


# --- frozen interpretation and deterministic refreshes -----------------------------------------

def cited_ids(narrative, attention=(), carried=()):
    """Every evidence reference the page will resolve: the narrative's cited IDs and numeric placeholders
    (the take's included), the selected triggers' rows, and the placeholders inside carried watches' criteria
    (written by an earlier analyst, rendered again beside this narrative)."""
    ids = set()
    records = [narrative["banner"], narrative["character"], *narrative["summary"], *narrative["watches"],
               *narrative.get("relationships", []), *narrative.get("watch_updates", []),
               *narrative.get("changes", [])]
    records += [p for section in narrative["sections"].values() for p in section]
    if narrative.get("take"):  # a narrative frozen under the v1 contract has no take
        records.append(narrative["take"])
    for record in records:
        ids |= set(record.get("evidence_ids", []))
        for value in record.values():
            if isinstance(value, str):
                ids |= set(PLACEHOLDER.findall(value))
    for item in attention:
        ids |= set(item.get("evidence_ids", []))
    for watch in carried:
        # A watch that carries its own creation-time values needs nothing frozen for them here; a legacy
        # watch without them is frozen at the values of this run, the best record available.
        if not watch.get("values"):
            for key in ("hypothesis", "confirmation", "contradiction"):
                ids |= set(PLACEHOLDER.findall(watch.get(key) or ""))
        ids |= set(watch.get("evidence_refs", []))
    return ids


def interpretation_record(packet, narrative, context=None, app_version="", context_hash=None, origin=None):
    """Freeze one accepted synthesis for the deterministic refreshes that follow it.

    The record holds the narrative, every row it cites at the values the analyst saw, the prior state it
    assessed, its resolved watch horizons, and its selected attention triggers. A refresh renders exactly
    this record under a later data clock; nothing in it is recomputed from newer evidence.
    `origin` overrides identity fields for a fictional replay only.
    """
    run = packet["run"]
    now = timestamp(run["target_time"])
    context = context or {}
    prior = context.get("prior_state") or {"status": "cold_start", "reason": ""}
    values = dict(evidence_catalog(packet), **prior_values(context))
    selected = {item["id"] for item in narrative.get("attention", [])}
    attention = [dict(id=a["id"], symbol=a["symbol"], reason=a["reason"], date=a.get("date"),
                      evidence_ids=list(a["evidence_ids"]))
                 for a in packet.get("attention", []) if a["id"] in selected]
    ids = cited_ids(narrative, attention, prior.get("watches", []))
    evidence = {ident: {key: row[key] for key in INTERPRETATION_FIELDS if key in row}
                for ident, row in values.items() if ident in ids}
    counts = {}
    for comparison in context.get("comparisons", []):
        counts[comparison["status"]] = counts.get(comparison["status"], 0) + 1
    horizons = []
    for watch in narrative["watches"]:
        try:
            horizons.append(resolve_horizon(watch["horizon"], now, packet.get("events", []), run["checkpoint"]))
        except ValueError:
            horizons.append(dict(declared=watch["horizon"], phrase=watch["horizon"].replace("_", " ").title()))
    record = dict(
        schema_version=CONTINUITY_SCHEMA, kind="interpretation",
        origin=dict(run_id=run.get("run_id"), mode=run["mode"], checkpoint=run["checkpoint"],
                    session_date=run["session"]["date"], target_time=run["target_time"],
                    evidence_hash=digest(packet), context_hash=context_hash, narrative_hash=digest(narrative),
                    app_version=app_version, commissioning=bool(run.get("commissioning")),
                    experiment=bool(run.get("experiment"))),
        session=dict(date=run["session"]["date"], previous_session=run["session"]["previous_session"],
                     close=run["session"]["close"], evidence_cutoff=run["target_time"]),
        narrative=narrative, evidence=evidence,
        prior_state=dict(status=prior.get("status", "cold_start"), reason=prior.get("reason", ""),
                         anchors=prior.get("anchors", {}),
                         watches=[{key: watch[key] for key in ("id", "lifecycle", "evaluability", "hypothesis",
                                                               "horizon", "evidence_refs", "values") if key in watch}
                                  for watch in prior.get("watches", [])]),
        continuity=dict(comparisons=sum(counts.values()), changed=counts.get("changed", 0),
                        repeated=counts.get("no_new_observation", 0)),
        horizons=horizons, attention=attention)
    if origin:
        record["origin"].update(origin)
    return _hashed(record)


def carried_state(packet, prior, comparisons, interpretation, app_version=""):
    """Package a deterministic refresh: fresh observed state, the carried assessment unchanged.

    Watches keep their lifecycle as admitted (a passed horizon has expired, nothing is reassessed),
    relationships carry unchanged, and the character is the interpretation's, labeled with the checkpoint
    that produced it. Snapshots come from this run's evidence so the next comparison sees fresh values.
    """
    run = packet["run"]
    run_id = run["run_id"]
    catalog = {ident: row for ident, row in evidence_catalog(model_packet(packet)).items() if "value" in row}
    watches = []
    for watch in prior.get("watches", []):
        record = json.loads(json.dumps(watch))
        record["evaluability"] = evaluability(record, prior, comparisons)
        if not record.get("values"):
            record["values"] = criterion_values(record, catalog)  # legacy record: freeze at this carry
        watches.append(record)
    watches = watches[:CARRY_LIMIT]
    relationships = [json.loads(json.dumps(r)) for r in prior.get("relationships", [])][:CARRY_LIMIT]
    character, interpreted_by = None, None
    if interpretation is not None:
        narrative = interpretation["narrative"]
        interpreted_by = interpretation["origin"]["run_id"]
        character = dict(text=narrative["character"]["text"], label=narrative["banner"]["label"],
                         evidence_refs=list(narrative["character"]["evidence_ids"]),
                         interpreted_at=interpretation["origin"]["target_time"],
                         checkpoint=interpretation["origin"]["checkpoint"], run_id=interpreted_by)
    wanted = {ident for ident, row in catalog.items() if row["topic"] in ANCHOR_INSTRUMENTS}
    for watch in watches:
        wanted |= set(watch["evidence_refs"])
    for relationship in relationships:
        wanted |= set(relationship["evidence_refs"])
    if character:
        wanted |= set(character["evidence_refs"])
    snapshots = {ident: _snapshot(catalog[ident], run_id) for ident in sorted(wanted) if ident in catalog}
    for watch in watches:
        for ref in watch["evidence_refs"]:
            if ref not in snapshots:
                for anchor_rows in prior.get("snapshots", {}).values():
                    if ref in anchor_rows:
                        snapshots[ref] = anchor_rows[ref]
                        break
    state = dict(
        schema_version=CONTINUITY_SCHEMA, kind="edition_state",
        origin=dict(run_id=run_id, mode=run["mode"], checkpoint=run["checkpoint"],
                    session_date=run["session"]["date"], target_time=run["target_time"],
                    evidence_hash=digest(packet), context_hash=None, narrative_hash=None,
                    app_version=app_version, commissioning=bool(run.get("commissioning")),
                    experiment=bool(run.get("experiment")), interpretation_run_id=interpreted_by),
        session=dict(date=run["session"]["date"], previous_session=run["session"]["previous_session"],
                     next_session=run["session"].get("next_session"), close=run["session"]["close"],
                     evidence_cutoff=run["target_time"]),
        observed=dict(data_status=data_status(packet), anchors=prior.get("anchors", {}),
                      continuity=prior["status"], continuity_reason=prior.get("reason", ""),
                      comparisons=comparisons, snapshots=snapshots),
        assessment={"class": "INTERPRETATION", "origin": "carried", "interpretation_run_id": interpreted_by,
                    "character": character, "relationships": relationships, "watches": watches,
                    "retired": [], "changes": []},
        next_events=[dict(id=e["id"], title=e["title"], scheduled_at=e["scheduled_at"],
                          session_date=e.get("session_date"))
                     for e in packet["events"] if e.get("session_relation") == "NEXT SESSION"],
    )
    return _hashed(state)


"""Thin local entry point for local brief generation and inspection."""

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, glance
from .collect import ALPACA_UNIVERSE, alpaca_probe, collect_live, cuttingboard_record
from .context import analyst_context, edition_profile
from .continuity import (
    ARTIFACT_NAME,
    SLOTS,
    WORKFLOW_PATH,
    admit_interpretation,
    admit_prior_state,
    advance_bundle,
    bundle_path,
    carried_state,
    compare_all,
    continuity_context,
    edition_state,
    interpretation_record,
    load_bundle,
    own_repository_run,
    restore_bundle,
    select_artifact,
    session_handoff,
    write_bundle,
)
from .evidence import ROOT, digest, finalize_coverage, normalize_packet, read_json, timestamp
from .metrics import annotate_magnitude, derive
from .render import render
from .schedule import (
    CHECKPOINTS,
    ET,
    VANCOUVER,
    checkpoint_kind,
    checkpoint_session,
    current_phase,
    due,
    scheduled_checkpoint,
)
from .synthesize import (
    NARRATIVE_SCHEMA,
    NarrativeRejected,
    as_returned,
    construct_prompt,
    editorial_notes,
    narrative_schema,
    style_notes,
    synthesize,
    validate_narrative,
)

# Assets (prompt, config, templates) come from ROOT; generated state lives under RUN_ROOT.
RUN_ROOT = ROOT
SAMPLE_CONTINUITY = ROOT / "tests/fixtures/continuity.sample.json"
CLOSING_DATA_FOR_HANDOFF = {"COMPLETED_SESSION", "PROVISIONAL_NEAR_CLOSE"}
# Attempt accounting, not continuity: one record per synthesis checkpoint per exchange session, written before a
# production provider request is sent and uploaded by the workflow as `market-brief-attempt-<session>-<checkpoint>`.
ATTEMPT_SCHEMA = "market-brief.synthesis-attempt.v1"
ATTEMPT_ARTIFACT = "market-brief-attempt"


def output_directory(root, mode, target, checkpoint="PREMARKET"):
    root = Path(root).resolve()
    base = root / "runs"
    if base.resolve() != base:
        raise ValueError("runs directory must not be a symlink")
    folder = base / target.strftime("%Y-%m-%d")
    if folder.resolve() != folder:
        raise ValueError("session directory must not be a symlink")
    # Named by the resolved checkpoint: a commissioning run carries the phase it collected in.
    folder = folder / f"{mode.lower()}-{checkpoint.lower()}-{target.strftime('%H%M%S')}-{uuid.uuid4().hex[:8]}"
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def latest_output_path(root=None):
    return Path(ROOT if root is None else root).resolve() / "output" / "latest.html"


def update_latest(root, page):
    output = Path(root).resolve() / "output"
    if output.exists() and output.is_symlink():
        raise ValueError("output directory must not be a symlink")
    output.mkdir(exist_ok=True)
    latest = output / "latest.html"
    if latest.is_symlink():
        raise ValueError("latest output cannot be a symlink")
    temporary = output / f"latest-{uuid.uuid4().hex}.tmp"
    temporary.write_text(page)
    temporary.replace(latest)


def open_latest(root=None, opener=None):
    if root is None:
        root = ROOT
    if opener is None:
        opener = webbrowser.open
    latest = latest_output_path(root)
    if not latest.is_file():
        print(f"No latest brief exists at {latest}; run 'python -m market_brief premarket --replay' first.",
              file=sys.stderr)
        return 2
    opener(latest.as_uri())
    print(latest)
    return 0


def publish_latest(root=None):
    root = Path(ROOT if root is None else root).resolve()
    latest = latest_output_path(root)
    if not latest.is_file():
        raise ValueError(f"No latest brief exists at {latest}; render a brief before publishing")
    publish = root / "publish"
    if publish.exists() and publish.is_symlink():
        raise ValueError("publish directory must not be a symlink")
    publish.mkdir(exist_ok=True)
    index = publish / "index.html"
    if index.is_symlink():
        raise ValueError("published index cannot be a symlink")
    temporary = publish / f"index-{uuid.uuid4().hex}.tmp"
    temporary.write_text(latest.read_text())
    temporary.replace(index)
    return index


def previous_brief(root=None):
    pointer = Path(RUN_ROOT if root is None else root) / "runs/latest-success.json"
    if not pointer.is_file() or pointer.is_symlink():
        return None
    try:
        value = json.loads(pointer.read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def checkpoint_marker(root, session_date, checkpoint):
    return Path(root) / "runs" / "checkpoints" / f"{session_date}-{checkpoint}.json"


def mark_checkpoint(root, session_date, checkpoint, actual_started_at):
    marker = checkpoint_marker(root, session_date, checkpoint)
    marker.parent.mkdir(parents=True, exist_ok=True)
    if marker.is_symlink():
        raise ValueError("checkpoint marker cannot be a symlink")
    write_json(marker, dict(session_date=session_date, checkpoint=checkpoint,
                            actual_started_at=actual_started_at))


def attempt_key(session_date, checkpoint):
    return f"{session_date}-{checkpoint}"


def attempt_path(root, session_date, checkpoint):
    return Path(root) / "runs" / "attempts" / f"{attempt_key(session_date, checkpoint)}.json"


def record_attempt(root, session_date, checkpoint, run_id, attempt):
    """Record that this checkpoint's paid synthesis was attempted, before its provider request is sent.

    Written whatever the synthesis then does and read only by `scheduled()`; never admitted, restored or rendered.
    A crash between this write and the request over-counts, never under-counts. A failed write is a ValueError,
    so nothing is sent."""
    path = attempt_path(root, session_date, checkpoint)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise ValueError("attempt record cannot be a symlink")
        temporary = path.parent / f"attempt-{uuid.uuid4().hex}.tmp"
        write_json(temporary, dict(schema_version=ATTEMPT_SCHEMA, session_date=session_date, checkpoint=checkpoint,
                                   run_id=run_id, route=attempt.get("route"),
                                   requested_model=attempt.get("requested_model"),
                                   requested_at=attempt.get("requested_at")))
        temporary.replace(path)
    except OSError:
        raise ValueError("the paid synthesis attempt could not be recorded; no provider request was sent") from None
    return path


def earlier_attempt(args, session_date, checkpoint):
    """Where this session's paid synthesis of `checkpoint` was already attempted, or None.

    This workspace's own record first; then, on Actions, a record uploaded by this repository's own run of the
    schedule workflow on the expected branch, whatever that run concluded (the upload proves the attempt, not its
    acceptance). Origin is decided as for continuity restore, by `own_repository_run` on the run details, never by
    the listing's optional repository ids. The workflow's one concurrency group starts no run before the previous one
    has finished uploading. Raises when the uploaded records cannot be listed, so the caller never pays on an
    unanswered question."""
    local = attempt_path(RUN_ROOT, session_date, checkpoint)
    if local.is_file():
        return f"record {local.name}"
    repository = getattr(args, "repository", None) or os.environ.get("GITHUB_REPOSITORY")
    if not repository:
        return None  # not on Actions: no other runner shares this checkpoint
    name = f"{ATTEMPT_ARTIFACT}-{attempt_key(session_date, checkpoint)}"
    listing = _gh_json([f"repos/{repository}/actions/artifacts?name={name}&per_page=100"])
    artifacts = listing.get("artifacts") if isinstance(listing, dict) else None
    if not isinstance(artifacts, list):
        raise ValueError("attempt records could not be listed")
    for artifact in artifacts:
        if not isinstance(artifact, dict) or artifact.get("name") != name:
            continue
        run = artifact.get("workflow_run")
        if not isinstance(run, dict) or run.get("head_branch") != getattr(args, "branch", "main"):
            continue
        details = _gh_json([f"repos/{repository}/actions/runs/{run['id']}"])
        # A fork's pull_request run can run its own edit of schedule.yml; only this repository's own run counts.
        if isinstance(details, dict) and details.get("path") == WORKFLOW_PATH and own_repository_run(details):
            return f"artifact {artifact.get('id')} from run {run['id']}"
    return None


def merge_input(collected, supplied):
    if supplied.get("mode") != "LIVE":
        raise ValueError("sample input cannot be relabeled as a live run")
    for key in ("sources", "observations", "history", "events", "context_items"):
        records = {row["id"]: row for row in collected.get(key, [])}
        records.update({row["id"]: row for row in supplied.get(key, [])})
        collected[key] = list(records.values())
    return collected


def guard_manual_repay(args, session_date, checkpoint, target, metadata):
    """A manual production synthesis pays again only when the owner says so (W43 S2a).

    The scheduled path never pays twice for a checkpoint (`scheduled()`); a manual dispatch bypasses that gate, so a
    checkpoint whose paid attempt is already recorded is refused, before any request, unless `--repay` is given. A
    manual run outside the checkpoint's due window is the owner's recovery: allowed and recorded as such."""
    ready, info = due(target, checkpoint)
    if not ready:
        metadata["outside_window"] = dict(scheduled_at=info["scheduled_at"], collected_at=target.isoformat())
        print(f"OUTSIDE WINDOW / {checkpoint} / manual run collected {target.isoformat(timespec='seconds')}; "
              f"scheduled {info['scheduled_at']}", flush=True)
    if getattr(args, "repay", False):
        metadata["repay"] = True
        print(f"REPAY / {checkpoint} / the owner asked for another paid attempt", flush=True)
        return
    try:
        earlier = earlier_attempt(args, session_date, checkpoint)
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired):
        raise ValueError(f"earlier paid attempts for {checkpoint} on {session_date} could not be checked; no provider "
                         "request was made (dispatch with repay to pay regardless)") from None
    if earlier:
        raise ValueError(f"paid synthesis of {checkpoint} already attempted for {session_date} ({earlier}); no "
                         "provider request was made (dispatch with repay to pay again)")


def degradable(exc):
    """Whether a synthesis failure may publish as the deterministic refresh (OD-1): the analysis was attempted and
    not accepted, never a refusal or fault before any request."""
    return (isinstance(exc, NarrativeRejected) or isinstance(getattr(exc, "attempt", None), dict)
            or "edition budget" in str(exc))


def record_rejection(folder, metadata, exc):
    """A rejected run's accounting: the error, the paid call it was (at what it cost when known) and the response."""
    metadata.update(validation="FAILED", error=str(exc))
    attempt = getattr(exc, "attempt", None)
    if isinstance(attempt, dict):
        # A request was sent: the rejection is recorded as the paid call it was, at what it cost when known.
        metadata.update(model_route=attempt.get("route", metadata["model_route"]), model=attempt)
        usage = attempt.get("usage")
        if isinstance(usage, dict) and usage:
            # The log line an accepted call prints, so a rejected call's cost is visible without the artifact.
            print("Synthesis usage (not accepted): " + " ".join(f"{k}={v}" for k, v in usage.items()), flush=True)
    rejected = getattr(exc, "narrative", None)
    if rejected is not None:
        # The generated output is the diagnostic; keep it beside the evidence it failed against.
        write_json(folder / "narrative.rejected.json", rejected)


def missing_prints_reason(packet):
    """Why a refresh has no current print, from the packet: none returned, or how many came back too old and when the
    newest was stamped (2026-10-15 HOURLY_1400: the feed stopped at 12:56 ET, an hour before the run)."""
    prints = [r for r in packet["observations"] if r.get("frequency") == "intraday"]
    if not prints:
        return ("refresh has no timestamped current prints: the price source returned none; "
                "the last accepted page stands")
    statuses = {}
    for row in prints:
        statuses[row.get("status") or "unknown"] = statuses.get(row.get("status") or "unknown", 0) + 1
    detail = ", ".join(f"{count} {status.lower()}" for status, count in sorted(statuses.items()))
    stamped = []
    for row in prints:
        if row.get("status") == "STALE":
            try:
                stamped.append(timestamp(row["observed_at"]))
            except (KeyError, TypeError, ValueError, AttributeError):
                pass
    if stamped:
        newest = max(stamped)
        detail += (f"; the newest stale print was stamped {newest.astimezone(ET):%-I:%M:%S %p} ET / "
                   f"{newest.astimezone(VANCOUVER):%-I:%M:%S %p} PT")
    return (f"refresh has no timestamped current prints: {len(prints)} intraday prints ({detail}); "
            "the last accepted page stands")


def sample_interpretation(packet, prior, comparisons):
    """A fictional replay of a deterministic checkpoint renders the fixture narrative as the carried
    interpretation, dated to the session's premarket so the two clocks read as they would in production."""
    profile = edition_profile("PREMARKET")
    context = dict(analyst_context(packet, profile, comparisons, prior),
                   **continuity_context(prior, comparisons, profile))
    fixture = "narrative.continuity.json" if prior["status"] == "available" else "narrative.sample.json"
    narrative = validate_narrative(read_json(ROOT / "tests/fixtures" / fixture), packet, context)
    premarket = checkpoint_session(timestamp(packet["run"]["target_time"]), "PREMARKET")
    # Never dated after the replayed data: a fixture targeted before its premarket slot keeps its own clock.
    interpreted_at = min(premarket["scheduled_at"], packet["run"]["target_time"], key=timestamp)
    origin = dict(run_id="sample-premarket-fixture", checkpoint="PREMARKET", target_time=interpreted_at)
    return interpretation_record(packet, narrative, context, __version__, digest(context), origin)


def run(args):
    started = datetime.now(timezone.utc)
    mode = "SAMPLE" if args.replay else "LIVE"
    commissioning = bool(getattr(args, "commissioning", False))
    experiment = bool(getattr(args, "experiment", False))
    production = mode == "LIVE" and not experiment and not commissioning
    full = bool(getattr(args, "full_packet", False))
    if args.replay:
        raw = read_json(args.input or ROOT / "tests/fixtures/evidence.sample.json")
        target = timestamp(raw["target_time"])
        # External source quotations must pass the same narrow adapter in replay.
        if raw.get("cuttingboard"):
            raw["cuttingboard"] = cuttingboard_record(raw["cuttingboard"], target, target)
    else:
        raw = collect_live(started, include_cuttingboard=args.cuttingboard)
        if args.input:
            raw = merge_input(raw, read_json(args.input))
        # The evidence cutoff is when collection finished. At the run's start, every print reported during
        # collection read as a future observation, and the busiest tickers lost their current print.
        target = datetime.now(timezone.utc)
    # A commissioning run describes the market phase it actually collected in.
    checkpoint = current_phase(target) if commissioning else args.checkpoint
    kind = checkpoint_kind(checkpoint)
    packet = normalize_packet(raw, target, mode, checkpoint)
    if commissioning:
        packet["run"]["commissioning"] = True
    if experiment:
        packet["run"]["experiment"] = True
    packet["previous"] = previous_brief()
    universe = read_json(ROOT / "config/universe.json")
    thresholds = read_json(ROOT / "config/magnitude.json")
    derive(packet, universe, thresholds)
    annotate_magnitude(packet, thresholds)
    finalize_coverage(packet)
    # Make unavailable, unimplemented categories explicit in every packet/report.
    config = read_json(ROOT / "config/sources.json")
    packet["coverage"]["limitations"].append("Not automated: " + ", ".join(config["deferred"]))
    checkpoint_data = checkpoint_session(target, checkpoint)
    packet["run"].update(collection_started_at=started.isoformat(), actual_started_at=started.isoformat(),
                         collection_completed_at=datetime.now(timezone.utc).isoformat(),
                         scheduled_checkpoint_at=checkpoint_data["scheduled_at"],
                         app_version=__version__, universe_hash=digest(universe),
                         sources_config_hash=digest(config), manual_input=bool(args.input))
    folder = output_directory(RUN_ROOT, mode, target, checkpoint)
    # The folder name is the run identity every artifact of this run carries.
    packet["run"]["run_id"] = folder.name
    # Structured prior state: admitted by exchange session, compared deterministically.
    continuity_source = (getattr(args, "continuity", None) or SAMPLE_CONTINUITY) if args.replay \
        else bundle_path(RUN_ROOT)
    bundle, bundle_note = load_bundle(continuity_source)
    prior = admit_prior_state(bundle, packet)
    if bundle_note and prior["status"] != "available":
        prior["reason"] = f"{prior['reason']}; {bundle_note}" if prior["reason"] else bundle_note
    comparisons = compare_all(prior, packet)
    page = Path(RUN_ROOT) / "publish" / "index.html"
    packet["continuity"] = dict(status=prior["status"], reason=prior["reason"], anchors=prior["anchors"],
                                comparisons=comparisons,
                                unpublished=unpublished_anchors(prior["anchors"],
                                                                page.read_text() if page.is_file() else ""))
    # Optional Global Equity Proxies line (five US-listed country ETFs): display only, never in the packet, so
    # evidence, continuity and the analyst's context are unchanged; any failure is a quiet line, never a failed run.
    overseas = (glance.view(glance.resolve(target, packet["run"]["session"], checkpoint))
                if mode == "LIVE" and not experiment else None)
    write_json(folder / "evidence.json", packet)
    evidence_hash = digest(packet)
    context, interpretation, interpretation_note = None, None, ""
    if kind == "synthesis":
        # The analyst reads exactly this saved projection; the validator checks references against it.
        profile = edition_profile(checkpoint)
        context = dict(analyst_context(packet, profile, comparisons, prior),
                       **continuity_context(prior, comparisons, profile))
        write_json(folder / "analyst_context.json", context)
    else:
        # A deterministic checkpoint renders the last accepted synthesis of this session, unchanged.
        interpretation, interpretation_note = admit_interpretation(bundle, packet)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, check=False).stdout.strip()
    metadata = dict(app_version=__version__, code_revision=revision or "unknown", mode=mode,
                    run_id=folder.name,
                    target_time=target.isoformat(), started_at=started.isoformat(),
                    meaningful_premarket=packet["run"]["session"]["meaningful_premarket"],
                    checkpoint=checkpoint, kind=kind, actual_started_at=started.isoformat(),
                    scheduled_checkpoint_at=checkpoint_data["scheduled_at"],
                    coverage=packet["coverage"]["status"], evidence_hash=evidence_hash,
                    context_schema=context["schema_version"] if context else None,
                    context_hash=digest(context) if context else None,
                    validation="NOT_RUN", model_route="none", synthesis=dict(kind=kind, calls=0),
                    experiment=experiment, commissioning=commissioning,
                    synthesis_projection="full" if full else "compact",
                    continuity=dict(status=prior["status"], reason=prior["reason"],
                                    anchors={k: v["run_id"] for k, v in prior["anchors"].items()},
                                    comparisons=len(comparisons), advanced=False))
    try:
        if packet["coverage"]["status"] == "INSUFFICIENT":
            raise ValueError("no usable observations/events/context; evidence diagnostic only")
        narrative = None
        degraded = None
        if kind == "synthesis":
            try:
                if args.replay and not args.synthesize:
                    fixture = "narrative.continuity.json" if prior["status"] == "available" else "narrative.sample.json"
                    narrative = read_json(ROOT / "tests/fixtures" / fixture)
                    validate_narrative(narrative, packet, None if full else context, NARRATIVE_SCHEMA if full else None)
                    system, prompt = construct_prompt(packet, full=full, context=context)
                    model = dict(route="fixture-replay", resolved_models=[],
                                 prompt_hash=digest(dict(system=system, user=prompt)),
                                 evidence_hash=evidence_hash)
                else:
                    if production and not getattr(args, "scheduled", False):
                        guard_manual_repay(args, packet["run"]["session"]["date"], checkpoint, target, metadata)
                    print("Evidence collected; requesting one isolated structured synthesis.", flush=True)

                    def requesting(attempt):
                        # The paid-attempt boundary, immediately before the provider request is sent. A production
                        # attempt is recorded first, so no later runner pays for this checkpoint again whatever the
                        # response turns out to be; if the record cannot be written, nothing is sent.
                        if production:
                            # Keyed like every acceptance record (page, bundle origin): the run's own exchange-calendar
                            # date. On a trading day that is the session `scheduled()` asks about; a run on a weekend or
                            # holiday never stands for the next session's checkpoint.
                            record = record_attempt(RUN_ROOT, packet["run"]["session"]["date"], checkpoint, folder.name,
                                                    attempt)
                            metadata["attempt_record"] = str(record.relative_to(RUN_ROOT))
                        metadata["synthesis"]["calls"] = 1
                        metadata.update(model_route=attempt["route"], model=attempt)
                    narrative, model = synthesize(packet, full=full, context=context, on_request=requesting)
                    metadata["synthesis"]["calls"] = 1
                repairs = model.get("repairs") or []
                if repairs:
                    # The response as the analyst returned it stays beside the one accepted after the rewrite.
                    write_json(folder / "narrative.returned.json", as_returned(narrative, repairs))
                    metadata["repairs"] = repairs
                # Overshoot is the analyst's: measured on the response as returned, not on the rewrite.
                notes = editorial_notes(as_returned(narrative, repairs),
                                        NARRATIVE_SCHEMA if full else narrative_schema(context["edition"]))
                metadata["editorial"] = notes
                for note in notes:
                    print(f"Editorial overshoot kept: {note['path']} {note['keyword']} {note['actual']} > "
                          f"{note['limit']}", flush=True)
                # Voice telemetry, observation only: it never changes acceptance, publication or the call count.
                metadata["style"] = style_notes(narrative)
                metadata.update(model_route=model["route"], model=model, narrative_hash=digest(narrative))
                # The accepted response is on disk before anything else can fail; it is paid for.
                write_json(folder / "narrative.json", narrative)
                # Package this edition's state from the same validated response; no second model call.
                state = edition_state(packet, narrative, prior, comparisons, metadata["context_hash"],
                                      metadata["narrative_hash"], __version__)
                interpretation = interpretation_record(packet, narrative, context, __version__,
                                                       metadata["context_hash"])
            except ValueError as exc:
                # A rejected opening analysis still publishes the checkpoint: the deterministic refresh of this same
                # collection under the session's last accepted interpretation (owner ruling OD-1). No second model
                # call, nothing recollected, and it never counts as a synthesis: the frozen interpretation is not
                # replaced. Without an accepted interpretation (a rejected PREMARKET) the run fails closed as before.
                # Only a production generation that was actually attempted and not accepted degrades: a rejected
                # response, a failure after the request was sent, or an over-budget context that could not be sent.
                # A refusal before any request, an experiment or a commissioning run fails closed as before.
                if not production or not degradable(exc):
                    raise
                interpretation, interpretation_note = admit_interpretation(bundle, packet)
                if interpretation is None or interpretation["origin"].get("checkpoint") == checkpoint:
                    # No earlier analysis to stand, or this checkpoint's own analysis already stands.
                    raise
                record_rejection(folder, metadata, exc)
                degraded = dict(checkpoint=checkpoint, scheduled_at=checkpoint_data["scheduled_at"], reason=str(exc))
                narrative = None
                print(f"Synthesis not accepted ({exc}); publishing the deterministic refresh under interpretation "
                      f"{interpretation['origin']['run_id']}.", flush=True)
        if kind != "synthesis" or degraded:
            if interpretation is None and args.replay:
                # A fictional replay carries the fixture narrative; a fixture mismatch is a diagnosed rejection.
                interpretation, interpretation_note = sample_interpretation(packet, prior, comparisons), ""
            if kind == "refresh" or degraded:
                if interpretation is None:
                    raise ValueError(interpretation_note)
                if not packet["coverage"].get("current_premarket"):
                    raise ValueError(missing_prints_reason(packet))
            state = carried_state(packet, prior, comparisons, interpretation, __version__)
            closing = state["observed"]["data_status"]
            if kind == "close" and closing["status"] not in CLOSING_DATA_FOR_HANDOFF:
                raise ValueError(f"close snapshot has no observation from this session ({closing['reason']}); "
                                 "the last accepted page stands")
            metadata["interpretation"] = None if interpretation is None else dict(
                run_id=interpretation["origin"]["run_id"], checkpoint=interpretation["origin"]["checkpoint"],
                target_time=interpretation["origin"]["target_time"], content_hash=interpretation["content_hash"])
        write_json(folder / "edition_state.json", state)
        handoff, handoff_reason = session_handoff(state)
        if handoff is not None:
            write_json(folder / "session_handoff.json", handoff)
        published = False
        if interpretation is not None:
            try:
                repairs = None if degraded else (metadata.get("model") or {}).get("repairs")
                markdown, page = render(packet, narrative, None if degraded else context,
                                        interpretation=interpretation, glance=overseas, repairs=repairs,
                                        degraded=degraded)
            except Exception as exc:  # noqa: BLE001 - any rendering defect is diagnosed like a rejection
                # The accepted narrative is already on disk; nothing has been published.
                stage = "the degraded refresh" if degraded else "acceptance"
                raise ValueError(f"render failed after {stage} ({type(exc).__name__}: {exc})") from None
            (folder / "brief.md").write_text(markdown)
            (folder / "brief.html").write_text(page)
            update_latest(RUN_ROOT, page)
            if mode == "LIVE" and not experiment:
                publish_latest(RUN_ROOT)
            metadata.update(markdown_hash=digest(markdown), html_hash=digest(page))
            published = True
        else:
            # Only a close reaches here: the session hands off even when no synthesis was accepted today.
            print(f"Close snapshot recorded without a page ({interpretation_note}).", flush=True)
        metadata.update(validation="DEGRADED" if degraded else "PASS", published=published)
        if degraded:
            metadata["degraded_from"] = degraded
        metadata["continuity"].update(data_status=state["observed"]["data_status"],
                                      handoff="written" if handoff else handoff_reason,
                                      watches=[w["id"] for w in state["assessment"]["watches"]])
        if production and packet["coverage"]["status"] in {"READY", "PARTIAL"}:
            pointer = RUN_ROOT / "runs/latest-success.json"
            if pointer.is_symlink():
                raise ValueError("latest-success pointer cannot be a symlink")
            temporary = RUN_ROOT / "runs" / f"latest-{uuid.uuid4().hex}.tmp"
            write_json(temporary, {"run": str(folder.relative_to(RUN_ROOT)), "evidence_hash": digest(packet)})
            temporary.replace(pointer)
            # Only accepted production state advances the bundle; SAMPLE, experiment and
            # commissioning runs never touch it. A refresh never rewrites the frozen interpretation.
            write_bundle(bundle_path(RUN_ROOT),
                         advance_bundle(bundle, state, handoff, datetime.now(timezone.utc),
                                        interpretation if kind == "synthesis" and not degraded else None))
            metadata["continuity"]["advanced"] = True
    except ValueError as exc:
        record_rejection(folder, metadata, exc)
        print(f"Brief not accepted: {exc}. Diagnostic: {folder}", file=sys.stderr)
        return 2
    finally:
        metadata["completed_at"] = datetime.now(timezone.utc).isoformat()
        write_json(folder / "metadata.json", metadata)
    outcome = ("degraded to refresh" if degraded else "validated" if kind == "synthesis"
               else "refreshed" if published else "recorded")
    print(f"{mode} / {packet['coverage']['status']} / {outcome}: {folder / 'brief.html'}")
    return 0


def _gh_json(args, runner=subprocess.run):
    result = runner(["gh", "api", *args], capture_output=True, text=True, check=False, timeout=60)
    if result.returncode != 0:
        raise ValueError("GitHub API request failed")
    return json.loads(result.stdout)


def restore_continuity(args, runner=subprocess.run):
    """Install the newest accepted production bundle from a prior runner, or cold start explicitly.

    Only the absence of accepted state is a cold start: no eligible artifact (none yet, or only foreign, ineligible
    or expired ones), or a bundle whose records the content rules drop. A restore that cannot finish (the GitHub
    API, a run lookup, the download, a timeout, installing the bundle) returns non-zero, so the workflow stops
    before collection: a cold bundle from this run would otherwise replace accepted state for every later run.
    """
    destination = bundle_path(RUN_ROOT)
    try:
        if args.from_file:
            source = Path(args.from_file)
            origin = f"file {source}"
        else:
            repository = args.repository or os.environ.get("GITHUB_REPOSITORY")
            if not repository:
                raise ValueError("repository not configured")
            listing = _gh_json([f"repos/{repository}/actions/artifacts?name={ARTIFACT_NAME}&per_page=50"], runner)
            artifact = select_artifact(listing.get("artifacts", []),
                                       lambda run_id: _gh_json([f"repos/{repository}/actions/runs/{run_id}"], runner),
                                       branch=args.branch)
            if artifact is None:
                print("Continuity: cold start; no accepted bundle from a main-branch run.")
                return 0
            download = RUN_ROOT / "runs" / "continuity" / "restore"
            download.mkdir(parents=True, exist_ok=True)
            result = runner(["gh", "run", "download", str(artifact["workflow_run"]["id"]), "-n", ARTIFACT_NAME,
                             "-D", str(download), "-R", repository],
                            capture_output=True, text=True, check=False, timeout=120)
            if result.returncode != 0:
                raise ValueError("artifact download failed")
            source = download / "bundle.json"
            if not source.is_file():
                # The accepted artifact exists; a download that yields no bundle is a failed restore, not absence.
                raise ValueError("artifact download held no bundle")
            origin = f"artifact {artifact.get('id')} from run {artifact['workflow_run']['id']}"
        bundle, note = restore_bundle(source, destination)
        slots = {slot: (bundle[slot]["origin"]["run_id"] if bundle.get(slot) else None) for slot in SLOTS}
        if any(slots.values()):
            print(f"Continuity: restored {origin}; " + ", ".join(f"{k}={v}" for k, v in slots.items())
                  + (f"; {note}" if note else ""))
        else:
            print(f"Continuity: cold start; {origin} held no usable production state" + (f" ({note})" if note else ""))
    except (ValueError, OSError, KeyError, TypeError, AttributeError, subprocess.TimeoutExpired) as exc:
        print(f"Continuity: restore failed ({type(exc).__name__}); stopping before collection.")
        return 2
    return 0


EVIDENCE_CUTOFF_ATTRIBUTE = re.compile(r'<html[^>]*\sdata-evidence-cutoff="([^"]+)"')


def unpublished_anchors(anchors, page):
    """The same-session anchors that were collected and accepted but never reached readers.

    `page` is the page main holds in this run's checkout (`publish/index.html`), the newest one published. An anchor
    whose evidence cutoff is later than that page's was accepted (its continuity upload proves it) and then not
    published, for example when the push was refused (2026-10-12 OPEN_1M). The anchor stays the analytical
    comparison; only its reader wording changes. A page without the attribute, or no page, decides nothing.
    """
    match = EVIDENCE_CUTOFF_ATTRIBUTE.search(page or "")
    if not match:
        return []
    try:
        published = timestamp(match.group(1))
        return [slot for slot, anchor in anchors.items() if slot != "previous_close"
                and timestamp(anchor["evidence_cutoff"]) > published]
    except (KeyError, TypeError, ValueError):
        return []


def completed_in_bundle(root, session_date, checkpoint):
    """Whether the restored continuity bundle already holds an accepted production record of this
    checkpoint for this session. The bundle survives runner boundaries, so a wake whose checkout predates
    the earlier run's publish still sees that the checkpoint (and any paid synthesis) already happened."""
    bundle, _ = load_bundle(bundle_path(root))
    for slot in SLOTS:
        origin = (bundle.get(slot) or {}).get("origin") or {}
        if (origin.get("session_date") == session_date and origin.get("checkpoint") == checkpoint
                and origin.get("mode") == "LIVE" and not origin.get("commissioning")
                and not origin.get("experiment")):
            return True
    return False


def scheduled(args):
    now = datetime.now(timezone.utc)
    ready, info = due(now, args.checkpoint)
    if not info["trading_day"]:
        print(f"SKIP / {args.checkpoint} / non-trading session {info['session_date']}")
        return 0
    if not ready:
        print(f"SKIP / {args.checkpoint} / outside checkpoint window; scheduled {info['scheduled_at']}")
        return 0
    marker = checkpoint_marker(RUN_ROOT, info["session_date"], args.checkpoint)
    published = Path(RUN_ROOT) / "publish" / "index.html"
    already_published = (published.is_file()
                         and f'data-session-date="{info["session_date"]}"' in published.read_text()
                         and f'data-checkpoint="{args.checkpoint}"' in published.read_text())
    if marker.is_file() or already_published or completed_in_bundle(RUN_ROOT, info["session_date"], args.checkpoint):
        print(f"SKIP / {args.checkpoint} / already completed for {info['session_date']}")
        return 0
    if checkpoint_kind(args.checkpoint) == "synthesis":
        # One paid generation per synthesis checkpoint and session: the authority is that a provider request was
        # attempted, never that its analysis was accepted (accepted state stays the continuity bundle's).
        try:
            earlier = earlier_attempt(args, info["session_date"], args.checkpoint)
        except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired):
            print(f"NOT RUN / {args.checkpoint} / earlier paid attempts for {info['session_date']} could not be "
                  "checked; no provider request was made", file=sys.stderr)
            return 2
        if earlier:
            print(f"SKIP / {args.checkpoint} / paid synthesis already attempted for {info['session_date']} "
                  f"({earlier})")
            return 0
    args.replay = False
    args.scheduled = True  # this gate already established that the checkpoint was never paid for
    result = run(args)
    if result == 0:
        mark_checkpoint(RUN_ROOT, info["session_date"], args.checkpoint, now.isoformat())
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="One local pre-market briefing, grounded in evidence")
    parser.add_argument("command", choices=["premarket", "schedule", "resolve-scheduled",
                                             "alpaca-probe", "open", "publish", "continuity-restore",
                                             "glance-baseline"])
    parser.add_argument("--replay", action="store_true", help="offline fictional evidence + narrative")
    parser.add_argument("--input", type=Path, help="sourced input JSON; SAMPLE for replay, LIVE otherwise")
    parser.add_argument("--synthesize", action="store_true", help="call Claude even for SAMPLE evidence")
    parser.add_argument("--cuttingboard", action="store_true", help="optional public GET-only quotation")
    parser.add_argument("--checkpoint", choices=CHECKPOINTS, default="PREMARKET")
    parser.add_argument("--full", action="store_true", help="probe the configured full Alpaca universe")
    parser.add_argument("--full-packet", action="store_true",
                        help="diagnostic: send the original evidence-plus-catalog payload "
                             "instead of the compact projection")
    parser.add_argument("--experiment", action="store_true",
                        help="retain the run locally without publishing or recording success")
    parser.add_argument("--commissioning", action="store_true",
                        help="label a manual live run by its actual collection time and market phase")
    parser.add_argument("--repay", action="store_true",
                        help="manual synthesis: pay again although this checkpoint's attempt is already recorded")
    parser.add_argument("--continuity", type=Path,
                        help="replay only: continuity bundle to admit instead of the sample fixture")
    parser.add_argument("--from-file", type=Path, help="continuity-restore: install this bundle file")
    parser.add_argument("--repository", help="continuity-restore: owner/repo (default GITHUB_REPOSITORY)")
    parser.add_argument("--branch", default="main", help="continuity-restore: expected artifact branch")
    args = parser.parse_args(argv)
    try:
        if args.command == "alpaca-probe":
            result = alpaca_probe(datetime.now(timezone.utc), ALPACA_UNIVERSE if args.full else ("SPY", "QQQ"))
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.command == "open":
            return open_latest(RUN_ROOT)
        if args.command == "publish":
            print(publish_latest(RUN_ROOT))
            return 0
        if args.command == "continuity-restore":
            return restore_continuity(args)
        if args.command == "glance-baseline":
            # Manual, once: --from-file closes.csv (market,date,close; about three years per index).
            if not args.from_file:
                raise ValueError("glance-baseline needs --from-file")
            print(glance.write_baseline(args.from_file))
            return 0
        if args.command == "resolve-scheduled":
            now = datetime.now(timezone.utc)
            checkpoint = scheduled_checkpoint(now)
            if checkpoint is None:
                # The workflow captures stdout as the checkpoint; the reason goes to the log.
                print(f"SKIP / - / no checkpoint due at {now.isoformat(timespec='seconds')}", file=sys.stderr)
            print(checkpoint or "SKIP")
            return 0
        if args.command == "schedule":
            return scheduled(args)
        return run(args)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        # Input/provider/model contents and credential-bearing exceptions never enter logs.
        print(f"Market Brief could not run ({type(exc).__name__}); check input schema/access.",
              file=sys.stderr)
        return 2

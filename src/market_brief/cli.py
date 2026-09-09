"""Thin local entry point for local brief generation and inspection."""

import argparse
import json
import os
import subprocess
import sys
import uuid
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .collect import ALPACA_UNIVERSE, alpaca_probe, collect_live, cuttingboard_record
from .context import analyst_context
from .continuity import (
    ARTIFACT_NAME,
    admit_prior_state,
    advance_bundle,
    bundle_path,
    compare_all,
    continuity_context,
    edition_state,
    load_bundle,
    restore_bundle,
    select_artifact,
    session_handoff,
    write_bundle,
)
from .evidence import ROOT, digest, finalize_coverage, normalize_packet, read_json, timestamp
from .metrics import annotate_magnitude, derive
from .render import render
from .schedule import CHECKPOINTS, checkpoint_session, current_phase, due, scheduled_checkpoint
from .synthesize import construct_prompt, synthesize, validate_narrative

# Assets (prompt, config, templates) come from ROOT; generated state lives under RUN_ROOT.
RUN_ROOT = ROOT
SAMPLE_CONTINUITY = ROOT / "tests/fixtures/continuity.sample.json"


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


def merge_input(collected, supplied):
    if supplied.get("mode") != "LIVE":
        raise ValueError("sample input cannot be relabeled as a live run")
    for key in ("sources", "observations", "history", "events", "context_items"):
        records = {row["id"]: row for row in collected.get(key, [])}
        records.update({row["id"]: row for row in supplied.get(key, [])})
        collected[key] = list(records.values())
    return collected


def run(args):
    started = datetime.now(timezone.utc)
    mode = "SAMPLE" if args.replay else "LIVE"
    commissioning = bool(getattr(args, "commissioning", False))
    experiment = bool(getattr(args, "experiment", False))
    full = bool(getattr(args, "full_packet", False))
    if args.replay:
        raw = read_json(args.input or ROOT / "tests/fixtures/evidence.sample.json")
        target = timestamp(raw["target_time"])
        # External source quotations must pass the same narrow adapter in replay.
        if raw.get("cuttingboard"):
            raw["cuttingboard"] = cuttingboard_record(raw["cuttingboard"], target, target)
    else:
        target = started
        raw = collect_live(target, include_cuttingboard=args.cuttingboard)
        if args.input:
            raw = merge_input(raw, read_json(args.input))
    # A commissioning run describes the market phase it actually collected in.
    checkpoint = current_phase(target) if commissioning else args.checkpoint
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
    packet["continuity"] = dict(status=prior["status"], reason=prior["reason"], anchors=prior["anchors"],
                                comparisons=comparisons)
    write_json(folder / "evidence.json", packet)
    evidence_hash = digest(packet)
    # The analyst reads exactly this saved projection; the validator checks references against it.
    context = dict(analyst_context(packet), **continuity_context(prior, comparisons))
    write_json(folder / "analyst_context.json", context)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, check=False).stdout.strip()
    metadata = dict(app_version=__version__, code_revision=revision or "unknown", mode=mode,
                    run_id=folder.name,
                    target_time=target.isoformat(), started_at=started.isoformat(),
                    meaningful_premarket=packet["run"]["session"]["meaningful_premarket"],
                    checkpoint=checkpoint, actual_started_at=started.isoformat(),
                    scheduled_checkpoint_at=checkpoint_data["scheduled_at"],
                    coverage=packet["coverage"]["status"], evidence_hash=evidence_hash,
                    context_schema=context["schema_version"], context_hash=digest(context),
                    validation="NOT_RUN", model_route="none", experiment=experiment,
                    commissioning=commissioning, synthesis_projection="full" if full else "compact",
                    continuity=dict(status=prior["status"], reason=prior["reason"],
                                    anchors={k: v["run_id"] for k, v in prior["anchors"].items()},
                                    comparisons=len(comparisons), advanced=False))
    try:
        if packet["coverage"]["status"] == "INSUFFICIENT":
            raise ValueError("no usable observations/events/context; evidence diagnostic only")
        if args.replay and not args.synthesize:
            fixture = "narrative.continuity.json" if prior["status"] == "available" else "narrative.sample.json"
            narrative = read_json(ROOT / "tests/fixtures" / fixture)
            validate_narrative(narrative, packet, None if full else context)
            system, prompt = construct_prompt(packet, full=full, context=context)
            model = dict(route="fixture-replay", resolved_models=[],
                         prompt_hash=digest(dict(system=system, user=prompt)),
                         evidence_hash=evidence_hash)
        else:
            print("Evidence collected; requesting one isolated structured synthesis.", flush=True)
            narrative, model = synthesize(packet, full=full, context=context)
        markdown, page = render(packet, narrative, context)
        (folder / "brief.md").write_text(markdown)
        (folder / "brief.html").write_text(page)
        update_latest(RUN_ROOT, page)
        if mode == "LIVE" and not experiment:
            publish_latest(RUN_ROOT)
        write_json(folder / "narrative.json", narrative)
        metadata.update(validation="PASS", model_route=model["route"], model=model,
                        narrative_hash=digest(narrative),
                        markdown_hash=digest(markdown), html_hash=digest(page))
        # Package this edition's state from the same validated response; no second model call.
        state = edition_state(packet, narrative, prior, comparisons, metadata["context_hash"],
                              metadata["narrative_hash"], __version__)
        write_json(folder / "edition_state.json", state)
        handoff, handoff_reason = session_handoff(state)
        if handoff is not None:
            write_json(folder / "session_handoff.json", handoff)
        metadata["continuity"].update(data_status=state["observed"]["data_status"],
                                      handoff="written" if handoff else handoff_reason,
                                      watches=[w["id"] for w in state["assessment"]["watches"]])
        production = mode == "LIVE" and not experiment and not commissioning
        if production and packet["coverage"]["status"] in {"READY", "PARTIAL"}:
            pointer = RUN_ROOT / "runs/latest-success.json"
            if pointer.is_symlink():
                raise ValueError("latest-success pointer cannot be a symlink")
            temporary = RUN_ROOT / "runs" / f"latest-{uuid.uuid4().hex}.tmp"
            write_json(temporary, {"run": str(folder.relative_to(RUN_ROOT)), "evidence_hash": digest(packet)})
            temporary.replace(pointer)
            # Only accepted production state advances the bundle; SAMPLE, experiment and
            # commissioning runs never touch it.
            write_bundle(bundle_path(RUN_ROOT), advance_bundle(bundle, state, handoff, datetime.now(timezone.utc)))
            metadata["continuity"]["advanced"] = True
    except ValueError as exc:
        metadata.update(validation="FAILED", error=str(exc))
        print(f"Brief not accepted: {exc}. Diagnostic: {folder}", file=sys.stderr)
        return 2
    finally:
        metadata["completed_at"] = datetime.now(timezone.utc).isoformat()
        write_json(folder / "metadata.json", metadata)
    print(f"{mode} / {packet['coverage']['status']} / validated: {folder / 'brief.html'}")
    return 0


def _gh_json(args, runner=subprocess.run):
    result = runner(["gh", "api", *args], capture_output=True, text=True, check=False, timeout=60)
    if result.returncode != 0:
        raise ValueError("GitHub API request failed")
    return json.loads(result.stdout)


def restore_continuity(args, runner=subprocess.run):
    """Install the newest accepted production bundle from a prior runner, or cold start explicitly.

    Never fails the job: an absent, foreign, or corrupt bundle produces a current-only brief.
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
                print("Continuity: cold start; no accepted bundle from a successful main-branch run.")
                return 0
            download = RUN_ROOT / "runs" / "continuity" / "restore"
            download.mkdir(parents=True, exist_ok=True)
            result = runner(["gh", "run", "download", str(artifact["workflow_run"]["id"]), "-n", ARTIFACT_NAME,
                             "-D", str(download), "-R", repository],
                            capture_output=True, text=True, check=False, timeout=120)
            if result.returncode != 0:
                raise ValueError("artifact download failed")
            source = download / "bundle.json"
            origin = f"artifact {artifact.get('id')} from run {artifact['workflow_run']['id']}"
        bundle, note = restore_bundle(source, destination)
        slots = {slot: (bundle[slot]["origin"]["run_id"] if bundle.get(slot) else None)
                 for slot in ("close", "premarket", "latest")}
        if any(slots.values()):
            print(f"Continuity: restored {origin}; " + ", ".join(f"{k}={v}" for k, v in slots.items())
                  + (f"; {note}" if note else ""))
        else:
            print(f"Continuity: cold start; {origin} held no usable production state" + (f" ({note})" if note else ""))
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(f"Continuity: cold start; restore failed ({type(exc).__name__}).")
    return 0


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
    if marker.is_file() or already_published:
        print(f"SKIP / {args.checkpoint} / already completed for {info['session_date']}")
        return 0
    args.replay = False
    result = run(args)
    if result == 0:
        mark_checkpoint(RUN_ROOT, info["session_date"], args.checkpoint, now.isoformat())
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="One local pre-market briefing, grounded in evidence")
    parser.add_argument("command", choices=["premarket", "schedule", "resolve-scheduled",
                                             "alpaca-probe", "open", "publish", "continuity-restore"])
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
        if args.command == "resolve-scheduled":
            print(scheduled_checkpoint(datetime.now(timezone.utc)) or "SKIP")
            return 0
        if args.command == "schedule":
            return scheduled(args)
        return run(args)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        # Input/provider/model contents and credential-bearing exceptions never enter logs.
        print(f"Market Brief could not run ({type(exc).__name__}); check input schema/access.",
              file=sys.stderr)
        return 2

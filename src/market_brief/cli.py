"""Thin local entry point for local brief generation and inspection."""

import argparse
import json
import subprocess
import sys
import uuid
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .collect import collect_live, cuttingboard_record
from .evidence import ROOT, digest, finalize_coverage, normalize_packet, read_json, timestamp
from .metrics import derive
from .render import render
from .synthesize import construct_prompt, synthesize, validate_narrative


def output_directory(root, mode, target):
    root = Path(root).resolve()
    base = root / "runs"
    if base.resolve() != base:
        raise ValueError("runs directory must not be a symlink")
    folder = base / target.strftime("%Y-%m-%d")
    if folder.resolve() != folder:
        raise ValueError("session directory must not be a symlink")
    folder = folder / f"{mode.lower()}-premarket-{target.strftime('%H%M%S')}-{uuid.uuid4().hex[:8]}"
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
    packet = normalize_packet(raw, target, mode)
    universe = read_json(ROOT / "config/universe.json")
    derive(packet, universe)
    finalize_coverage(packet)
    # Make unavailable, unimplemented categories explicit in every packet/report.
    config = read_json(ROOT / "config/sources.json")
    packet["coverage"]["limitations"].append("Not automated: " + ", ".join(config["deferred"]))
    packet["run"].update(collection_started_at=started.isoformat(),
                         collection_completed_at=datetime.now(timezone.utc).isoformat(),
                         app_version=__version__, universe_hash=digest(universe),
                         sources_config_hash=digest(config), manual_input=bool(args.input))
    folder = output_directory(ROOT, mode, target)
    write_json(folder / "evidence.json", packet)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, check=False).stdout.strip()
    metadata = dict(app_version=__version__, code_revision=revision or "unknown", mode=mode,
                    target_time=target.isoformat(), started_at=started.isoformat(),
                    meaningful_premarket=packet["run"]["session"]["meaningful_premarket"],
                    coverage=packet["coverage"]["status"], evidence_hash=digest(packet),
                    validation="NOT_RUN", model_route="none")
    try:
        if packet["coverage"]["status"] == "INSUFFICIENT":
            raise ValueError("no usable observations/events/context; evidence diagnostic only")
        if args.replay and not args.synthesize:
            narrative = read_json(ROOT / "tests/fixtures/narrative.sample.json")
            validate_narrative(narrative, packet)
            system, prompt = construct_prompt(packet)
            model = dict(route="fixture-replay", resolved_models=[],
                         prompt_hash=digest(dict(system=system, user=prompt)),
                         evidence_hash=digest(packet))
        else:
            print("Evidence collected; requesting one isolated Claude synthesis.", flush=True)
            narrative, model = synthesize(packet)
        markdown, page = render(packet, narrative)
        (folder / "brief.md").write_text(markdown)
        (folder / "brief.html").write_text(page)
        update_latest(ROOT, page)
        write_json(folder / "narrative.json", narrative)
        metadata.update(validation="PASS", model_route=model["route"], model=model,
                        markdown_hash=digest(markdown), html_hash=digest(page))
        if mode == "LIVE" and packet["coverage"]["status"] == "READY":
            pointer = ROOT / "runs/latest-success.json"
            if pointer.is_symlink():
                raise ValueError("latest-success pointer cannot be a symlink")
            temporary = ROOT / "runs" / f"latest-{uuid.uuid4().hex}.tmp"
            write_json(temporary, {"run": str(folder.relative_to(ROOT)), "evidence_hash": digest(packet)})
            temporary.replace(pointer)
    except ValueError as exc:
        metadata.update(validation="FAILED", error=str(exc))
        print(f"Brief not accepted: {exc}. Diagnostic: {folder}", file=sys.stderr)
        return 2
    finally:
        metadata["completed_at"] = datetime.now(timezone.utc).isoformat()
        write_json(folder / "metadata.json", metadata)
    print(f"{mode} / {packet['coverage']['status']} / validated: {folder / 'brief.html'}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="One local pre-market briefing, grounded in evidence")
    parser.add_argument("command", choices=["premarket", "open", "publish"])
    parser.add_argument("--replay", action="store_true", help="offline fictional evidence + narrative")
    parser.add_argument("--input", type=Path, help="sourced input JSON; SAMPLE for replay, LIVE otherwise")
    parser.add_argument("--synthesize", action="store_true", help="call Claude even for SAMPLE evidence")
    parser.add_argument("--cuttingboard", action="store_true", help="optional public GET-only quotation")
    args = parser.parse_args(argv)
    try:
        if args.command == "open":
            return open_latest()
        if args.command == "publish":
            print(publish_latest())
            return 0
        return run(args)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        # Input/provider/model contents and credential-bearing exceptions never enter logs.
        print(f"Market Brief could not run ({type(exc).__name__}); check input schema/access.",
              file=sys.stderr)
        return 2

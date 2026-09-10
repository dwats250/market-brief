import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from market_brief import cli
from market_brief.collect import (
    CB,
    SourceError,
    calendar_events,
    collect_live,
    cuttingboard_record,
    fed_context,
    treasury_rows,
)
from market_brief.evidence import (
    ROOT,
    finalize_coverage,
    model_packet,
    normalize_packet,
    read_json,
    timestamp,
)
from market_brief.metrics import derive
from market_brief.synthesize import construct_prompt, synthesize, validate_narrative

NOW = datetime(2026, 9, 8, 12, 45, tzinfo=timezone.utc)


def freeze_clock(monkeypatch, value):
    """Pin the CLI's wall clock so live-mode tests do not drift past the fixture's history."""
    fixed = datetime.fromisoformat(value).astimezone(timezone.utc)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz else fixed.replace(tzinfo=None)
    monkeypatch.setattr(cli, "datetime", Frozen)
    return fixed


def fixture_packet():
    raw = read_json(ROOT / "tests/fixtures/evidence.sample.json")
    raw["cuttingboard"] = cuttingboard_record(raw["cuttingboard"], NOW, NOW)
    return finalize_coverage(derive(normalize_packet(raw, NOW, "SAMPLE"),
                                    read_json(ROOT / "config/universe.json")))


def narrative():
    return read_json(ROOT / "tests/fixtures/narrative.sample.json")


def test_replay_narrative_valid_and_cuttingboard_failure_independent():
    packet = fixture_packet()
    packet["cuttingboard"] = {"status": "UNAVAILABLE", "reason": "offline"}
    assert validate_narrative(narrative(), packet)
    assert packet["coverage"]["status"] == "READY"


@pytest.mark.parametrize("mutation", ["unknown", "number", "mode", "banner", "section", "current",
                                      "placeholder", "attention", "cb"])
def test_bad_model_output_is_rejected(mutation):
    value = narrative()
    if mutation == "unknown":
        value["summary"][0]["evidence_ids"] = ["invented"]
    elif mutation == "number":
        value["summary"][0]["text"] = "SPY rose 8.8%."
    elif mutation == "mode":
        value["mode"] = "LIVE"
    elif mutation == "banner":
        del value["banner"]["label"]
    elif mutation == "section":
        value["sections"]["macro"] = "not an array"
    elif mutation == "current":
        value["summary"][0]["text"] = "Today the market is strong."
    elif mutation == "placeholder":
        value["summary"][0]["text"] = "Yield is {{treasury-2y}}."
    elif mutation == "attention":
        value["attention_ids"] = ["invented"]
    else:
        value["sections"]["cuttingboard"] = [value["summary"][0]]
    with pytest.raises(ValueError):
        validate_narrative(value, fixture_packet())


def test_prompt_removes_raw_history_and_unpermitted_facts():
    packet = fixture_packet()
    next(s for s in packet["sources"] if s["id"] == "sample-prices")["llm_allowed"] = False
    projected = model_packet(packet)
    assert not projected["derived"] and not projected["attention"]
    assert "history" not in projected
    system, data = construct_prompt(packet)
    assert "untrusted DATA" in system
    assert "fictional-only" not in data
    assert "history-SPY" not in data


def test_full_universe_synthesis_projection_is_bounded_and_history_stays_internal():
    packet = fixture_packet()
    source = packet["sources"][0]["id"]
    packet["history"] = []
    for index in range(21):
        symbol = f"S{index:02d}"
        packet["history"].append(dict(
            id=f"history-{symbol}", symbol=symbol,
            dates=[f"2026-06-{day:02d}" for day in range(1, 66)],
            closes=[100 + day / 100 for day in range(65)], adjustment="split",
            session="regular_close", source_id=source, retrieved_at="2026-09-08T12:00:00+00:00"))
    packet["derived"] = [dict(packet["derived"][0], id=f"derived-{index:03d}",
                               topic=f"S{index % 21:02d}") for index in range(105)]
    original_history = json.loads(json.dumps(packet["history"]))
    _, prompt = construct_prompt(packet)
    assert len(prompt.encode()) < 120_000
    assert packet["history"] == original_history and len(packet["history"]) == 21
    projected = model_packet(packet)
    assert "history" not in projected
    assert all("retrieved_at" not in row for row in projected["derived"])


def test_synthesis_projection_excludes_stale_and_unavailable_rows():
    packet = fixture_packet()
    source = packet["sources"][0]["id"]
    stale = dict(packet["derived"][0], id="stale-row", source_id=source, status="STALE")
    unavailable = dict(packet["derived"][0], id="unavailable-row", source_id=source, status="UNAVAILABLE")
    packet["derived"].extend([stale, unavailable])
    projected = model_packet(packet)
    ids = {row["id"] for row in projected["derived"]}
    assert "stale-row" not in ids and "unavailable-row" not in ids


def test_synthesis_projection_is_deterministic():
    packet = fixture_packet()
    assert model_packet(packet) == model_packet(packet)


@pytest.mark.parametrize("full", [False, True])
def test_claude_is_single_isolated_tools_off_route(monkeypatch, full):
    from market_brief.context import edition_profile
    from market_brief.synthesize import NARRATIVE_SCHEMA, narrative_schema, transport_schema

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("market_brief.synthesize.shutil.which", lambda _: "/usr/bin/claude")
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        assert kwargs["input"] and kwargs["timeout"] == 180
        assert "output_schema" not in json.loads(kwargs["input"])
        assert json.loads(argv[argv.index("--json-schema")+1]) == transport_schema(
            NARRATIVE_SCHEMA if full else narrative_schema(edition_profile("PREMARKET")))
        assert argv[argv.index("--tools")+1] == ""
        assert "--safe-mode" in argv and "--no-session-persistence" in argv
        assert "--strict-mcp-config" in argv and "--continue" not in argv
        assert not list(Path(kwargs["cwd"]).iterdir())
        assert "APCA_API_KEY_ID" not in kwargs["env"]
        return SimpleNamespace(returncode=0, stdout=json.dumps({"structured_output": narrative(),
                                  "modelUsage": {"claude-test": {}}}))
    output, meta = synthesize(fixture_packet(), runner=runner, full=full)
    assert output["mode"] == "SAMPLE" and len(calls) == 1
    assert meta["resolved_models"] == ["claude-test"]
    from market_brief.evidence import digest
    assert meta["schema_hash"] == digest(json.loads(calls[0][calls[0].index("--json-schema")+1]))


def test_model_timeout_has_no_raw_exception(monkeypatch):
    monkeypatch.setattr("market_brief.synthesize.shutil.which", lambda _: "/usr/bin/claude")

    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("private-command", 180)
    with pytest.raises(ValueError, match="timed out"):
        synthesize(fixture_packet(), runner=fail)


def test_no_live_relabeling():
    with pytest.raises(ValueError):
        normalize_packet(read_json(ROOT / "tests/fixtures/evidence.sample.json"), NOW, "LIVE")
    with pytest.raises(ValueError):
        cli.merge_input({}, {"mode": "SAMPLE"})


def test_bad_history_and_benchmark_mismatch():
    packet = fixture_packet()
    packet["history"][0]["dates"][-1] = "2026-09-03"
    derive(packet, read_json(ROOT / "config/universe.json"))
    assert not any(r["id"] == "SPY-daily" for r in packet["derived"])
    assert not any(r["id"] == "QQQ-spread20" for r in packet["derived"])
    assert packet["history_errors"]


def test_cuttingboard_subset_preserves_halt_and_ignores_artifact_urls():
    raw = read_json(ROOT / "tests/fixtures/cuttingboard.sample.json")
    raw["artifacts"] = {"path": "/tmp/cuttingboard-test/secret"}
    raw["trade_candidates"] = [{"entry": 123, "stop": 100}]
    result = cuttingboard_record(raw, NOW, NOW)
    assert result["outcome"] == "HALT" and result["permission"] == "HALT"
    assert "artifacts" not in result and "trade_candidates" not in result
    for bad in ({}, [], {**raw, "schema_version": "v99"},
                {**raw, "generated_at": "2026-09-08T13:00:00Z"}):
        assert cuttingboard_record(bad, NOW, NOW)["status"] == "INVALID"
    raw["generated_at"] = "2026-09-04T12:00:00Z"
    assert cuttingboard_record(raw, NOW, NOW)["status"] == "STALE"


def test_fetch_failures_are_bounded_and_cb_optional():
    calls = []

    def unavailable(url, deadline):
        calls.append(url)
        raise SourceError("HTTP 403")
    raw = collect_live(NOW, True, fetcher=unavailable)
    assert len(calls) == 4 and calls[-1] == CB
    assert raw["cuttingboard"]["status"] == "UNAVAILABLE"
    assert raw["sources"][0]["reason"] == "HTTP 403"
    assert not raw["observations"]


def test_treasury_date_and_basis():
    text = '''<feed><entry><NEW_DATE>2026-09-03T00:00:00</NEW_DATE>
      <BC_2YEAR>4.00</BC_2YEAR><BC_5YEAR>4.1</BC_5YEAR><BC_10YEAR>4.2</BC_10YEAR></entry>
      <entry><NEW_DATE>2026-09-04T00:00:00</NEW_DATE><BC_2YEAR>3.93</BC_2YEAR>
      <BC_5YEAR>4.1</BC_5YEAR><BC_10YEAR>4.2</BC_10YEAR></entry></feed>'''
    rows = treasury_rows(text, NOW, NOW)
    assert rows[0]["observed_at"] == "2026-09-04"
    assert rows[1]["value"] == pytest.approx(-7)
    assert rows[0]["frequency"] == "daily"


def test_calendar_and_news_cutoff():
    ics = '''BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;TZID=America/New_York:20260908T100000
SUMMARY:Sample release
END:VEVENT
END:VCALENDAR'''
    events = calendar_events(ics, NOW, NOW)
    assert events[0]["scheduled_at"] == "2026-09-08T14:00:00+00:00"
    assert events[0]["published_at"] is None  # checked time is not publication time
    with pytest.raises(SourceError):
        calendar_events("BEGIN:VCALENDAR\nEND:VCALENDAR", NOW, NOW)
    rss = '''<rss><channel><item><title>Future news</title>
      <pubDate>Tue, 08 Sep 2026 15:00:00 GMT</pubDate></item></channel></rss>'''
    assert not fed_context(rss, NOW)


def test_cli_replay_and_invalid_model_leave_no_latest_pointer(tmp_path, monkeypatch):
    # Assets are read from this repo, but the generated output is confined to a temporary test root.
    original = cli.output_directory
    monkeypatch.setattr(cli, "output_directory", lambda root, *rest:
                        original(tmp_path, *rest))
    monkeypatch.setattr(cli, "update_latest", lambda root, page: None)
    assert cli.main(["premarket", "--replay"]) == 0
    directories = list((tmp_path / "runs").glob("*/*"))
    assert len(directories) == 1
    assert "FICTIONAL SAMPLE" in (directories[0] / "brief.html").read_text()
    monkeypatch.setattr(cli, "synthesize", lambda _: (_ for _ in ()).throw(ValueError("invalid IDs")))
    assert cli.main(["premarket", "--replay", "--synthesize"]) == 2
    failed = [d for d in (tmp_path / "runs").glob("*/*") if not (d / "brief.html").exists()]
    assert len(failed) == 1 and (failed[0] / "metadata.json").exists()
    assert not (tmp_path / "runs/latest-success.json").exists()


def test_output_symlink_escape_rejected(tmp_path):
    destination = tmp_path / "elsewhere"
    destination.mkdir()
    root = tmp_path / "repo"
    root.mkdir()
    (root / "runs").symlink_to(destination, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        cli.output_directory(root, "SAMPLE", timestamp("2026-09-08T12:45:00Z"))


def test_latest_output_is_stable_and_openable(tmp_path, monkeypatch):
    cli.update_latest(tmp_path, "<html>first</html>")
    cli.update_latest(tmp_path, "<html>second</html>")
    latest = tmp_path / "output/latest.html"
    assert latest.read_text() == "<html>second</html>"
    opened = []
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url))
    assert cli.main(["open"]) == 0
    assert opened == [latest.resolve().as_uri()]


def test_open_command_reports_missing_latest(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    assert cli.main(["open"]) == 2
    assert "No latest brief exists" in capsys.readouterr().err


def test_publish_copies_only_human_facing_latest(tmp_path):
    latest = tmp_path / "output/latest.html"
    latest.parent.mkdir()
    latest.write_text("<html>shareable brief</html>")
    result = cli.publish_latest(tmp_path)
    assert result == tmp_path / "publish/index.html"
    assert result.read_text() == latest.read_text()
    assert [path.name for path in result.parent.iterdir()] == ["index.html"]


# --- continuity across runner boundaries ------------------------------------------------------

def test_fresh_workspace_restores_a_valid_bundle_and_rejects_foreign_state(tmp_path, monkeypatch):
    from test_continuity import live_cli

    from market_brief.continuity import bundle_path, load_bundle
    first = tmp_path / "runner-a"
    first.mkdir()
    runner = live_cli(monkeypatch, first, "2026-09-04T20:03:00+00:00", "CLOSE_1M")
    assert runner.main(["premarket", "--checkpoint", "CLOSE_1M"]) == 0
    exported = bundle_path(first)
    folder = next(p for p in (first / "runs/2026-09-04").iterdir() if (p / "evidence.json").exists())
    assert folder.name.startswith("live-close_1m-")
    # A second, empty workspace restores from the file the first runner uploaded.
    second = tmp_path / "runner-b"
    second.mkdir()
    monkeypatch.setattr(cli, "RUN_ROOT", second)
    assert cli.main(["continuity-restore", "--from-file", str(exported)]) == 0
    restored, note = load_bundle(bundle_path(second))
    assert note == "" and restored["close"]["content_hash"] == load_bundle(exported)[0]["close"]["content_hash"]
    runner = live_cli(monkeypatch, second, "2026-09-08T12:45:00+00:00", "PREMARKET", intraday=False)
    assert runner.main(["premarket", "--checkpoint", "PREMARKET"]) == 0
    folder = next(p for p in (second / "runs/2026-09-08").iterdir() if (p / "evidence.json").exists())
    assert folder.name.startswith("live-premarket-")
    context = json.loads((folder / "analyst_context.json").read_text())
    assert context["prior_state"]["status"] == "available"
    # A bundle written by a SAMPLE, experiment, or commissioning origin never installs.
    foreign = json.loads(exported.read_text())
    for slot in ("close", "premarket", "latest"):
        if foreign[slot]:
            foreign[slot]["origin"]["mode"] = "SAMPLE"
            foreign[slot]["content_hash"] = cli.digest({k: v for k, v in foreign[slot].items() if k != "content_hash"})
    (tmp_path / "foreign.json").write_text(json.dumps(foreign))
    third = tmp_path / "runner-c"
    third.mkdir()
    monkeypatch.setattr(cli, "RUN_ROOT", third)
    assert cli.main(["continuity-restore", "--from-file", str(tmp_path / "foreign.json")]) == 0
    assert not bundle_path(third).exists()
    assert cli.main(["continuity-restore", "--from-file", str(tmp_path / "absent.json")]) == 0
    assert not bundle_path(third).exists()


def test_artifact_selection_requires_main_branch_successful_expected_workflow():
    from market_brief.continuity import select_artifact
    runs = {
        1: {"conclusion": "success", "path": ".github/workflows/schedule.yml"},
        2: {"conclusion": "failure", "path": ".github/workflows/schedule.yml"},
        3: {"conclusion": "success", "path": ".github/workflows/pages.yml"},
        4: {"conclusion": "success", "path": ".github/workflows/schedule.yml"},
    }
    def artifact(ident, run, branch, created, expired=False):
        return {"id": ident, "expired": expired, "created_at": created,
                "workflow_run": {"id": run, "head_branch": branch}}
    artifacts = [
        artifact(10, 2, "main", "2026-09-08T20:10:00Z"),       # failed run
        artifact(11, 3, "main", "2026-09-08T19:10:00Z"),       # other workflow
        artifact(12, 4, "feature", "2026-09-08T18:10:00Z"),    # wrong branch
        artifact(13, 1, "main", "2026-09-08T17:10:00Z", True),  # expired
        artifact(14, 1, "main", "2026-09-08T13:10:00Z"),       # acceptable
    ]
    assert select_artifact(artifacts, runs.get)["id"] == 14
    assert select_artifact(artifacts[:4], runs.get) is None
    assert select_artifact([], runs.get) is None


def test_restore_uses_gh_only_for_listing_run_lookup_and_download(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from market_brief.continuity import bundle_path, load_bundle
    exported = ROOT / "tests/fixtures/continuity.sample.json"
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["gh", "api"] and "artifacts" in argv[2]:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"artifacts": [
                {"id": 7, "expired": False, "created_at": "2026-09-04T20:10:00Z",
                 "workflow_run": {"id": 99, "head_branch": "main"}}]}))
        if argv[:2] == ["gh", "api"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(
                {"conclusion": "success", "path": ".github/workflows/schedule.yml"}))
        assert argv[:3] == ["gh", "run", "download"] and argv[3] == "99"
        target = Path(argv[argv.index("-D") + 1])
        target.mkdir(parents=True, exist_ok=True)
        (target / "bundle.json").write_text(exported.read_text())
        return SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    args = SimpleNamespace(from_file=None, repository="owner/repo", branch="main")
    assert cli.restore_continuity(args, runner=runner) == 0
    assert [c[:2] for c in calls] == [["gh", "api"], ["gh", "api"], ["gh", "run"]]
    # The sample fixture is SAMPLE-origin state: a production restore drops it and cold starts.
    assert not bundle_path(tmp_path).exists()
    assert load_bundle(bundle_path(tmp_path))[1] == "no continuity bundle"


def test_pages_payload_is_only_the_human_brief_and_archives_stay_outside():
    workflow = (ROOT / ".github/workflows/schedule.yml").read_text()
    pages_step = workflow.split("Upload Pages artifact", 1)[1].split("uses: actions/deploy-pages", 1)[0]
    assert "path: publish" in pages_step and "runs" not in pages_step
    assert "actions: read" in workflow
    assert "name: market-brief-continuity\n" in workflow
    assert "inputs.experiment != true && inputs.commissioning != true" in workflow
    assert "!runs/continuity/" in workflow

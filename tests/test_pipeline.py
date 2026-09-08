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


def test_claude_is_single_isolated_tools_off_route(monkeypatch):
    monkeypatch.setattr("market_brief.synthesize.shutil.which", lambda _: "/usr/bin/claude")
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        assert kwargs["input"] and kwargs["timeout"] == 180
        assert argv[argv.index("--tools")+1] == ""
        assert "--safe-mode" in argv and "--no-session-persistence" in argv
        assert "--strict-mcp-config" in argv and "--continue" not in argv
        assert not list(Path(kwargs["cwd"]).iterdir())
        assert "APCA_API_KEY_ID" not in kwargs["env"]
        return SimpleNamespace(returncode=0, stdout=json.dumps({"structured_output": narrative(),
                                  "modelUsage": {"claude-test": {}}}))
    output, meta = synthesize(fixture_packet(), runner=runner)
    assert output["mode"] == "SAMPLE" and len(calls) == 1
    assert meta["resolved_models"] == ["claude-test"]


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
    monkeypatch.setattr(cli, "output_directory", lambda root, mode, target:
                        original(tmp_path, mode, target))
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

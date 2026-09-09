"""Commissioning runs describe the market phase they actually collected in."""

import json
from datetime import datetime, timezone

from test_pipeline import fixture_packet, narrative

from market_brief import cli
from market_brief.evidence import ROOT, read_json
from market_brief.render import render
from market_brief.schedule import current_phase


def utc(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def test_current_phase_partitions_the_trading_day():
    assert current_phase(utc("2026-09-08T12:45:00+00:00")) == "PREMARKET"
    assert current_phase(utc("2026-09-08T13:31:00+00:00")) == "OPEN_1M"
    assert current_phase(utc("2026-09-08T13:59:00+00:00")) == "OPEN_1M"
    assert current_phase(utc("2026-09-08T14:00:00+00:00")) == "OPEN_30M"
    assert current_phase(utc("2026-09-08T18:00:00+00:00")) == "OPEN_30M"
    assert current_phase(utc("2026-09-08T19:07:00+00:00")) == "AFTERNOON"
    assert current_phase(utc("2026-09-08T19:59:00+00:00")) == "AFTERNOON"
    assert current_phase(utc("2026-09-08T20:00:00+00:00")) == "CLOSE_1M"
    assert current_phase(utc("2026-09-08T23:30:00+00:00")) == "CLOSE_1M"


def test_current_phase_respects_holidays_and_early_closes():
    assert current_phase(utc("2026-09-07T15:00:00+00:00")) == "PREMARKET"
    assert current_phase(utc("2026-11-27T17:30:00+00:00")) == "OPEN_30M"
    assert current_phase(utc("2026-11-27T18:05:00+00:00")) == "CLOSE_1M"


def test_commissioning_run_resolves_phase_from_clock(tmp_path, monkeypatch):
    raw = read_json(ROOT / "tests/fixtures/evidence.sample.json")
    raw["target_time"] = "2026-09-08T20:03:00+00:00"
    for row in raw["history"]:
        row["retrieved_at"] = raw["target_time"]
    for row in raw["observations"]:
        row["retrieved_at"] = raw["target_time"]
    fixture = tmp_path / "postclose.json"
    fixture.write_text(json.dumps(raw))
    original = cli.output_directory
    monkeypatch.setattr(cli, "output_directory", lambda root, *rest: original(tmp_path, *rest))
    monkeypatch.setattr(cli, "update_latest", lambda root, page: None)
    monkeypatch.setattr(cli, "publish_latest", lambda root: None)
    assert cli.main(["premarket", "--replay", "--commissioning", "--input", str(fixture)]) == 0
    folder = next((tmp_path / "runs").glob("*/*"))
    evidence = json.loads((folder / "evidence.json").read_text())
    assert evidence["run"]["checkpoint"] == "CLOSE_1M"
    assert evidence["run"]["commissioning"] is True
    page = (folder / "brief.html").read_text()
    head = page.split("<h1>", 1)[0]
    assert 'data-checkpoint="COMMISSIONING"' in head
    assert "pre-market edition" not in head.lower()
    assert "close +1m" in head.lower()


def test_replay_commissioning_stays_sample_and_never_publishes(tmp_path, monkeypatch):
    original = cli.output_directory
    monkeypatch.setattr(cli, "output_directory", lambda root, *rest: original(tmp_path, *rest))
    monkeypatch.setattr(cli, "update_latest", lambda root, page: None)
    published = []
    monkeypatch.setattr(cli, "publish_latest", lambda root: published.append(root))
    assert cli.main(["premarket", "--replay", "--commissioning"]) == 0
    assert cli.main(["premarket", "--replay"]) == 0
    assert published == []
    for folder in (tmp_path / "runs").glob("*/*"):
        head = (folder / "brief.html").read_text().split("<h1>", 1)[0]
        assert "SAMPLE" in head
        assert "LIVE COMMISSIONING" not in head


def test_live_commissioning_header_names_the_phase():
    packet = fixture_packet()
    packet["run"]["mode"] = "LIVE"
    packet["run"]["checkpoint"] = "AFTERNOON"
    packet["run"]["commissioning"] = True
    value = narrative()
    value["mode"] = "LIVE"
    md, page = render(packet, value)
    head = page.split("<h1>", 1)[0]
    assert "LIVE COMMISSIONING" in head
    assert "Afternoon edition" in head
    assert "pre-market edition" not in head.lower()
    assert 'data-checkpoint="COMMISSIONING"' in head
    assert "as of 5:45 AM PT" in head and "collected 5:45 AM PT" in head
    assert "Afternoon edition" in md


def test_scheduled_header_uses_human_checkpoint_labels():
    packet = fixture_packet()
    packet["run"]["mode"] = "LIVE"
    packet["run"]["checkpoint"] = "OPEN_30M"
    packet["run"]["session"]["meaningful_premarket"] = True
    value = narrative()
    value["mode"] = "LIVE"
    _, page = render(packet, value)
    head = page.split("<h1>", 1)[0]
    visible = head.split("<body>", 1)[1]
    assert "Opening structure edition" in visible
    assert "OPEN_30M" not in visible and "OPEN 30M" not in visible
    assert 'data-checkpoint="OPEN_30M"' in head

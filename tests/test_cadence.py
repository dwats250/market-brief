"""The daily cadence: two syntheses, deterministic refreshes under a frozen interpretation, a
deterministic close that hands the session off, and the two-clock page that results."""

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone

import exchange_calendars as xcals
import pytest
from test_contract import edition_response
from test_pipeline import fixture_packet, freeze_clock, narrative
from test_render import Page

from market_brief import cli
from market_brief.context import edition_profile, editions_config
from market_brief.continuity import (
    SLOTS,
    admit_interpretation,
    admit_prior_state,
    bundle_path,
    load_bundle,
    write_bundle,
)
from market_brief.evidence import ROOT, evidence_catalog, read_json
from market_brief.render import (
    NO_PRINT,
    NOT_APPLICABLE,
    NOT_COLLECTED,
    direction,
    formatted,
    next_update_label,
    presentation,
    render,
    source_rows,
)
from market_brief.schedule import CHECKPOINT_KINDS, CHECKPOINTS, SYNTHESIS_CHECKPOINTS, next_checkpoint
from market_brief.synthesize import validate_narrative

TUE = "2026-09-08"


def utc(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


class Day:
    """Drive the CLI through one production day with frozen clocks, counting analyst calls."""

    def __init__(self, monkeypatch, root):
        self.monkeypatch, self.root = monkeypatch, root
        self.calls, self.published, self.narratives = [], [], {}

    def run(self, now, checkpoint, *, intraday=True, value=None, mutate=None, last_history_date="2026-09-04",
            print_at=None, fail_synthesis=False, command="premarket", scale_last_close=None,
            prints=(("SPY", -0.53), ("QQQ", -0.61), ("XLI", 0.4))):
        freeze_clock(self.monkeypatch, now)
        raw = read_json(ROOT / "tests/fixtures/evidence.sample.json")
        raw["mode"] = "LIVE"
        cal = xcals.get_calendar("XNYS")
        for row in raw["history"]:
            row["retrieved_at"] = now
            if row["dates"][-1] != last_history_date:
                sessions = cal.sessions_in_range("2026-01-01", last_history_date)
                row["dates"] = [s.date().isoformat() for s in sessions[-len(row["closes"]):]]
            if scale_last_close and row["symbol"] in scale_last_close:
                row["closes"][-1] = round(row["closes"][-1] * scale_last_close[row["symbol"]], 4)
        for row in raw["observations"] + raw["events"]:
            row["retrieved_at"] = now
            if "checked_at" in row:
                row["checked_at"] = now
        if intraday:
            for symbol, value_ in prints:
                raw["observations"].append(dict(
                    id=f"{symbol}-intraday", topic=symbol, metric="premarket return", value=value_, unit="%",
                    baseline="latest trade versus previous regular close", frequency="intraday",
                    observed_at=print_at or now, retrieved_at=now, source_id="sample-prices",
                    status="AVAILABLE", reason=""))
        self.monkeypatch.setattr(cli, "collect_live", lambda target, include_cuttingboard=False: raw)

        def synthesize(packet, full=False, context=None, **kwargs):
            """The analyst stand-in: counts the call and returns a narrative that production would accept
            (validated against the exact context), or the given one, or fails like a rejected response."""
            self.calls.append(packet["run"]["checkpoint"])
            if fail_synthesis:
                raise ValueError("synthesis rejected")
            live = value or edition_response(edition_profile(packet["run"]["checkpoint"]), context)
            if mutate:
                mutate(live)
            live["mode"] = "LIVE"
            validate_narrative(live, packet, context)
            self.narratives[checkpoint] = live
            return live, {"route": "test"}
        self.monkeypatch.setattr(cli, "synthesize", synthesize)
        self.monkeypatch.setattr(cli, "RUN_ROOT", self.root)
        self.monkeypatch.setattr(cli, "update_latest", lambda root, page: None)
        self.monkeypatch.setattr(cli, "publish_latest", lambda root: self.published.append(checkpoint))
        return cli.main([command, "--checkpoint", checkpoint])

    def attempts(self, checkpoint, session=TUE):
        prefix = f"live-{checkpoint.lower()}-"
        folder = self.root / "runs" / session
        return [p for p in folder.iterdir() if p.name.startswith(prefix)] if folder.exists() else []

    def folder(self, checkpoint, session=TUE):
        prefix = f"live-{checkpoint.lower()}-"
        return next(p for p in sorted((self.root / "runs" / session).iterdir()) if p.name.startswith(prefix))

    def page(self, checkpoint, session=TUE):
        return (self.folder(checkpoint, session) / "brief.html").read_text()

    def metadata(self, checkpoint, session=TUE):
        return json.loads((self.folder(checkpoint, session) / "metadata.json").read_text())

    def bundle(self):
        bundle, note = load_bundle(bundle_path(self.root))
        assert note == ""
        return bundle


PASSED = " · horizon passed"


def interpretation_fragments(page):
    """The parts of the page that belong to the interpretation clock: headline through the read,
    what changed, and the watches. Everything else on the page belongs to the data clock. The one
    deterministic annotation a refresh may add to a watch, `horizon passed`, is stripped before comparing."""
    head = page.split("<h1>", 1)[1].split('<div class="figures">', 1)[0]
    since = page.split('<div class="since">', 1)[1].split("<section", 1)[0] if '<div class="since">' in page else ""
    watches = page.split('<span class="eyebrow">Watches</span>', 1)[1].split('<span class="eyebrow">Flagged', 1)[0]
    return head, since, watches.replace(PASSED, "")


@pytest.fixture
def day(monkeypatch, tmp_path):
    return Day(monkeypatch, tmp_path)


# --- the scheduler and the configuration agree on what synthesizes --------------------------------

def test_only_the_two_synthesis_checkpoints_carry_an_edition_profile():
    assert SYNTHESIS_CHECKPOINTS == ("PREMARKET", "OPEN_30M")
    assert set(editions_config()["editions"]) == set(SYNTHESIS_CHECKPOINTS)
    for checkpoint in CHECKPOINTS:
        if CHECKPOINT_KINDS[checkpoint] == "synthesis":
            assert edition_profile(checkpoint)["profile"] in {"rich", "light"}
        else:
            with pytest.raises(ValueError, match="deterministic checkpoint"):
                edition_profile(checkpoint)
    assert CHECKPOINT_KINDS["CLOSE_1M"] == "close" and CHECKPOINT_KINDS["OPEN_1M"] == "refresh"
    assert all(CHECKPOINT_KINDS[c] == "refresh" for c in CHECKPOINTS if c.startswith("HOURLY_"))


def test_next_update_label_follows_the_scheduler():
    def label(now, current):
        return next_update_label(next_checkpoint(utc(now), current), TUE)
    assert label(f"{TUE}T13:00:00+00:00", "PREMARKET") == "Next update · 6:31 AM PT"
    assert label(f"{TUE}T13:31:00+00:00", "OPEN_1M") == "Next update · 7:00 AM PT · interpretation"
    assert label(f"{TUE}T14:01:00+00:00", "OPEN_30M") == "Next update · 8:00 AM PT"
    assert label(f"{TUE}T19:00:00+00:00", "HOURLY_1500") == "Next update · 1:01 PM PT · close snapshot"
    assert label(f"{TUE}T20:03:00+00:00", "CLOSE_1M") == "Next update · Wed, Sep 9 · 6:00 AM PT premarket"
    # Friday's close points at Tuesday: Labor Day is skipped.
    assert next_update_label(next_checkpoint(utc("2026-09-04T20:03:00+00:00"), "CLOSE_1M"), "2026-09-04") \
        == "Next update · Tue, Sep 8 · 6:00 AM PT premarket"


# --- one production day ----------------------------------------------------------------------------

def test_one_production_day_synthesizes_twice_and_refreshes_deterministically(day):
    # 6:00 AM PT: the premarket edition is the day's rich synthesis.
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    assert day.calls == ["PREMARKET"]
    premarket = day.page("PREMARKET")
    assert "LIVE · Premarket edition · Tuesday, Sep 8" in premarket
    assert "As of 6:00 AM PT · Next update · 6:31 AM PT" in premarket
    bundle = day.bundle()
    assert bundle["interpretation"]["origin"]["checkpoint"] == "PREMARKET"
    premarket_interpretation = bundle["interpretation"]["content_hash"]
    assert day.metadata("PREMARKET")["synthesis"] == dict(kind="synthesis", calls=1)

    # 6:31 AM PT: the open +1M refresh renders opening prints under the premarket interpretation.
    assert day.run(f"{TUE}T13:31:00+00:00", "OPEN_1M") == 0
    assert day.calls == ["PREMARKET"]
    opening = day.page("OPEN_1M")
    assert "LIVE · Opening refresh · Tuesday, Sep 8" in opening
    assert ("Analysis anchored 6:00 AM PT · Observed record refreshed 6:31 AM PT · Next update · 7:00 AM PT · "
            "interpretation") in opening
    assert interpretation_fragments(opening)[0] == interpretation_fragments(premarket)[0]
    bundle = day.bundle()
    assert bundle["interpretation"]["content_hash"] == premarket_interpretation  # never rewritten by a refresh
    assert bundle["latest"]["origin"]["checkpoint"] == "OPEN_1M"
    assert bundle["latest"]["assessment"]["origin"] == "carried"
    assert bundle["premarket"]["origin"]["checkpoint"] == "PREMARKET"
    assert day.metadata("OPEN_1M")["synthesis"] == dict(kind="refresh", calls=0)
    assert day.metadata("OPEN_1M")["interpretation"]["checkpoint"] == "PREMARKET"

    # 7:00 AM PT: the one interpretive update after the open, against the premarket and opening anchors.
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M") == 0
    assert day.calls == ["PREMARKET", "OPEN_30M"]
    structure = day.page("OPEN_30M")
    assert "LIVE · Opening structure edition · Tuesday, Sep 8" in structure
    assert "As of 7:01 AM PT · Next update · 8:00 AM PT" in structure
    # The fixture narrative interprets no change, so the block stays out rather than showing an empty heading;
    # the anchors it would have named are still recorded.
    assert "<h2>What changed</h2>" not in structure
    view = presentation(json.loads((day.folder("OPEN_30M") / "evidence.json").read_text()),
                        day.narratives["OPEN_30M"],
                        json.loads((day.folder("OPEN_30M") / "analyst_context.json").read_text()))
    assert view["since"]["label"] == "vs premarket and the 6:31 AM PT refresh"
    context = json.loads((day.folder("OPEN_30M") / "analyst_context.json").read_text())
    assert set(context["prior_state"]["anchors"]) == {"premarket", "latest"}
    bundle = day.bundle()
    assert bundle["interpretation"]["origin"]["checkpoint"] == "OPEN_30M"
    structure_interpretation = bundle["interpretation"]["content_hash"]
    assert structure_interpretation != premarket_interpretation

    # 10:00 and 11:00 AM PT: hourly refreshes, zero analyst calls, the 7:00 interpretation byte for byte.
    assert day.run(f"{TUE}T17:00:00+00:00", "HOURLY_1300") == 0
    assert day.run(f"{TUE}T18:00:00+00:00", "HOURLY_1400") == 0
    assert day.calls == ["PREMARKET", "OPEN_30M"]
    ten, eleven = day.page("HOURLY_1300"), day.page("HOURLY_1400")
    assert "Analysis anchored 7:01 AM PT · Observed record refreshed 10:00 AM PT · Next update · 11:00 AM PT" in ten
    assert "Analysis anchored 7:01 AM PT · Observed record refreshed 11:00 AM PT · Next update · 12:00 PM PT" in eleven
    assert interpretation_fragments(ten) == interpretation_fragments(structure)
    assert interpretation_fragments(eleven) == interpretation_fragments(ten)
    assert ten != eleven  # the observed record moved: clocks, tables, ledger
    # Deterministic state a refresh may add: the 7:00 opening-hour watch has run out by 10:00.
    assert PASSED not in structure and f"Through the opening hour{PASSED}" in ten
    assert 'data-checkpoint="HOURLY_1300"' in ten and 'data-checkpoint="HOURLY_1400"' in eleven
    assert "as of 10:00 AM PT" in ten and "as of 11:00 AM PT" in eleven  # table captions carry the data clock
    for checkpoint in ("HOURLY_1300", "HOURLY_1400"):
        assert day.metadata(checkpoint)["synthesis"] == dict(kind="refresh", calls=0)
        assert not (day.folder(checkpoint) / "analyst_context.json").exists()
        assert not (day.folder(checkpoint) / "narrative.json").exists()
    bundle = day.bundle()
    assert bundle["interpretation"]["content_hash"] == structure_interpretation
    assert bundle["latest"]["origin"]["checkpoint"] == "HOURLY_1400"
    assert bundle["latest"]["assessment"]["interpretation_run_id"] == bundle["interpretation"]["origin"]["run_id"]

    # 1:03 PM PT: the close is a deterministic snapshot that hands the session off; no synthesis.
    assert day.run(f"{TUE}T20:03:00+00:00", "CLOSE_1M", print_at=f"{TUE}T19:59:58+00:00") == 0
    assert day.calls == ["PREMARKET", "OPEN_30M"]
    close = day.page("CLOSE_1M")
    assert "LIVE · Close snapshot · Tuesday, Sep 8" in close
    assert ("Analysis anchored 7:01 AM PT · Observed record refreshed 1:03 PM PT · Next update · Wed, Sep 9 · "
            "6:00 AM PT premarket") in close
    assert interpretation_fragments(close)[:2] == interpretation_fragments(structure)[:2]
    assert "horizon passed" in close  # the 7:00 watches ran into the close, which has now happened
    assert (day.folder("CLOSE_1M") / "session_handoff.json").exists()
    assert day.metadata("CLOSE_1M")["synthesis"] == dict(kind="close", calls=0)
    assert day.metadata("CLOSE_1M")["continuity"]["handoff"] == "written"
    bundle = day.bundle()
    assert bundle["close"]["kind"] == "session_handoff" and bundle["close"]["session"]["date"] == TUE
    assert bundle["close"]["observed"]["closing_data"]["status"] == "PROVISIONAL_NEAR_CLOSE"
    character = bundle["close"]["assessment"]["closing_character"]
    assert character["checkpoint"] == "OPEN_30M" and character["provisional"] is True
    assert character["text"] == day.narratives["OPEN_30M"]["character"]["text"]
    assert bundle["interpretation"]["content_hash"] == structure_interpretation
    # The close carries the 7:00 update's watches plus the premarket watch it kept, nothing newer.
    origins = {w["origin_run_id"] for w in bundle["close"]["assessment"]["watches"]}
    assert bundle["interpretation"]["origin"]["run_id"] in origins
    assert origins <= {bundle["interpretation"]["origin"]["run_id"], bundle["premarket"]["origin"]["run_id"]}
    assert all(slot in bundle for slot in SLOTS)
    assert day.published == ["PREMARKET", "OPEN_1M", "OPEN_30M", "HOURLY_1300", "HOURLY_1400", "CLOSE_1M"]

    # Wednesday 6:00 AM PT: the next premarket admits the deterministic close and synthesizes again.
    assert day.run("2026-09-09T13:00:00+00:00", "PREMARKET", intraday=False, last_history_date=TUE) == 0
    assert day.calls == ["PREMARKET", "OPEN_30M", "PREMARKET"]
    wednesday = day.page("PREMARKET", "2026-09-09")
    assert "As of 6:00 AM PT · Next update · 6:31 AM PT" in wednesday
    context = json.loads((day.folder("PREMARKET", "2026-09-09") / "analyst_context.json").read_text())
    view = presentation(json.loads((day.folder("PREMARKET", "2026-09-09") / "evidence.json").read_text()),
                        narrative(), context)
    assert view["since"]["label"] == "vs the previous close · Tue, Sep 8"
    assert context["prior_state"]["status"] == "available"
    assert set(context["prior_state"]["anchors"]) == {"previous_close"}
    assert context["prior_state"]["anchors"]["previous_close"]["checkpoint"] == "CLOSE_1M"
    assert context["prior_state"]["closing_character"]["checkpoint"] == "OPEN_30M"
    assert {w["id"].split("-")[1] for w in context["prior_state"]["watches"]} == {"live"}
    comparisons = {c["id"]: c for c in json.loads(
        (day.folder("PREMARKET", "2026-09-09") / "evidence.json").read_text())["continuity"]["comparisons"]}
    assert comparisons["cmp-previous_close-SPY-daily"]["status"] == "changed"


def test_refresh_fails_closed_without_a_same_session_interpretation(day):
    # Nothing accepted today: a refresh has no interpretation to carry and publishes nothing.
    assert day.run(f"{TUE}T17:00:00+00:00", "HOURLY_1300") == 2
    assert day.calls == [] and day.published == []
    assert not (day.folder("HOURLY_1300") / "brief.html").exists()
    assert "no accepted interpretation for this session" in day.metadata("HOURLY_1300")["error"]
    assert not bundle_path(day.root).exists()


def test_refresh_without_current_prints_keeps_the_last_accepted_page(day):
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    before = bundle_path(day.root).read_text()
    assert day.run(f"{TUE}T17:00:00+00:00", "HOURLY_1300", intraday=False) == 2
    assert day.published == ["PREMARKET"] and day.calls == ["PREMARKET"]
    assert "no timestamped current prints" in day.metadata("HOURLY_1300")["error"]
    assert bundle_path(day.root).read_text() == before  # nothing advanced, nothing rewritten


def test_close_without_session_observations_neither_publishes_nor_hands_off(day):
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    before = bundle_path(day.root).read_text()
    assert day.run(f"{TUE}T20:03:00+00:00", "CLOSE_1M", intraday=False) == 2
    assert "no observation from this session" in day.metadata("CLOSE_1M")["error"]
    assert bundle_path(day.root).read_text() == before
    assert day.published == ["PREMARKET"]


def test_close_without_any_interpretation_still_hands_the_session_off(day):
    assert day.run(f"{TUE}T20:03:00+00:00", "CLOSE_1M", print_at=f"{TUE}T19:59:58+00:00") == 0
    assert day.calls == [] and day.published == []  # no page: there is no interpretation to show
    assert not (day.folder("CLOSE_1M") / "brief.html").exists()
    assert (day.folder("CLOSE_1M") / "session_handoff.json").exists()
    bundle = day.bundle()
    assert bundle["close"]["assessment"]["closing_character"] is None
    assert bundle["close"]["assessment"]["watches"] == [] and bundle["interpretation"] is None
    packet = fixture_packet()
    packet["run"]["mode"] = "LIVE"
    packet["run"]["target_time"] = "2026-09-09T13:00:00+00:00"
    packet["run"]["session"].update(date="2026-09-09", previous_session=TUE)
    prior = admit_prior_state(bundle, packet)
    assert prior["status"] == "available" and prior["closing_character"] is None
    assert admit_interpretation(bundle, packet) == (None, "no accepted interpretation for this session: absent")


def test_a_replayed_refresh_never_reaches_the_analyst_and_dates_its_sample_interpretation(tmp_path, monkeypatch):
    raw = read_json(ROOT / "tests/fixtures/evidence.sample.json")
    raw["observations"].append(dict(
        id="SPY-intraday", topic="SPY", metric="premarket return", value=-0.53, unit="%",
        baseline="latest trade versus previous regular close", frequency="intraday",
        observed_at=raw["target_time"], retrieved_at=raw["target_time"], source_id="sample-prices",
        status="AVAILABLE", reason=""))
    fixture = tmp_path / "sample.json"
    fixture.write_text(json.dumps(raw))
    freeze_clock(monkeypatch, raw["target_time"])
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(cli, "update_latest", lambda root, page: None)
    monkeypatch.setattr(cli, "synthesize", lambda *a, **k: (_ for _ in ()).throw(AssertionError("analyst called")))
    assert cli.main(["premarket", "--replay", "--checkpoint", "HOURLY_1300", "--input", str(fixture)]) == 0
    folder = next(p for p in (tmp_path / "runs").glob("*/*") if (p / "evidence.json").exists())
    page = (folder / "brief.html").read_text()
    assert "SAMPLE · Hourly refresh" in page and "FICTIONAL SAMPLE" in page
    # The fixture is targeted before its premarket slot, so the sample interpretation keeps the data's clock.
    assert "Analysis anchored 5:45 AM PT · Observed record refreshed 5:45 AM PT" in page
    metadata = json.loads((folder / "metadata.json").read_text())
    assert metadata["synthesis"] == dict(kind="refresh", calls=0)
    assert metadata["interpretation"]["run_id"] == "sample-premarket-fixture"
    # A fictional replay never advances production continuity.
    assert not bundle_path(tmp_path).exists()


# --- the observed record on a refresh page is this run's; anchors stay intact -------------------------

@pytest.mark.parametrize("first_attempt_fails", [True, False])
def test_the_alternate_season_wake_never_retries_or_reruns_the_opening_structure_synthesis(
        day, capsys, first_attempt_fails):
    """PDT: the 14:01 UTC wake is the 7:00 update's one natural attempt. The 14:31 UTC wake exists for
    PST's open +1M; at 7:31 PDT it resolves to SKIP and never reaches the analyst, whether the first
    attempt failed or succeeded."""
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    assert day.run(f"{TUE}T13:31:00+00:00", "OPEN_1M", command="schedule") == 0
    expected = 2 if first_attempt_fails else 0
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M", command="schedule", fail_synthesis=first_attempt_fails) \
        == expected
    assert day.calls == ["PREMARKET", "OPEN_30M"] and len(day.attempts("OPEN_30M")) == 1
    # 7:31 PDT: the scheduler resolves nothing, and an explicit OPEN_30M dispatch is outside its window.
    freeze_clock(day.monkeypatch, f"{TUE}T14:31:00+00:00")
    capsys.readouterr()
    assert cli.main(["resolve-scheduled"]) == 0 and capsys.readouterr().out.strip() == "SKIP"
    assert day.run(f"{TUE}T14:31:00+00:00", "OPEN_30M", command="schedule") == 0
    assert "SKIP / OPEN_30M / outside checkpoint window" in capsys.readouterr().out
    assert day.calls == ["PREMARKET", "OPEN_30M"] and len(day.attempts("OPEN_30M")) == 1
    assert day.published == ["PREMARKET", "OPEN_1M"] + ([] if first_attempt_fails else ["OPEN_30M"])
    # The hourly refreshes that follow are unaffected and still make no analyst call.
    assert day.run(f"{TUE}T15:01:00+00:00", "HOURLY_1100", command="schedule") == 0
    assert day.calls == ["PREMARKET", "OPEN_30M"]
    interpreted = day.metadata("HOURLY_1100")["interpretation"]["checkpoint"]
    assert interpreted == ("PREMARKET" if first_attempt_fails else "OPEN_30M")


def test_a_queue_delayed_earlier_wake_cannot_repeat_a_completed_checkpoint_on_a_fresh_runner(day, capsys):
    """A dispatch queued behind another run starts on a fresh runner whose checkout predates the earlier
    run's publish and which has no marker file. The restored continuity bundle already names the completed
    checkpoint, so the scheduler skips: no second synthesis, no duplicate refresh."""
    from market_brief.cli import checkpoint_marker
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M", command="schedule") == 0
    assert day.calls == ["PREMARKET", "OPEN_30M"]
    # Ten minutes later, inside the synthesis window, on a runner without the marker or the new page.
    checkpoint_marker(day.root, TUE, "OPEN_30M").unlink()
    capsys.readouterr()
    assert day.run(f"{TUE}T14:11:00+00:00", "OPEN_30M", command="schedule") == 0
    assert "SKIP / OPEN_30M / already completed" in capsys.readouterr().out
    assert day.calls == ["PREMARKET", "OPEN_30M"] and len(day.attempts("OPEN_30M")) == 1
    # The same holds for a deterministic refresh: one attempt folder, one publish.
    assert day.run(f"{TUE}T17:01:00+00:00", "HOURLY_1300", command="schedule") == 0
    checkpoint_marker(day.root, TUE, "HOURLY_1300").unlink()
    capsys.readouterr()
    assert day.run(f"{TUE}T17:06:00+00:00", "HOURLY_1300", command="schedule") == 0
    assert "SKIP / HOURLY_1300 / already completed" in capsys.readouterr().out
    assert len(day.attempts("HOURLY_1300")) == 1
    assert day.published == ["PREMARKET", "OPEN_30M", "HOURLY_1300"]


def test_a_failed_opening_structure_synthesis_leaves_refreshes_on_the_premarket_interpretation(day):
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    premarket = day.bundle()["interpretation"]["content_hash"]
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M", fail_synthesis=True) == 2
    assert day.calls == ["PREMARKET", "OPEN_30M"] and day.published == ["PREMARKET"]
    assert day.bundle()["interpretation"]["content_hash"] == premarket  # a rejected synthesis freezes nothing
    assert day.run(f"{TUE}T17:00:00+00:00", "HOURLY_1300") == 0
    page = day.page("HOURLY_1300")
    assert "Analysis anchored 6:00 AM PT · Observed record refreshed 10:00 AM PT" in page
    assert "Interpretation: PREMARKET" in page.split("Technical details", 1)[1]
    assert day.metadata("HOURLY_1300")["interpretation"]["checkpoint"] == "PREMARKET"
    assert day.calls == ["PREMARKET", "OPEN_30M"]  # the refresh did not retry the analyst


def test_a_bundle_from_before_the_interpretation_slot_loads_and_the_first_refresh_fails_closed(day):
    """A live bundle written by the previous release has no `interpretation` key."""
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    bundle = json.loads(bundle_path(day.root).read_text())
    del bundle["interpretation"]
    write_bundle(bundle_path(day.root), bundle)
    loaded, note = load_bundle(bundle_path(day.root))
    assert note == "" and loaded["interpretation"] is None and loaded["premarket"] is not None
    assert day.run(f"{TUE}T13:31:00+00:00", "OPEN_1M") == 2
    assert day.published == ["PREMARKET"]
    assert "no accepted interpretation for this session: absent" in day.metadata("OPEN_1M")["error"]


def test_a_placeholder_inside_a_watch_survives_synthesis_refreshes_the_close_and_the_next_premarket(day):
    """A watch criterion may quote a number as {{evidence-id}}. Carried into later editions, it must
    resolve at every render (a rendering failure after a paid call would discard the narrative)."""
    value = narrative()
    value["watches"][0].update(condition="If SPY holds its {{SPY-daily}} daily gain after the open, "
                                         "check whether participation extends beyond the selected mega-cap.")
    assert "SPY-daily" in value["watches"][0]["evidence_ids"]
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False, value=value) == 0
    assert "holds its +0.06 % daily gain" in day.page("PREMARKET")
    carried_id = next(w["id"] for w in day.bundle()["latest"]["assessment"]["watches"] if "{{" in w["hypothesis"])
    # The 7:00 synthesis carries the watch without reassessing it; every later page still resolves it.
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M") == 0
    assert day.run(f"{TUE}T17:00:00+00:00", "HOURLY_1300") == 0
    assert day.run(f"{TUE}T20:03:00+00:00", "CLOSE_1M", print_at=f"{TUE}T19:59:58+00:00") == 0
    for checkpoint in ("OPEN_30M", "HOURLY_1300", "CLOSE_1M"):
        page = day.page(checkpoint)
        assert "holds its +0.06 % daily gain" in page and "{{" not in page, checkpoint
        assert day.metadata(checkpoint)["validation"] == "PASS"
        # The carried watch's marker labels the quoted row like any other row, at the creation-time value.
        carried_block = page.split("holds its +0.06 % daily gain", 2)[-1].split("</details>", 1)[0]
        assert '<a href="#evidence-SPY-daily">SPY · Daily return</a><b>+0.06 %</b>' in carried_block, checkpoint
        assert ">SPY-daily</a>" not in page
    # Wednesday: Tuesday's close moved SPY's daily return well away from the value the watch quoted. The
    # carried criterion still renders the number its author saw; only a new watch quotes the new one.
    def reassess(live):
        live["watch_updates"] = [dict(carried_id=carried_id, assessment="unresolved",
                                      reason="The premarket has no session print to test it against.",
                                      evidence_ids=["SPY-daily", "previous_close:SPY-daily"])]
        live["watches"][0].update(condition="If SPY keeps its {{SPY-daily}} daily gain, check participation.")
    assert day.run("2026-09-09T13:00:00+00:00", "PREMARKET", intraday=False, last_history_date=TUE,
                   mutate=reassess, scale_last_close={"SPY": 1.02}) == 0
    page = day.page("PREMARKET", "2026-09-09")
    evidence = json.loads((day.folder("PREMARKET", "2026-09-09") / "evidence.json").read_text())
    today = formatted(next(row for row in evidence["derived"] if row["id"] == "SPY-daily"))
    assert today != "+0.06 %" and today.startswith("+2.")
    assert "holds its +0.06 % daily gain" in page and "{{" not in page  # the carried criterion did not drift
    assert f"keeps its {today} daily gain" in page  # the newly accepted watch quotes the new value
    assert '<span class="meta">From an earlier read · unresolved' in page
    context = json.loads((day.folder("PREMARKET", "2026-09-09") / "analyst_context.json").read_text())
    carried = next(w for w in context["prior_state"]["watches"] if w["id"] == carried_id)
    assert carried["values"]["SPY-daily"]["value"] == pytest.approx(0.06, abs=0.005)
    assert carried["values"]["SPY-daily"]["topic"] == "SPY"
    assert carried["values"]["SPY-daily"]["metric"] == "daily return"
    assert (day.folder("PREMARKET", "2026-09-09") / "narrative.json").exists()


def test_a_watch_written_before_criteria_carried_values_is_frozen_at_its_first_carry(day):
    """A live bundle from the previous release holds watches without `values`. The first edition that
    carries such a watch freezes its quoted rows, so the criterion stops drifting from there on."""
    value = narrative()
    value["watches"][0].update(condition="If SPY holds its {{SPY-daily}} daily gain after the open, "
                                         "check whether participation extends beyond the selected mega-cap.")
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False, value=value) == 0
    bundle = json.loads(bundle_path(day.root).read_text())
    from market_brief.continuity import _hashed
    for slot in ("premarket", "latest"):
        for watch in bundle[slot]["assessment"]["watches"]:
            watch.pop("values", None)
        bundle[slot] = _hashed({k: v for k, v in bundle[slot].items() if k != "content_hash"})
    write_bundle(bundle_path(day.root), bundle)
    assert day.run(f"{TUE}T13:31:00+00:00", "OPEN_1M") == 0  # a refresh carries and freezes it
    carried = next(w for w in day.bundle()["latest"]["assessment"]["watches"] if "{{" in w["hypothesis"])
    assert carried["values"]["SPY-daily"]["value"] == pytest.approx(0.06, abs=0.005)
    assert "holds its +0.06 % daily gain" in day.page("OPEN_1M")


def test_refresh_page_keeps_every_anchor_and_cites_frozen_values_with_their_clock(day):
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M") == 0
    assert day.run(f"{TUE}T17:00:00+00:00", "HOURLY_1300") == 0
    page = day.page("HOURLY_1300")
    parsed = Page()
    parsed.feed(page)
    evidence = json.loads((day.folder("HOURLY_1300") / "evidence.json").read_text())
    catalog = evidence_catalog(evidence)
    assert {f"evidence-{i}" for i in catalog} <= parsed.ids
    assert set(parsed.refs) <= parsed.ids
    assert page.count("<script>") == 1 and "Content-Security-Policy" in page
    # The interpretation's markers show the rows the analyst saw, stamped with the interpretation's clock.
    marker = re.search(r'<details class="cite">(.*?)</details>', page, re.S).group(1)
    assert "7:01 AM PT" in marker or "Fri, Sep 4" in marker
    technical = page.split("Technical details", 1)[1]
    assert "none; deterministic refresh under interpretation run live-open_30m-" in technical
    assert "Interpretation: OPEN_30M" in technical


# --- absence vocabulary, signed zero, contrast, mobile -------------------------------------------

def test_absence_vocabulary_is_three_words():
    assert formatted(dict(value=None, unit="%")) == NO_PRINT
    packet = fixture_packet()
    packet["sources"].append(dict(id="bea", name="BEA calendar", kind="calendar", url="https://www.bea.gov/news/schedule",
                                  retrieved_at="2026-09-08T12:40:00+00:00", status="UNAVAILABLE",
                                  reason="not automated in this slice; sourced input supported",
                                  llm_allowed=True, retention_allowed=True))
    rows = {row["id"]: row for row in source_rows(packet["sources"])}
    assert rows["bea"]["reason"] == NOT_COLLECTED
    view = presentation(packet, narrative())
    gld = next(row for row in view["cross_asset"]["rows"] if row["symbol"] == "GLD")
    assert gld["relative"]["display"] == NOT_APPLICABLE  # GLD is the benchmark: structurally no spread
    _, page = render(packet, narrative())
    reading = page.split("<h2>Sources", 1)[0]
    for retired in ("n/a", "Unavailable", "not exposed", "not automated in this slice"):
        assert retired not in reading, retired
    assert "not collected" in page.split("<summary>Sources", 1)[1]


def test_a_value_that_rounds_to_zero_is_an_unsigned_neutral_zero():
    row = dict(id="XLY-intraday", topic="XLY", metric="intraday return", value=-0.001, unit="%",
               frequency="intraday", status="AVAILABLE", observed_at="2026-09-08T19:00:00+00:00")
    assert formatted(row) == "0.00 %" and direction(row) == "neutral"
    assert formatted(dict(row, value=0.004)) == "0.00 %" and direction(dict(row, value=0.004)) == "neutral"
    assert formatted(dict(row, value=-0.006)) == "-0.01 %" and direction(dict(row, value=-0.006)) == "negative"
    assert formatted(dict(row, value=0.0, unit="bp")) == "0.00 bp"


def contrast(foreground, background):
    def luminance(colour):
        channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    high, low = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def theme_tokens(css, selector):
    block = css.split(selector, 1)[1].split("}", 1)[0]
    return dict(re.findall(r"--([a-z]+):(#[0-9a-f]{6})", block))


def test_secondary_text_keeps_readable_contrast_in_both_themes():
    css = (ROOT / "templates/brief.html.j2").read_text()
    light, dark = theme_tokens(css, ":root{"), theme_tokens(css, 'html[data-theme="dark"]{')
    system_dark = theme_tokens(css, "html:not([data-theme]){")
    assert dark == system_dark  # the explicit dark choice and the system preference share one palette
    for tokens, floor in ((light, 3.9), (dark, 4.5)):
        paper = tokens["paper"]
        assert contrast(tokens["ink"], paper) >= 7
        assert contrast(tokens["muted"], paper) >= 4.5
        assert contrast(tokens["faint"], paper) >= floor
        assert contrast(tokens["faint"], paper) < contrast(tokens["muted"], paper)  # still the quieter tone
        for token in ("teal", "positive", "negative", "amber"):
            assert contrast(tokens[token], paper) >= 4.5, token


def phone_layout_width(page, tmp_path, width=390):
    """The widest rendered box when the page is laid out at a phone width, measured by headless Chrome.

    Headless Chrome's `--dump-dom` window is at least 500 px wide, so the page itself is constrained to
    the phone width (the mobile media query still applies) and every box's right edge is measured.
    """
    chrome = shutil.which("google-chrome") or shutil.which("google-chrome-stable") or shutil.which("chromium")
    if not chrome:
        pytest.skip("headless Chrome is not installed")
    probe = (f"<style>html,body{{width:{width}px!important;max-width:{width}px!important}}</style><script>"
             "let right = document.body.scrollWidth;"
             "for (const el of document.querySelectorAll('body *')) {"
             "  const box = el.getBoundingClientRect(); if (box.width > 0) right = Math.max(right, box.right); }"
             "document.title = 'RIGHT=' + Math.ceil(right);</script></body>")
    target = tmp_path / "brief.html"
    target.write_text(page.replace("</body>", probe, 1))
    result = subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
                             "--window-size=390,1200", "--dump-dom", target.as_uri()],
                            capture_output=True, text=True, timeout=90, check=False)
    found = re.search(r"RIGHT=(\d+)", result.stdout)
    assert found, result.stderr[-500:]
    return int(found.group(1))


def test_phone_width_has_no_horizontal_overflow(tmp_path):
    _, page = render(fixture_packet(), narrative())
    assert phone_layout_width(page, tmp_path) <= 390
    # The probe itself detects overflow: a 900 px box is reported.
    assert phone_layout_width(page.replace("<h1>", "<div style='width:900px'>x</div><h1>", 1), tmp_path) >= 900

"""WP-MB-RECOVERY-01: a data-only page when no analysis of the session was accepted, and a truthful receipt audit.

A rejected PREMARKET / OPEN_30M with no accepted analysis of the session, and a scheduled deterministic refresh with
none to carry, publish this run's own observations under "Analysis unavailable — current market observations only":
no model call, no narrative surface, no continuity advance, and the synthesis stays rejected. Every other case fails
closed exactly as before. The receipt audit keeps its missed-wake answer and reports each checkpoint's stage health
from the receipt payload, never counting a red run as a published page.

The two archived 2026-11-25 failures are exercised offline when their run archives are present (they are production
files and never committed): set MB_RECOVERY_EVIDENCE to the archive root, default /tmp/mb-wp-recovery-evidence.
"""

import html
import io
import json
import os
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
import test_cadence
from test_cadence import TUE, Day
from test_receipt_audit import ALL, EVENING, MON, REPO, FakeGitHub, full_day, run_details

from market_brief import audit, cli
from market_brief.continuity import bundle_path
from market_brief.evidence import digest, read_json
from market_brief.receipts import RECEIPT_SCHEMA, build_receipt
from market_brief.render import DATA_ONLY_HEADING, presentation, render
from market_brief.synthesize import NarrativeRejected, construct_prompt, enabled_repairs, repair_citations
from market_brief.synthesize import validate_narrative as strict_validator

FRI = "2026-09-04"
ARCHIVE = Path(os.environ.get("MB_RECOVERY_EVIDENCE", "/tmp/mb-wp-recovery-evidence"))
ARCHIVED = {
    "PREMARKET": ("premarket", "live-premarket-140233-87e0ea07", "unknown attention trigger"),
    "OPEN_30M": ("open30", "live-open_30m-150217-0cff9e93", "SMALL observations cannot anchor a watch"),
}
LEAKS = ("Rejected headline the reader must never see",
         "Rejected summary sentence that names a market mechanism",
         "Rejected take that would read as a stance",
         "Will the rejected watch question ever surface")
REJECTED = dict(banner=dict(title=LEAKS[0], limitation="", evidence_ids=[]),
                character=dict(text="Rejected character line", evidence_ids=[]),
                summary=[dict(text=LEAKS[1], evidence_ids=[])], take=dict(text=LEAKS[2], evidence_ids=[]),
                watches=[dict(id="w-1", question=LEAKS[3], evidence_ids=[])], relationships=[], attention=[])
NARRATIVE_FILES = ("narrative.json", "edition_state.json", "session_handoff.json")


def prose(value, packet_text=""):
    """Every reader sentence in a narrative (four words or more), except text the packet itself carries."""
    if isinstance(value, dict):
        return [s for v in value.values() for s in prose(v, packet_text)]
    if isinstance(value, list):
        return [s for v in value for s in prose(v, packet_text)]
    if isinstance(value, str) and len(value.split()) >= 4 and value not in packet_text:
        return [value]
    return []


def assert_no_leak(page, sentences):
    for sentence in sentences:
        assert sentence not in page and html.escape(sentence) not in page, sentence


# --- the archived 2026-11-25 failures, offline ------------------------------------------------------------------


def archived(checkpoint, tmp_path):
    folder_name, run_id, _ = ARCHIVED[checkpoint]
    source = ARCHIVE / folder_name / "2026-11-25" / run_id
    if not (source / "narrative.rejected.json").is_file():
        pytest.skip(f"archived production run not present: {source}")
    folder = tmp_path / run_id
    shutil.copytree(source, folder)  # never touch the archive itself
    return (folder, read_json(folder / "evidence.json"), read_json(folder / "analyst_context.json"),
            read_json(folder / "narrative.rejected.json"), read_json(folder / "metadata.json"))


def archived_rejection(metadata, rejected):
    exc = NarrativeRejected(metadata["error"], rejected)
    exc.attempt = dict(metadata["model"])
    return exc


@pytest.mark.parametrize("checkpoint", list(ARCHIVED))
def test_the_archived_rejection_is_still_rejected_by_the_strict_validator(checkpoint, tmp_path):
    _, packet, context, rejected, _ = archived(checkpoint, tmp_path)
    narrative, _ = repair_citations(rejected, context, enabled_repairs())
    with pytest.raises(ValueError, match=re.escape(ARCHIVED[checkpoint][2])):
        strict_validator(narrative, packet, context)


@pytest.mark.parametrize("checkpoint", list(ARCHIVED))
def test_the_archived_request_identity_is_exact(checkpoint, tmp_path):
    _, packet, context, _, metadata = archived(checkpoint, tmp_path)
    system, user = construct_prompt(packet, context=context)
    assert digest(packet) == metadata["evidence_hash"] == metadata["model"]["evidence_hash"]
    assert digest(context) == metadata["context_hash"]
    assert digest(dict(system=system, user=user)) == metadata["model"]["prompt_hash"]


@pytest.mark.parametrize("checkpoint", list(ARCHIVED))
def test_the_archived_rejection_publishes_observations_only_with_no_narrative(checkpoint, tmp_path):
    folder, packet, context, rejected, metadata = archived(checkpoint, tmp_path)
    fallback, refusal = cli.data_only_fallback(archived_rejection(metadata, rejected), packet, context, metadata,
                                               folder, True)
    assert refusal is None, refusal
    assert fallback["basis"] == "synthesis_rejected" and fallback["checkpoint"] == checkpoint
    assert fallback["response_id"] == metadata["model"]["response_id"]
    assert fallback["prompt_hash"] == metadata["model"]["prompt_hash"]
    markdown, page = render(packet, data_only=fallback)
    assert f"<h1>{html.escape(DATA_ONLY_HEADING)}</h1>" in page and DATA_ONLY_HEADING in markdown
    assert "Wednesday, November 25, 2026" in page and f"({checkpoint})" in page
    assert 'data-analysis="unavailable"' in page and 'data-session-date="2026-11-25"' in page
    assert 'id="evidence-ledger"' in page and "The take:" not in page
    assert_no_leak(page, prose(rejected, json.dumps(packet, ensure_ascii=False)))
    assert_no_leak(markdown, prose(rejected, json.dumps(packet, ensure_ascii=False)))
    view = presentation(packet, data_only=fallback)
    assert view["evidence"] and view["sources"] and view["macro"]["yields"]  # the observed record stays
    assert view["summary"] == [] and view["take"] is None and view["since"]["entries"] == []
    assert view["next"]["watches"] == [] and view["next"]["attention"] == [] and view["interpretation_groups"] == []


def test_a_data_only_page_excludes_optional_cuttingboard_commentary(tmp_path):
    folder, packet, context, rejected, metadata = archived("PREMARKET", tmp_path)
    fallback, _ = cli.data_only_fallback(archived_rejection(metadata, rejected), packet, context, metadata, folder,
                                         True)
    marker = "UPSTREAM-CB-MARKER"
    packet = dict(packet, cuttingboard=dict(status="AVAILABLE", outcome=marker, permission=marker,
                                            generated_at=marker, schema_version="v2"))
    markdown, page = render(packet, data_only=fallback)
    for text in (markdown, page):
        assert marker not in text and "Cuttingboard context" not in text and "SOURCE QUOTATION" not in text
    view = presentation(packet, data_only=fallback)
    assert view["cuttingboard_section"]["visible"] is False and view["cuttingboard_section"]["paragraphs"] == []
    # The project's own verified, dated record stays: market rows, rates, sources and evidence links.
    assert view["evidence"] and view["sources"] and view["macro"]["yields"]
    assert 'id="evidence-ledger"' in page and f'id="evidence-{view["evidence"][0]["id"]}"' in page


@pytest.mark.parametrize("checkpoint", list(ARCHIVED))
def test_the_archived_rejection_fails_closed_on_any_changed_input(checkpoint, tmp_path):
    folder, packet, context, rejected, metadata = archived(checkpoint, tmp_path)

    def refused(exc=None, packet_=packet, context_=context, metadata_=metadata, scheduled=True):
        result, reason = cli.data_only_fallback(exc or archived_rejection(metadata, rejected), packet_, context_,
                                                metadata_, folder, scheduled)
        assert result is None
        return reason
    assert refused(ValueError("OpenRouter transport failure")) == "not a validator rejection"
    bare = NarrativeRejected(metadata["error"], rejected)  # no request on record
    assert "not on record" in refused(bare)
    assert "changed" in refused(packet_=dict(packet, previous={"edited": True}))
    assert "changed" in refused(context_=dict(context, note="edited"))
    assert "does not match" in refused(archived_rejection(dict(metadata, model=dict(metadata["model"],
                                                                                    prompt_hash="0" * 64)), rejected))
    assert "not a scheduled production" in refused(scheduled=False)
    assert "not a scheduled production" in refused(metadata_=dict(metadata, experiment=True))
    for status in ("INSUFFICIENT", "PENDING"):
        coverage = dict(packet["coverage"], status=status)
        assert cli.current_session_gate(dict(packet, coverage=coverage), metadata, True) == (None, f"coverage {status}")
    # A rejection on disk the evidence no longer matches.
    (folder / "evidence.json").write_text(json.dumps(dict(packet, previous={"edited": True})))
    assert "changed" in refused()


def test_the_current_session_gate_rejects_missing_future_and_previous_session_prints(tmp_path):
    _, packet, _, _, metadata = archived("PREMARKET", tmp_path)
    assert cli.current_session_gate(packet, metadata, True)[0]
    cutoff = packet["run"]["target_time"]

    def with_prints(observed_at):
        rows = [dict(r, observed_at=observed_at) if r.get("frequency") == "intraday" else r
                for r in packet["observations"]]
        return dict(packet, observations=rows)
    assert cli.current_session_gate(with_prints("2026-11-24T20:59:00+00:00"), metadata, True)[0] is None
    assert cli.current_session_gate(with_prints("2026-11-25T20:00:00+00:00"), metadata, True)[0] is None
    assert cli.current_session_gate(with_prints("2026-11-25"), metadata, True)[0] is None  # undated
    no_prints = dict(packet, observations=[r for r in packet["observations"] if r.get("frequency") != "intraday"])
    assert cli.current_session_gate(no_prints, metadata, True)[0] is None
    yesterday = dict(packet, run=dict(packet["run"], session=dict(packet["run"]["session"], date="2026-11-24")))
    assert cli.current_session_gate(yesterday, metadata, True)[0] is None
    assert cli.current_session_gate(packet, dict(metadata, target_time="2026-11-25T14:30:00+00:00"), True)[0] is None
    assert cutoff == metadata["target_time"]


# --- a scheduled production day, end to end with no provider ------------------------------------------------------


def rejecting(day, response_id="gen-test-rejected"):
    """The real wire's contract at the paid boundary, with no provider: record the attempt, then reject strictly."""
    def wire(packet, full=False, context=None, on_request=None, **kwargs):
        system, user = construct_prompt(packet, full=full, context=context)
        attempt = dict(route="openrouter", attempts=1, prompt_hash=digest(dict(system=system, user=user)),
                       evidence_hash=digest(packet), response_id=response_id, resolved_model="test/model",
                       usage=dict(prompt_tokens=100, completion_tokens=20, cost=0.0123))
        if on_request:
            on_request(dict(attempt))
        day.calls.append(packet["run"]["checkpoint"])
        exc = NarrativeRejected("unknown attention trigger", json.loads(json.dumps(REJECTED)))
        exc.attempt = attempt
        raise exc
    day.monkeypatch.setattr(test_cadence, "wire_synthesize", wire)


@pytest.fixture
def day(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    return Day(monkeypatch, tmp_path)


def bundle_bytes(day):
    path = bundle_path(day.root)
    return path.read_bytes() if path.is_file() else None


def assert_nothing_accepted(day, checkpoint):
    folder = day.folder(checkpoint)
    for name in NARRATIVE_FILES:
        assert not (folder / name).exists(), name
    assert not (day.root / "runs" / "latest-success.json").exists()


def test_a_rejected_premarket_with_no_analysis_publishes_observations_only(day):
    rejecting(day)
    assert day.run(f"{TUE}T13:01:00+00:00", "PREMARKET", command="schedule", wire=True) == 0
    assert day.calls == ["PREMARKET"] and day.published == ["PREMARKET"]
    page = day.page("PREMARKET")
    assert f"<h1>{html.escape(DATA_ONLY_HEADING)}</h1>" in page and "Tuesday, September 8, 2026" in page
    assert_no_leak(page, LEAKS)
    metadata = day.metadata("PREMARKET")
    # The validator's outcome stays true; publication is recorded apart from it.
    assert metadata["validation"] == "FAILED" and metadata["publication"] == "DATA_ONLY"
    assert metadata["published"] is True
    assert metadata["error"] == "unknown attention trigger"  # the rejection stays on record
    assert metadata["model"]["response_id"] == "gen-test-rejected" and metadata["model"]["usage"]["cost"] == 0.0123
    assert metadata["synthesis"]["calls"] == 1 and metadata["continuity"]["advanced"] is False
    assert metadata["data_only"]["basis"] == "synthesis_rejected"
    assert read_json(day.folder("PREMARKET") / "narrative.rejected.json") == REJECTED
    assert (day.root / "runs" / "attempts" / f"{TUE}-PREMARKET.json").is_file()
    assert_nothing_accepted(day, "PREMARKET")
    assert bundle_bytes(day) is None


def test_refreshes_after_a_failed_opening_publish_their_own_collection_with_no_model_call(day):
    rejecting(day)
    assert day.run(f"{TUE}T13:01:00+00:00", "PREMARKET", command="schedule", wire=True) == 0
    assert day.run(f"{TUE}T13:31:00+00:00", "OPEN_1M", command="schedule") == 0
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M", command="schedule", wire=True) == 0
    assert day.run(f"{TUE}T15:01:00+00:00", "HOURLY_1100", command="schedule") == 0
    assert day.calls == ["PREMARKET", "OPEN_30M"]  # the refreshes asked nothing of the analyst
    assert day.published == ["PREMARKET", "OPEN_1M", "OPEN_30M", "HOURLY_1100"]
    for checkpoint in ("OPEN_1M", "HOURLY_1100"):
        metadata = day.metadata(checkpoint)
        assert metadata["validation"] == "NOT_APPLICABLE" and metadata["publication"] == "DATA_ONLY"
        assert metadata["published"] is True and metadata["synthesis"] == dict(kind="refresh", calls=0)
        assert "error" not in metadata
        assert metadata["data_only"]["basis"] == "no_accepted_analysis" and "model" not in metadata
        assert metadata["continuity"]["advanced"] is False and metadata.get("interpretation") is None
        assert DATA_ONLY_HEADING in day.page(checkpoint) and "no analysis was requested" in day.page(checkpoint)
        assert_no_leak(day.page(checkpoint), LEAKS)
        assert_nothing_accepted(day, checkpoint)
    opening = day.metadata("OPEN_30M")
    assert opening["validation"] == "FAILED" and opening["publication"] == "DATA_ONLY"
    assert opening["model"]["response_id"] == "gen-test-rejected" and opening["error"]
    assert bundle_bytes(day) is None


def test_yesterdays_interpretation_is_never_reused(day):
    assert day.run(f"{FRI}T13:00:00+00:00", "PREMARKET", intraday=False, last_history_date="2026-09-03") == 0
    friday = day.narratives["PREMARKET"]
    before = bundle_bytes(day)
    rejecting(day)
    assert day.run(f"{TUE}T13:01:00+00:00", "PREMARKET", command="schedule", wire=True) == 0
    assert day.run(f"{TUE}T13:31:00+00:00", "OPEN_1M", command="schedule") == 0
    for checkpoint in ("PREMARKET", "OPEN_1M"):
        page = day.page(checkpoint)
        assert DATA_ONLY_HEADING in page
        # Friday's prose, except sentences Tuesday's own evidence carries (an event title the ledger shows).
        assert_no_leak(page, prose(friday, (day.folder(checkpoint) / "evidence.json").read_text()))
    assert bundle_bytes(day) == before  # the restored bundle is exactly what the next run restores


def test_an_accepted_premarket_keeps_the_normal_path(day):
    assert day.run(f"{TUE}T13:01:00+00:00", "PREMARKET", command="schedule", intraday=False) == 0
    assert day.run(f"{TUE}T13:31:00+00:00", "OPEN_1M", command="schedule") == 0
    for checkpoint in ("PREMARKET", "OPEN_1M"):
        metadata = day.metadata(checkpoint)
        assert metadata["validation"] == "PASS" and "data_only" not in metadata
        assert metadata["continuity"]["advanced"] is True and DATA_ONLY_HEADING not in day.page(checkpoint)
    assert day.metadata("OPEN_1M")["interpretation"]["checkpoint"] == "PREMARKET"


def test_a_rejected_opening_with_the_premarket_accepted_still_degrades_as_before(day):
    assert day.run(f"{TUE}T13:01:00+00:00", "PREMARKET", command="schedule", intraday=False) == 0
    rejecting(day)
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M", command="schedule", wire=True) == 0
    metadata = day.metadata("OPEN_30M")
    assert metadata["validation"] == "DEGRADED" and "data_only" not in metadata


@pytest.mark.parametrize("flags", [(), ("--experiment",), ("--commissioning",)])
def test_unscheduled_experiment_and_commissioning_rejections_fail_closed(day, flags):
    """A manual, experiment or commissioning run is never a scheduled production checkpoint: no data-only page."""
    rejecting(day)
    assert day.run(f"{TUE}T13:01:00+00:00", "PREMARKET", wire=True, flags=flags) == 2
    assert day.published == [] and bundle_bytes(day) is None
    runs = day.root / "runs" / TUE
    for folder in runs.iterdir() if runs.is_dir() else ():
        if (folder / "metadata.json").is_file():
            metadata = read_json(folder / "metadata.json")
            assert metadata["validation"] == "FAILED" and "data_only" not in metadata
            assert not (folder / "brief.html").exists()


def test_an_unscheduled_refresh_without_analysis_fails_closed(day):
    assert day.run(f"{TUE}T13:31:00+00:00", "OPEN_1M") == 2
    assert day.published == [] and "no accepted interpretation" in day.metadata("OPEN_1M")["error"]


def test_a_rejection_without_its_request_on_record_fails_closed(day):
    # The stand-in raises a NarrativeRejected that carries no attempt: no request identity, no page.
    assert day.run(f"{TUE}T13:01:00+00:00", "PREMARKET", command="schedule", fail_synthesis=True) == 2
    assert day.published == [] and day.metadata("PREMARKET")["validation"] == "FAILED"
    assert "data_only" not in day.metadata("PREMARKET")


def test_a_non_rejection_failure_after_the_request_fails_closed(day):
    """Only the strict validator's NarrativeRejected qualifies; a transport or parse failure does not."""
    def failing(packet, full=False, context=None, on_request=None, **kwargs):
        if on_request:
            on_request(dict(route="openrouter", attempts=1))
        exc = ValueError("OpenRouter returned malformed structured synthesis")
        exc.attempt = dict(route="openrouter", attempts=1)
        raise exc
    day.monkeypatch.setattr(test_cadence, "wire_synthesize", failing)
    assert day.run(f"{TUE}T13:01:00+00:00", "PREMARKET", command="schedule", wire=True) == 2
    assert day.published == [] and day.metadata("PREMARKET")["validation"] == "FAILED"


def test_a_scheduled_refresh_with_stale_prints_fails_closed(day):
    assert day.run(f"{TUE}T15:01:00+00:00", "HOURLY_1100", command="schedule", intraday=False) == 2
    assert day.published == [] and day.metadata("HOURLY_1100")["validation"] == "FAILED"


def test_a_scheduled_close_keeps_its_own_path(day):
    """The close hands the session off (edition state, session handoff, continuity); a data-only page would have to
    skip exactly those writes, so the close never publishes one. Retained at the close handoff boundary."""
    rejecting(day)
    assert day.run(f"{TUE}T13:01:00+00:00", "PREMARKET", command="schedule", wire=True) == 0
    assert day.run(f"{TUE}T20:03:00+00:00", "CLOSE_1M", command="schedule", print_at=f"{TUE}T19:59:58+00:00") == 0
    metadata = day.metadata("CLOSE_1M")
    assert "data_only" not in metadata and "publication" not in metadata and metadata["published"] is False
    assert metadata["continuity"]["handoff"] == "written" and metadata["continuity"]["advanced"] is True
    assert (day.folder("CLOSE_1M") / "session_handoff.json").is_file()
    assert "CLOSE_1M" not in day.published
    packet = read_json(day.folder("CLOSE_1M") / "evidence.json")
    fallback, refusal = cli.refresh_data_only(packet, metadata, "no accepted interpretation", True)
    assert fallback is None and "not a deterministic refresh" in refusal


# --- receipts: a data-only page is published, never an accepted analysis -------------------------------------------


GREEN = dict(resolve="success", restore="success", pipeline="success", continuity="success", publish="success",
             current="success", deploy_attempt_1="success", deploy_attempt_2="skipped", deploy="success",
             archive="success")


def env(checkpoint, **steps):
    value = {f"RECEIPT_{k.upper()}": v for k, v in dict(GREEN, **steps).items()}
    return dict(value, RECEIPT_CHECKPOINT=checkpoint, GITHUB_RUN_ID="4242", GITHUB_RUN_ATTEMPT="1")


def at(iso):
    return datetime.fromisoformat(iso).astimezone(timezone.utc)


def test_a_data_only_receipt_is_published_with_the_synthesis_still_rejected(day):
    rejecting(day)
    assert day.run(f"{TUE}T13:01:00+00:00", "PREMARKET", command="schedule", wire=True) == 0
    receipt = build_receipt(env("PREMARKET"), day.root, at(f"{TUE}T13:04:00+00:00"))
    assert receipt["outcome"] == "published_data_only" and receipt["page"] == "published_data_only"
    assert receipt["validation"] == "FAILED" and receipt["publication"] == "DATA_ONLY"
    assert receipt["error"] == "unknown attention trigger"
    assert receipt["synthesis"]["status"] == "rejected" and receipt["analysis_accepted"] is False
    assert receipt["synthesis"]["response_id"] == "gen-test-rejected" and receipt["synthesis"]["cost"] == 0.0123
    assert receipt["synthesis"]["calls"] == 1 and receipt["published"] is True
    assert receipt["data_only"] == "synthesis_rejected" and receipt["collection"] == "completed"
    assert receipt["evidence"] == "preserved"
    # A cold start: no bundle on disk, so the ignored empty continuity upload preserves nothing.
    assert bundle_bytes(day) is None and receipt["continuity"] == "not_present"


def test_a_data_only_receipt_after_an_accepted_friday_preserves_the_unchanged_bundle(day):
    assert day.run(f"{FRI}T13:00:00+00:00", "PREMARKET", intraday=False, last_history_date="2026-09-03") == 0
    before = bundle_bytes(day)
    assert before is not None
    rejecting(day)
    assert day.run(f"{TUE}T13:01:00+00:00", "PREMARKET", command="schedule", wire=True) == 0
    receipt = build_receipt(env("PREMARKET"), day.root, at(f"{TUE}T13:04:00+00:00"))
    assert receipt["outcome"] == "published_data_only" and receipt["continuity"] == "preserved"
    assert bundle_bytes(day) == before  # preserved means the restored bundle, byte for byte
    # The same bundle whose upload did not succeed is never claimed as preserved.
    for step, expected in (("skipped", "unchanged"), ("cancelled", "unchanged"), ("", "unchanged"),
                           ("failure", "upload_failed")):
        receipt = build_receipt(env("PREMARKET", continuity=step), day.root, at(f"{TUE}T13:04:00+00:00"))
        assert receipt["continuity"] == expected, step
    assert bundle_bytes(day) == before


def test_a_symlinked_bundle_is_not_a_present_bundle(day, tmp_path):
    rejecting(day)
    assert day.run(f"{TUE}T13:01:00+00:00", "PREMARKET", command="schedule", wire=True) == 0
    target = tmp_path / "elsewhere.json"
    target.write_text("{}")
    path = bundle_path(day.root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)
    receipt = build_receipt(env("PREMARKET"), day.root, at(f"{TUE}T13:04:00+00:00"))
    assert receipt["continuity"] == "not_present"


def test_a_data_only_refresh_receipt_has_no_synthesis(day):
    rejecting(day)
    assert day.run(f"{TUE}T13:01:00+00:00", "PREMARKET", command="schedule", wire=True) == 0
    assert day.run(f"{TUE}T13:31:00+00:00", "OPEN_1M", command="schedule") == 0
    receipt = build_receipt(env("OPEN_1M", continuity="skipped"), day.root, at(f"{TUE}T13:33:00+00:00"))
    assert receipt["outcome"] == "published_data_only" and receipt["synthesis"] is None
    assert receipt["validation"] == "NOT_APPLICABLE" and receipt["publication"] == "DATA_ONLY"
    assert receipt["published"] is True and receipt["analysis_accepted"] is False
    assert receipt["data_only"] == "no_accepted_analysis" and receipt["continuity"] == "not_present"


@pytest.mark.parametrize(("steps", "outcome"), [(dict(pipeline="failure"), "pipeline_crashed"),
                                                (dict(deploy="failure"), "deploy_failed"),
                                                (dict(publish="failure"), "push_failed")])
def test_a_data_only_run_that_failed_later_is_never_published(day, steps, outcome):
    rejecting(day)
    assert day.run(f"{TUE}T13:01:00+00:00", "PREMARKET", command="schedule", wire=True) == 0
    receipt = build_receipt(env("PREMARKET", **steps), day.root, at(f"{TUE}T13:04:00+00:00"))
    assert receipt["outcome"] == outcome and receipt["page"] == "not_published" and receipt["published"] is False
    assert receipt["validation"] == "FAILED"


def test_an_accepted_synthesis_receipt_advances_continuity(day):
    assert day.run(f"{TUE}T13:01:00+00:00", "PREMARKET", command="schedule", intraday=False) == 0
    receipt = build_receipt(env("PREMARKET"), day.root, at(f"{TUE}T13:04:00+00:00"))
    assert receipt["outcome"] == "published" and receipt["analysis_accepted"] is True
    assert receipt["continuity"] == "advanced" and receipt["data_only"] is None and bundle_bytes(day) is not None
    # An advanced bundle whose upload did not succeed is never claimed as advanced.
    skipped = build_receipt(env("PREMARKET", continuity="skipped"), day.root, at(f"{TUE}T13:04:00+00:00"))
    assert skipped["continuity"] == "unknown"
    path = bundle_path(day.root)
    path.unlink()
    missing = build_receipt(env("PREMARKET"), day.root, at(f"{TUE}T13:04:00+00:00"))
    assert missing["continuity"] == "not_present"


def test_a_skipped_run_preserves_no_evidence(day):
    receipt = build_receipt(env("PREMARKET"), day.root, at(f"{TUE}T13:04:00+00:00"))
    assert receipt["collection"] == "not_attempted" and receipt["evidence"] == "none_collected"
    assert receipt["page"] == "not_published"


# --- the receipt audit: payloads, conclusions, and the missed wake --------------------------------------------------


def payload(checkpoint, run_id, **change):
    kind = "synthesis" if checkpoint in ("PREMARKET", "OPEN_30M") else "close" if checkpoint == "CLOSE_1M" \
        else "refresh"
    value = dict(schema_version=RECEIPT_SCHEMA, session_date=MON, checkpoint=checkpoint, run_id=str(run_id),
                 kind=kind, outcome="published", page="published", deployed=True, collection="completed",
                 evidence="preserved", continuity="advanced",
                 synthesis=dict(status="accepted") if kind == "synthesis" else None)
    return dict(value, **change)


def zipped(value):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("receipt.json", json.dumps(value) if not isinstance(value, bytes) else value)
    return buffer.getvalue()


class Payloads:
    def __init__(self, archives):
        self.archives, self.requests = archives, []

    def __call__(self, request):
        self.requests.append(request[0])
        artifact = int(request[0].split("/artifacts/", 1)[1].split("/", 1)[0])
        if artifact not in self.archives:
            raise ValueError("GitHub API request failed")
        return self.archives[artifact]


def day_with_payloads(checkpoints=ALL, start=1000, **changes):
    artifacts = full_day(MON, checkpoints, start)
    archives = {}
    for artifact in artifacts:
        run_id = artifact["workflow_run"]["id"]
        artifact["id"] = run_id
        checkpoint = artifact["name"].split(f"{MON}-", 1)[1].rsplit("-", 1)[0]
        archives[run_id] = zipped(payload(checkpoint, run_id, **changes.get(checkpoint, {})))
    return artifacts, archives


def line(lines, checkpoint):
    return next(text for text in lines if text.startswith(f"  {checkpoint}:"))


def test_the_audit_reports_each_checkpoints_stage_health_from_its_payload():
    artifacts, archives = day_with_payloads()
    code, lines = audit.audit(EVENING, FakeGitHub(artifacts), REPO, gh_bytes=Payloads(archives))
    assert code == 0 and "all 9 checkpoints" in lines[0] and "wakes only, not publication" in lines[0]
    assert lines[1] == (f"PUBLICATION / {MON} / all 9 of 9 published by a successful run "
                        "(0 data only, no analysis)")
    premarket = line(lines, "PREMARKET")
    assert "wake observed" in premarket and "run concluded success" in premarket
    assert "collection completed" in premarket and "synthesis accepted" in premarket
    assert "page published" in premarket and "evidence preserved" in premarket and "continuity advanced" in premarket
    assert "synthesis not applicable" in line(lines, "HOURLY_1100")


def test_a_data_only_page_is_published_and_never_an_accepted_analysis():
    artifacts, archives = day_with_payloads(
        PREMARKET=dict(outcome="published_data_only", page="published_data_only", continuity="preserved",
                       synthesis=dict(status="rejected")),
        OPEN_1M=dict(outcome="published_data_only", page="published_data_only", continuity="unchanged"))
    code, lines = audit.audit(EVENING, FakeGitHub(artifacts), REPO, gh_bytes=Payloads(archives))
    premarket = line(lines, "PREMARKET")
    assert code == 0 and "synthesis rejected" in premarket and "synthesis accepted" not in premarket
    assert "page published (data only: current observations, no analysis)" in premarket
    assert "continuity preserved" in premarket and "continuity unchanged" in line(lines, "OPEN_1M")
    assert lines[1] == f"PUBLICATION / {MON} / all 9 of 9 published by a successful run (2 data only, no analysis)"


@pytest.mark.parametrize("conclusion", ["failure", "cancelled"])
def test_a_red_run_never_counts_as_a_published_page_whatever_its_receipt_claims(conclusion):
    artifacts, archives = day_with_payloads()
    github = FakeGitHub(artifacts, {1000: run_details(1000, conclusion=conclusion)})
    code, lines = audit.audit(EVENING, github, REPO, gh_bytes=Payloads(archives))
    premarket = line(lines, "PREMARKET")
    assert code == 0  # the wake still happened
    assert f"page not published (run concluded {conclusion}" in premarket and "page published" not in premarket
    # A green wake verdict never reads as a green publication verdict.
    assert "all wakes observed" in lines[0] and "wakes only, not publication" in lines[0]
    assert lines[1] == (f"PUBLICATION / {MON} / FAILED OR UNVERIFIED for 1 of 9 checkpoints: PREMARKET; "
                        "8 of 9 published by a successful run (0 data only, no analysis)")


def test_a_receipt_artifact_alone_is_not_publication_health():
    artifacts, archives = day_with_payloads()
    archives[1000] = zipped(payload("OPEN_1M", 1000))  # another checkpoint's receipt
    archives[1001] = b"not a zip"
    del archives[1002]  # the download fails
    archives[1003] = zipped(b"{not json")
    code, lines = audit.audit(EVENING, FakeGitHub(artifacts), REPO, gh_bytes=Payloads(archives))
    assert code == 0
    for checkpoint in ALL[:4]:
        assert "receipt payload unreadable; stage health unknown" in line(lines, checkpoint)
    assert lines[1] == (f"PUBLICATION / {MON} / FAILED OR UNVERIFIED for 4 of 9 checkpoints: "
                        f"{', '.join(ALL[:4])}; 5 of 9 published by a successful run (0 data only, no analysis)")
    # Without a payload reader nothing is claimed either.
    code, unread = audit.audit(EVENING, FakeGitHub(artifacts), REPO)
    assert code == 0 and "receipt payload not read; stage health unknown" in line(unread, "PREMARKET")
    assert unread[1] == f"PUBLICATION / {MON} / UNVERIFIED: receipt payloads not read"


def test_a_missing_hourly_1100_is_still_detected_with_payloads_read():
    artifacts, archives = day_with_payloads([c for c in ALL if c != "HOURLY_1100"])
    payloads = Payloads(archives)
    code, lines = audit.audit(EVENING, FakeGitHub(artifacts), REPO, gh_bytes=payloads)
    assert code == 1
    assert lines[0].startswith(f"MISSED / {MON} / 1 of 9") and lines[0].endswith("HOURLY_1100")
    assert "  HOURLY_1100: NO RECEIPT" in lines
    assert lines[1].startswith(f"PUBLICATION / {MON} / FAILED OR UNVERIFIED for 1 of 9 checkpoints: HOURLY_1100;")
    assert len(payloads.requests) == 8


def test_the_cli_reads_receipt_payloads_through_gh(monkeypatch, capsys):
    from types import SimpleNamespace
    artifacts, archives = day_with_payloads()
    github = FakeGitHub(artifacts)
    monkeypatch.setattr(audit, "utc_now", lambda: EVENING)

    def runner(argv, **kwargs):
        assert argv[:2] == ["gh", "api"]
        if argv[2].endswith("/zip"):
            assert "text" not in kwargs  # binary
            return SimpleNamespace(returncode=0, stdout=Payloads(archives)([argv[2]]))
        return SimpleNamespace(returncode=0, stdout=json.dumps(github([argv[2]])))
    assert cli.audit_receipts(SimpleNamespace(repository=REPO, date=None, branch="main"), runner=runner) == 0
    out = capsys.readouterr().out
    assert f"PUBLICATION / {MON} / all 9 of 9 published" in out and "wake observed" in out


def test_the_archived_hourly_1200_refresh_publishes_observations_only(tmp_path):
    """The 2026-11-25 HOURLY_1200 production run failed closed with only the prior session's interpretation on hand;
    from its archived evidence and metadata alone the scheduled refresh now publishes its own collection."""
    source = ARCHIVE / "hourly1200" / "2026-11-25" / "live-hourly_1200-170230-631ca1f7"
    if not ((source / "evidence.json").is_file() and (source / "metadata.json").is_file()):
        pytest.skip(f"archived production run not present: {source}")
    packet, metadata = read_json(source / "evidence.json"), read_json(source / "metadata.json")
    assert digest(packet) == metadata["evidence_hash"]  # exactly the archived collection
    fallback, refusal = cli.refresh_data_only(packet, metadata, metadata["error"], True)
    assert refusal is None, refusal
    assert fallback["basis"] == "no_accepted_analysis" and fallback["checkpoint"] == "HOURLY_1200"
    assert fallback["reason"] == metadata["error"] and fallback["evidence_hash"] == metadata["evidence_hash"]
    assert fallback["current_prints"] > 0
    markdown, page = render(packet, data_only=fallback)
    assert f"<h1>{html.escape(DATA_ONLY_HEADING)}</h1>" in page and DATA_ONLY_HEADING in markdown
    assert "Wednesday, November 25, 2026" in page and "(HOURLY_1200)" in page
    assert 'data-analysis="unavailable"' in page and 'data-session-date="2026-11-25"' in page
    assert 'id="evidence-ledger"' in page and "The take:" not in page
    view = presentation(packet, data_only=fallback)
    assert view["evidence"] and view["sources"]  # the observed record stays
    # No interpretation is admitted: not this session's (there is none) and not 2026-11-24's.
    assert view["summary"] == [] and view["take"] is None and view["since"]["entries"] == []
    assert view["next"]["watches"] == [] and view["next"]["attention"] == [] and view["interpretation_groups"] == []

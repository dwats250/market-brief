"""W43 S1a: one receipt per checkpoint run, counted by class, never able to gate anything.

The classes keep the week's denominators honest (owner correction F): a degraded opening is a delivered fallback,
not an accepted synthesis; a stale-price refusal is an explained collection failure, not accepted data; deployment
is GitHub's word and reader delivery stays pending until it is verified."""

from datetime import datetime, timezone

import pytest
from test_cadence import TUE, day  # noqa: F401  (fixture)
from test_cloudflare_scheduler import workflow_text

from market_brief.receipts import build_receipt, outcome_class

GREEN = dict(resolve="success", restore="success", pipeline="success", continuity="success", publish="success",
             current="success", deploy="success")


def env(checkpoint, **steps):
    outcomes = dict(GREEN, **steps)
    value = {f"RECEIPT_{k.upper()}": v for k, v in outcomes.items()}
    return dict(value, RECEIPT_CHECKPOINT=checkpoint, GITHUB_RUN_ID="4242", GITHUB_RUN_ATTEMPT="1",
                RECEIPT_PUBLISH_SHA="abc1234")


def at(iso):
    return datetime.fromisoformat(iso).astimezone(timezone.utc)


def test_an_accepted_synthesis_is_published_with_its_paid_call_and_delivery_pending(day):  # noqa: F811
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    receipt = build_receipt(env("PREMARKET"), day.root, at(f"{TUE}T13:03:10+00:00"))
    assert receipt["outcome"] == "published" and receipt["session_date"] == TUE
    assert receipt["synthesis"]["status"] == "accepted" and receipt["delivery"] == "pending"
    assert receipt["deployed"] is True and receipt["publish_sha"] == "abc1234"
    assert receipt["scheduled_at"] == f"{TUE}T13:00:00+00:00" and receipt["lag_seconds"] == 190


def test_a_degraded_opening_is_a_delivered_fallback_not_an_accepted_synthesis(day):  # noqa: F811
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M", fail_synthesis=True) == 0
    receipt = build_receipt(env("OPEN_30M"), day.root, at(f"{TUE}T14:03:00+00:00"))
    assert receipt["outcome"] == "published_degraded"
    assert receipt["synthesis"]["status"] == "rejected_degraded"
    assert receipt["degraded_from"]["checkpoint"] == "OPEN_30M"


def test_a_stale_price_refusal_is_an_explained_collection_failure(day):  # noqa: F811
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    assert day.run(f"{TUE}T17:00:00+00:00", "HOURLY_1300", intraday=False) == 2
    receipt = build_receipt(env("HOURLY_1300", pipeline="failure", continuity="skipped", publish="skipped",
                                current="skipped", deploy="skipped"), day.root, at(f"{TUE}T17:01:00+00:00"))
    assert receipt["outcome"] == "refused_stale_data" and receipt["delivery"] == "none"
    assert "no timestamped current prints" in receipt["error"]


@pytest.mark.parametrize(("steps", "metadata", "expected"), [
    (dict(restore="failure", pipeline="skipped"), None, "restore_failed"),
    (dict(pipeline="success"), None, "skipped"),
    (dict(pipeline="failure"), None, "pipeline_crashed"),
    (dict(pipeline="cancelled"), None, "not_finished"),
    (dict(publish="failure", current="skipped", deploy="skipped"), dict(validation="PASS"), "push_failed"),
    (dict(deploy="failure"), dict(validation="PASS"), "deploy_failed"),
    (dict(current="success", deploy="skipped"), dict(validation="PASS"), "not_deployed"),
    (dict(pipeline="failure"), dict(validation="FAILED", kind="synthesis", error="unknown reference"),
     "synthesis_rejected"),
    (dict(), dict(validation="PASS", published=False), "recorded_without_page"),
    # Review fixes: a crash after the run folder existed, and a failed continuity upload, are not push failures.
    (dict(pipeline="failure", continuity="skipped", publish="skipped"), dict(validation="NOT_RUN"),
     "pipeline_crashed"),
    (dict(continuity="failure", publish="skipped", current="skipped", deploy="skipped"), dict(validation="PASS"),
     "continuity_upload_failed"),
])
def test_the_earliest_deciding_stage_names_the_class(steps, metadata, expected):
    assert outcome_class(dict(GREEN, **steps), metadata) == expected


def test_unknown_step_outcomes_are_unknown_not_success(day):  # noqa: F811
    receipt = build_receipt(dict(env("HOURLY_1300"), RECEIPT_DEPLOY="maybe", RECEIPT_PUBLISH=""), day.root,
                            at(f"{TUE}T17:01:00+00:00"))
    assert receipt["steps"]["deploy"] == "" and receipt["steps"]["publish"] == ""
    assert receipt["deployed"] is False and receipt["delivery"] == "none"


def test_the_receipt_runs_last_always_and_can_never_fail_the_run():
    workflow = workflow_text()
    brief = workflow.split("\n  brief:\n", 1)[1].split("\n  cloudflare-smoke:\n", 1)[0]
    steps = brief.split("\n      - name: ")[1:]
    names = [s.split("\n", 1)[0] for s in steps]
    assert names[-2:] == ["Write this checkpoint's receipt", "Upload the checkpoint receipt"]
    assert names.index("Write this checkpoint's receipt") > names.index("Upload the paid synthesis attempt record")
    for step in steps[-2:]:
        assert "continue-on-error: true" in step and "if: always()" in step
    assert "retention-days: 90" in steps[-1]
    # Experiments and commissioning runs are not checkpoints.
    assert "inputs.commissioning != true && inputs.experiment != true" in steps[-2]
    # The publish step reports its own commit for the receipt, on both success paths.
    publish = next(s for s in steps if s.startswith("Publish latest successful brief"))
    assert publish.count('echo "sha=$own" >> "${GITHUB_OUTPUT:-/dev/null}"') == 2

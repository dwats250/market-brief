"""R2: one paid generation per synthesis checkpoint and session, across runners, accepted or rejected.

Duplicate suppression used to prove only acceptance: the scheduler skipped a checkpoint that its local marker, the
checked-out page or the restored continuity bundle recorded. A rejected generation leaves none of those, so a fresh
runner inside the same synthesis window paid again. Now a production synthesis records the attempt before its
provider request is sent, the workflow uploads that record whatever the synthesis then does, and `scheduled()`
never pays for a checkpoint that has one. Attempt records are accounting, never continuity: nothing admits,
restores or renders them. A rejected generation's metadata records the paid call it was.
"""

import json
import re
from types import SimpleNamespace

import pytest
from test_cadence import TUE, Day
from test_ci_triggers import top_level
from test_continuity_restore import HOME, REPOSITORY, Actions, own, workflow_steps
from test_contract import edition_response
from test_pipeline import fixture_packet, narrative

from market_brief import cli
from market_brief.context import edition_profile
from market_brief.continuity import ARTIFACT_NAME, WORKFLOW_PATH, bundle_path
from market_brief.evidence import ROOT
from market_brief.synthesize import (
    OPENROUTER_FALLBACK_MODEL,
    OPENROUTER_MODEL,
    NarrativeRejected,
    _ModelUnavailableError,
    synthesize_openrouter,
)

WED = "2026-09-09"
COST = {"prompt_tokens": 2200, "completion_tokens": 3100, "total_tokens": 5300, "cost": 0.154}


class Served:
    status = 200

    def __init__(self, value):
        self.body = json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return self.body


class PaidActions(Actions):
    """R1's model of schedule.yml on main, now paying through the real analyst path.

    The scheduler lists uploaded attempt records through the same fake GitHub API that serves continuity; the real
    synthesize() sends every request to a fake OpenRouter endpoint that counts it as a paid generation; each run
    ends with the workflow's last, always() step, which uploads the run's attempt record. Runs are sequential, as
    the workflow's one concurrency group makes them.
    """

    def __init__(self, monkeypatch, tmp_path):
        super().__init__(monkeypatch, tmp_path)
        self.requests, self.lookups, self.verdict, self.unlistable = [], [], "accept", False
        monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-only")
        monkeypatch.setattr("market_brief.synthesize.urlopen", self.openrouter)
        monkeypatch.setattr(cli, "_gh_json", self.gh_json)

    def gh_json(self, args, runner=None):
        if cli.ATTEMPT_ARTIFACT in args[0]:
            self.lookups.append(args[0])
            if self.unlistable:
                raise ValueError("GitHub API request failed")
        return json.loads(self.gh(["gh", "api", *args]).stdout)

    def openrouter(self, request, timeout):
        payload = json.loads(request.data)
        context = json.loads(payload["messages"][1]["content"])
        del context["output_schema"]
        checkpoint = context["run"]["checkpoint"]
        self.requests.append(checkpoint)
        if self.verdict == "timeout":
            raise TimeoutError()
        value = edition_response(edition_profile(checkpoint), context)
        value["mode"] = "LIVE"
        if self.verdict == "reject":
            value["summary"][0]["evidence_ids"] = ["not-supplied"]  # fails grounding: a paid, rejected generation
        return Served({"id": f"gen-{len(self.requests)}", "model": OPENROUTER_MODEL, "provider": "Anthropic",
                       "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}],
                       "usage": COST})

    def run(self, now, checkpoint, publication="success", continuity_upload="success", verdict="accept",
            command="schedule", **kwargs):
        self.verdict = verdict
        run_id = 1001 + len(self.runs)
        root = self.tmp_path / f"runner-{run_id}"
        root.mkdir()
        self.runs[run_id] = own(status="in_progress", conclusion=None, path=WORKFLOW_PATH, head_branch="main")
        self.monkeypatch.setattr(cli, "RUN_ROOT", root)
        restore = SimpleNamespace(from_file=None, repository=REPOSITORY, branch="main")
        assert cli.restore_continuity(restore, runner=self.gh) == 0
        day = self.days[run_id] = Day(self.monkeypatch, root)
        code = day.run(now, checkpoint, command=command, wire=True, **kwargs)
        self.published += day.published
        self.upload(run_id, f"market-brief-run-{checkpoint}-{run_id}", now, 1)
        if code == 0 and bundle_path(root).is_file() and continuity_upload == "success":
            self.upload(run_id, ARTIFACT_NAME, now, 2, bundle_path(root).read_bytes())
        # The workflow's last steps, always(): whatever the pipeline, the continuity upload or publication did.
        for record in sorted((root / "runs/attempts").glob("*.json")):
            self.upload(run_id, f"{cli.ATTEMPT_ARTIFACT}-{record.stem}", now, 5, record.read_bytes())
        failed = code != 0 or publication != "success" or continuity_upload != "success"
        self.runs[run_id].update(status="completed", conclusion="failure" if failed else "success")
        return run_id

    def names(self, run_id):
        return [a["name"] for a in self.artifacts if a["workflow_run"]["id"] == run_id]

    def metadata(self, run_id, checkpoint, session=TUE):
        return self.days[run_id].metadata(checkpoint, session)


@pytest.fixture
def actions(monkeypatch, tmp_path):
    return PaidActions(monkeypatch, tmp_path)


def attempt(session, checkpoint):
    return f"{cli.ATTEMPT_ARTIFACT}-{session}-{checkpoint}"


# --- a second fresh runner never pays for a checkpoint already attempted ---------------------------------

@pytest.mark.parametrize(("continuity_upload", "reason"), [
    ("success", "already completed"),  # the restored bundle records the accepted edition, as before
    ("failed", "paid synthesis already attempted"),  # no bundle to prove it: the attempt record still does
])
def test_an_accepted_synthesis_is_never_paid_for_twice(actions, capsys, continuity_upload, reason):
    first = actions.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False, continuity_upload=continuity_upload)
    assert actions.metadata(first, "PREMARKET")["validation"] == "PASS"
    assert attempt(TUE, "PREMARKET") in actions.names(first)
    capsys.readouterr()
    # Ten minutes later, inside the twenty-minute window, a fresh runner with no marker and no page.
    second = actions.run(f"{TUE}T13:11:00+00:00", "PREMARKET", intraday=False)
    assert f"SKIP / PREMARKET / {reason}" in capsys.readouterr().out
    assert actions.requests == ["PREMARKET"] and not actions.days[second].attempts("PREMARKET")


def test_a_rejected_synthesis_is_never_paid_for_again(actions, capsys):
    first = actions.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False, verdict="reject")
    assert actions.runs[first]["conclusion"] == "failure"
    assert actions.names(first) == [f"market-brief-run-PREMARKET-{first}", attempt(TUE, "PREMARKET")]
    capsys.readouterr()
    second = actions.run(f"{TUE}T13:11:00+00:00", "PREMARKET", intraday=False)
    out = capsys.readouterr().out
    assert f"SKIP / PREMARKET / paid synthesis already attempted for {TUE} (artifact 2 from run {first})" in out
    assert actions.requests == ["PREMARKET"] and not actions.days[second].attempts("PREMARKET")
    assert actions.runs[second]["conclusion"] == "success" and actions.published == []


def test_a_manual_production_attempt_counts_and_experiments_stay_outside(actions, capsys):
    """An owner's manual production dispatch is not gated, but its paid attempt is recorded, so the scheduled wake
    that follows it inside the window does not pay again."""
    actions.run(f"{TUE}T12:50:00+00:00", "PREMARKET", intraday=False, verdict="reject", command="premarket")
    capsys.readouterr()
    actions.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)
    assert "SKIP / PREMARKET / paid synthesis already attempted" in capsys.readouterr().out
    assert actions.requests == ["PREMARKET"]


# --- rejected analysis never becomes continuity or a page -------------------------------------------------

def test_a_rejected_synthesis_advances_nothing_and_publishes_nothing(actions):
    premarket = actions.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)
    accepted = actions.bundle(premarket)
    rejected = actions.run(f"{TUE}T14:01:00+00:00", "OPEN_30M", verdict="reject")
    day = actions.days[rejected]
    # Its runner's bundle is exactly the restored one, and it uploaded only its archive and its attempt record.
    assert json.loads(bundle_path(day.root).read_text()) == accepted
    assert actions.names(rejected) == [f"market-brief-run-OPEN_30M-{rejected}", attempt(TUE, "OPEN_30M")]
    assert actions.published == ["PREMARKET"]
    folder = day.folder("OPEN_30M")
    assert (folder / "narrative.rejected.json").exists() and not (folder / "brief.html").exists()
    assert json.loads((folder / "metadata.json").read_text())["continuity"]["advanced"] is False
    # The next refresh carries the premarket interpretation; the attempt record is nowhere in continuity.
    refresh = actions.run(f"{TUE}T15:01:00+00:00", "HOURLY_1100")
    assert actions.metadata(refresh, "HOURLY_1100")["interpretation"]["checkpoint"] == "PREMARKET"
    assert "attempt" not in json.dumps(sorted(actions.bundle(refresh)))
    assert actions.requests == ["PREMARKET", "OPEN_30M"]


# --- scoping: checkpoint, kind and session ------------------------------------------------------------------

def test_premarket_and_the_opening_structure_update_are_independent_paid_checkpoints(actions, capsys):
    actions.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False, verdict="reject")
    structure = actions.run(f"{TUE}T14:01:00+00:00", "OPEN_30M")
    assert actions.metadata(structure, "OPEN_30M")["validation"] == "PASS"
    capsys.readouterr()
    actions.run(f"{TUE}T14:11:00+00:00", "OPEN_30M")
    assert "SKIP / OPEN_30M" in capsys.readouterr().out
    assert actions.requests == ["PREMARKET", "OPEN_30M"]
    assert sorted(a["name"] for a in actions.artifacts if a["name"].startswith(cli.ATTEMPT_ARTIFACT)) == [
        attempt(TUE, "OPEN_30M"), attempt(TUE, "PREMARKET")]


def test_deterministic_checkpoints_never_pay_record_or_ask(actions):
    actions.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)
    asked = len(actions.lookups)
    for now, checkpoint, kwargs in ((f"{TUE}T13:31:00+00:00", "OPEN_1M", {}),
                                    (f"{TUE}T15:01:00+00:00", "HOURLY_1100", {}),
                                    (f"{TUE}T20:03:00+00:00", "CLOSE_1M", dict(print_at=f"{TUE}T19:59:58+00:00"))):
        run_id = actions.run(now, checkpoint, **kwargs)
        metadata = actions.metadata(run_id, checkpoint)
        assert metadata["validation"] == "PASS" and metadata["synthesis"]["calls"] == 0
        assert metadata["model_route"] == "none" and "attempt_record" not in metadata
        assert not (actions.days[run_id].root / "runs/attempts").exists()
        assert not any(name.startswith(cli.ATTEMPT_ARTIFACT) for name in actions.names(run_id))
    assert len(actions.lookups) == asked  # no attempt listing either
    assert actions.requests == ["PREMARKET"]


def test_the_next_session_is_not_suppressed_by_this_sessions_attempt(actions):
    actions.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False, verdict="reject")
    wednesday = actions.run(f"{WED}T13:01:00+00:00", "PREMARKET", intraday=False, last_history_date=TUE)
    assert actions.requests == ["PREMARKET", "PREMARKET"]
    assert actions.metadata(wednesday, "PREMARKET", WED)["validation"] == "PASS"
    assert attempt(WED, "PREMARKET") in actions.names(wednesday)


def test_a_weekend_manual_run_never_consumes_the_next_sessions_checkpoint(actions):
    """A manual production dispatch on a non-trading day (the workflow's default inputs) is keyed by its own calendar
    date, as its page and bundle are, never by the session the exchange calendar would move it to."""
    saturday, monday = "2026-09-12", "2026-09-14"
    weekend = actions.run(f"{saturday}T13:01:00+00:00", "PREMARKET", intraday=False, command="premarket",
                          last_history_date="2026-09-11")
    record = actions.metadata(weekend, "PREMARKET", saturday)["attempt_record"]
    assert record == f"runs/attempts/{saturday}-PREMARKET.json"
    assert attempt(saturday, "PREMARKET") in actions.names(weekend)
    actions.run(f"{monday}T13:01:00+00:00", "PREMARKET", intraday=False, last_history_date="2026-09-11")
    assert actions.requests == ["PREMARKET", "PREMARKET"]  # Monday's premarket still pays, once
    assert attempt(monday, "PREMARKET") in [a["name"] for a in actions.artifacts]


# --- a rejected generation is accounted as the paid call it was ---------------------------------------------

@pytest.mark.parametrize("verdict", ["reject", "timeout"])
def test_a_rejected_generation_records_the_paid_call_truthfully(actions, verdict):
    run_id = actions.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False, verdict=verdict)
    metadata = actions.metadata(run_id, "PREMARKET")
    assert metadata["validation"] == "FAILED"
    assert metadata["synthesis"] == dict(kind="synthesis", calls=1)
    assert metadata["model_route"] == "openrouter"
    model = metadata["model"]
    assert model["attempts"] == 1 and model["requested_model"] == OPENROUTER_MODEL and model["profile"] == "rich"
    assert model["requested_at"] and model["prompt_hash"] and model["evidence_hash"]
    if verdict == "reject":
        # A generation came back and was billed: its usage and cost are on record, as for an accepted one.
        assert model["usage"] == COST and model["response_id"] == "gen-1"
        assert model["resolved_model"] == OPENROUTER_MODEL and model["finish_reason"] == "stop"
        assert "unsupplied evidence reference" in metadata["error"]
    else:
        assert "usage" not in model and "no automatic paid retry" in metadata["error"]
    assert metadata["attempt_record"] == f"runs/attempts/{TUE}-PREMARKET.json"
    record = json.loads((actions.days[run_id].root / metadata["attempt_record"]).read_text())
    assert record == dict(schema_version=cli.ATTEMPT_SCHEMA, session_date=TUE, checkpoint="PREMARKET",
                          run_id=metadata["run_id"], route="openrouter", requested_model=OPENROUTER_MODEL,
                          requested_at=model["requested_at"])


def test_a_failure_before_any_request_is_neither_a_call_nor_an_attempt(actions, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")  # nothing is sent: no credentials, and no analyst CLI either
    first = actions.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)
    metadata = actions.metadata(first, "PREMARKET")
    assert metadata["validation"] == "FAILED" and metadata["synthesis"]["calls"] == 0
    assert metadata["model_route"] == "none" and "attempt_record" not in metadata
    assert actions.names(first) == [f"market-brief-run-PREMARKET-{first}"]
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only")
    actions.run(f"{TUE}T13:11:00+00:00", "PREMARKET", intraday=False)
    assert actions.requests == ["PREMARKET"]  # the one paid attempt of the checkpoint


# --- fail closed: an unanswered question never pays --------------------------------------------------------

def test_an_unlistable_attempt_record_store_never_pays(actions, capsys):
    actions.unlistable = True
    run_id = actions.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)
    assert f"NOT RUN / PREMARKET / earlier paid attempts for {TUE} could not be checked" in capsys.readouterr().err
    assert actions.requests == [] and actions.runs[run_id]["conclusion"] == "failure"
    assert not actions.days[run_id].attempts("PREMARKET")


def test_a_record_that_cannot_be_written_sends_nothing(tmp_path, monkeypatch):
    from test_continuity import live_cli
    runner = live_cli(monkeypatch, tmp_path, f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs/attempts").write_text("not a directory")
    sent = []

    def paid(packet, on_request=None, **kwargs):
        on_request(dict(route="openrouter", requested_model=OPENROUTER_MODEL, requested_at="t"))
        sent.append(packet["run"]["checkpoint"])
        raise AssertionError("unreachable")
    monkeypatch.setattr(runner, "synthesize", paid)
    assert runner.main(["premarket", "--checkpoint", "PREMARKET"]) == 2
    assert sent == []
    folder = next(p for p in (tmp_path / f"runs/{TUE}").iterdir() if (p / "metadata.json").exists())
    metadata = json.loads((folder / "metadata.json").read_text())
    assert "could not be recorded; no provider request was sent" in metadata["error"]
    assert metadata["synthesis"]["calls"] == 0


@pytest.mark.parametrize(("flags", "recorded"), [([], True), (["--experiment"], False), (["--commissioning"], False)])
def test_only_production_attempts_are_recorded(tmp_path, monkeypatch, flags, recorded):
    """Experiments and commissioning runs pay under the owner's hand and stay isolated from production: they are
    accounted in their own metadata but never suppress, or are suppressed by, the production checkpoint."""
    from test_continuity import live_cli
    runner = live_cli(monkeypatch, tmp_path, f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)

    def paid(packet, on_request=None, **kwargs):
        on_request(dict(route="openrouter", requested_model=OPENROUTER_MODEL, requested_at="t"))
        raise ValueError("rejected after the call")
    monkeypatch.setattr(runner, "synthesize", paid)
    assert runner.main(["premarket", "--checkpoint", "PREMARKET", *flags]) == 2
    assert cli.attempt_path(tmp_path, TUE, "PREMARKET").is_file() is recorded
    folder = next(p for p in (tmp_path / f"runs/{TUE}").iterdir() if (p / "metadata.json").exists())
    metadata = json.loads((folder / "metadata.json").read_text())
    assert metadata["synthesis"]["calls"] == 1 and metadata["model_route"] == "openrouter"
    assert ("attempt_record" in metadata) is recorded


# --- the synthesis seam: the hook runs once, before the first request, and never without one -----------------

def served(value, **extra):
    return dict({"id": "r", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]},
                **extra)


def test_the_attempt_hook_runs_once_immediately_before_the_first_request():
    events = []

    def requester(payload, api_key):
        events.append(("request", payload["model"]))
        return served(narrative())
    synthesize_openrouter(fixture_packet(), api_key="k", requester=requester,
                          on_request=lambda a: events.append(("hook", a["requested_model"], a["attempts"])))
    # The hook describes the request about to be sent, so metadata taken from it never says zero requests.
    assert events == [("hook", OPENROUTER_MODEL, 1), ("request", OPENROUTER_MODEL)]


def test_no_hook_and_no_attempt_when_nothing_is_sent(monkeypatch):
    from market_brief import synthesize as module
    hooks = []

    def must_not_send(payload, api_key):
        raise AssertionError("a request was sent")
    with pytest.raises(ValueError, match="credentials are not configured") as exc:
        synthesize_openrouter(fixture_packet(), requester=must_not_send, on_request=hooks.append)
    assert not hasattr(exc.value, "attempt")
    real = module.edition_profile
    monkeypatch.setattr(module, "edition_profile", lambda checkpoint: dict(real(checkpoint), input_limit_bytes=100))
    with pytest.raises(ValueError, match="exceeds the rich edition budget") as exc:
        synthesize_openrouter(fixture_packet(), api_key="k", requester=must_not_send, on_request=hooks.append)
    assert not hasattr(exc.value, "attempt") and hooks == []


def test_a_hook_that_fails_sends_nothing():
    def must_not_send(payload, api_key):
        raise AssertionError("a request was sent")

    def unrecorded(attempt):
        raise ValueError("the paid synthesis attempt could not be recorded; no provider request was sent")
    with pytest.raises(ValueError, match="could not be recorded"):
        synthesize_openrouter(fixture_packet(), api_key="k", requester=must_not_send, on_request=unrecorded)


def test_the_bounded_fallback_is_one_attempt_and_its_rejection_is_accounted_as_two_requests():
    hooks, sent = [], []
    invalid = narrative()
    del invalid["banner"]["limitation"]

    def requester(payload, api_key):
        sent.append(payload["model"])
        if payload["model"] == OPENROUTER_MODEL:
            raise _ModelUnavailableError(404, {"message": f"No endpoints found for {OPENROUTER_MODEL}"})
        return served(invalid, model=OPENROUTER_FALLBACK_MODEL, usage={"cost": 0.09})
    with pytest.raises(NarrativeRejected) as exc:
        synthesize_openrouter(fixture_packet(), api_key="k", requester=requester, on_request=hooks.append)
    assert len(hooks) == 1 and sent == [OPENROUTER_MODEL, OPENROUTER_FALLBACK_MODEL]
    accounted = exc.value.attempt
    assert accounted["attempts"] == 2 and accounted["requested_model"] == OPENROUTER_FALLBACK_MODEL
    assert accounted["fallback_used"] is True and accounted["primary_failure"]["model"] == OPENROUTER_MODEL
    assert accounted["usage"] == {"cost": 0.09} and accounted["resolved_model"] == OPENROUTER_FALLBACK_MODEL


# --- the lookup: this workflow's record on the expected branch, whatever the run concluded -----------------

def test_only_a_record_uploaded_by_this_workflow_on_the_branch_counts(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    name = attempt(TUE, "PREMARKET")
    fork = dict(id=9999, full_name="someone/market-brief")
    runs = {7: own(path=WORKFLOW_PATH, conclusion="cancelled"), 8: own(path=".github/workflows/pages.yml"),
            9: dict(path=WORKFLOW_PATH, conclusion="success", repository=HOME, head_repository=fork),
            10: dict(path=WORKFLOW_PATH, conclusion="success", repository=HOME, head_repository=None),
            11: dict(path=WORKFLOW_PATH, conclusion="success")}
    listed = []

    def serve(artifacts):
        def gh_json(args, runner=None):
            if "/actions/runs/" in args[0]:
                return runs[int(args[0].rsplit("/", 1)[1])]
            listed.append(args[0])
            return {"artifacts": artifacts}
        monkeypatch.setattr(cli, "_gh_json", gh_json)

    def artifact(ident, run, branch="main", named=name, expired=False, **listing_ids):
        # The listing's repository ids are optional in GitHub's schema: absent unless a case sets them.
        return dict(id=ident, name=named, expired=expired, workflow_run=dict(id=run, head_branch=branch,
                                                                            **listing_ids))
    args = SimpleNamespace(repository=None, branch="main")
    # Another branch, another workflow, another name; a fork's pull_request run of its own edit of schedule.yml
    # from a branch it named main, whether the listing omits its repository ids (None == None once passed) or
    # claims they match; a deleted fork's null head; a run with no origin at all. None of them is this
    # repository's attempt: the run details decide, as for continuity restore.
    serve([artifact(1, 7, branch="feature"), artifact(2, 8), artifact(3, 7, named=name + "-X"), "junk",
           artifact(5, 9), artifact(6, 9, repository_id=4242, head_repository_id=4242), artifact(12, 10),
           artifact(13, 11)])
    assert cli.earlier_attempt(args, TUE, "PREMARKET") is None
    assert listed == [f"repos/{REPOSITORY}/actions/artifacts?name={name}&per_page=100"]
    # A cancelled run's record and an expired record still prove the attempt: the upload is the proof.
    serve([artifact(4, 7, expired=True)])
    assert cli.earlier_attempt(args, TUE, "PREMARKET") == "artifact 4 from run 7"
    serve(None)
    with pytest.raises(ValueError, match="could not be listed"):
        cli.earlier_attempt(args, TUE, "PREMARKET")
    # This workspace's own record needs no listing, and off Actions there is no other runner to ask.
    cli.record_attempt(tmp_path, TUE, "PREMARKET", "run-x", {})
    monkeypatch.setattr(cli, "_gh_json", lambda *a, **k: pytest.fail("listed"))
    assert cli.earlier_attempt(args, TUE, "PREMARKET") == f"record {TUE}-PREMARKET.json"
    cli.attempt_path(tmp_path, TUE, "PREMARKET").unlink()
    monkeypatch.delenv("GITHUB_REPOSITORY")
    assert cli.earlier_attempt(args, TUE, "PREMARKET") is None


# --- the workflow: the record is uploaded last and always; the scheduler can list it; one run at a time ------

def test_the_workflow_uploads_every_paid_attempt_last_and_the_scheduler_can_list_it():
    steps = workflow_steps("brief")
    naming = [i for i, step in enumerate(steps) if re.search(r"^        id: attempt$", step["code"], re.M)]
    uploads = [i for i, step in enumerate(steps) if f"name: {cli.ATTEMPT_ARTIFACT}-" in step["code"]]
    # The last two steps: after the pipeline, the continuity upload and every publication step, so this record
    # can never gate them, and always(), so a rejected, failed or cancelled attempt is recorded too.
    assert naming == [len(steps) - 2] and uploads == [len(steps) - 1]
    for step in steps[-2:]:
        assert re.search(r"^        if: always\(\) && ", step["code"], re.M)
    assert "for record in runs/attempts/*.json; do" in steps[-2]["code"]
    assert 'echo "key=$(basename "$record" .json)" >> "$GITHUB_OUTPUT"' in steps[-2]["code"]
    upload = steps[-1]["code"]
    assert "uses: actions/upload-artifact@" in upload
    assert f"name: {cli.ATTEMPT_ARTIFACT}-${{{{ steps.attempt.outputs.key }}}}" in upload
    assert "path: runs/attempts/${{ steps.attempt.outputs.key }}.json" in upload
    scheduler = [step for step in steps if "python -m market_brief schedule --checkpoint" in step["code"]]
    assert scheduler and all("GH_TOKEN: ${{ github.token }}" in step["code"] for step in scheduler)


def test_every_run_that_can_pay_waits_for_the_previous_one_to_finish():
    """The durability boundary: a run's attempt record is uploaded before that run completes, and the workflow's one
    concurrency group starts no other run of it (or of pages.yml) until then."""
    text = (ROOT / WORKFLOW_PATH).read_text()
    assert top_level(text, "concurrency") == ["  group: market-brief-pages", "  cancel-in-progress: false"]
    assert "actions: read" in "\n".join(top_level(text, "permissions"))
    assert "\n    concurrency:" not in text.split("\njobs:\n", 1)[1]  # no job overrides it

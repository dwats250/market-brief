"""R1: accepted continuity survives a failed publication.

The scheduler uploads the continuity bundle before it publishes (push, then Pages). A run's conclusion speaks
for the whole job, publication included, so a push or Pages failure after an accepted upload made that bundle
ineligible and the next runner restored an older one, rolling accepted state back. Eligibility now rests on
the continuity artifact itself: the workflow uploads it only when every earlier step, the pipeline among them,
succeeded, so a run that concluded `failure` after its upload is as eligible as one that concluded `success`.
A rejected synthesis never uploads, a cancelled or unfinished run stays ineligible, and admission of what is
restored is unchanged.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from test_cadence import TUE, Day

from market_brief import cli
from market_brief.continuity import ARTIFACT_NAME, WORKFLOW_PATH, bundle_path, select_artifact
from market_brief.evidence import ROOT

REPOSITORY = "dwats250/market-brief"
FRI = "2026-09-04"
# A workflow run's origin as GitHub returns it: the repository that owns the run and the one its head came from.
HOME = dict(id=4242, full_name=REPOSITORY)


def own(**fields):
    """A workflow run of this repository's own head, as GitHub returns it."""
    return dict(fields, repository=HOME, head_repository=HOME)


class Actions:
    """schedule.yml on main, one fresh runner per run, with `gh` answered the way GitHub answers it.

    A run restores through `continuity-restore`, runs the pipeline as a Cloudflare wake does, archives its run
    folder (always), and uploads the continuity bundle only when the pipeline step succeeded: the step gate that
    `test_the_workflow_uploads_continuity_only_after_acceptance_and_before_publication` pins in the workflow.
    Publication runs after the upload and decides only the run's conclusion. Every runner starts from a checkout
    without the earlier pages or checkpoint markers, as after a failed push: the bundle is its only memory.
    """

    def __init__(self, monkeypatch, tmp_path):
        self.monkeypatch, self.tmp_path = monkeypatch, tmp_path
        self.runs, self.artifacts, self.files, self.days = {}, [], {}, {}
        self.downloads, self.calls, self.published = [], [], []

    def gh(self, argv, **kwargs):
        if argv[:2] == ["gh", "api"]:
            url = urlparse(argv[2])
            if url.path == f"repos/{REPOSITORY}/actions/artifacts":
                wanted = parse_qs(url.query).get("name", [None])[0]
                listed = sorted((a for a in self.artifacts if wanted in (None, a["name"])),
                                key=lambda a: a["created_at"], reverse=True)  # newest first, as GitHub lists
                return SimpleNamespace(returncode=0, stdout=json.dumps({"artifacts": listed}))
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.runs[int(url.path.rsplit("/", 1)[1])]))
        assert argv[:3] == ["gh", "run", "download"] and argv[argv.index("-R") + 1] == REPOSITORY
        key = (int(argv[3]), argv[argv.index("-n") + 1])
        if key not in self.files:
            return SimpleNamespace(returncode=1, stdout="")
        target = Path(argv[argv.index("-D") + 1])
        target.mkdir(parents=True, exist_ok=True)
        (target / "bundle.json").write_bytes(self.files[key])
        self.downloads.append(key[0])
        return SimpleNamespace(returncode=0, stdout="")

    def upload(self, run_id, name, now, minutes, content=None):
        created = datetime.fromisoformat(now).astimezone(timezone.utc) + timedelta(minutes=minutes)
        head = self.runs[run_id]["head_repository"]["id"]
        self.artifacts.append(dict(id=len(self.artifacts) + 1, name=name, expired=False,
                                   created_at=created.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                   workflow_run=dict(id=run_id, head_branch="main", repository_id=HOME["id"],
                                                     head_repository_id=head)))
        if content is not None:
            self.files[run_id, name] = content

    def run(self, now, checkpoint, publication="success", **kwargs):
        """One workflow run. `publication` is what happened after the upload: "success", or "failed" (the push
        was rejected or the Pages deployment failed; either way the run concludes `failure`)."""
        run_id = 1001 + len(self.runs)
        root = self.tmp_path / f"runner-{run_id}"
        root.mkdir()
        self.runs[run_id] = own(status="in_progress", conclusion=None, path=WORKFLOW_PATH, head_branch="main")
        self.monkeypatch.setattr(cli, "RUN_ROOT", root)
        restore = SimpleNamespace(from_file=None, repository=REPOSITORY, branch="main")
        assert cli.restore_continuity(restore, runner=self.gh) == 0
        day = self.days[run_id] = Day(self.monkeypatch, root)
        code = day.run(now, checkpoint, command="schedule", **kwargs)
        self.calls += day.calls
        self.published += day.published
        self.upload(run_id, f"market-brief-run-{checkpoint}-{run_id}", now, 1)
        if code == 0 and bundle_path(root).is_file():
            self.upload(run_id, ARTIFACT_NAME, now, 2, bundle_path(root).read_bytes())
        failed = code != 0 or publication != "success"
        self.runs[run_id].update(status="completed", conclusion="failure" if failed else "success")
        return run_id

    def bundle(self, run_id):
        return json.loads(self.files[run_id, ARTIFACT_NAME])


@pytest.fixture
def actions(monkeypatch, tmp_path):
    return Actions(monkeypatch, tmp_path)


# --- a failed publication no longer rolls accepted state back -------------------------------------------

def test_an_accepted_synthesis_survives_a_failed_publication(actions):
    premarket = actions.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)
    structure = actions.run(f"{TUE}T14:01:00+00:00", "OPEN_30M", publication="failed")
    # A red run whose synthesis was accepted and uploaded before its push or Pages deployment failed.
    assert actions.runs[structure]["conclusion"] == "failure"
    accepted = actions.bundle(structure)["interpretation"]
    assert accepted["origin"]["checkpoint"] == "OPEN_30M"
    refresh = actions.run(f"{TUE}T15:01:00+00:00", "HOURLY_1100")
    assert actions.downloads == [premarket, structure]  # the red run's bundle, not the older green one
    metadata = actions.days[refresh].metadata("HOURLY_1100")
    assert metadata["validation"] == "PASS" and metadata["synthesis"] == dict(kind="refresh", calls=0)
    # The page carries the accepted interpretation, not the premarket one it would have rolled back to.
    assert metadata["interpretation"]["checkpoint"] == "OPEN_30M"
    assert metadata["interpretation"]["content_hash"] == accepted["content_hash"]
    assert metadata["continuity"]["anchors"]["latest"] == accepted["origin"]["run_id"]
    assert actions.calls == ["PREMARKET", "OPEN_30M"]
    # The next green run supersedes the red one; a red run's bundle is restored only while it is the newest.
    actions.run(f"{TUE}T16:01:00+00:00", "HOURLY_1200")
    assert actions.downloads == [premarket, structure, refresh]


def test_a_close_handoff_survives_a_failed_publication_into_the_next_premarket(actions):
    close = actions.run(f"{FRI}T20:01:00+00:00", "CLOSE_1M", publication="failed")
    handoff = actions.bundle(close)["close"]
    assert actions.runs[close]["conclusion"] == "failure" and handoff["session"]["date"] == FRI
    premarket = actions.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)
    assert actions.downloads == [close]
    context = json.loads((actions.days[premarket].folder("PREMARKET") / "analyst_context.json").read_text())
    assert context["prior_state"]["status"] == "available"  # not a cold start
    assert context["prior_state"]["anchors"]["previous_close"]["run_id"] == handoff["origin"]["run_id"]


def test_a_wake_after_a_failed_publication_skips_the_checkpoint_its_bundle_records(actions, capsys):
    """The documented completion proof now also holds when the earlier run's publish failed: a queue-delayed
    wake inside the synthesis window restores that run's bundle and skips, so the analyst is not paid twice."""
    first = actions.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False, publication="failed")
    capsys.readouterr()
    again = actions.run(f"{TUE}T13:11:00+00:00", "PREMARKET", intraday=False)
    assert "SKIP / PREMARKET / already completed" in capsys.readouterr().out
    assert actions.downloads == [first]
    assert actions.calls == ["PREMARKET"] and not actions.days[again].attempts("PREMARKET")


# --- a rejected synthesis never becomes restorable ----------------------------------------------------------

def test_a_rejected_synthesis_uploads_nothing_and_the_last_accepted_state_is_restored(actions):
    premarket = actions.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)
    rejected = actions.run(f"{TUE}T14:01:00+00:00", "OPEN_30M", fail_synthesis=True)
    assert actions.runs[rejected]["conclusion"] == "failure"
    # The run archived its diagnostic (always) and uploaded no continuity: the upload step never ran.
    assert [a["name"] for a in actions.artifacts if a["workflow_run"]["id"] == rejected] == [
        f"market-brief-run-OPEN_30M-{rejected}"]
    # Its runner's bundle is still the restored one: nothing from the rejected response was written to it.
    assert json.loads(bundle_path(actions.days[rejected].root).read_text()) == actions.bundle(premarket)
    refresh = actions.run(f"{TUE}T15:01:00+00:00", "HOURLY_1100")
    assert actions.downloads == [premarket, premarket]
    assert actions.days[refresh].metadata("HOURLY_1100")["interpretation"]["checkpoint"] == "PREMARKET"
    assert actions.calls == ["PREMARKET", "OPEN_30M"]  # the refresh retried nothing


# --- the normal day is unchanged ------------------------------------------------------------------------------

def test_a_green_day_restores_each_run_from_the_one_before_it(actions):
    ids = [actions.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False),
           actions.run(f"{TUE}T13:31:00+00:00", "OPEN_1M"),
           actions.run(f"{TUE}T14:01:00+00:00", "OPEN_30M"),
           actions.run(f"{TUE}T15:01:00+00:00", "HOURLY_1100")]
    assert [actions.runs[i]["conclusion"] for i in ids] == ["success"] * 4
    assert actions.downloads == ids[:-1]
    assert actions.calls == ["PREMARKET", "OPEN_30M"]
    assert actions.published == ["PREMARKET", "OPEN_1M", "OPEN_30M", "HOURLY_1100"]
    last = actions.bundle(ids[-1])
    assert last["interpretation"]["origin"]["checkpoint"] == "OPEN_30M"
    assert last["premarket"]["origin"]["checkpoint"] == "PREMARKET"
    assert last["latest"]["origin"]["checkpoint"] == "HOURLY_1100"


# --- eligibility: the continuity artifact of a run that finished ------------------------------------------------

def artifact(ident, run, created, name=ARTIFACT_NAME, branch="main", expired=False):
    return dict(id=ident, name=name, expired=expired, created_at=created, workflow_run=dict(id=run, head_branch=branch))


@pytest.mark.parametrize(("conclusion", "eligible"), [
    ("success", True),
    ("failure", True),  # accepted and uploaded, then the push or the Pages deployment failed
    ("cancelled", False),  # stopped by hand: unchanged
    ("timed_out", False),
    (None, False),  # unfinished: unchanged
])
def test_a_runs_continuity_artifact_is_eligible_whatever_its_publication_did(conclusion, eligible):
    runs = {1: own(conclusion="success", path=WORKFLOW_PATH), 2: own(conclusion=conclusion, path=WORKFLOW_PATH)}
    older, newer = artifact(10, 1, "2026-09-08T13:03:00Z"), artifact(20, 2, "2026-09-08T14:03:00Z")
    assert select_artifact([older, newer], runs.get)["id"] == (20 if eligible else 10)


@pytest.mark.parametrize("newer", [
    artifact(20, 2, "2026-09-08T14:03:00Z", name="market-brief-run-OPEN_30M-2"),  # a rejected run's archive
    artifact(21, 2, "2026-09-08T14:03:00Z", branch="feature"),
    artifact(22, 3, "2026-09-08T14:03:00Z"),  # another workflow
    artifact(23, 2, "2026-09-08T14:03:00Z", expired=True),
])
def test_a_failed_run_is_eligible_only_through_its_own_continuity_artifact_on_main(newer):
    """Admitting `failure` widened nothing else: name, branch, workflow and expiry are checked as before, so
    older valid continuity is still the one restored when the newest candidate is not eligible."""
    runs = {1: own(conclusion="success", path=WORKFLOW_PATH), 2: own(conclusion="failure", path=WORKFLOW_PATH),
            3: own(conclusion="failure", path=".github/workflows/pages.yml")}
    assert select_artifact([artifact(10, 1, "2026-09-08T13:03:00Z"), newer], runs.get)["id"] == 10


# --- the workflow step gate the artifact's proof rests on ------------------------------------------------------

def workflow_steps(job):
    """One job's steps in schedule.yml, in order, each with its text and its code (the text without comments)."""
    text = (ROOT / WORKFLOW_PATH).read_text()
    body = re.split(r"\n  (?=[^\s#])", text.split(f"\n  {job}:\n", 1)[1], maxsplit=1)[0]
    steps = re.split(r"^      - ", body.split("\n    steps:\n", 1)[1], flags=re.M)[1:]
    return [dict(text=step, code="\n".join(line for line in step.splitlines() if not line.strip().startswith("#")))
            for step in steps]


def test_the_workflow_uploads_continuity_only_after_acceptance_and_before_publication():
    steps = workflow_steps("brief")
    named = re.compile(rf"^          name: {ARTIFACT_NAME}$", re.M)
    uploads = [i for i, step in enumerate(steps) if named.search(step["text"])]
    assert len(uploads) == 1
    index = uploads[0]
    upload = steps[index]
    assert "uses: actions/upload-artifact@" in upload["code"] and "path: runs/continuity/bundle.json" in upload["code"]
    # No status function anywhere in the step, so it inherits success(): it runs only when every earlier step
    # succeeded. No earlier step may fail quietly, and the pipeline's own failure must fail its step.
    assert not re.search(r"\b(always|success|failure|cancelled)\s*\(", upload["code"])
    assert not any("continue-on-error" in step["code"] for step in steps[:index])
    pipeline = [i for i, step in enumerate(steps)
                if re.search(r"python -m market_brief (premarket|schedule)\b", step["code"])]
    assert pipeline and max(pipeline) < index
    for i in pipeline:
        assert not re.search(r"\|\|\s*(true|:)|set \+e|exit 0", steps[i]["code"]), steps[i]["code"]
    # Every publication step comes after it, so its outcome cannot decide whether the upload happened.
    publication = {
        kind: [i for i, step in enumerate(steps) if marker in step["code"]]
        for kind, marker in (("push", "git push"), ("configure", "uses: actions/configure-pages@"),
                             ("pages artifact", "uses: actions/upload-pages-artifact@"),
                             ("deploy", "uses: actions/deploy-pages@"))
    }
    assert all(positions and min(positions) > index for positions in publication.values()), publication

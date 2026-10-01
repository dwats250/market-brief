"""R1: accepted continuity survives a failed publication.

The scheduler uploads the continuity bundle before it publishes (push, then Pages). A run's conclusion speaks
for the whole job, publication included, so a push or Pages failure after an accepted upload made that bundle
ineligible and the next runner restored an older one, rolling accepted state back. Eligibility now rests on
the continuity artifact itself: the workflow uploads it only when every earlier step, the pipeline among them,
succeeded, so a run that concluded `failure` after its upload is as eligible as one that concluded `success`.
A rejected synthesis never uploads, a cancelled or unfinished run stays ineligible, and admission of what is
restored is unchanged.

Pre-freeze: the diagnostic run archive comes after the continuity upload and publication, so its failure can gate
neither; and a restore that cannot finish (the GitHub API, the download, a timeout) stops the run instead of cold
starting it, because that run's cold bundle would replace accepted state for every run after it. Only the absence
of an eligible bundle is a cold start.
"""

import json
import os
import re
import subprocess
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

    A run restores through `continuity-restore`; a restore that fails fails its step, and every later step that
    inherits success() is skipped, so the run collects, synthesizes, uploads and publishes nothing. Otherwise it runs
    the pipeline as a Cloudflare wake does and uploads the continuity bundle only when the pipeline step succeeded:
    the step gate that `test_the_workflow_uploads_continuity_only_after_acceptance_and_before_publication` pins in
    the workflow. Publication runs after the upload and decides only the run's conclusion; the diagnostic archive
    comes after both (always). `broken` names the GitHub calls that fail during a run ("list", "run", "download").
    Every runner starts from a checkout without the earlier pages or checkpoint markers, as after a failed push:
    the bundle is its only memory.
    """

    def __init__(self, monkeypatch, tmp_path):
        self.monkeypatch, self.tmp_path = monkeypatch, tmp_path
        self.runs, self.artifacts, self.files, self.days, self.restores = {}, [], {}, {}, {}
        self.downloads, self.calls, self.published, self.broken = [], [], [], set()

    def gh(self, argv, **kwargs):
        if argv[:2] == ["gh", "api"]:
            url = urlparse(argv[2])
            if url.path == f"repos/{REPOSITORY}/actions/artifacts":
                if "list" in self.broken:
                    return SimpleNamespace(returncode=1, stdout="")
                wanted = parse_qs(url.query).get("name", [None])[0]
                listed = sorted((a for a in self.artifacts if wanted in (None, a["name"])),
                                key=lambda a: a["created_at"], reverse=True)  # newest first, as GitHub lists
                return SimpleNamespace(returncode=0, stdout=json.dumps({"artifacts": listed}))
            if "run" in self.broken:
                return SimpleNamespace(returncode=1, stdout="")
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.runs[int(url.path.rsplit("/", 1)[1])]))
        assert argv[:3] == ["gh", "run", "download"] and argv[argv.index("-R") + 1] == REPOSITORY
        key = (int(argv[3]), argv[argv.index("-n") + 1])
        if key not in self.files or "download" in self.broken:
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
        restored = self.restores[run_id] = cli.restore_continuity(restore, runner=self.gh)
        if restored != 0:
            # The restore step failed: nothing after it runs but the always() steps, which find nothing to archive.
            self.runs[run_id].update(status="completed", conclusion="failure")
            return run_id
        day = self.days[run_id] = Day(self.monkeypatch, root)
        code = day.run(now, checkpoint, command="schedule", **kwargs)
        self.calls += day.calls
        self.published += day.published
        if code == 0 and bundle_path(root).is_file():
            self.upload(run_id, ARTIFACT_NAME, now, 2, bundle_path(root).read_bytes())
        self.upload(run_id, f"market-brief-run-{checkpoint}-{run_id}", now, 3)
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


PUBLICATION = ("git push", "uses: actions/configure-pages@", "uses: actions/upload-pages-artifact@",
               "uses: actions/deploy-pages@")


def test_the_diagnostic_archive_cannot_gate_continuity_or_publication():
    """A failed step turns off every later step that inherits success(). The run archive is diagnostic, so it and
    every other step that runs whatever happened before it (always(), failure(), cancelled()) come after the
    continuity upload and every publication step: an archive failure can decide neither. The archive still runs
    always(), so a rejected synthesis or a failed publication is archived too."""
    steps = workflow_steps("brief")
    gated = [i for i, step in enumerate(steps)
             if re.search(rf"^          name: {ARTIFACT_NAME}$", step["text"], re.M)
             or any(marker in step["code"] for marker in PUBLICATION)]
    assert len(gated) == 1 + len(PUBLICATION)
    independent = [i for i, step in enumerate(steps) if re.search(r"\b(always|failure|cancelled)\s*\(", step["code"])]
    assert independent and min(independent) > max(gated), (independent, gated)
    archive = [i for i, step in enumerate(steps) if "name: market-brief-run-" in step["code"]]
    assert len(archive) == 1 and archive[0] in independent
    assert "uses: actions/upload-artifact@" in steps[archive[0]]["code"]
    assert re.search(r"^        if: always\(\) && ", steps[archive[0]]["code"], re.M)



# --- a run never redeploys a page older than main's (scheduler liveness) ------------------------------------

def test_the_deploy_guard_follows_the_continuity_upload_and_the_push_and_gates_only_pages():
    """A run whose checkout predates main's latest publication (a duplicate or queue-delayed wake that skipped its
    checkpoint) must not redeploy its older page. The guard decides only the Pages steps; it comes after the
    continuity upload and the push, inherits success() and changes nothing, so it can gate neither."""
    steps = workflow_steps("brief")
    guards = [i for i, step in enumerate(steps) if re.search(r"^        id: current$", step["code"], re.M)]
    assert len(guards) == 1
    guard = guards[0]
    upload = next(i for i, step in enumerate(steps)
                  if re.search(rf"^          name: {ARTIFACT_NAME}$", step["text"], re.M))
    push = next(i for i, step in enumerate(steps) if "git push" in step["code"])
    pages = [i for i, step in enumerate(steps) if any(marker in step["code"] for marker in PUBLICATION[1:])]
    assert upload < push < guard < min(pages) and len(pages) == 3
    code = steps[guard]["code"]
    assert not re.search(r"\b(always|success|failure|cancelled)\s*\(", code) and "continue-on-error" not in code
    # A step timeout fails the run (restorable), never the job timeout's cancel (which would make it ineligible).
    assert re.search(r"^        timeout-minutes: [1-5]$", code, re.M)
    assert not any(marker in code for marker in PUBLICATION)
    for i in pages:
        assert re.search(r"^        if: .* && steps\.current\.outputs\.deploy == 'true'$", steps[i]["code"], re.M)


def guard_script():
    [step] = [step for step in workflow_steps("brief") if re.search(r"^        id: current$", step["code"], re.M)]
    script = step["text"].split("        run: |\n", 1)[1]
    lines = []
    for line in script.splitlines():
        if line and not line.startswith("          "):
            break
        lines.append(line[10:])
    return "\n".join(lines) + "\n"


GIT = dict(GIT_AUTHOR_NAME="Market Brief", GIT_AUTHOR_EMAIL="brief@example.invalid", GIT_COMMITTER_NAME="Market Brief",
           GIT_COMMITTER_EMAIL="brief@example.invalid", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")


class Origin:
    """A bare `main` and depth-1 checkouts of it, as actions/checkout leaves a run."""

    def __init__(self, root):
        self.root, self.url = root, (root / "origin.git").as_uri()
        self.git(root, "init", "--quiet", "--bare", "--initial-branch=main", str(root / "origin.git"))
        seed = root / "seed"
        self.git(root, "init", "--quiet", "--initial-branch=main", str(seed))
        (seed / "publish").mkdir()
        (seed / "publish/index.html").write_text("HOURLY_1200 page")
        (seed / "README.md").write_text("Market Brief")
        self.git(seed, "add", ".")
        self.git(seed, "commit", "--quiet", "-m", "Publish HOURLY_1200 brief")
        self.git(seed, "push", "--quiet", self.url, "main")

    def git(self, cwd, *args):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                       env=dict(os.environ, **GIT))

    def checkout(self, name):
        self.git(self.root, "clone", "--quiet", "--depth=1", self.url, name)
        return self.root / name

    def commit(self, clone, path, text, message):
        (clone / path).write_text(text)
        self.git(clone, "add", path)
        self.git(clone, "commit", "--quiet", "-m", message)
        self.git(clone, "push", "--quiet", "origin", "HEAD:refs/heads/main")

    def guard(self, clone, label="HOURLY_1400"):
        output = clone / ".output"
        result = subprocess.run(["bash", "-e", "-c", guard_script()], cwd=clone, capture_output=True, text=True,
                                check=False, env=dict(os.environ, **GIT, LABEL=label, GITHUB_OUTPUT=str(output)))
        return result, output.read_text() if output.exists() else ""


@pytest.fixture
def origin(tmp_path):
    return Origin(tmp_path)


def test_the_run_that_published_deploys_its_own_page(origin):
    run = origin.checkout("run")
    origin.commit(run, "publish/index.html", "HOURLY_1400 page", "Publish HOURLY_1400 brief")
    result, output = origin.guard(run)
    assert result.returncode == 0 and output == "deploy=true\n"


def test_a_current_checkout_with_nothing_new_still_deploys_mains_page(origin):
    result, output = origin.guard(origin.checkout("run"))
    assert result.returncode == 0 and output == "deploy=true\n"


def test_a_stale_checkout_never_redeploys_its_older_page(origin):
    """A second wake for a completed checkpoint, dispatched before the first one's publish, skips its checkpoint
    and must not put the older page back over the newer one."""
    stale = origin.checkout("stale")
    first = origin.checkout("first")
    origin.commit(first, "publish/index.html", "HOURLY_1400 page", "Publish HOURLY_1400 brief")
    result, output = origin.guard(stale)
    assert result.returncode == 0 and output == "deploy=false\n"
    assert result.stdout.strip().splitlines()[-1] == (
        "NOT DEPLOYED / HOURLY_1400 / this checkout's page is behind main; the newer page stays live")


def test_an_unrelated_commit_on_main_does_not_hold_back_a_new_page(origin):
    run = origin.checkout("run")
    origin.commit(run, "publish/index.html", "HOURLY_1400 page", "Publish HOURLY_1400 brief")
    owner = origin.checkout("owner")
    origin.commit(owner, "README.md", "Market Brief, revised", "Revise the README")
    result, output = origin.guard(run)
    assert result.returncode == 0 and output == "deploy=true\n"


def test_a_tag_named_main_never_stands_in_for_the_branch(origin):
    stale = origin.checkout("stale")
    origin.git(stale, "tag", "main")
    origin.git(stale, "push", "--quiet", "origin", "refs/tags/main")
    first = origin.checkout("first")
    origin.commit(first, "publish/index.html", "HOURLY_1400 page", "Publish HOURLY_1400 brief")
    result, output = origin.guard(stale)
    assert result.returncode == 0 and output == "deploy=false\n"


def test_a_file_the_commit_does_not_hold_is_never_deployed_unseen(origin):
    """Pages uploads the working tree of publish/, so anything there that main does not hold blocks the deploy."""
    run = origin.checkout("run")
    (run / "publish/extra.html").write_text("not on main")
    result, output = origin.guard(run)
    assert result.returncode == 0 and output == "deploy=false\n"


def test_a_guard_that_cannot_see_main_deploys_nothing(origin):
    run = origin.checkout("run")
    origin.git(run, "remote", "set-url", "origin", (origin.root / "missing.git").as_uri())
    result, output = origin.guard(run)
    assert result.returncode != 0 and output == ""


def test_a_skipping_run_reuploads_the_bundle_it_restored_unchanged(actions, capsys):
    """A second wake for a completed checkpoint advances nothing: what it uploads is what it restored."""
    first = actions.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)
    capsys.readouterr()
    again = actions.run(f"{TUE}T13:03:10+00:00", "PREMARKET", intraday=False)
    assert "SKIP / PREMARKET / already completed" in capsys.readouterr().out
    assert actions.bundle(again) == actions.bundle(first)
    assert actions.calls == ["PREMARKET"] and actions.published == ["PREMARKET"]

# --- a restore that cannot finish stops the run; only absence is a cold start -----------------------------------

def test_no_accepted_artifact_is_a_cold_start_and_the_run_goes_on(actions, capsys):
    first = actions.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)
    assert actions.restores[first] == 0
    assert "Continuity: cold start; no accepted bundle from a main-branch run." in capsys.readouterr().out
    assert actions.calls == ["PREMARKET"] and actions.published == ["PREMARKET"]
    assert actions.bundle(first)["interpretation"]["origin"]["checkpoint"] == "PREMARKET"


def test_a_foreign_artifact_alone_is_still_absence(actions):
    """Ineligible is not an error: a fork's same-named artifact is passed over and the run cold starts."""
    actions.runs[999] = dict(own(conclusion="success", path=WORKFLOW_PATH, head_branch="main"),
                             head_repository=dict(id=77, full_name="someone/fork"))
    actions.upload(999, ARTIFACT_NAME, f"{TUE}T12:00:00+00:00", 0, b"{}")
    first = actions.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)
    assert actions.restores[first] == 0 and actions.downloads == [] and actions.calls == ["PREMARKET"]


@pytest.mark.parametrize("broken", ["list", "run", "download"])
def test_a_restore_that_cannot_finish_stops_the_run_and_accepted_state_survives(actions, capsys, broken):
    premarket = actions.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)
    structure = actions.run(f"{TUE}T14:01:00+00:00", "OPEN_30M")
    capsys.readouterr()
    actions.broken = {broken}
    failed = actions.run(f"{TUE}T15:01:00+00:00", "HOURLY_1100")
    actions.broken = set()
    assert actions.restores[failed] == 2 and actions.runs[failed]["conclusion"] == "failure"
    assert "Continuity: restore failed (ValueError); stopping before collection." in capsys.readouterr().out
    # Nothing after the restore ran: no collection, no bundle, no page, no artifact of any kind.
    assert failed not in actions.days and not bundle_path(actions.tmp_path / f"runner-{failed}").exists()
    assert [a["name"] for a in actions.artifacts if a["workflow_run"]["id"] == failed] == []
    assert actions.published == ["PREMARKET", "OPEN_30M"]
    # So no cold bundle replaced accepted state: the next run restores the structure update's.
    refresh = actions.run(f"{TUE}T16:01:00+00:00", "HOURLY_1200")
    assert actions.downloads == [premarket, structure]
    metadata = actions.days[refresh].metadata("HOURLY_1200")
    assert metadata["interpretation"]["checkpoint"] == "OPEN_30M"
    assert metadata["continuity"]["anchors"]["latest"] == actions.bundle(structure)["latest"]["origin"]["run_id"]
    assert actions.calls == ["PREMARKET", "OPEN_30M"]


def answering(listing="", lookup="", download=None, raises=None):
    """A `gh` runner: the artifact listing and the run lookup print these, the download writes `download` as
    bundle.json (or nothing, exiting 0), and `raises`, when given, is raised by every call."""
    def runner(argv, **kwargs):
        if raises is not None:
            raise raises
        if argv[:2] == ["gh", "api"]:
            return SimpleNamespace(returncode=0, stdout=listing if "artifacts" in argv[2] else lookup)
        if download is not None:
            target = Path(argv[argv.index("-D") + 1])
            target.mkdir(parents=True, exist_ok=True)
            (target / "bundle.json").write_text(download)
        return SimpleNamespace(returncode=0, stdout="")
    return runner


LISTED = json.dumps({"artifacts": [artifact(7, 99, "2026-09-08T14:03:00Z")]})
OWN_RUN = json.dumps(own(conclusion="success", path=WORKFLOW_PATH))


@pytest.mark.parametrize("runner", [
    answering(raises=subprocess.TimeoutExpired(["gh"], 60)),  # the API or the download timed out
    answering(raises=FileNotFoundError("gh")),  # the CLI is not there
    answering(listing="<html>rate limited</html>"),  # a listing that is not the API's JSON
    answering(listing="[]"),  # nor a JSON object
    answering(listing=LISTED, lookup="{"),  # a run lookup that is not JSON
    answering(listing=LISTED, lookup=OWN_RUN),  # a download that exits 0 and writes no bundle
], ids=["timeout", "no-gh", "unreadable-listing", "listing-not-an-object", "unreadable-run", "empty-download"])
def test_every_restore_error_returns_non_zero_and_installs_nothing(tmp_path, monkeypatch, runner):
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    args = SimpleNamespace(from_file=None, repository=REPOSITORY, branch="main")
    assert cli.restore_continuity(args, runner=runner) == 2
    assert not bundle_path(tmp_path).exists()


def test_a_successful_restore_is_unchanged(tmp_path, monkeypatch, actions):
    premarket = actions.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    args = SimpleNamespace(from_file=None, repository=REPOSITORY, branch="main")
    runner = answering(listing=json.dumps({"artifacts": actions.artifacts}), lookup=OWN_RUN,
                       download=actions.files[premarket, ARTIFACT_NAME].decode())
    assert cli.restore_continuity(args, runner=runner) == 0
    assert json.loads(bundle_path(tmp_path).read_text()) == actions.bundle(premarket)


def test_a_failed_restore_stops_the_workflow_before_collection():
    steps = workflow_steps("brief")
    restore = [i for i, step in enumerate(steps) if "python -m market_brief continuity-restore" in step["code"]]
    pipeline = [i for i, step in enumerate(steps)
                if re.search(r"python -m market_brief (premarket|schedule)\b", step["code"])]
    assert len(restore) == 1 and pipeline and restore[0] < min(pipeline)
    # Its exit status is the step's: nothing swallows it, and nothing after it that collects, uploads continuity
    # or publishes runs whatever came before (they all inherit success()).
    code = steps[restore[0]]["code"]
    assert "continue-on-error" not in code and not re.search(r"\|\|\s*(true|:)|set \+e|exit 0", code)
    gated = [*pipeline, *(i for i, step in enumerate(steps)
                          if re.search(rf"^          name: {ARTIFACT_NAME}$", step["text"], re.M)
                          or any(marker in step["code"] for marker in PUBLICATION))]
    assert all(not re.search(r"\b(always|failure|cancelled)\s*\(", steps[i]["code"]) for i in gated)

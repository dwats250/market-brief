"""S1: continuity restore accepts artifacts only from this repository's own workflow runs.

A fork's pull_request run executes the pull request's own edit of schedule.yml, so it can upload an artifact named
`market-brief-continuity` from a branch it named main, under this workflow's path, with a success or failure
conclusion. The bundle's hashes are public content digests and its origin fields are self-declared, so a forged
bundle passes every content check, and its interpretation would be rendered by the next refresh. Eligibility now
also requires the run's head repository to be the repository that owns the run, read from the run's two origin
fields; GitHub's schema marks both required, but the head can come back null (a deleted fork), so anything
missing, null or malformed is not eligible.
"""

import json

import pytest
from test_cadence import TUE
from test_continuity_restore import FRI, HOME, Actions, artifact, own

from market_brief.continuity import ARTIFACT_NAME, WORKFLOW_PATH, select_artifact
from market_brief.evidence import digest

FORK = dict(id=9999, full_name="someone/market-brief")
FORGED = "forged-by-a-fork"


class Forks(Actions):
    """R1's model of schedule.yml on main, plus a fork's pull_request run of its own edit of the workflow."""

    def forge(self, now, content, conclusion="success"):
        """A completed fork run on a branch it named main, under this workflow's path, that uploaded `content` as
        the continuity artifact."""
        run_id = 1001 + len(self.runs)
        self.runs[run_id] = dict(status="completed", conclusion=conclusion, path=WORKFLOW_PATH, head_branch="main",
                                 event="pull_request", repository=HOME, head_repository=FORK)
        self.upload(run_id, ARTIFACT_NAME, now, 3, content)
        return run_id

    def prior_state(self, run_id, checkpoint="PREMARKET"):
        folder = self.days[run_id].folder(checkpoint)
        return json.loads((folder / "analyst_context.json").read_text())["prior_state"]


def forged(bundle):
    """A bundle that passes every content check: each record relabeled as the fork's and re-hashed, since the content
    hash is a public digest, not a signature."""
    value = json.loads(json.dumps(bundle))
    for slot in ("close", "premarket", "latest", "interpretation"):
        record = value.get(slot)
        if record:
            record["origin"]["run_id"] = FORGED
            record["content_hash"] = digest({k: v for k, v in record.items() if k != "content_hash"})
    return json.dumps(value).encode()


@pytest.fixture
def actions(monkeypatch, tmp_path):
    return Forks(monkeypatch, tmp_path)


# --- this repository's own runs restore as before -------------------------------------------------------------

@pytest.mark.parametrize(("publication", "conclusion"), [
    ("success", "success"),
    ("failed", "failure"),  # R1: accepted and uploaded, then the push or the Pages deployment failed
])
def test_this_repositorys_own_run_restores(actions, publication, conclusion):
    close = actions.run(f"{FRI}T20:01:00+00:00", "CLOSE_1M", publication=publication)
    assert actions.runs[close]["conclusion"] == conclusion
    handoff = actions.bundle(close)["close"]
    premarket = actions.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)
    assert actions.downloads == [close]
    prior = actions.prior_state(premarket)
    assert prior["status"] == "available"
    assert prior["anchors"]["previous_close"]["run_id"] == handoff["origin"]["run_id"]


# --- a foreign head repository never qualifies ---------------------------------------------------------------

@pytest.mark.parametrize("conclusion", ["success", "failure"])
def test_a_fork_artifact_on_a_branch_named_main_is_never_restored(actions, monkeypatch, tmp_path, conclusion):
    """The fork's artifact is the only candidate: right name, branch `main`, this workflow's path, an eligible
    conclusion, and a bundle that passes every content check. It is never downloaded, so the premarket cold
    starts."""
    (tmp_path / "donor").mkdir()
    donor = Actions(monkeypatch, tmp_path / "donor")
    genuine = donor.run(f"{FRI}T20:01:00+00:00", "CLOSE_1M")
    actions.forge(f"{FRI}T20:30:00+00:00", forged(donor.bundle(genuine)), conclusion=conclusion)
    premarket = actions.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)
    assert actions.downloads == []
    prior = actions.prior_state(premarket)
    assert prior["status"] == "cold_start" and FORGED not in json.dumps(prior)
    assert FORGED not in actions.days[premarket].page("PREMARKET")


def test_a_foreign_artifact_cannot_outrank_an_older_valid_one(actions):
    genuine = actions.run(f"{FRI}T20:01:00+00:00", "CLOSE_1M")
    handoff = actions.bundle(genuine)["close"]
    fork = actions.forge(f"{FRI}T20:40:00+00:00", forged(actions.bundle(genuine)))
    listed = sorted((a for a in actions.artifacts if a["name"] == ARTIFACT_NAME), key=lambda a: a["created_at"])
    assert [a["workflow_run"]["id"] for a in listed] == [genuine, fork]  # the fork's is the newest
    premarket = actions.run(f"{TUE}T13:01:00+00:00", "PREMARKET", intraday=False)
    assert actions.downloads == [genuine]
    prior = actions.prior_state(premarket)
    assert prior["anchors"]["previous_close"]["run_id"] == handoff["origin"]["run_id"] != FORGED


# --- origin that is missing, malformed or foreign is not proof -------------------------------------------------

@pytest.mark.parametrize("origin", [
    dict(),
    dict(repository=HOME),
    dict(head_repository=HOME),
    dict(repository=None, head_repository=None),
    dict(repository=HOME, head_repository=None),  # a deleted fork: the owner is known, the head is gone
    dict(repository={}, head_repository={}),  # no ids on either side: None == None proves nothing
    dict(repository={"id": "4242"}, head_repository={"id": "4242"}),
    dict(repository={"id": True}, head_repository={"id": True}),
    dict(repository={"id": 4242.0}, head_repository={"id": 4242.0}),
    dict(repository=HOME, head_repository=FORK),
], ids=["no-origin", "no-head", "no-owner", "null", "deleted-fork-head", "no-ids", "string-ids", "bool-ids",
        "float-ids", "fork"])
def test_missing_ambiguous_or_foreign_origin_is_never_eligible(origin):
    """GitHub's schema marks `repository` and `head_repository` (each with an integer id) required, but a deleted
    fork comes back with a null head, so nothing gives missing, null or malformed origin a stronger reading than
    'unproven': it fails closed, to the older valid artifact when there is one and to a cold start when there is
    not."""
    runs = {1: own(conclusion="success", path=WORKFLOW_PATH), 2: dict(conclusion="success", path=WORKFLOW_PATH,
                                                                       **origin)}
    older, newer = artifact(10, 1, "2026-09-08T13:03:00Z"), artifact(20, 2, "2026-09-08T14:03:00Z")
    assert select_artifact([older, newer], runs.get)["id"] == 10
    assert select_artifact([newer], runs.get) is None


def test_the_newest_run_of_this_repository_is_still_chosen():
    """The predicate reads only the run lookup already made; the listing's optional origin fields are not needed."""
    runs = {1: own(conclusion="success", path=WORKFLOW_PATH), 2: own(conclusion="failure", path=WORKFLOW_PATH)}
    older, newer = artifact(10, 1, "2026-09-08T13:03:00Z"), artifact(20, 2, "2026-09-08T14:03:00Z")
    assert "repository_id" not in newer["workflow_run"]
    assert select_artifact([older, newer], runs.get)["id"] == 20

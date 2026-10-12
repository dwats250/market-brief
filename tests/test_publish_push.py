"""The publish step retries only its own commit, never forces, stops if main moved, and fails safely.

2026-10-12 OPEN_1M: the commit was made, `git push` was refused with "fatal error in commit_refs" and main did not
move; the next run's push, unchanged, landed. The step is run here against a bare origin whose pre-receive hook
refuses the first pushes, which GitHub reports the same way ("[remote rejected] main -> main").
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/schedule.yml"
GIT = dict(GIT_AUTHOR_NAME="Market Brief", GIT_AUTHOR_EMAIL="brief@example.invalid", GIT_COMMITTER_NAME="Market Brief",
           GIT_COMMITTER_EMAIL="brief@example.invalid", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")


def publish_step():
    text = WORKFLOW.read_text()
    return "      - name: Publish latest successful brief" + text.split(
        "      - name: Publish latest successful brief", 1)[1].split("\n      - name:", 1)[0]


def publish_script():
    script = publish_step().split("        run: |\n", 1)[1]
    lines = [line[10:] for line in script.splitlines() if line.startswith("          ") or not line]
    # The label is a workflow expression; sleeping is skipped so the test takes no wall time.
    return "sleep() { :; }\n" + "\n".join(lines).replace("${{ steps.checkpoint.outputs.label }}", "OPEN_1M") + "\n"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env=dict(os.environ, **GIT)).stdout.strip()


@pytest.fixture
def remote(tmp_path):
    """A bare main holding a premarket page, a depth-1 checkout of it, and a hook that refuses the first N pushes."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--quiet", "--bare", "--initial-branch=main", str(origin))
    seed = tmp_path / "seed"
    git(tmp_path, "init", "--quiet", "--initial-branch=main", str(seed))
    (seed / "publish").mkdir()
    (seed / "publish/index.html").write_text("PREMARKET page")
    git(seed, "add", ".")
    git(seed, "commit", "--quiet", "-m", "Publish PREMARKET brief")
    git(seed, "push", "--quiet", origin.as_uri(), "main")
    run = tmp_path / "run"
    git(tmp_path, "clone", "--quiet", "--depth=1", origin.as_uri(), str(run))

    def refuse(times):
        hook = origin / "hooks/pre-receive"
        count = tmp_path / "refusals"
        count.write_text("0")
        hook.write_text(f"#!/bin/sh\nn=$(cat {count}); echo $((n + 1)) > {count}\n"
                        f"if [ \"$n\" -lt {times} ]; then echo 'fatal error in commit_refs'; exit 1; fi\n")
        hook.chmod(0o755)
        return count

    return origin, run, refuse


def publish(run, page, prelude=""):
    if page is not None:
        (run / "publish/index.html").write_text(page)
    # GitHub's default `run` shell is `bash -e {0}`.
    return subprocess.run(["bash", "-e", "-c", prelude + publish_script()], cwd=run, capture_output=True, text=True,
                          check=False, env=dict(os.environ, **GIT))


def test_one_refused_push_is_retried_and_main_gets_the_page(remote):
    origin, run, refuse = remote
    attempts = refuse(1)
    result = publish(run, "OPEN_1M page")
    assert result.returncode == 0, result.stderr
    assert attempts.read_text().strip() == "2"
    assert git(origin, "log", "-1", "--format=%s", "main") == "Publish OPEN_1M brief"
    assert git(origin, "show", "main:publish/index.html") == "OPEN_1M page"


def test_a_refusal_that_persists_fails_the_step_and_main_keeps_its_page(remote):
    origin, run, refuse = remote
    attempts = refuse(99)
    result = publish(run, "OPEN_1M page")
    assert result.returncode != 0
    assert attempts.read_text().strip() == "3"
    assert "NOT PUBLISHED" in result.stdout
    assert git(origin, "show", "main:publish/index.html") == "PREMARKET page"


def test_main_moved_after_the_checkout_stops_at_once_and_is_never_overwritten(remote, tmp_path):
    origin, run, refuse = remote
    other = tmp_path / "other"
    git(tmp_path, "clone", "--quiet", origin.as_uri(), str(other))
    (other / "publish/index.html").write_text("OPEN_30M page")
    git(other, "commit", "--quiet", "-am", "Publish OPEN_30M brief")
    git(other, "push", "--quiet", "origin", "main")
    attempts = refuse(0)  # the hook refuses nothing: the remote rejects this push only because main moved
    result = publish(run, "OPEN_1M page")
    assert result.returncode != 0
    assert "main moved" in result.stdout and attempts.read_text().strip() == "0"  # one push, refused client-side
    assert git(origin, "show", "main:publish/index.html") == "OPEN_30M page"


def test_a_refusal_reported_after_main_already_took_the_commit_counts_as_published(remote):
    """If GitHub moved the ref and still reported failure, main is this run's commit: published, not retried."""
    origin, run, refuse = remote
    refuse(0)
    mark = run.parent / "reported"
    prelude = (f'git() {{ if [ "$1" = push ] && [ ! -e {mark} ]; then touch {mark}; command git "$@"; return 1; fi; '
               'command git "$@"; }\n')
    result = publish(run, "OPEN_1M page", prelude)
    assert result.returncode == 0 and "despite the refusal" in result.stdout
    assert git(origin, "show", "main:publish/index.html") == "OPEN_1M page"


def test_the_step_never_forces_and_pushes_only_this_runs_commit():
    script = publish_script()
    assert re.findall(r"git push\b[^;]*", script) == ["git push"]  # bare: the branch it checked out, no refspec
    assert not re.search(r"--force|\s-f\b|\+main|\+HEAD|reset --hard|rebase|pull", script)


def test_nothing_to_publish_pushes_nothing(remote):
    origin, run, refuse = remote
    attempts = refuse(99)
    result = publish(run, None)
    assert result.returncode == 0 and "No publishable changes" in result.stdout
    assert attempts.read_text().strip() == "0"


def test_the_publish_step_has_its_own_timeout_so_a_hung_push_fails_rather_than_cancels():
    """A cancelled run's continuity bundle is not restorable; a failed one's is (continuity.RESTORABLE_CONCLUSIONS)."""
    assert re.search(r"^        timeout-minutes: [1-5]$", publish_step(), re.M)

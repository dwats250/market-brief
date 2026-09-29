"""Tests run on every pull request and on every substantive push to main.

The scheduler's own publication commits change only the generated page, so a push whose whole diff is that
page is ignored; a push that changes anything else runs the suite. The ignore list is pinned to exactly the
paths the scheduler stages, so it can never hide a source, test, template or configuration change.
"""

from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).parents[1] / ".github/workflows"


def top_level(text, key):
    """The lines of one top-level workflow key, comments dropped, exactly as written."""
    lines = text.splitlines()
    body = []
    for line in lines[lines.index(f"{key}:") + 1:]:
        if line and not line.startswith(" "):
            break
        if line.strip() and not line.strip().startswith("#"):
            body.append(line)
    return body


def paths_ignore():
    trigger = top_level((WORKFLOWS / "tests.yml").read_text(), "on")
    ignored = []
    for line in trigger[trigger.index("    paths-ignore:") + 1:]:
        if not line.startswith("      - "):
            break
        ignored.append(line.removeprefix("      - ").strip('"'))
    return ignored


def test_tests_run_on_every_pull_request_and_on_pushes_to_main():
    assert top_level((WORKFLOWS / "tests.yml").read_text(), "on") == [
        "  pull_request:",
        "  push:",
        "    branches: [main]",
        "    paths-ignore:",
        '      - "publish/index.html"',
        "  workflow_dispatch:",
    ]


def test_the_ignore_list_is_exactly_what_the_scheduler_commits():
    schedule = (WORKFLOWS / "schedule.yml").read_text()
    step = schedule.split("- name: Publish latest successful brief", 1)[1].split("\n      - name:", 1)[0]
    commands = [line.strip() for line in step.splitlines()]
    staged = [command.removeprefix("git add ") for command in commands if command.startswith("git add")]
    assert staged == paths_ignore() == ["publish/index.html"]
    # The commit carries only what was staged (no -a / --all).
    assert [c for c in commands if c.startswith("git commit")] == [
        'git commit -m "Publish ${{ steps.checkpoint.outputs.label }} brief"']
    # No other workflow writes to the repository.
    writers = [path.name for path in sorted(WORKFLOWS.glob("*.yml"))
               if any(command in path.read_text() for command in ("git push", "git commit", "git add"))]
    assert writers == ["schedule.yml"]


def push_runs_tests(changed, ignored):
    """GitHub's paths-ignore rule for a push: it runs unless every changed path is ignored."""
    assert not any(ch in pattern for pattern in ignored for ch in "*?[]!+"), "literal paths only"
    return any(path not in ignored for path in changed)


@pytest.mark.parametrize(("commit", "changed", "runs"), [
    ("618168b scheduler: Publish HOURLY_1200 brief", ["publish/index.html"], False),
    ("020ce8b direct push to main", ["tests/test_rates_module.py"], True),
    ("282726a direct push with the page", ["publish/index.html", "src/market_brief/render.py",
                                           "templates/brief.html.j2", "tests/test_rates_module.py"], True),
    ("ae7ffce squash merge of #35", ["templates/brief.html.j2"], True),
    ("368c4af merge of #38 (abridged)", ["DECISIONS.md", "src/market_brief/render.py"], True),
])
def test_a_publication_only_commit_is_skipped_and_a_substantive_push_runs(commit, changed, runs):
    assert push_runs_tests(changed, paths_ignore()) is runs, commit

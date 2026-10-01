"""Scheduler liveness: the Cloudflare Worker clears one never-started wake that has stalled its concurrency group.

On 2026-09-30 the HOURLY_1300 wake (#203) took the group and its `brief` job never received a runner; every later
wake waited behind it and was replaced by the next, so the rest of the day never published. Before each dispatch the
Worker now inspects the group's unfinished runs, and normal-cancels the run holding the group only when the evidence
shows it is this repository's own Cloudflare wake on main, that no job of it ever had a runner or a step, that a
later checkpoint has superseded it (it is at least the synthesis window, twenty minutes, old: at the next wake), and
that the queue is live (the run before it got a runner within those twenty minutes). Anything unknown, malformed or
ambiguous leaves every run alone and logs why; the Worker always dispatches.

These tests run the real `cloudflare/src/index.js` under Node against a fake GitHub that serves the run, job and
dispatch endpoints from a state the test gives it. Every GitHub answer is scripted; nothing leaves the machine.
"""

import copy
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from test_cloudflare_scheduler import wake_minutes

from market_brief.schedule import CHECKPOINTS, scheduled_checkpoint

ROOT = Path(__file__).parents[1]
REPOSITORY = "dwats250/market-brief"
HOME = dict(id=4242, full_name=REPOSITORY)
TOKEN = "test-dispatch-token-never-logged"
ENV = dict(GITHUB_REPOSITORY=REPOSITORY, GH_DISPATCH_TOKEN=TOKEN)
SCHEDULE = ".github/workflows/schedule.yml"
PAGES = ".github/workflows/pages.yml"
WAKE = "Cloudflare wake-up"
MANUAL = "Scheduled Market Brief"
PLAIN_WAKEUP = {"ref": "main", "inputs": {"cloudflare_wakeup": "true"}}

# The fake GitHub. A scenario line on stdin is {scheduledTime, env, state, overrides}; the answer line on stdout holds
# every request the Worker made, its console output, the error it threw and the state after its cancels.
HARNESS = r"""
import { createInterface } from "node:readline";

const worker = (await import(process.env.WORKER_MODULE)).default;
const realTimeout = AbortSignal.timeout.bind(AbortSignal);
const realSetTimeout = globalThis.setTimeout;

function respond(status, json, text) {
  if (status === 204) return new Response(null, { status });
  const body = text !== undefined ? text : JSON.stringify(json === undefined ? {} : json);
  return new Response(body, { status, headers: { "content-type": "application/json" } });
}

function hang(signal) {
  return new Promise((resolve, reject) => {
    if (signal) signal.addEventListener("abort", () => reject(signal.reason));
  });
}

const view = (run) => Object.fromEntries(Object.entries(run).filter(([key]) => key !== "jobs" && !key.startsWith("_")));
const newestFirst = (a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : b.id - a.id);

function serve(state, method, path, query, body, signal) {
  const find = (id) => state.runs.find((run) => String(run.id) === id);
  let m;
  if (method === "GET" && path === "actions/runs") {
    const listed = state.runs.filter((run) => run.status === query.status).sort(newestFirst);
    return respond(200, { total_count: listed.length,
                          workflow_runs: listed.slice(0, Number(query.per_page || 30)).map(view) });
  }
  if (method === "GET" && (m = path.match(/^actions\/runs\/(\d+)$/))) {
    const run = find(m[1]);
    return run ? respond(200, view(run)) : respond(404, { message: "Not Found" });
  }
  if (method === "GET" && (m = path.match(/^actions\/runs\/(\d+)\/jobs$/))) {
    const run = find(m[1]);
    if (!run) return respond(404, { message: "Not Found" });
    return respond(200, { total_count: run.jobs.length, jobs: run.jobs });
  }
  if (method === "GET" && (m = path.match(/^actions\/workflows\/([^/]+)\/runs$/))) {
    let listed = state.runs.filter((run) => run.path === `.github/workflows/${m[1]}`);
    if (query.branch) listed = listed.filter((run) => run.head_branch === query.branch);
    if (query.created) {
      if (!query.created.startsWith("<")) return respond(422, { message: "unsupported created filter" });
      listed = listed.filter((run) => run.created_at < query.created.slice(1));
    }
    listed.sort(newestFirst);
    return respond(200, { total_count: listed.length,
                          workflow_runs: listed.slice(0, Number(query.per_page || 30)).map(view) });
  }
  if (method === "POST" && (m = path.match(/^actions\/runs\/(\d+)\/cancel$/))) {
    const run = find(m[1]);
    if (!run) return respond(404, { message: "Not Found" });
    const behaviour = run._cancel || "effective";
    if (behaviour === "hang") return hang(signal);
    if (behaviour === "refused") return respond(403, { message: "Resource not accessible by personal access token" });
    if (run.status === "completed") return respond(409, { message: "Cannot cancel a workflow run that is completed." });
    if (behaviour === "ignored") return respond(202, {});
    if (behaviour === "fails502") return respond(502, { message: "Bad Gateway" });
    if (behaviour === "runner_first") {
      // A runner reached the job after the Worker's re-read and before GitHub processed the cancel.
      Object.assign(run.jobs[0], { status: "in_progress", runner_id: 1000005399,
                                   runner_name: "GitHub Actions 1000005399",
                                   steps: [{ name: "Set up job", status: "in_progress", number: 1 }] });
    }
    run.status = "completed";
    run.conclusion = "cancelled";
    for (const job of run.jobs) {
      if (job.status !== "completed") { job.status = "completed"; job.conclusion = "cancelled"; }
    }
    return behaviour === "lands502" ? respond(502, { message: "Bad Gateway" }) : respond(202, {});
  }
  if (method === "POST" && path === "actions/workflows/schedule.yml/dispatches") {
    if (state.reject_note && body && body.inputs && "liveness_recovery" in body.inputs) {
      return respond(422, { message: "Unexpected inputs provided: [\"liveness_recovery\"]" });
    }
    return respond(state.dispatch_status || 204, { message: "dispatch answer" });
  }
  return null;
}

async function invoke(scenario) {
  const state = scenario.state;
  const requests = [], logs = [], delays = [], unmatched = [];
  const overrides = (scenario.overrides || []).map((override) => ({ ...override, seen: 0 }));
  AbortSignal.timeout = (ms) => { delays.push(["timeout", ms]); return realTimeout(Math.min(ms, 40)); };
  globalThis.setTimeout = (fn, ms, ...rest) => { delays.push(["wait", ms]); return realSetTimeout(fn, 0, ...rest); };
  for (const level of ["log", "info", "warn", "error"]) {
    console[level] = (...parts) => logs.push(parts.map(String).join(" "));
  }
  globalThis.fetch = async (input, init = {}) => {
    const url = new URL(String(input));
    const method = (init.method || "GET").toUpperCase();
    const headers = Object.fromEntries(Object.entries(init.headers || {}).map(([k, v]) => [k.toLowerCase(), v]));
    const body = init.body === undefined ? null : JSON.parse(init.body);
    const prefix = `/repos/${state.repository}/`;
    const path = url.pathname.startsWith(prefix) ? url.pathname.slice(prefix.length) : url.pathname;
    const query = Object.fromEntries(url.searchParams);
    requests.push({ method, host: url.host, path, query, body, authorization: headers.authorization || null,
                    timed: Boolean(init.signal) });
    for (const override of overrides) {
      if (override.method !== method || !new RegExp(override.path).test(path)) continue;
      override.seen += 1;
      if (override.seen <= (override.skip || 0)) continue;
      if (override.times !== undefined && override.seen - (override.skip || 0) > override.times) continue;
      if (override.hang) return hang(init.signal);
      if (override.delay) await new Promise((resolve) => realSetTimeout(resolve, override.delay));
      return respond(override.status || 200, override.json, override.text);
    }
    const answer = serve(state, method, path, query, body, init.signal);
    if (answer) return answer;
    unmatched.push(`${method} ${path}`);
    return respond(599, { message: "unscripted request" });
  };
  let error = null;
  try {
    await worker.scheduled({ scheduledTime: Date.parse(scenario.scheduledTime), cron: "1 13-21 * * MON-FRI",
                             noRetry() {} }, scenario.env, { waitUntil() {} });
  } catch (caught) {
    error = String((caught && caught.message) || caught);
  }
  return { requests, logs, error, delays, unmatched, state };
}

for await (const line of createInterface({ input: process.stdin })) {
  if (line.trim()) process.stdout.write(JSON.stringify(await invoke(JSON.parse(line))) + "\n");
}
"""


class Result:
    def __init__(self, value):
        self.requests, self.logs, self.error = value["requests"], value["logs"], value["error"]
        self.delays, self.unmatched, self.state = value["delays"], value["unmatched"], value["state"]

    @property
    def cancels(self):
        return [int(r["path"].split("/")[2]) for r in self.requests
                if r["method"] == "POST" and r["path"].endswith("/cancel")]

    @property
    def dispatches(self):
        return [r["body"] for r in self.requests if r["method"] == "POST" and r["path"].endswith("/dispatches")]

    @property
    def liveness(self):
        entries = []
        for line in self.logs:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and "liveness" in entry:
                entries.append(entry)
        return entries

    def outcome(self, kind):
        return [entry for entry in self.liveness if entry["liveness"] == kind]

    def run(self, run_id):
        return next(run for run in self.state["runs"] if run["id"] == run_id)


class Worker:
    """The real Worker module in one Node process; each tick is one scheduled() invocation against a fresh state."""

    def __init__(self, tmp_path):
        node = shutil.which("node")
        if not node:
            if os.environ.get("CI"):
                pytest.fail("Node.js is required in CI to test the Cloudflare Worker")
            pytest.skip("Node.js is not installed")
        module = tmp_path / "worker.mjs"  # an ES module on any Node version, whatever package.json may say
        shutil.copyfile(ROOT / "cloudflare/src/index.js", module)
        self.process = subprocess.Popen([node, "--input-type=module", "-e", HARNESS], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                        env=dict(os.environ, WORKER_MODULE=module.as_uri()))

    def tick(self, at, runs=(), *, overrides=(), env=ENV, **state):
        scenario = dict(scheduledTime=iso(at), env=env, overrides=list(overrides),
                        state=dict(state, repository=REPOSITORY, runs=copy.deepcopy(list(runs))))
        self.process.stdin.write(json.dumps(scenario) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise AssertionError(self.process.stderr.read())
        result = Result(json.loads(line))
        assert result.unmatched == [], result.unmatched
        return result

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=10)


@pytest.fixture(scope="module")
def worker(tmp_path_factory):
    session = Worker(tmp_path_factory.mktemp("worker"))  # one Node process; every tick gets a fresh state
    yield session
    session.close()


def utc(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def iso(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def skipped(job_id, name, created):
    return dict(id=job_id, name=name, status="completed", conclusion="skipped", created_at=iso(created),
                started_at=iso(created), completed_at=iso(created), runner_id=None, runner_name=None, steps=[])


def stuck_jobs(base, created, status="queued", runner_id=0, runner_name=""):
    """A brief job that never received a runner, as #203's did (runner 0, no name, no step), and the two jobs
    whose `if:` was false."""
    return [dict(id=base, name="brief", status=status, conclusion=None, created_at=iso(created),
                 started_at=iso(created), completed_at=None, runner_id=runner_id, runner_name=runner_name, steps=[]),
            skipped(base + 1, "cloudflare-smoke", created), skipped(base + 2, "continuity-check", created)]


def ran_jobs(base, created, started, finished=None):
    brief = dict(id=base, name="brief", status="completed" if finished else "in_progress",
                 conclusion="success" if finished else None, created_at=iso(created), started_at=iso(started),
                 completed_at=iso(finished) if finished else None, runner_id=1000 + base % 1000,
                 runner_name=f"GitHub Actions {1000 + base % 1000}",
                 steps=[dict(name="Set up job", status="completed", conclusion="success", number=1,
                             started_at=iso(started), completed_at=iso(started))])
    return [brief, skipped(base + 1, "cloudflare-smoke", created), skipped(base + 2, "continuity-check", created)]


def wake(run_id, number, created, *, status="queued", jobs=None, title=WAKE, path=SCHEDULE, branch="main",
         event="workflow_dispatch", attempt=1, conclusion=None, cancel=None, repository=HOME, head=HOME):
    run = dict(id=run_id, run_number=number, name="Scheduled Market Brief", display_title=title, path=path,
               event=event, head_branch=branch, run_attempt=attempt, status=status, conclusion=conclusion,
               created_at=iso(created), repository=repository, head_repository=head,
               jobs=[] if jobs is None else jobs)
    if cancel:
        run["_cancel"] = cancel
    return run


# The Sep 30 state at the 18:01 UTC tick, from the GitHub API: #202 ran HOURLY_1200 at once; #203 took the group at
# 17:02:05 and its brief job never got a runner.
T202, T203 = utc("2026-09-30T16:01:58+00:00"), utc("2026-09-30T17:02:03+00:00")
TICK = utc("2026-09-30T18:01:00+00:00")


def run_202(**fields):
    return wake(36741213779, 202, T202, status="completed", conclusion="success",
                jobs=ran_jobs(109975791839, T202 + timedelta(seconds=1), T202 + timedelta(seconds=6),
                              T202 + timedelta(seconds=72)), **fields)


def run_203(**fields):
    fields.setdefault("jobs", stuck_jobs(110000782238, T203 + timedelta(seconds=2)))
    return wake(36748511695, 203, T203, **fields)


def sep30(**fields):
    return [run_202(), run_203(**fields)]


# --- A: a quiet tick is unchanged, and anything uncertain cancels nothing and still dispatches -------------

def test_a_quiet_tick_dispatches_exactly_as_before(worker):
    result = worker.tick(TICK, [run_202()])
    assert result.error is None
    assert result.dispatches == [PLAIN_WAKEUP] and result.cancels == []
    assert [entry["liveness"] for entry in result.liveness] == ["idle"]
    # Five small status reads and the dispatch: well inside the Free plan's fifty subrequests.
    reads = [r for r in result.requests if r["method"] == "GET"]
    assert sorted(r["query"]["status"] for r in reads) == ["in_progress", "pending", "queued", "requested", "waiting"]
    assert len(result.requests) == 6


def test_runs_outside_the_lane_and_a_young_holder_are_left_alone(worker):
    tests_run = wake(1, 48, TICK - timedelta(hours=2), status="in_progress", path=".github/workflows/tests.yml",
                     event="push", title="Tests", jobs=ran_jobs(10, TICK - timedelta(hours=2), TICK))
    young = wake(2, 204, TICK + timedelta(seconds=-30), jobs=stuck_jobs(20, TICK - timedelta(seconds=29)))
    result = worker.tick(TICK, [run_202(), tests_run, young])
    assert result.cancels == [] and result.dispatches == [PLAIN_WAKEUP]
    [left] = result.outcome("left")
    assert left["run_id"] == 2 and "superseded" in left["reason"]


@pytest.mark.parametrize("override", [
    pytest.param(dict(method="GET", path="^actions/runs$", hang=True), id="a hung run list"),
    pytest.param(dict(method="GET", path="^actions/runs$", text="<html>busy</html>"), id="a list that is not JSON"),
    pytest.param(dict(method="GET", path="^actions/runs$", json={"workflow_runs": "none"}), id="a malformed list"),
    pytest.param(dict(method="GET", path="^actions/runs$", status=500, json={"message": "Server Error"}),
                 id="a failed list"),
    pytest.param(dict(method="GET", path="^actions/runs/36748511695/jobs$", json={"total_count": 1}),
                 id="a jobs answer without jobs"),
    pytest.param(dict(method="GET", path="^actions/runs/36748511695/jobs$", hang=True), id="hung jobs"),
    pytest.param(dict(method="GET", path="^actions/workflows/schedule.yml/runs$", text="nope"),
                 id="an unreadable earlier run"),
    pytest.param(dict(method="GET", path="^actions/runs/36741213779/jobs$", status=502, json={}),
                 id="unreadable earlier jobs"),
])
def test_an_unanswered_or_malformed_github_state_cancels_nothing_and_still_dispatches(worker, override):
    result = worker.tick(TICK, sep30(), overrides=[override])
    assert result.error is None
    assert result.cancels == []
    assert result.dispatches == [PLAIN_WAKEUP]
    assert result.outcome("unknown") or result.outcome("left")


def test_every_github_read_is_bounded_in_time(worker):
    result = worker.tick(TICK, sep30())
    reads = [r for r in result.requests if r["method"] == "GET"]
    assert reads and all(r["timed"] for r in reads)
    assert {ms for kind, ms in result.delays if kind == "timeout"} == {10_000}


# --- B: a never-started wake superseded by a later checkpoint is cleared before the dispatch ---------------

@pytest.mark.parametrize("status", ["queued", "waiting", "pending", "requested"])
@pytest.mark.parametrize(("runner_id", "runner_name"), [(0, ""), (None, None)])
def test_a_never_started_wake_superseded_by_a_later_checkpoint_is_cleared_before_the_dispatch(
        worker, status, runner_id, runner_name):
    jobs = stuck_jobs(110000782238, T203 + timedelta(seconds=2), status, runner_id, runner_name)
    result = worker.tick(TICK, sep30(jobs=jobs))
    assert result.error is None
    assert result.cancels == [36748511695]
    assert result.run(36748511695)["conclusion"] == "cancelled"
    # The candidate and its jobs are re-read immediately before the cancel; the cancel is verified after it.
    sequence = [(r["method"], r["path"]) for r in result.requests]
    cancel = sequence.index(("POST", "actions/runs/36748511695/cancel"))
    assert sequence[cancel - 2:cancel] == [("GET", "actions/runs/36748511695"),
                                          ("GET", "actions/runs/36748511695/jobs")]
    assert sequence[cancel + 1] == ("GET", "actions/runs/36748511695")
    assert sequence[-1] == ("POST", "actions/workflows/schedule.yml/dispatches")
    [cleared] = result.outcome("cleared")
    assert cleared["run_id"] == 36748511695 and cleared["run_number"] == 203
    assert cleared["created_at"] == "2026-09-30T17:02:03Z" and cleared["threshold_minutes"] == 20
    assert cleared["age_minutes"] == pytest.approx(58.95, abs=0.05)
    assert "never started" in cleared["reason"] and "superseded" in cleared["reason"]
    # The next run's log carries the same record.
    [dispatch] = result.dispatches
    note = dispatch["inputs"].pop("liveness_recovery")
    assert dispatch == PLAIN_WAKEUP
    assert "36748511695" in note and "#203" in note and "2026-09-30T17:02:03Z" in note
    assert "never started" in note and "superseded" in note and "\n" not in note


@pytest.mark.parametrize(("minutes", "cleared"), [(19.9, False), (20, True)])
def test_the_threshold_is_the_twenty_minute_synthesis_window(worker, minutes, cleared):
    result = worker.tick(T203 + timedelta(minutes=minutes), sep30())
    if cleared:
        assert result.cancels == [36748511695] and "liveness_recovery" in result.dispatches[0]["inputs"]
    else:
        assert result.cancels == [] and result.dispatches == [PLAIN_WAKEUP]
        assert "superseded" in result.outcome("left")[0]["reason"]


# --- C: anything that started, or that is not this lane's own Cloudflare wake, is never touched ------------

def test_an_executing_synthesis_is_never_cancelled_however_many_wakes_arrive(worker):
    created = utc("2026-09-30T14:01:40+00:00")
    running = wake(7, 199, created, status="in_progress",
                   jobs=ran_jobs(70, created + timedelta(seconds=2), created + timedelta(seconds=8)))
    runs = [run_202(), running]
    for hour in range(15, 22):
        for minute in (1, 31):
            result = worker.tick(utc(f"2026-09-30T{hour}:{minute:02d}:00+00:00"), runs)
            assert result.cancels == [] and result.dispatches == [PLAIN_WAKEUP]
            assert result.outcome("left")[0]["run_id"] == 7


def started_step(run):
    run["jobs"][0]["steps"] = [dict(name="Set up job", status="in_progress", conclusion=None, number=1,
                                    started_at="2026-09-30T17:02:10Z", completed_at=None)]


def mutate(field, value):
    def apply(run):
        run[field] = value
    return apply


def brief(field, value):
    def apply(run):
        run["jobs"][0][field] = value
    return apply


def drop(field):
    def apply(run):
        del run["jobs"][0][field]
    return apply


@pytest.mark.parametrize("change", [
    pytest.param(brief("runner_id", 1000005325), id="a runner id"),
    pytest.param(brief("runner_name", "GitHub Actions 1000005325"), id="a runner name"),
    pytest.param(started_step, id="a started step"),
    pytest.param(brief("status", "in_progress"), id="a job in progress"),
    pytest.param(lambda run: run["jobs"][0].update(status="completed", conclusion="success"), id="completed work"),
    pytest.param(mutate("status", "in_progress"), id="a run in progress"),
    pytest.param(mutate("run_attempt", 2), id="a re-run"),
    pytest.param(mutate("head_branch", "ops/d1-gates"), id="another branch"),
    pytest.param(mutate("display_title", MANUAL), id="a manual dispatch"),
    pytest.param(mutate("path", PAGES), id="pages.yml"),
    pytest.param(mutate("event", "push"), id="not a dispatch"),
    pytest.param(mutate("head_repository", dict(id=9999, full_name="someone/market-brief")), id="a fork's head"),
    pytest.param(mutate("repository", None), id="no repository"),
    pytest.param(mutate("created_at", None), id="no creation time"),
    pytest.param(drop("steps"), id="no step list"),
    pytest.param(drop("runner_id"), id="no runner field"),
])
def test_only_a_verified_never_started_cloudflare_wake_is_ever_cleared(worker, change):
    holder = run_203()
    change(holder)
    result = worker.tick(TICK, [run_202(), holder])
    assert result.error is None
    assert result.cancels == []
    assert result.dispatches == [PLAIN_WAKEUP]
    assert result.outcome("left") or result.outcome("unknown")


def test_a_runner_assigned_between_the_reads_leaves_the_run_alone(worker):
    """The candidate is re-read immediately before the cancel; a runner that arrived since leaves it alone."""
    picked_up = stuck_jobs(110000782238, T203 + timedelta(seconds=2))
    picked_up[0].update(runner_id=1000005325, runner_name="GitHub Actions 1000005325")
    result = worker.tick(TICK, sep30(), overrides=[dict(method="GET", path="^actions/runs/36748511695/jobs$", skip=1,
                                                        json=dict(total_count=3, jobs=picked_up))])
    assert result.cancels == [] and result.dispatches == [PLAIN_WAKEUP]
    assert "re-read" in result.outcome("left")[0]["reason"]


@pytest.mark.parametrize("earlier", [
    pytest.param(lambda run: run["jobs"][0].update(started_at="2026-09-30T16:27:00Z"), id="a runner after 25 min"),
    pytest.param(lambda run: run.update(conclusion="cancelled", jobs=stuck_jobs(1, T202, "completed")),
                 id="never got a runner"),
    pytest.param(lambda run: run.update(jobs=[]), id="never got a job"),
])
def test_a_slow_or_stalled_queue_is_left_alone(worker, earlier):
    previous = run_202()
    earlier(previous)
    result = worker.tick(TICK, [previous, run_203()])
    assert result.cancels == [] and result.dispatches == [PLAIN_WAKEUP]
    assert "queue" in result.outcome("left")[0]["reason"]


def test_no_earlier_run_is_no_evidence_that_the_queue_is_live(worker):
    result = worker.tick(TICK, [run_203()])
    assert result.cancels == [] and result.dispatches == [PLAIN_WAKEUP]
    assert result.outcome("left")


def test_the_worker_only_reads_cancels_and_dispatches(worker):
    """Normal cancellation only: never force-cancel, re-run, delete or anything else Actions: write allows."""
    allowed = [("GET", "actions/runs"), ("GET", "actions/runs/<id>"), ("GET", "actions/runs/<id>/jobs"),
               ("GET", "actions/workflows/schedule.yml/runs"), ("POST", "actions/runs/<id>/cancel"),
               ("POST", "actions/workflows/schedule.yml/dispatches")]
    pending = wake(36755617810, 204, utc("2026-09-30T18:01:44+00:00"), status="pending")
    result = worker.tick(utc("2026-09-30T19:01:00+00:00"), sep30() + [pending])
    shapes = [(r["method"], "/".join("<id>" if part.isdigit() else part for part in r["path"].split("/")))
              for r in result.requests]
    assert set(shapes) <= set(allowed)
    assert all(r["host"] == "api.github.com" for r in result.requests)


def test_the_token_never_reaches_a_log_or_a_request_body(worker):
    for runs in (sep30(), [run_202()]):
        result = worker.tick(TICK, runs, reject_note=True)
        assert all(r["authorization"] == f"Bearer {TOKEN}" for r in result.requests)
        assert TOKEN not in json.dumps(result.logs) and TOKEN not in json.dumps([r["body"] for r in result.requests])


# --- holder identification: established from the group's run and job state, or nothing is cancelled -------

def test_the_holder_is_the_one_unfinished_lane_run_with_jobs(worker):
    pages = wake(5, 16, T203 + timedelta(minutes=1), status="pending", path=PAGES, event="push", title="Publish")
    result = worker.tick(TICK, sep30() + [pages])
    assert result.cancels == [36748511695]  # the pending pages.yml run is left alone, logged
    assert any(entry["run_id"] == 5 for entry in result.outcome("left"))


@pytest.mark.parametrize("extra", [
    pytest.param(lambda: wake(9, 210, T203 + timedelta(minutes=5), jobs=stuck_jobs(90, T203)), id="two runs with jobs"),
    pytest.param(lambda: wake(9, 199, T203 - timedelta(minutes=5), status="pending"),
                 id="a run without jobs older than the holder"),
])
def test_an_ambiguous_group_cancels_nothing(worker, extra):
    result = worker.tick(TICK, sep30() + [extra()])
    assert result.cancels == [] and result.dispatches == [PLAIN_WAKEUP]
    assert result.outcome("unknown")


def test_a_group_without_a_run_holding_jobs_cancels_nothing(worker):
    pending = wake(36755617810, 204, T203, status="pending")
    result = worker.tick(TICK, [run_202(), pending])
    assert result.cancels == [] and result.outcome("unknown")


def test_more_unfinished_lane_runs_than_one_group_holds_cancels_nothing(worker):
    """A group holds one run and one pending run; a crowd of lane runs is not understood, and reading each one's jobs
    would spend the tick's subrequests before the dispatch."""
    crowd = [wake(300 + i, 300 + i, T203 + timedelta(minutes=i + 1), status="pending") for i in range(3)]
    result = worker.tick(TICK, sep30() + crowd)
    assert result.cancels == [] and result.dispatches == [PLAIN_WAKEUP]
    assert result.outcome("unknown") and len(result.requests) == 6


def test_a_stall_tick_stays_far_inside_the_free_plan_subrequest_budget(worker):
    pending = wake(36755617810, 204, utc("2026-09-30T18:01:44+00:00"), status="pending")
    pages = wake(5, 16, utc("2026-09-30T18:30:00+00:00"), status="pending", path=PAGES, event="push", title="Publish")
    for runs, at in ((sep30(), TICK), (sep30() + [pending, pages], utc("2026-09-30T19:01:00+00:00"))):
        result = worker.tick(at, runs)
        # Worst case: 5 lists, 3 job lists, 2 reads for the run before the holder, 2 to re-check the holder, 9 per
        # cleared run (re-read, cancel, 5 checks, jobs) for 3 runs and 2 dispatches: 41, under the limit of 50.
        assert result.outcome("cleared") and len(result.requests) <= 25


def test_more_unfinished_runs_than_one_listing_shows_cancels_nothing(worker):
    crowd = [wake(100 + i, 100 + i, TICK - timedelta(minutes=i), status="queued", path=".github/workflows/tests.yml",
                  event="push", title="Tests") for i in range(21)]
    result = worker.tick(TICK, sep30() + crowd)
    assert result.cancels == [] and result.outcome("unknown")


# --- F: pending members, double wakes and cancels that do not take effect ----------------------------------

def test_a_pending_wake_of_this_lane_is_cleared_before_the_holder(worker):
    pending = wake(36755617810, 204, utc("2026-09-30T18:01:44+00:00"), status="pending")
    result = worker.tick(utc("2026-09-30T19:01:00+00:00"), sep30() + [pending])
    assert result.cancels == [36755617810, 36748511695]
    assert [entry["run_id"] for entry in result.outcome("cleared")] == [36755617810, 36748511695]
    note = result.dispatches[0]["inputs"]["liveness_recovery"]
    assert "#204" in note and "#203" in note


@pytest.mark.parametrize("pending", [
    pytest.param(wake(8, 204, utc("2026-09-30T18:01:44+00:00"), status="pending", title=MANUAL), id="a manual run"),
    pytest.param(wake(8, 204, utc("2026-09-30T18:01:44+00:00"), status="pending", branch="ops/x"), id="a branch"),
    pytest.param(wake(8, 204, utc("2026-09-30T18:59:30+00:00"), status="pending"), id="a young wake"),
])
def test_a_pending_run_that_is_not_safe_to_clear_is_left_and_the_holder_still_cleared(worker, pending):
    result = worker.tick(utc("2026-09-30T19:01:00+00:00"), sep30() + [pending])
    assert result.cancels == [36748511695]
    assert any(entry["run_id"] == 8 for entry in result.outcome("left"))


def test_a_double_fired_tick_clears_once(worker):
    first = worker.tick(TICK, sep30())
    assert first.cancels == [36748511695]
    # The second invocation of the same tick sees the run completed and cancels nothing.
    second = worker.tick(TICK, first.state["runs"])
    assert second.cancels == [] and second.dispatches == [PLAIN_WAKEUP]


def test_a_concurrent_double_fire_reports_the_run_already_completed(worker):
    """Both invocations read the stalled run; the other one's cancel lands first, so this cancel answers 409."""
    done = run_203(status="completed", conclusion="cancelled")
    done["jobs"][0].update(status="completed", conclusion="cancelled")
    stale_run = {key: value for key, value in run_203().items() if key != "jobs"}
    overrides = [dict(method="GET", path="^actions/runs/36748511695$", times=1, json=stale_run),
                 dict(method="GET", path="^actions/runs/36748511695/jobs$", times=2,
                      json=dict(total_count=3, jobs=run_203()["jobs"]))]
    # Members are listed from the stale view too.
    overrides.insert(0, dict(method="GET", path="^actions/runs$", times=1,
                             json=dict(total_count=1, workflow_runs=[stale_run])))
    result = worker.tick(TICK, [run_202(), done], overrides=overrides)
    assert result.error is None
    assert result.cancels == [36748511695] and result.outcome("already_completed")
    assert not result.outcome("cleared")
    assert "already completed" in result.dispatches[0]["inputs"]["liveness_recovery"]


def test_a_cancel_that_does_not_take_effect_is_reported_and_stops_the_clearing(worker):
    pending = wake(36755617810, 204, utc("2026-09-30T18:01:44+00:00"), status="pending", cancel="ignored")
    result = worker.tick(utc("2026-09-30T19:01:00+00:00"), sep30() + [pending])
    assert result.cancels == [36755617810]  # the holder is not cancelled after an uncertain pending cancel
    [entry] = result.outcome("cancel_not_effective")
    assert entry["run_id"] == 36755617810
    assert [ms for kind, ms in result.delays if kind == "wait"] == [2_000] * 5
    assert "not effective" in result.dispatches[0]["inputs"]["liveness_recovery"]
    assert not result.outcome("cleared")


@pytest.mark.parametrize(("cancel", "outcome"), [("refused", "cancel_failed"), ("hang", "cancel_not_effective")])
def test_a_refused_or_unanswered_cancel_is_reported_and_the_wake_still_dispatched(worker, cancel, outcome):
    result = worker.tick(TICK, sep30(cancel=cancel))
    assert result.error is None
    assert result.cancels == [36748511695]
    assert result.outcome(outcome) and not result.outcome("cleared")
    assert len(result.dispatches) == 1


# --- G: the recovery is logged, and the note can never stop a wake -----------------------------------------

def test_a_rejected_note_falls_back_to_a_plain_wakeup(worker):
    result = worker.tick(TICK, sep30(), reject_note=True)
    assert result.error is None
    assert len(result.dispatches) == 2
    assert "liveness_recovery" in result.dispatches[0]["inputs"] and result.dispatches[1] == PLAIN_WAKEUP
    assert result.outcome("note_rejected")


def test_a_failed_dispatch_still_fails_loudly(worker):
    result = worker.tick(TICK, [run_202()], dispatch_status=500)
    assert result.error == "GitHub workflow dispatch failed with HTTP 500"
    assert any('"error":"github_workflow_dispatch_rejected"' in line and '"status":500' in line
               for line in result.logs)


def test_missing_configuration_sends_nothing(worker):
    result = worker.tick(TICK, sep30(), env=dict(GITHUB_REPOSITORY=REPOSITORY))
    assert result.requests == []
    assert result.error == "Cloudflare scheduler is missing its GitHub dispatch configuration"


def test_log_lines_are_narrow(worker):
    """Each log line names the run and the decision; no GitHub payload is copied into the log."""
    result = worker.tick(utc("2026-09-30T19:01:00+00:00"),
                         sep30() + [wake(8, 204, utc("2026-09-30T18:01:44+00:00"), status="pending", title=MANUAL)])
    fields = {"liveness", "run_id", "run_number", "workflow", "branch", "created_at", "age_minutes",
              "threshold_minutes", "reason"}
    assert result.liveness and all(set(entry) <= fields for entry in result.liveness)
    assert all(len(line) < 600 for line in result.logs)


# --- the re-read and the cancel: what can change between them is detected, verified and reported -----------

def test_a_runner_that_arrives_after_the_re_read_is_reported_not_called_never_started(worker):
    result = worker.tick(TICK, sep30(cancel="runner_first"))
    assert result.cancels == [36748511695]
    assert not result.outcome("cleared")
    [entry] = result.outcome("cancelled_after_start")
    assert "after the re-read" in entry["reason"] and "paid attempt" in entry["reason"]
    note = result.dispatches[0]["inputs"]["liveness_recovery"]
    assert "never started" not in note and "after the re-read" in note


def test_a_cancel_that_cannot_be_checked_afterwards_is_reported_unverified(worker):
    result = worker.tick(TICK, sep30(), overrides=[
        dict(method="GET", path="^actions/runs/36748511695/jobs$", skip=2, status=500, json={})])
    assert result.cancels == [36748511695] and result.outcome("cancelled_unverified")
    assert not result.outcome("cleared")


def test_a_cancel_that_lands_but_answers_a_gateway_error_is_verified(worker):
    pending = wake(36755617810, 204, utc("2026-09-30T18:01:44+00:00"), status="pending", cancel="lands502")
    result = worker.tick(utc("2026-09-30T19:01:00+00:00"), sep30() + [pending])
    assert result.cancels == [36755617810, 36748511695]
    assert [entry["run_id"] for entry in result.outcome("cleared")] == [36755617810, 36748511695]


def test_a_gateway_error_on_a_cancel_that_did_not_land_is_not_effective(worker):
    result = worker.tick(TICK, sep30(cancel="fails502"))
    assert result.cancels == [36748511695] and result.outcome("cancel_not_effective")
    assert len(result.dispatches) == 1


def test_a_slow_re_read_is_not_current_enough_to_cancel_on(worker):
    result = worker.tick(TICK, sep30(), overrides=[
        dict(method="GET", path="^actions/runs/36748511695$", delay=2_100, json=run_view(run_203()))])
    assert result.cancels == [] and result.dispatches == [PLAIN_WAKEUP]
    assert "too slow" in result.outcome("left")[0]["reason"]


def test_a_pending_wake_is_cancelled_only_while_the_holder_still_qualifies(worker):
    """The holder is re-checked before any pending wake is cancelled: a holder that got a runner since the first
    read is not stalled, so nothing is cancelled at all."""
    pending = wake(36755617810, 204, utc("2026-09-30T18:01:44+00:00"), status="pending")
    picked_up = stuck_jobs(110000782238, T203 + timedelta(seconds=2))
    picked_up[0].update(runner_id=1000005325, runner_name="GitHub Actions 1000005325")
    result = worker.tick(utc("2026-09-30T19:01:00+00:00"), sep30() + [pending], overrides=[
        dict(method="GET", path="^actions/runs/36748511695/jobs$", skip=1, json=dict(total_count=3, jobs=picked_up))])
    assert result.cancels == [] and result.dispatches == [PLAIN_WAKEUP]


def test_an_inverted_runner_wait_is_no_evidence_of_a_live_queue(worker):
    previous = run_202()
    previous["jobs"][0].update(created_at="2026-09-30T16:40:00Z", started_at="2026-09-30T15:00:00Z")
    result = worker.tick(TICK, [previous, run_203()])
    assert result.cancels == [] and "queue" in result.outcome("left")[0]["reason"]


def run_view(run):
    return {key: value for key, value in run.items() if key != "jobs" and not key.startswith("_")}


# --- replays: one concurrency lane through a whole day, with runs resolved by the real scheduler ------------

class Lane:
    """One Market Brief concurrency lane as GitHub runs it: the run holding the group, at most one pending run (a
    newer one replaces it), and runs that resolve their checkpoint from the clock when they get a runner."""

    def __init__(self, day, wait=timedelta(seconds=10), stalled=()):
        self.day, self.wait, self.stalled = day, wait, set(stalled)
        self.runs, self.holder, self.pending, self.events = [], None, None, []
        self.completed, self.resolved, self.cancelled = {}, [], []
        # The previous session's last wake, which ran at once.
        before = utc(f"{day}T00:00:00+00:00") - timedelta(hours=2, minutes=58)
        self.runs.append(wake(1, 1, before, status="completed", conclusion="success",
                              jobs=ran_jobs(10, before, before + timedelta(seconds=6), before + timedelta(minutes=1))))

    def ticks(self):
        start = utc(f"{self.day}T00:00:00+00:00")
        return [start.replace(hour=hour, minute=minute) for hour, minute in sorted(wake_minutes())]

    def dispatch(self, at, tick):
        number = len(self.runs) + 1
        run = wake(number, number, at, status="pending")
        run["_tick"] = tick.strftime("%H:%M")
        self.runs.append(run)
        if self.holder is None:
            self.acquire(run, at)
        else:
            if self.pending is not None:  # GitHub cancels the older pending run
                self.pending.update(status="completed", conclusion="cancelled")
            self.pending = run

    def acquire(self, run, at):
        self.holder = run
        run.update(status="queued", jobs=stuck_jobs(100 * run["id"], at))
        if run["_tick"] not in self.stalled:
            self.events.append((at + self.wait, "start", run))

    def release(self, at):
        self.holder = None
        if self.pending is not None:
            run, self.pending = self.pending, None
            self.acquire(run, at)

    def advance(self, until):
        while True:
            due = sorted((event for event in self.events if event[0] <= until), key=lambda event: event[0])
            if not due:
                return
            at, kind, run = due[0]
            self.events.remove(due[0])
            if run is not self.holder or run["status"] == "completed":
                continue
            if kind == "start":
                run.update(status="in_progress", jobs=ran_jobs(100 * run["id"], at - self.wait, at))
                checkpoint = scheduled_checkpoint(at + timedelta(seconds=15))
                self.resolved.append((run["id"], checkpoint))
                if checkpoint and checkpoint not in self.completed:
                    self.completed[checkpoint] = run["id"]
                self.events.append((at + timedelta(minutes=2), "finish", run))
            else:
                started = at - timedelta(minutes=2)
                run.update(status="completed", conclusion="success",
                           jobs=ran_jobs(100 * run["id"], started - self.wait, started, at))
                self.release(at)

    def sync(self, result, at):
        """Apply the Worker's cancels and its dispatch to the lane."""
        for returned in result.state["runs"]:
            run = next(r for r in self.runs if r["id"] == returned["id"])
            if returned["status"] == "completed" and run["status"] != "completed":
                assert run["jobs"] == [] or run["jobs"][0]["runner_id"] in (0, None), "a started run was cancelled"
                run.update(status="completed", conclusion=returned["conclusion"], jobs=returned["jobs"])
                self.cancelled.append(run["id"])
                if run is self.holder:
                    self.release(at)
                elif run is self.pending:
                    self.pending = None
        for body in result.dispatches[-1:]:
            assert body["inputs"]["cloudflare_wakeup"] == "true"
            self.dispatch(at + timedelta(seconds=2), at)

    def replay(self, worker):
        """Every tick of the day through the real Worker; with no worker, the Worker as it was before recovery."""
        for tick in self.ticks():
            self.advance(tick + timedelta(seconds=40))
            if worker is None:
                self.dispatch(tick + timedelta(seconds=43), tick + timedelta(seconds=41))
                continue
            result = worker.tick(tick, self.runs)
            assert result.error is None
            self.sync(result, tick + timedelta(seconds=41))
        self.advance(utc(f"{self.day}T23:59:00+00:00"))
        return self

    def stalled_wake(self, tick):
        return next(run["id"] for run in self.runs if run.get("_tick") == tick)


ABBREVIATIONS = dict(PREMARKET="PM", OPEN_1M="O1", OPEN_30M="O30", HOURLY_1100="H11", HOURLY_1200="H12",
                     HOURLY_1300="H13", HOURLY_1400="H14", HOURLY_1500="H15", CLOSE_1M="C1")


def published(lane):
    return [ABBREVIATIONS[checkpoint] for checkpoint in CHECKPOINTS if checkpoint in lane.completed]


def test_the_sep_30_stall_costs_only_hourly_1300(worker):
    """#203 stuck before runner assignment; the 18:01 tick clears it; HOURLY_1300 stays missing; HOURLY_1400, the
    later hourlies and CLOSE_1M publish."""
    lane = Lane("2026-09-30", stalled={"17:01"}).replay(worker)
    assert published(lane) == ["PM", "O1", "O30", "H11", "H12", "H14", "H15", "C1"]
    assert lane.cancelled == [lane.stalled_wake("17:01")]
    # Nothing backfills: the run after the clear resolved HOURLY_1400, and no checkpoint ran twice.
    resolved = [checkpoint for _, checkpoint in lane.resolved if checkpoint]
    assert "HOURLY_1300" not in resolved and len(resolved) == len(set(resolved))


@pytest.mark.parametrize(("day", "stall", "expected"), [
    # The OPEN_1M wake is superseded by OPEN_30M at the next tick, before its own 45-minute tolerance has run out.
    ("2026-09-30", "13:31", ["PM", "O30", "H11", "H12", "H13", "H14", "H15", "C1"]),  # EDT
    ("2026-12-01", "14:31", ["PM", "O30", "H11", "H12", "H13", "H14", "H15", "C1"]),  # EST
    ("2026-11-02", "14:31", ["PM", "O30", "H11", "H12", "H13", "H14", "H15", "C1"]),  # the Monday after DST ends
    ("2026-03-09", "13:31", ["PM", "O30", "H11", "H12", "H13", "H14", "H15", "C1"]),  # the Monday after DST starts
    ("2026-11-27", "17:01", ["PM", "O1", "O30", "H11", "C1"]),  # early close: the H12 wake stalls, the close runs
])
def test_one_stalled_wake_costs_only_its_own_checkpoint(worker, day, stall, expected):
    lane = Lane(day, stalled={stall}).replay(worker)
    assert published(lane) == expected
    assert lane.cancelled == [lane.stalled_wake(stall)]


@pytest.mark.parametrize("day", ["2026-09-30", "2026-12-01", "2026-11-27"])
def test_a_green_day_never_cancels(worker, day):
    lane = Lane(day).replay(worker)
    expected = Lane(day)
    for tick in expected.ticks():
        checkpoint = scheduled_checkpoint(tick + timedelta(seconds=67))
        if checkpoint:
            expected.completed[checkpoint] = 0
    assert published(lane) == published(expected)
    assert lane.cancelled == []


@pytest.mark.parametrize(("day", "minutes", "recovered", "unrecovered"), [
    ("2026-09-30", 25, ["O1", "H11", "H12", "H13", "H14", "H15", "C1"],
     ["O1", "H11", "H12", "H13", "H14", "H15", "C1"]),
    ("2026-09-30", 35, ["O30", "H11", "H12", "H13", "H14", "H15", "C1"],
     ["O1", "O30", "H11", "H12", "H13", "H14", "H15", "C1"]),
    ("2026-09-30", 61, ["H11", "H12", "H13", "H14"], ["O30", "H11", "H12", "H13", "H14", "H15", "C1"]),
    ("2026-12-01", 61, ["O1", "H11", "H12", "H13"], ["PM", "O30", "H11", "H12", "H13", "H14", "H15", "C1"]),
])
def test_a_slow_but_live_queue_is_cleared_at_most_once_and_its_cost_is_pinned(worker, day, minutes, recovered,
                                                                             unrecovered):
    """Every run waits the same time for a runner. Clearing never repeats in a row, so recovery never starves the
    day; but the first wake still waiting at the next tick after a prompt run is cleared like a stalled one, and in a
    long, steady slowdown that one cancel delays every later run by a tick. The cost against no recovery at all is
    pinned here so it stays visible (owner ruling 1 accepted the trade for recovery from a stalled wake)."""
    lane = Lane(day, wait=timedelta(minutes=minutes)).replay(worker)
    assert len(lane.cancelled) <= 1
    assert published(lane) == recovered
    assert published(Lane(day, wait=timedelta(minutes=minutes)).replay(None)) == unrecovered

const WORKFLOW = "schedule.yml";
const SCHEDULE_PATH = ".github/workflows/schedule.yml";
// The workflows that share the market-brief-pages concurrency group.
const LANE = new Set([SCHEDULE_PATH, ".github/workflows/pages.yml"]);
// schedule.yml's run-name for a Cloudflare wake-up; a manual dispatch keeps the workflow's own name.
const WAKE_TITLE = "Cloudflare wake-up";
// schedule.TOLERANCE_MINUTES["synthesis"], the narrowest due window. Wakes are at least thirty minutes apart, so a
// never-started wake is this old only at a later wake, when a later checkpoint has superseded the one its own wake
// would have resolved: started now, it could no longer resolve that checkpoint.
const SUPERSEDED_MINUTES = 20;
const UNFINISHED = ["queued", "in_progress", "waiting", "pending", "requested"];
const NOT_STARTED = new Set(["queued", "waiting", "pending", "requested"]);
const LISTED = 20;
// A group holds one run and at most one pending run; one more covers a pending run being replaced. More than this
// is not a state the Worker understands, and it bounds the subrequests one tick can make (Free plan: fifty).
const MOST_MEMBERS = 3;
const REQUEST_TIMEOUT_MS = 10_000;
const SWEEP_BUDGET_MS = 60_000;
const CANCEL_CHECKS = 5;
const CANCEL_CHECK_MS = 2_000;

class Unclear extends Error {}

export default {
  async scheduled(controller, env) {
    const repository = env.GITHUB_REPOSITORY;
    const token = env.GH_DISPATCH_TOKEN;
    if (!repository || !token) {
      throw new Error("Cloudflare scheduler is missing its GitHub dispatch configuration");
    }

    const github = connect(repository, token);
    let note = "";
    try {
      note = await recover(github, repository, controller.scheduledTime);
    } catch (error) {
      // Anything unanswered, malformed or unexpected leaves every run alone; the wake is still dispatched.
      record({ liveness: "unknown", reason: describe(error) });
    }
    await dispatch(github, note);
  },

  async fetch() {
    return new Response("Market Brief scheduler", { status: 200 });
  },
};

async function dispatch(github, note) {
  const inputs = { cloudflare_wakeup: "true" };
  if (note) inputs.liveness_recovery = note;
  let response = await github.send("POST", `actions/workflows/${WORKFLOW}/dispatches`, { ref: "main", inputs });
  if (response.status === 422 && note) {
    // A workflow without the note's input (a deploy-order mismatch or a rollback) must never stop a wake.
    record({ liveness: "note_rejected", reason: "GitHub refused the recovery note; dispatched a plain wake-up" });
    response = await github.send("POST", `actions/workflows/${WORKFLOW}/dispatches`, {
      ref: "main",
      inputs: { cloudflare_wakeup: "true" },
    });
  }

  if (!response.ok) {
    const body = (await response.text()).slice(0, 1000);
    console.error(JSON.stringify({
      error: "github_workflow_dispatch_rejected",
      status: response.status,
      body,
    }));
    throw new Error(`GitHub workflow dispatch failed with HTTP ${response.status}`);
  }
}

// Inspect the lane, qualify the run holding it, normal-cancel, verify; returns the note for the next run's log.
async function recover(github, repository, now) {
  if (!Number.isFinite(now)) throw new Unclear("the tick has no scheduled time");
  github.deadline = Date.now() + SWEEP_BUDGET_MS;
  const members = await laneRuns(github);
  if (members.length === 0) {
    record({ liveness: "idle" });
    return "";
  }
  if (members.length > MOST_MEMBERS) throw new Unclear("more unfinished runs than one group holds");
  for (const run of members) run.jobs = await jobsOf(github, run.id);

  // GitHub does not name the group's holder. With a workflow-level group a run gets jobs only once it holds the
  // group, so the holder is the one unfinished lane run with jobs, and every other one must be newer and job-less.
  const holding = members.filter((run) => run.jobs.length > 0);
  if (holding.length !== 1) {
    throw new Unclear(holding.length ? "more than one unfinished run has jobs" : "no unfinished run has jobs");
  }
  const holder = holding[0];
  const pending = members.filter((run) => run !== holder);
  if (pending.some((run) => !(created(run) > created(holder)))) {
    throw new Unclear("a run without jobs is not newer than the run holding the group");
  }

  const problem = wakeProblem(holder, repository, now, true) ?? await quietQueue(github, holder);
  if (problem) {
    record(entry("left", holder, now, problem));
    for (const run of pending) record(entry("left", run, now, "pending behind a run that is left alone"));
    return "";
  }

  // Pending wakes of this lane go first, so cancelling the holder promotes none of them; anything else is left.
  const order = [];
  for (const run of pending.sort((a, b) => created(b) - created(a))) {
    const reason = wakeProblem(run, repository, now, false);
    if (reason) record(entry("left", run, now, `${reason}; pending, so it may run once the group is free`));
    else order.push([run, false]);
  }
  order.push([holder, true]);

  const outcomes = [];
  for (const [run, holds] of order) {
    const outcome = await clear(github, repository, now, run, holds);
    if (outcome) outcomes.push(outcome);
    if (!outcome || !["cleared", "already_completed"].includes(outcome.liveness)) break;
  }
  return outcomes.map(describeOutcome).join("; ").slice(0, 900);
}

async function laneRuns(github) {
  const runs = new Map();
  for (const status of UNFINISHED) {
    const listing = await github.read(`actions/runs?status=${status}&per_page=${LISTED}`);
    const listed = listing?.workflow_runs;
    if (!Array.isArray(listed) || !Number.isInteger(listing.total_count)) {
      throw new Unclear("a run listing was malformed");
    }
    if (listing.total_count > listed.length) throw new Unclear("more unfinished runs than one listing shows");
    for (const run of listed) {
      if (!isObject(run) || !Number.isInteger(run.id) || typeof run.path !== "string") {
        throw new Unclear("a listed run was malformed");
      }
      if (LANE.has(run.path)) runs.set(run.id, run);
    }
  }
  return [...runs.values()];
}

async function jobsOf(github, id) {
  const listing = await github.read(`actions/runs/${id}/jobs?filter=latest&per_page=100`);
  const jobs = listing?.jobs;
  if (!Array.isArray(jobs) || !Number.isInteger(listing.total_count) || listing.total_count > jobs.length
      || !jobs.every(isObject)) {
    throw new Unclear("a job listing was malformed");
  }
  return jobs;
}

// Why `run` is not a never-started Cloudflare wake of this lane that a later checkpoint has superseded, or null.
// `holds`: the run holding the group, whose jobs exist; otherwise a pending run, which has none.
function wakeProblem(run, repository, now, holds) {
  if (run.path !== SCHEDULE_PATH) return "not a schedule.yml run";
  if (run.head_branch !== "main") return "a run from another branch";
  if (run.event !== "workflow_dispatch") return "not a workflow dispatch";
  if (run.run_attempt !== 1) return "a re-run";
  if (run.display_title !== WAKE_TITLE) return "not a Cloudflare wake (a manual dispatch)";
  const home = run.repository;
  if (!isObject(home) || home.full_name !== repository || !Number.isInteger(home.id)
      || !isObject(run.head_repository) || run.head_repository.id !== home.id) {
    return "not this repository's own run";
  }
  if (!NOT_STARTED.has(run.status)) return `the run is ${run.status}`;
  if (!Number.isFinite(created(run))) return "no creation time";
  if (age(run, now) < SUPERSEDED_MINUTES) {
    return `under ${SUPERSEDED_MINUTES} minutes old: not yet superseded by a later checkpoint`;
  }
  if (!Array.isArray(run.jobs)) return "its jobs are unknown";
  if (!holds) return run.jobs.length === 0 ? null : "a pending run with jobs";
  if (run.jobs.length === 0) return "no jobs";
  for (const job of run.jobs) {
    const reason = jobProblem(job);
    if (reason) return reason;
  }
  return run.jobs.some((job) => NOT_STARTED.has(job.status)) ? null : "no job is waiting";
}

function jobProblem(job) {
  if (!Array.isArray(job.steps)) return "a job without a step list";
  if (job.steps.length > 0) return "a job has started steps";
  if (!("runner_id" in job) || !("runner_name" in job)) return "a job without runner fields";
  if (![null, 0].includes(job.runner_id) || ![null, ""].includes(job.runner_name)) return "a job has a runner";
  if (job.status === "completed") return job.conclusion === "skipped" ? null : "a job has completed";
  return NOT_STARTED.has(job.status) ? null : `a job is ${job.status}`;
}

// The live-queue condition: the run before the holder got a runner within the same twenty minutes. A slow but
// working queue is left alone, so recovery never starves it by cancelling each run just before its runner comes.
async function quietQueue(github, holder) {
  const before = encodeURIComponent(`<${holder.created_at}`);
  const listing = await github.read(`actions/workflows/${WORKFLOW}/runs?branch=main&per_page=1&created=${before}`);
  const listed = listing?.workflow_runs;
  if (!Array.isArray(listed)) throw new Unclear("the run before the holder could not be read");
  if (listed.length === 0) return "no earlier run shows that the queue is live";
  const earlier = listed[0];
  if (!isObject(earlier) || !Number.isInteger(earlier.id) || !(created(earlier) < created(holder))) {
    throw new Unclear("the run before the holder was malformed");
  }
  const prompt = (await jobsOf(github, earlier.id)).some((job) => Number.isInteger(job.runner_id)
    && job.runner_id > 0 && Array.isArray(job.steps) && job.steps.length > 0
    && Date.parse(job.started_at) - Date.parse(job.created_at) <= SUPERSEDED_MINUTES * 60_000);
  return prompt ? null : `the run before it did not get a runner within ${SUPERSEDED_MINUTES} minutes: `
    + "a slow or stalled queue is left alone";
}

async function clear(github, repository, now, run, holds) {
  // Re-read the candidate and its jobs immediately before the cancel; any change leaves it alone.
  let fresh;
  try {
    fresh = await github.read(`actions/runs/${run.id}`);
    if (!isObject(fresh) || fresh.id !== run.id) throw new Unclear("the re-read run was malformed");
    fresh.jobs = await jobsOf(github, run.id);
  } catch (error) {
    record(entry("left", run, now, `could not be re-read before the cancel (${describe(error)})`));
    return null;
  }
  const problem = wakeProblem(fresh, repository, now, holds);
  if (problem) {
    record(entry("left", fresh, now, `changed when re-read before the cancel: ${problem}`));
    return null;
  }

  let answer = null;
  try {
    answer = (await github.send("POST", `actions/runs/${run.id}/cancel`, undefined, true)).status;
  } catch {
    answer = null; // no answer: the cancel may still have landed, so it is verified below
  }
  let liveness;
  if (answer === 409) {
    const now409 = await state(github, run.id);
    liveness = now409?.status === "completed" ? "already_completed" : "cancel_failed";
  } else if (answer !== null && answer !== 202) {
    liveness = "cancel_failed";
  } else {
    liveness = "cancel_not_effective";
    for (let check = 0; check < CANCEL_CHECKS; check += 1) {
      await new Promise((resolve) => setTimeout(resolve, CANCEL_CHECK_MS));
      const current = await state(github, run.id);
      if (current?.status === "completed") {
        liveness = current.conclusion === "cancelled" ? "cleared" : "already_completed";
        break;
      }
    }
  }
  const reason = {
    cleared: "never started (no runner, no step) and superseded by a later checkpoint; cancelled",
    already_completed: "had already completed when the cancel landed",
    cancel_failed: `the cancel was refused${answer ? ` (HTTP ${answer})` : ""}`,
    cancel_not_effective: `the cancel did not take effect within ${CANCEL_CHECKS * CANCEL_CHECK_MS / 1000} s`,
  }[liveness];
  const outcome = entry(liveness, fresh, now, reason);
  record(outcome);
  return outcome;
}

async function state(github, id) {
  try {
    return await github.read(`actions/runs/${id}`);
  } catch {
    return null;
  }
}

function describeOutcome(outcome) {
  const run = `run ${outcome.run_id} (#${outcome.run_number}, created ${outcome.created_at}, `
    + `${outcome.age_minutes} min old)`;
  return {
    cleared: `cleared ${run}: never started (no runner, no step); superseded by a later checkpoint`,
    already_completed: `${run} had already completed`,
    cancel_failed: `cancel of ${run} failed: ${outcome.reason}`,
    cancel_not_effective: `cancel of ${run} requested but not effective: ${outcome.reason}`,
  }[outcome.liveness];
}

function entry(liveness, run, now, reason) {
  return {
    liveness,
    run_id: run.id,
    run_number: run.run_number,
    workflow: typeof run.path === "string" ? run.path.split("/").pop() : null,
    branch: run.head_branch,
    created_at: run.created_at,
    age_minutes: Number.isFinite(created(run)) ? Math.round(age(run, now) * 100) / 100 : null,
    threshold_minutes: SUPERSEDED_MINUTES,
    reason,
  };
}

function record(value) {
  console.log(JSON.stringify(value));
}

function describe(error) {
  if (error instanceof Unclear) return error.message;
  if (error?.name === "TimeoutError" || error?.name === "AbortError") return "a GitHub request timed out";
  if (error instanceof SyntaxError) return "a GitHub answer was not JSON";
  return `unexpected ${error?.name ?? "error"}`;
}

function created(run) {
  return Date.parse(run.created_at);
}

function age(run, now) {
  return (now - created(run)) / 60_000;
}

function isObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function connect(repository, token) {
  const headers = {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
    "User-Agent": "market-brief-cloudflare-scheduler",
    "X-GitHub-Api-Version": "2022-11-28",
  };
  const base = `https://api.github.com/repos/${repository}/`;
  const github = {
    deadline: Infinity,
    // Reads and cancels are bounded in time; the dispatch is sent exactly as before.
    send(method, path, body, bounded = false) {
      const init = { method, headers };
      if (body !== undefined) init.body = JSON.stringify(body);
      if (bounded) init.signal = AbortSignal.timeout(REQUEST_TIMEOUT_MS);
      return fetch(base + path, init);
    },
    async read(path) {
      if (Date.now() > github.deadline) throw new Unclear("the sweep ran out of time");
      const response = await github.send("GET", path, undefined, true);
      if (!response.ok) throw new Unclear(`GitHub answered ${response.status} to a read`);
      return response.json();
    },
  };
  return github;
}

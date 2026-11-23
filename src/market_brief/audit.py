"""Daily receipt audit: did every checkpoint the exchange calendar expected today leave a receipt?

A checkpoint whose wake never started (a Cloudflare cron that did not fire, a dispatch GitHub refused) leaves no run,
so no failed run and no failure email. This audit runs on GitHub's own schedule, independent of Cloudflare, after the
session's close: it lists this repository's receipt artifacts (`market-brief-receipt-<session>-<checkpoint>-<run>`)
and fails when an expected checkpoint has none, so GitHub's existing failure email reports it. It is read-only:
no collection, model call, publication, replay or recovery. A run that started and failed already emails; its
receipt still counts here, because this audit answers only whether the wake happened.
"""

import re
from datetime import datetime, timedelta, timezone

import exchange_calendars as xcals

from .continuity import WORKFLOW_PATH, own_repository_run
from .schedule import CHECKPOINTS, ET, checkpoint_session, tolerance_minutes

RECEIPT_NAME = re.compile(r"^market-brief-receipt-(\d{4}-\d{2}-\d{2})-([A-Z0-9_]+)-(\d+)$")
# How long after the session's CLOSE_1M the audit waits before a missing receipt counts: the close run, its receipt
# upload and a liveness retry all finish well inside it. The workflow's cron sits hours later still.
GRACE = timedelta(minutes=60)
# A receipt counts for its checkpoint only when its run was created inside that checkpoint's wake window: from a few
# minutes before the scheduled minute to the scheduler's own due tolerance plus queueing slack. A manual run at another
# time (a weekend test, a morning rerun of the close) proves nothing about the wake and never hides a missed one.
EARLY_SLACK = timedelta(minutes=5)
QUEUE_SLACK = timedelta(minutes=15)
# Sessions searched back for the latest one that has closed (a delayed schedule still audits it).
LOOKBACK_DAYS = 7
MAX_PAGES = 10


def expected_checkpoints(session_date):
    """The calendar's checkpoint infos on `session_date` (holidays: none; early closes: no late hourlies) and the
    session's CLOSE_1M time, or ([], None) when it is not a session."""
    cal = xcals.get_calendar("XNYS")
    if not cal.is_session(session_date):
        return [], None
    probe = datetime.fromisoformat(f"{session_date}T12:00:00").replace(tzinfo=ET)
    infos = [checkpoint_session(probe, checkpoint) for checkpoint in CHECKPOINTS]
    close = next(info["scheduled_at"] for info in infos if info["checkpoint"] == "CLOSE_1M")
    return [info for info in infos if info["applicable"]], datetime.fromisoformat(close)


def audit_target(now):
    """The most recent session whose CLOSE_1M plus GRACE has passed, or (None, reason). A run on a holiday re-audits
    the session before it; a schedule GitHub delayed (even past midnight) still audits the session that closed. A run
    GitHub drops entirely is not caught up: the next run audits its own session."""
    cal = xcals.get_calendar("XNYS")
    day = now.astimezone(ET).date()
    for back in range(LOOKBACK_DAYS + 1):
        candidate = (day - timedelta(days=back)).isoformat()
        if not cal.is_session(candidate):
            continue
        _, close = expected_checkpoints(candidate)
        if now >= close + GRACE:
            return candidate, None
    return None, f"no session closed in the {LOOKBACK_DAYS} days before {day.isoformat()}"


def wake_window(info):
    scheduled = datetime.fromisoformat(info["scheduled_at"])
    return (scheduled - EARLY_SLACK,
            scheduled + timedelta(minutes=tolerance_minutes(info["checkpoint"])) + QUEUE_SLACK)


def found_receipts(artifacts, run_lookup, session_date, infos, branch="main", workflow_path=WORKFLOW_PATH):
    """(found, outside): checkpoint -> run id for this session's receipts whose run is this repository's own completed
    run of the expected workflow on `branch`, created inside the checkpoint's wake window; `outside` lists the
    otherwise valid receipts from runs created at another time (reported, never counted).

    The artifact name alone proves nothing. Any conclusion counts: a run that failed already sent its own email, and a
    cancelled one (a stall the liveness Worker cleared) still ran its always() receipt step; the question here is
    only whether the wake happened."""
    windows = {info["checkpoint"]: wake_window(info) for info in infos}
    found, outside = {}, {}
    for artifact in artifacts:
        if not isinstance(artifact, dict) or artifact.get("expired"):
            continue
        match = RECEIPT_NAME.match(str(artifact.get("name", "")))
        run = artifact.get("workflow_run")
        if not match or match.group(1) != session_date or match.group(2) not in windows:
            continue
        if not isinstance(run, dict) or run.get("head_branch") != branch or str(run.get("id")) != match.group(3):
            continue
        checkpoint = match.group(2)
        if checkpoint in found:
            continue
        details = run_lookup(run["id"])
        if not isinstance(details, dict) or details.get("status") != "completed" \
                or details.get("path") != workflow_path or not own_repository_run(details):
            continue
        # Created (dispatched) or started inside the window: a wake queued behind an earlier run decides its
        # checkpoint when it starts, so its start time is the one that matches.
        times = []
        for key in ("created_at", "run_started_at"):
            try:
                when = datetime.fromisoformat(str(details.get(key)).replace("Z", "+00:00"))
            except ValueError:
                continue  # an absent time proves nothing
            if when.tzinfo is not None:
                times.append(when)
        start, end = windows[checkpoint]
        if any(start <= when <= end for when in times):
            found[checkpoint] = run["id"]
            outside.pop(checkpoint, None)
        else:
            outside.setdefault(checkpoint, []).append(run["id"])
    return found, outside


def list_artifacts(gh_json, repository, session_date):
    """This repository's artifacts, newest first, down to the day before the session. A listing that hits MAX_PAGES
    before reaching that day is incomplete and raises: a partial list would report real receipts as missing."""
    floor = (datetime.fromisoformat(session_date) - timedelta(days=1)).date().isoformat()
    artifacts = []
    for page in range(1, MAX_PAGES + 1):
        listing = gh_json([f"repos/{repository}/actions/artifacts?per_page=100&page={page}"])
        if not isinstance(listing, dict) or not isinstance(listing.get("artifacts"), list):
            raise ValueError("artifact listing malformed")
        batch = listing["artifacts"]
        artifacts.extend(batch)
        if len(batch) < 100 or any(str(a.get("created_at", ""))[:10] < floor for a in batch if isinstance(a, dict)):
            return artifacts
    raise ValueError(f"artifact listing incomplete after {MAX_PAGES} pages")


def audit(now, gh_json, repository, session_date=None, branch="main"):
    """(exit code, report lines). 0: every expected checkpoint left a receipt, or nothing is due; 1: missing."""
    if session_date is None:
        session_date, reason = audit_target(now)
        if session_date is None:
            return 0, [f"AUDIT / nothing to audit: {reason}"]
    infos, close = expected_checkpoints(session_date)
    if not infos:
        return 0, [f"AUDIT / {session_date} / not a trading session; no checkpoint expected"]
    if now < close + GRACE:
        return 0, [f"AUDIT / {session_date} / not auditable before {(close + GRACE).isoformat()}"]
    artifacts = list_artifacts(gh_json, repository, session_date)
    found, outside = found_receipts(artifacts, lambda run_id: gh_json([f"repos/{repository}/actions/runs/{run_id}"]),
                                    session_date, infos, branch)
    expected = [info["checkpoint"] for info in infos]
    missing = [checkpoint for checkpoint in expected if checkpoint not in found]
    lines = []
    for checkpoint in expected:
        if checkpoint in found:
            lines.append(f"  {checkpoint}: run {found[checkpoint]}")
        elif checkpoint in outside:
            lines.append(f"  {checkpoint}: NO WAKE (receipt only from run(s) outside the wake window: "
                         + ", ".join(str(r) for r in outside[checkpoint]) + ")")
        else:
            lines.append(f"  {checkpoint}: NO RECEIPT")
    if missing:
        return 1, [f"MISSED / {session_date} / {len(missing)} of {len(expected)} checkpoints left no receipt from "
                   "their wake: " + ", ".join(missing), *lines]
    return 0, [f"AUDIT / {session_date} / all {len(expected)} checkpoints left a receipt", *lines]


def utc_now():
    return datetime.now(timezone.utc)

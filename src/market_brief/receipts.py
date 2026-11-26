"""One receipt per scheduled checkpoint run: what each stage of the run did, so outcomes are counted, not inferred.

Written by the workflow's last `always()` step from the step outcomes GitHub reports and the run's own metadata, and
uploaded as `market-brief-receipt-<session>-<checkpoint>-<run id>` (W43 S1a). A receipt is accounting only: it is
never restored, admitted or rendered, and its failure gates nothing. Reader delivery is not known here; it stays
`pending` until a later observer verifies the live page (S1b). Classes keep the denominators honest: a degraded
opening is a delivered fallback, not an accepted synthesis; a stale-price refusal is an explained collection failure,
not accepted data.
"""

import json
from datetime import datetime
from pathlib import Path

from .continuity import bundle_path
from .schedule import checkpoint_kind, checkpoint_session

RECEIPT_SCHEMA = "market-brief.receipt.v2"
STEPS = ("resolve", "restore", "pipeline", "continuity", "publish", "current",
         "deploy_attempt_1", "deploy_attempt_2", "deploy", "archive")
# Outcome classes under which the run itself claims a reader page. A claim only: the receipt audit also requires the
# workflow run's own conclusion to be success before it counts one as published.
PAGE_CLASSES = {"published": "published", "published_degraded": "published",
                "published_data_only": "published_data_only"}
# GitHub step outcomes; an absent step (never reached, or not in this run's path) reads as "" and is unknown.
OUTCOMES = {"success", "failure", "cancelled", "skipped"}


def run_metadata(root, session_date, checkpoint):
    """The metadata of this run's attempt at `checkpoint`, or None when the pipeline wrote none (a skip, a failed
    restore, a crash before the run folder existed). A fresh runner holds at most one such folder."""
    folder = Path(root) / "runs" / session_date
    found = sorted(folder.glob(f"live-{checkpoint.lower()}-*/metadata.json")) if folder.is_dir() else []
    if not found:
        return None
    try:
        value = json.loads(found[-1].read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def outcome_class(outcomes, metadata):
    """The one class a checkpoint run is counted under. Ordered: the earliest stage that decided the run wins."""
    if outcomes["restore"] == "failure":
        return "restore_failed"
    if outcomes["pipeline"] in ("cancelled", ""):
        return "not_finished"
    if metadata is None:
        return "skipped" if outcomes["pipeline"] == "success" else "pipeline_crashed"
    validation, error = metadata.get("validation"), metadata.get("error") or ""
    # WP-MB-RECOVERY-01: publication is recorded apart from the validator. A data-only page keeps its synthesis
    # FAILED (or not applicable on a refresh) and is classed by the page it published, never as an accepted analysis.
    data_only = metadata.get("publication") == "DATA_ONLY" and metadata.get("published") is True
    if outcomes["pipeline"] == "failure" and (validation != "FAILED" or data_only):
        # The pipeline step failed without a diagnosed refusal (a crash after the run folder existed).
        return "pipeline_crashed"
    if validation == "FAILED" and not data_only:
        if "no timestamped current prints" in error or "has no observation from this session" in error:
            return "refused_stale_data"
        return "synthesis_rejected" if metadata.get("kind") == "synthesis" else "refused"
    if outcomes["continuity"] == "failure":
        return "continuity_upload_failed"
    if outcomes["publish"] != "success":
        return "push_failed"
    if outcomes["current"] == "success" and outcomes["deploy"] != "success":
        return "deploy_failed" if outcomes["deploy"] in ("failure", "cancelled") else "not_deployed"
    if validation == "DEGRADED":
        return "published_degraded"
    if data_only:
        # Current observations only, no analysis; never an accepted synthesis.
        return "published_data_only"
    if not metadata.get("published", True):
        return "recorded_without_page"
    return "published"


def bundle_present(root):
    """Whether a continuity bundle is on disk as a regular file (never a symlink). The workflow uploads it with
    `if-no-files-found: ignore`, so a successful upload step alone does not prove a bundle was carried."""
    path = bundle_path(root)
    return not path.is_symlink() and path.is_file()


def stage_health(outcomes, metadata, outcome, bundle=False):
    """Each stage's own state for the receipt audit (WP-MB-RECOVERY-01), from the step outcomes, the run's metadata
    and whether a continuity bundle exists: collection, the page the run claims, evidence archived, continuity.
    Claims only; the audit decides publication."""
    if metadata is not None:
        collection = "completed" if metadata.get("evidence_hash") else "attempted"
    else:
        collection = "attempted" if outcomes["pipeline"] in ("failure", "cancelled") else "not_attempted"
    advanced = bool(((metadata or {}).get("continuity") or {}).get("advanced"))
    if outcomes["continuity"] == "failure":
        continuity = "upload_failed"
    elif not bundle:
        continuity = "not_present"  # nothing to carry: an ignored empty upload preserves and advances nothing
    elif outcomes["continuity"] == "success":
        # An advanced bundle uploaded, or the restored bundle re-uploaded unchanged.
        continuity = "advanced" if advanced else "preserved"
    elif not advanced:
        continuity = "unchanged"  # on disk, but its upload did not succeed: never claimed as preserved
    else:
        continuity = "unknown"
    evidence = {"success": "preserved", "failure": "not_preserved", "cancelled": "not_preserved",
                "skipped": "not_preserved"}.get(outcomes["archive"], "unknown")
    if evidence == "preserved" and not (metadata or {}).get("evidence_hash"):
        # The archive step ignores an empty upload: success with no collected evidence preserves nothing.
        evidence = "none_collected"
    data_only = (metadata or {}).get("data_only")
    return dict(collection=collection, page=PAGE_CLASSES.get(outcome, "not_published"), evidence=evidence,
                continuity=continuity, published=bool((metadata or {}).get("published")) and outcome in PAGE_CLASSES,
                data_only=data_only.get("basis") if isinstance(data_only, dict) else None,
                analysis_accepted=(metadata or {}).get("validation") == "PASS" and (metadata or {}).get("kind") ==
                "synthesis")


def build_receipt(environment, root, now):
    """The receipt for this run from its environment (`RECEIPT_*` step outcomes, the checkpoint, the run identity)."""
    checkpoint = environment.get("RECEIPT_CHECKPOINT", "")
    outcomes = {step: environment.get(f"RECEIPT_{step.upper()}", "").strip() for step in STEPS}
    outcomes = {step: (value if value in OUTCOMES else "") for step, value in outcomes.items()}
    # The receipt step runs minutes after the checkpoint, on the same exchange session.
    info = checkpoint_session(now, checkpoint)
    metadata = run_metadata(root, info["session_date"], checkpoint)
    started = (metadata or {}).get("started_at")
    kind = checkpoint_kind(checkpoint)
    model = (metadata or {}).get("model") or {}
    usage = model.get("usage") if isinstance(model.get("usage"), dict) else {}
    synthesis = None
    if kind == "synthesis":
        validation = (metadata or {}).get("validation")
        synthesis = dict(status={"PASS": "accepted", "DEGRADED": "rejected_degraded", "FAILED": "rejected"}.get(
                             validation, "not_run"),
                         calls=((metadata or {}).get("synthesis") or {}).get("calls", 0),
                         model=model.get("resolved_model") or model.get("requested_model"),
                         response_id=model.get("response_id"), prompt_tokens=usage.get("prompt_tokens"),
                         completion_tokens=usage.get("completion_tokens"), cost=usage.get("cost"),
                         repairs=len((metadata or {}).get("repairs") or []))
    scheduled_at = info["scheduled_at"]
    lag = (now - datetime.fromisoformat(scheduled_at)).total_seconds() if scheduled_at else None
    outcome = outcome_class(outcomes, metadata)
    return dict(
        **stage_health(outcomes, metadata, outcome, bundle_present(root)),
        schema_version=RECEIPT_SCHEMA, session_date=info["session_date"], checkpoint=checkpoint, kind=kind,
        run_id=environment.get("GITHUB_RUN_ID"), run_attempt=environment.get("GITHUB_RUN_ATTEMPT"),
        scheduled_at=scheduled_at, started_at=started, receipt_at=now.isoformat(), steps=outcomes,
        outcome=outcome,
        validation=(metadata or {}).get("validation"), publication=(metadata or {}).get("publication"),
        error=(metadata or {}).get("error"),
        coverage=(metadata or {}).get("coverage"), synthesis=synthesis,
        degraded_from=(metadata or {}).get("degraded_from"),
        publish_sha=environment.get("RECEIPT_PUBLISH_SHA") or None,
        # Deployed is GitHub's word; delivery is the reader's page, verified separately (S1b).
        deployed=outcomes["deploy"] == "success", delivery="pending" if outcomes["deploy"] == "success" else "none",
        lag_seconds=None if lag is None else round(lag),
        evidence_cutoff=(metadata or {}).get("target_time"), metadata_run=(metadata or {}).get("run_id"))


def write_receipt(root, receipt):
    folder = Path(root) / "runs" / "receipts"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{receipt['session_date']}-{receipt['checkpoint']}-{receipt['run_id'] or 'local'}.json"
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return path

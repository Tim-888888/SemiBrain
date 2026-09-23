"""Agent-owned retention metadata and terminal-run replica cleanup."""

from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from semibrain_common.runtime import database, failure, internal_identity, now

from semibrain_agent.citations import cited_markers

router = APIRouter()
TERMINAL = {"succeeded", "partial", "failed", "cancelled"}


class RetentionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: UUID
    owner_id: str = Field(max_length=128)
    snapshot_id: UUID | None = None
    content_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    lineage_aliases: list[str] = Field(default_factory=list, max_length=512)
    purge: bool = False


def lifecycle(form, db):
    run = db.runs.find_one({"_id": str(form.run_id)})
    # The business caller supplies ownership checked against its immutable job.
    if not run or run.get("command", {}).get("subject_ref") != form.owner_id:
        failure("RETENTION_RUN_UNAVAILABLE", 404)
    ref = f"web:{form.snapshot_id}:{form.content_hash}" if form.snapshot_id else None
    refs = list(dict.fromkeys([ref, *form.lineage_aliases])) if ref else []
    records = list(db.evidence.find({"lineage_refs": {"$in": refs}})) if refs else []
    latest, active = None, run.get("status") not in TERMINAL
    for item in records:
        owner_run = db.runs.find_one({"_id": item["run_id"]})
        if not owner_run or owner_run.get("command", {}).get("subject_ref") != form.owner_id:
            active = True  # Fail closed on inconsistent lineage.
            continue
        if owner_run.get("status") not in TERMINAL:
            active = True
        for report in db.reports.find({"run_id": item["run_id"]}):
            markers = cited_markers(report.get("body_markdown", ""))
            bound = any(c.get("evidence_id") == item["evidence_id"] and c.get("marker") in markers
                        for c in report.get("citation_bindings", []))
            stamp = owner_run.get("completed_at")
            if bound and stamp and (latest is None or stamp > latest):
                latest = stamp
    completed = run.get("completed_at")
    return {"active": active or completed is None, "completed_at": completed,
            "last_cited_at": latest, "policy_version": run.get("retention_version"),
            "run": run, "records": records}


def purge_replicas(db, run_id):
    # Terminal runs never resume in place; replies/resumes create a new run.
    # Preserve reports, source handles, task results, hashes, accounting and ACLs.
    db.model_turns.update_many({"run_id": run_id}, {"$unset": {"prompt_preview": "", "turn": ""},
        "$set": {"replicas_expired_at": now()}})
    db.observations.update_many({"run_id": run_id}, {"$unset": {"observation.evidence": ""},
        "$set": {"replicas_expired_at": now()}})
    db.tasks.update_many({"run_id": run_id}, {"$unset": {"state": ""}})
    db.context_compactions.update_many({"run_id": run_id},
        {"$unset": {"summary": "", "history_manifest": "", "covered_hashes": ""},
         "$set": {"replicas_expired_at": now()}})
    db.checkpoints.delete_many({"thread_id": run_id})
    db.checkpoint_writes.delete_many({"thread_id": run_id})
    db.runs.update_one({"_id": run_id}, {"$unset": {"committed_checkpoint_ref": "", "context_heads": ""},
        "$set": {"replicas_expired_at": now()}})


@router.post("/internal/v1/retention/snapshot")
def snapshot(form: RetentionRequest, request: Request):
    internal_identity(request, {"business"})
    db = database("agent")
    status = lifecycle(form, db)
    if form.purge:
        if status["active"] or status["policy_version"] != 1:
            failure("RETENTION_PROTECTED", 409)
        days = 90 if status["last_cited_at"] else 30
        expires = (status["last_cited_at"] or status["completed_at"]) + timedelta(days=days)
        if not form.snapshot_id or expires > now():
            failure("RETENTION_NOT_DUE", 409)
        ref = f"web:{form.snapshot_id}:{form.content_hash}"
        db.evidence.update_many({"lineage_refs": {"$in": list(dict.fromkeys([ref, *form.lineage_aliases]))}}, {"$unset": {"content": "", "text": ""},
            "$set": {"body_expired_at": now()}})
        for run_id in {r["run_id"] for r in status["records"]}:
            purge_replicas(db, run_id)
    return {k: v for k, v in status.items() if k not in {"run", "records"}}


class SweepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dry_run: bool = True


@router.post("/internal/v1/retention/replicas")
def sweep(form: SweepRequest, request: Request):
    internal_identity(request, {"business"})
    db = database("agent")
    rows = list(db.runs.find({"retention_version": 1,
        "status": {"$in": list(TERMINAL)}, "completed_at": {"$lt": now() - timedelta(days=7)},
        "replicas_expired_at": {"$exists": False}}).limit(100))
    if not form.dry_run:
        for row in rows:
            purge_replicas(db, row["_id"])
    return {"dry_run": form.dry_run, "eligible_runs": len(rows), "run_ids": [r["_id"] for r in rows]}

"""Observed run metrics and explicit human labels; execution success is not quality."""

import math
from collections import Counter
from uuid import UUID

from fastapi import APIRouter, Query, Request
from semibrain_common.runtime import (
    canonical,
    database,
    digest,
    failure,
    internal_identity,
    now,
    transaction,
)
from semibrain_contracts.evaluation import EvaluationCommand

router = APIRouter()


def db():
    return database("agent")


def summarize(items):
    completed = [row for row in items if row["status"] in {"succeeded", "partial", "failed", "cancelled"}]
    duration = sorted(row["elapsed_ms"] for row in completed if row.get("elapsed_ms") is not None)
    labeled = [row["evaluation"] for row in items if row.get("evaluation")]
    checked = [row for row in labeled if row["grounding"] != "not_checked"]
    classifications = [(row["expected_action"], row.get("predicted_action")) for row in labeled if row.get("expected_action")]
    classes = sorted({expected for expected, _ in classifications} | {actual for _, actual in classifications if actual})
    f1 = []
    for label in classes:
        tp = sum(a == b == label for a, b in classifications)
        fp = sum(a != label and b == label for a, b in classifications)
        fn = sum(a == label and b != label for a, b in classifications)
        f1.append(2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0)
    return {"run_count": len(items), "terminal_count": len(completed),
        "status_counts": dict(Counter(row["status"] for row in items)),
        "p95_elapsed_ms": duration[max(0, math.ceil(len(duration) * .95) - 1)] if duration else None,
        "elapsed_sample_count": len(duration), "human_label_count": len(labeled),
        "quality_counts": dict(Counter(row["outcome"] for row in labeled)),
        "grounding_checked_count": len(checked),
        "grounding_supported_rate": sum(row["grounding"] == "supported" for row in checked) / len(checked) if checked else None,
        "action_label_count": len(classifications), "action_macro_f1": sum(f1) / len(f1) if f1 else None,
        "currency_cost": None, "cost_status": "price_not_configured"}


@router.get("/internal/v1/admin/evaluations")
def overview(request: Request, actor_id: UUID, limit: int = Query(default=50, ge=1, le=100)):
    internal_identity(request, {"conversation"})
    owner = str(actor_id)
    rows = list(db().runs.find({"command.subject_ref": owner}, {"command.input.question": 0, "body_draft": 0}).sort("created_at", -1).limit(limit))
    ids = [row["_id"] for row in rows]
    calls = list(db().model_calls.find({"run_id": {"$in": ids}}, {"usage": 1, "run_id": 1}).limit(10001))
    truncated = len(calls) > 10000
    calls = calls[:10000]
    by_run = {}
    for call in calls:
        metrics = by_run.setdefault(call["run_id"], {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0, "unknown_usage": 0, "unknown_cache": 0})
        metrics["requests"] += 1
        usage = call.get("usage") or {}
        if not all(isinstance(usage.get(key), int) and not isinstance(usage[key], bool) and usage[key] >= 0 for key in ("input_tokens", "output_tokens")):
            metrics["unknown_usage"] += 1
            continue
        for key in ("input_tokens", "output_tokens"):
            metrics[key] += usage[key]
        cached = usage.get("cached_input_tokens")
        if isinstance(cached, int) and not isinstance(cached, bool) and 0 <= cached <= usage["input_tokens"]:
            metrics["cached_input_tokens"] += usage["cached_input_tokens"]
        else:
            metrics["unknown_cache"] += 1
    labels = {row["run_id"]: row for row in db().evaluations.find({"actor_id": owner, "run_id": {"$in": ids}})}
    items = []
    for row in rows:
        elapsed = max(0, int((row["completed_at"] - row["started_at"]).total_seconds() * 1000)) if row.get("completed_at") and row.get("started_at") else None
        items.append({"run_id": row["_id"], "status": row["status"], "strategy": row.get("strategy", "quick_qa"),
            "created_at": row["created_at"], "elapsed_ms": elapsed, "configuration_version": (row.get("agent_configuration") or {}).get("version"),
            "predicted_action": row.get("understanding", {}).get("action"), "model_usage": by_run.get(row["_id"]),
            "evaluation": labels.get(row["_id"])})
    return {"items": items, "summary": summarize(items), "limit": limit, "calls_truncated": truncated,
        "scope": "current_admin_latest_runs", "method": "explicit_human_labels_not_execution_status"}


@router.post("/internal/v1/admin/evaluations")
def save(form: EvaluationCommand, request: Request):
    internal_identity(request, {"conversation"})
    owner, run_id = str(form.actor_id), str(form.edit.run_id)
    identity = digest(canonical([owner, run_id]))
    hashed = digest(canonical(form.model_dump(mode="json")))
    def commit(session):
        prior = db().evaluation_commands.find_one({"_id": str(form.edit.request_id)}, session=session)
        if prior:
            if prior["payload_hash"] != hashed:
                failure("IDEMPOTENCY_CONFLICT", 409)
            return prior["result"]
        run = db().runs.find_one({"_id": run_id, "command.subject_ref": owner}, session=session)
        if not run or run["status"] not in {"succeeded", "partial", "failed", "cancelled"}:
            failure("EVALUATION_RUN_UNAVAILABLE", 409)
        old = db().evaluations.find_one({"_id": identity}, session=session)
        revision = old["revision"] if old else 0
        if revision != form.edit.expected_revision:
            failure("REVISION_CONFLICT", 409)
        reviewed_at = now()
        # BSON persists milliseconds. Return that same precision on first write
        # so a retried command is byte-for-byte equivalent after a DB round trip.
        reviewed_at = reviewed_at.replace(microsecond=reviewed_at.microsecond // 1000 * 1000)
        row = {"_id": identity, "actor_id": owner, "run_id": run_id, "revision": revision + 1,
            **form.edit.model_dump(mode="json", exclude={"request_id", "expected_revision", "run_id"}),
            "predicted_action": run.get("understanding", {}).get("action"), "reviewed_at": reviewed_at}
        db().evaluations.replace_one({"_id": identity}, row, upsert=True, session=session)
        db().evaluation_commands.insert_one({"_id": str(form.edit.request_id), "payload_hash": hashed, "result": row,
            "actor_id": owner, "created_at": now()}, session=session)
        return row
    return transaction(commit)

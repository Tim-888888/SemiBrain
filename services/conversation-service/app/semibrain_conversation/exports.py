"""Exports are generated from authorized immutable reports, not client HTML."""

from datetime import timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, Request
from pymongo import ReturnDocument
from semibrain_common.runtime import failure, internal_identity, now
from semibrain_contracts.exports import ExportRequest, ExportSource

from semibrain_conversation.access import business, run_snapshot
from semibrain_conversation.auth import current_user, db

router = APIRouter()


@router.post("/v1/runs/{run_id}/exports", status_code=202)
def create(run_id: UUID, form: ExportRequest, user=Depends(current_user)):
    run_snapshot(user, str(run_id))
    return business(
        user,
        "POST",
        "/internal/v1/exports",
        operation="report.export",
        json={**form.model_dump(mode="json"), "run_id": str(run_id)},
    ).json()


@router.get("/v1/exports/{export_id}")
def status(export_id: UUID, user=Depends(current_user)):
    return business(
        user, "GET", f"/internal/v1/exports/{export_id}", operation="report.export"
    ).json()


@router.get("/v1/runs/{run_id}/exports")
def list_exports(run_id: UUID, user=Depends(current_user)):
    run_snapshot(user, str(run_id))
    result = business(
        user, "GET", f"/internal/v1/report-exports/{run_id}", operation="report.export"
    ).json()
    row = db().gateway_runs.find_one({"_id": str(run_id), "owner_id": user["_id"]})
    result["automatic"] = public_automatic((row or {}).get("answer_export"))
    return result


def public_automatic(value):
    if not isinstance(value, dict):
        return None
    return {k: value[k] for k in ("format", "status", "export_id", "asset_id", "filename", "error") if k in value}


def process_automatic():
    """Durable post-report delivery; retries share the same business idempotency key."""
    moment = now()
    row = db().gateway_runs.find_one_and_update(
        {"answer_export.status": {"$in": ["pending", "running"]}, "$and": [
            {"$or": [{"answer_export.lease_until": {"$lte": moment}}, {"answer_export.lease_until": {"$exists": False}}]},
            {"$or": [{"answer_export.next_at": {"$lte": moment}}, {"answer_export.next_at": {"$exists": False}}]},
        ]},
        {"$set": {"answer_export.lease_until": moment + timedelta(seconds=90)},
         "$inc": {"answer_export.fence": 1}},
        sort=[("created_at", 1)], return_document=ReturnDocument.AFTER,
    )
    if not row:
        return False
    item = row["answer_export"]
    predicate = {"_id": row["_id"], "answer_export.fence": item["fence"],
                 "answer_export.lease_until": {"$gt": moment}}
    changes = {"lease_until": moment, "next_at": moment + timedelta(seconds=2)}
    try:
        if item["deadline_at"] <= moment:
            failure("EXPORT_DELIVERY_TIMEOUT", 409)
        if row.get("cancel_requested_at") or row.get("status") not in {"succeeded", "partial"}:
            failure("EXPORT_RUN_UNAVAILABLE", 409)
        user = db().users.find_one({"_id": row["owner_id"], "enabled": True, "auth_version": row["auth_version"]})
        if not user:
            failure("EXPORT_OWNER_REVOKED", 403)
        if not item.get("export_id"):
            request_id = str(uuid5(NAMESPACE_URL, "semibrain:answer-export:" + row["_id"] + ":" + item["report_id"] + ":md"))
            result = business(user, "POST", "/internal/v1/exports", operation="report.export",
                              json={"request_id": request_id, "run_id": row["_id"], "format": "md"}).json()
        else:
            result = business(user, "GET", "/internal/v1/exports/" + item["export_id"], operation="report.export").json()
        changes.update({k: result[k] for k in ("export_id", "asset_id", "filename", "error") if k in result})
        changes["status"] = result["status"] if result["status"] in {"succeeded", "failed"} else "running"
        changes["failures"] = 0
    except Exception as exc:
        count = item.get("failures", 0) + 1
        detail = getattr(exc, "detail", {})
        code = detail.get("code", "EXPORT_SERVICE_UNAVAILABLE") if isinstance(detail, dict) else "EXPORT_SERVICE_UNAVAILABLE"
        terminal = count >= 3 or getattr(exc, "status_code", None) in {403, 404, 410} or item["deadline_at"] <= moment
        changes.update(status="failed" if terminal else item["status"], failures=count, error=code,
                       next_at=moment + timedelta(seconds=min(60, 5 * count)))
    # Do not overwrite a newer claim or touch the content's succeeded/partial status.
    predicate["answer_export.lease_until"] = {"$gt": now()}
    db().gateway_runs.update_one(predicate, {"$set": {"answer_export." + k: v for k, v in changes.items()}})
    return True


@router.post("/internal/v1/exports/source")
def source(form: ExportSource, request: Request):
    internal_identity(request, {"business"})
    user = db().users.find_one(
        {"_id": str(form.owner_id), "enabled": True, "auth_version": form.auth_version}
    )
    if not user:
        failure("EXPORT_OWNER_REVOKED", 403)
    report = run_snapshot(user, str(form.run_id))
    if not report.get("report_id") or report["status"] not in {"succeeded", "partial"}:
        failure("EXPORT_FINAL_REPORT_REQUIRED", 409)
    if form.content_hash and form.content_hash != report["content_hash"]:
        failure("EXPORT_SOURCE_CHANGED", 409)
    return {
        **{
            key: report.get(key)
            for key in (
                "run_id",
                "report_id",
                "revision",
                "body_markdown",
                "content_hash",
                "citations",
                "image_refs",
                "lineage_refs",
            )
        },
        "claim": {
            "subject_id": user["_id"],
            "auth_version": user["auth_version"],
            "role": user["role"],
            "resource_ids": [*user.get("resource_ids", ["demo"]), "owner:" + user["_id"]],
            "run_id": None,
        },
    }

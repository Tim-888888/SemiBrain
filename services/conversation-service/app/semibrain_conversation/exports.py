"""Exports are generated from authorized immutable reports, not client HTML."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from semibrain_common.runtime import failure, internal_identity
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
    return business(
        user, "GET", f"/internal/v1/report-exports/{run_id}", operation="report.export"
    ).json()


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

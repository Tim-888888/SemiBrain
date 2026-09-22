"""Authenticated gateway routes; knowledge and query records stay in the business service."""

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from semibrain_common.runtime import failure

from semibrain_conversation.access import business
from semibrain_conversation.auth import admin, current_user

router = APIRouter()


@router.post("/v1/attachments/images", status_code=201)
def upload_image(file: UploadFile = File(...), allow_external: bool = Form(False),
                 user=Depends(current_user)):
    raw = file.file.read(3 * 1024**2 + 1)
    if not raw or len(raw) > 3 * 1024**2:
        failure("IMAGE_SIZE_INVALID", 413)
    return business(user, "POST", "/internal/v1/attachments/images", operation="attachment.upload",
                    files={"file": (file.filename, raw, file.content_type)},
                    data={"allow_external": str(allow_external).lower()}).json()


@router.post("/v1/attachments/{asset_id}/revoke")
def revoke_attachment(asset_id: UUID, user=Depends(current_user)):
    return business(user, "POST", f"/internal/v1/attachments/{asset_id}/revoke",
                    operation="attachment.upload").json()


@router.get("/v1/knowledge/documents")
def documents(user=Depends(current_user)):
    return business(
        user, "GET", "/internal/v1/knowledge/documents", operation="knowledge.read"
    ).json()


@router.post("/admin/v1/knowledge/uploads", status_code=202)
def upload(
    request: Request,
    file: UploadFile = File(...),
    request_id: UUID = Form(...),
    document_path: str = Form(...),
    visibility: str = Form("demo"),
    data_origin: str = Form("public"),
    allow_external: bool = Form(False),
    document_id: str = Form(""),
    expected_revision: int = Form(0),
    user=Depends(admin),
):
    raw = file.file.read(32 * 1024**2 + 1)
    if len(raw) > 32 * 1024**2:
        failure("UPLOAD_TOO_LARGE", 413)
    return business(
        user,
        "POST",
        "/internal/v1/knowledge/uploads",
        operation="knowledge.manage",
        timeout=120,
        files={"file": (file.filename, raw, file.content_type)},
        data={
            "request_id": str(request_id),
            "document_path": document_path,
            "visibility": visibility,
            "data_origin": data_origin,
            "allow_external": str(allow_external).lower(),
            "document_id": document_id,
            "expected_revision": str(expected_revision),
        },
    ).json()


@router.get("/admin/v1/knowledge/jobs/{job_id}")
def ingestion(job_id: UUID, user=Depends(admin)):
    return business(
        user, "GET", "/internal/v1/knowledge/jobs/" + str(job_id), operation="knowledge.read"
    ).json()


@router.get("/admin/v1/knowledge/documents/{document_id}/preview")
def preview(document_id: UUID, version: UUID, user=Depends(admin)):
    return business(
        user,
        "GET",
        "/internal/v1/knowledge/documents/" + str(document_id) + "/preview",
        operation="knowledge.read",
        params={"version": str(version)},
    ).json()


class Publication(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int
    version: UUID


@router.post("/admin/v1/knowledge/documents/{document_id}/publish")
def publish(document_id: UUID, form: Publication, user=Depends(admin)):
    return business(
        user,
        "POST",
        "/internal/v1/knowledge/documents/" + str(document_id) + "/publish",
        operation="knowledge.manage",
        json=form.model_dump(mode="json"),
    ).json()


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int


@router.post("/admin/v1/knowledge/documents/{document_id}/unpublish")
def unpublish(document_id: UUID, form: Revision, user=Depends(admin)):
    return business(
        user,
        "POST",
        "/internal/v1/knowledge/documents/" + str(document_id) + "/unpublish",
        operation="knowledge.manage",
        json=form.model_dump(mode="json"),
    ).json()


@router.get("/v1/assets/{asset_id}/content")
def download(asset_id: UUID, user=Depends(current_user)):
    response = business(
        user,
        "GET",
        "/internal/v1/assets/" + str(asset_id) + "/content",
        operation="asset.read",
        timeout=60,
    )
    return Response(
        response.content,
        headers={
            name: response.headers[name]
            for name in (
                "content-type",
                "content-disposition",
                "cache-control",
                "x-content-type-options",
            )
            if name in response.headers
        },
    )


@router.get("/v1/business/tools")
def tools(user=Depends(current_user)):
    return business(user, "GET", "/internal/v1/tools", operation="business.catalog").json()


class ToolCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    logical_call_id: UUID
    tool: str
    arguments: dict


@router.post("/v1/business/jobs", status_code=202)
def query(form: ToolCommand, user=Depends(current_user)):
    from semibrain_conversation.access import RUN_OPS

    if form.tool not in RUN_OPS or not form.tool.startswith("business."):
        failure("TOOL_DENIED", 403)
    return business(
        user,
        "POST",
        "/internal/v1/tool-jobs",
        operation=form.tool,
        json=form.model_dump(mode="json"),
    ).json()


@router.get("/v1/business/jobs/{job_id}")
def result(job_id: UUID, tool: str, user=Depends(current_user)):
    from semibrain_conversation.access import RUN_OPS

    if tool not in RUN_OPS:
        failure("TOOL_DENIED", 403)
    return business(user, "GET", "/internal/v1/tool-jobs/" + str(job_id), operation=tool).json()

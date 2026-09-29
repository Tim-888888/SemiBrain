"""Knowledge management commands are validated and delegated to the owning service."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from semibrain_contracts.knowledge_review import ReviewParsed

from semibrain_conversation.access import business
from semibrain_conversation.auth import admin

router = APIRouter()


@router.post("/admin/v1/knowledge/jobs/{job_id}/cancel")
def cancel_ingestion(job_id: UUID, user=Depends(admin)):
    return business(user, "POST", f"/internal/v1/knowledge/jobs/{job_id}/cancel",
                    operation="knowledge.manage").json()


@router.post("/admin/v1/knowledge/documents/{document_id}/review", status_code=202)
def review(document_id: UUID, form: ReviewParsed, user=Depends(admin)):
    return business(user, "POST", f"/internal/v1/knowledge/documents/{document_id}/review",
                    operation="knowledge.manage", json=form.model_dump(mode="json")).json()


class EditChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=1)
    source_version: UUID
    chunk_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    text: str = Field(min_length=1, max_length=200000)


class Rollback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=1)
    version: UUID


@router.get("/admin/v1/knowledge/documents/{document_id}/versions")
def versions(document_id: UUID, user=Depends(admin)):
    return business(user, "GET", f"/internal/v1/knowledge/documents/{document_id}/versions", operation="knowledge.manage").json()


@router.get("/admin/v1/knowledge/documents/{document_id}/versions/{version}/{section}")
def content(document_id: UUID, version: UUID, section: Literal["chunks", "diff"], user=Depends(admin)):
    return business(user, "GET", f"/internal/v1/knowledge/documents/{document_id}/versions/{version}/{section}",
                    operation="knowledge.manage").json()


@router.post("/admin/v1/knowledge/documents/{document_id}/edit", status_code=202)
def edit(document_id: UUID, form: EditChunk, user=Depends(admin)):
    return business(user, "POST", f"/internal/v1/knowledge/documents/{document_id}/edit",
                    operation="knowledge.manage", json=form.model_dump(mode="json")).json()


@router.post("/admin/v1/knowledge/documents/{document_id}/rollback")
def rollback(document_id: UUID, form: Rollback, user=Depends(admin)):
    return business(user, "POST", f"/internal/v1/knowledge/documents/{document_id}/rollback",
                    operation="knowledge.manage", json=form.model_dump(mode="json")).json()

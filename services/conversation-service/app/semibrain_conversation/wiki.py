"""Wiki UI gateway; all content and provenance are owned by the business service."""

from uuid import UUID

from fastapi import APIRouter, Depends
from semibrain_contracts.wiki import WikiDraft

from semibrain_conversation.access import business
from semibrain_conversation.auth import admin, current_user

router = APIRouter()


@router.get("/v1/wiki/pages")
def pages(user=Depends(current_user)):
    return business(user, "GET", "/internal/v1/wiki/pages", operation="knowledge.read").json()


@router.get("/v1/wiki/pages/{document_id}")
def page(document_id: UUID, user=Depends(current_user)):
    return business(user, "GET", "/internal/v1/wiki/pages/" + str(document_id), operation="knowledge.read").json()


@router.post("/admin/v1/wiki/drafts", status_code=202)
def draft(form: WikiDraft, user=Depends(admin)):
    return business(user, "POST", "/internal/v1/wiki/drafts", operation="knowledge.manage",
                    json=form.model_dump(mode="json")).json()


@router.get("/admin/v1/wiki/pages/{document_id}/revisions/{version}")
def revision(document_id: UUID, version: UUID, user=Depends(admin)):
    return business(user, "GET", "/internal/v1/wiki/pages/" + str(document_id), operation="knowledge.read",
                    params={"version": str(version)}).json()

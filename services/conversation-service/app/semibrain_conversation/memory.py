"""Own-memory gateway: user identity and report authorization never come from model text."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from semibrain_common.runtime import call, failure, internal_identity
from semibrain_contracts.memory import MemoryDelete, MemoryEdit, MemorySetting, MemorySource

from semibrain_conversation.access import check_lineage, run_snapshot
from semibrain_conversation.auth import current_user, db

router = APIRouter()


def identity(user):
    return {"owner_id": user["_id"], "auth_version": user["auth_version"]}


@router.get("/v1/memories")
def memories(user=Depends(current_user)):
    return call("agent", "POST", "/internal/v1/memories/list", json=identity(user)).json()


@router.post("/v1/memories")
def save(form: MemoryEdit, user=Depends(current_user)):
    return call("agent", "POST", "/internal/v1/memories", json={**identity(user), "edit": form.model_dump(mode="json")}).json()


@router.post("/v1/memories/settings")
def configure(form: MemorySetting, user=Depends(current_user)):
    return call("agent", "POST", "/internal/v1/memories/settings", json={**identity(user), "change": form.model_dump(mode="json")}).json()


@router.post("/v1/memories/{memory_id}/delete")
def remove(memory_id: UUID, form: MemoryDelete, user=Depends(current_user)):
    return call("agent", "POST", f"/internal/v1/memories/{memory_id}/delete",
                json={**identity(user), "change": form.model_dump(mode="json")}).json()


@router.post("/internal/v1/memories/authorize")
def authorize(form: MemorySource, request: Request):
    internal_identity(request, {"agent"})
    user = db().users.find_one({"_id": str(form.owner_id), "enabled": True, "auth_version": form.auth_version})
    if not user:
        failure("MEMORY_OWNER_REVOKED", 403)
    check_lineage(user, form.lineage_refs)
    if not form.source_run_id:
        return {"valid": True}
    prior = run_snapshot(user, str(form.source_run_id))
    if prior.get("status") not in {"succeeded", "partial"} or not prior.get("body_markdown"):
        failure("MEMORY_SOURCE_UNAVAILABLE", 409)
    # Current versions, rather than historical read grants, govern reuse.
    check_lineage(user, prior.get("lineage_refs", []))
    return {key: prior.get(key) for key in ("run_id", "report_id", "body_markdown", "content_hash", "lineage_refs")}

